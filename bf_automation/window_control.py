"""Task-scoped background window inventory, capture and control."""

from __future__ import annotations

import ctypes
import hashlib
import io
import threading
import time
from functools import wraps
from types import SimpleNamespace
from typing import Any, ClassVar

from .lease import DesktopLease


def _uia_thread(method):
    @wraps(method)
    def invoke(self, *args, **kwargs):
        with self.uia.UIAutomationInitializerInThread():
            return method(self, *args, **kwargs)
    return invoke


class BackgroundControlUnavailable(RuntimeError):
    """Raised when an action cannot be completed without foreground input."""


class WindowController:
    _locks_guard = threading.Lock()
    _window_locks: ClassVar[dict[tuple, Any]] = {}

    def __init__(self, store, *, win_api=None, capture_backend=None, desktop_lease=None):
        self.store = store
        self.win_api = win_api or NativeWinApi()
        self.capture_backend = capture_backend or WindowCaptureBackend()
        self.desktop_lease = desktop_lease or DesktopLease()

    def _operation_lock(self, record):
        key = (str(self.store.state_root), record["hwnd"], record["pid"])
        with self._locks_guard:
            return self._window_locks.setdefault(key, threading.RLock())

    def inventory(
        self,
        token: str,
        *,
        include_hidden: bool = False,
        title_filter: str | None = None,
        process_id: int | None = None,
    ) -> dict:
        self.store.require(token)
        output = []
        for item in self.win_api.inventory(
            include_hidden=include_hidden,
            title_filter=title_filter,
            process_id=process_id,
        ):
            window_id = self.store.bind_window(
                token, hwnd=item["hwnd"], pid=item["pid"], title=item["title"]
            )
            public = {key: value for key, value in item.items() if key != "hwnd"}
            public["window_id"] = window_id
            output.append(public)
        return {"count": len(output), "windows": output}

    def _window(self, token: str, window_id: str) -> dict:
        record = self.store.resolve_window(token, window_id)
        if not self.win_api.validate_window(record["hwnd"], record["pid"]):
            raise LookupError("window_id is stale")
        return record

    def screenshot(self, token: str, window_id: str, *, allow_staging: bool = True) -> dict:
        record = self._window(token, window_id)
        with self._operation_lock(record):
            if getattr(self.win_api, "is_minimized", lambda hwnd: False)(record["hwnd"]):
                self.store.claim_window(token, window_id)
                with self.desktop_lease.hold(token):
                    capture = self.capture_backend.capture(record["hwnd"], allow_staging=allow_staging)
            else:
                capture = self.capture_backend.capture(record["hwnd"], allow_staging=allow_staging)
        capture = dict(capture)
        png_bytes = capture.pop("png_bytes", None)
        if png_bytes is not None:
            task_dir, _ = self.store.require(token)
            captures_dir = task_dir / "captures"
            captures_dir.mkdir(parents=True, exist_ok=True)
            output = captures_dir / f"{window_id}-{time.time_ns()}.png"
            output.write_bytes(png_bytes)
            capture["path"] = str(output)
            capture["sha256"] = hashlib.sha256(png_bytes).hexdigest()
        capture["window_id"] = window_id
        capture["pid"] = record["pid"]
        capture["title"] = record["title"]
        return {"window_capture": capture}

    def snapshot(
        self,
        token: str,
        window_id: str,
        *,
        include_image: bool = True,
        max_elements: int = 120,
        allow_staging: bool = True,
    ) -> dict:
        if max_elements < 1 or max_elements > 200:
            raise ValueError("max_elements must be between 1 and 200")
        record = self._window(token, window_id)
        with self._operation_lock(record):
            snapshot = dict(self.win_api.snapshot(record["hwnd"], max_elements=max_elements))
        snapshot.update(
            {
                "window_id": window_id,
                "pid": record["pid"],
                "title": record["title"],
            }
        )
        if include_image:
            snapshot["capture"] = self.screenshot(
                token, window_id, allow_staging=allow_staging
            )["window_capture"]
        return {"window_snapshot": snapshot}

    def control(
        self,
        token: str,
        window_id: str,
        *,
        action: str,
        element_id: str | None = None,
        text: str | None = None,
        clear: bool = False,
        loc: list[int] | None = None,
        button: str = "left",
        clicks: int = 1,
        shortcut: str | None = None,
        allow_foreground_fallback: bool = False,
    ) -> dict:
        record = self._window(token, window_id)
        kwargs = {
            "action": action,
            "element": element_id,
            "text": text,
            "clear": clear,
            "loc": loc,
            "button": button,
            "clicks": clicks,
        }
        if shortcut is not None or action == "shortcut":
            kwargs["shortcut"] = shortcut
        with self._operation_lock(record):
            self.store.claim_window(token, window_id)
            return self._dispatch_control(token, window_id, record, kwargs, allow_foreground_fallback)

    def _dispatch_control(self, token, window_id, record, kwargs, allow_foreground_fallback):
        try:
            result = self.win_api.control(record["hwnd"], **kwargs)
        except BackgroundControlUnavailable as exc:
            if not allow_foreground_fallback:
                return {
                    "accepted": False,
                    "verified": False,
                    "verification_required": True,
                    "mode": "background_refused",
                    "reason": str(exc),
                    "window_id": window_id,
                    "foreground_fallback_allowed": False,
                }
            with self.desktop_lease.hold(token):
                result = self.win_api.foreground_control(record["hwnd"], **kwargs)
        result = dict(result)
        result.update(
            {
                "window_id": window_id,
                "foreground_fallback_allowed": bool(allow_foreground_fallback),
            }
        )
        return result


class NativeWinApi:
    def __init__(self):
        import win32con
        import win32gui
        import win32process
        import windows_mcp.uia as uia

        self.win32con = win32con
        self.win32gui = win32gui
        self.win32process = win32process
        self.uia = uia
        self._elements: dict[tuple[int, str], Any] = {}

    def inventory(self, *, include_hidden=False, title_filter=None, process_id=None):
        windows = []
        title_filter_folded = title_filter.casefold() if title_filter else None

        def collect(hwnd, _):
            if not self.win32gui.IsWindow(hwnd):
                return True
            visible = bool(self.win32gui.IsWindowVisible(hwnd))
            if not include_hidden and not visible:
                return True
            title = self.win32gui.GetWindowText(hwnd) or ""
            if not title.strip():
                return True
            if title_filter_folded and title_filter_folded not in title.casefold():
                return True
            _, pid = self.win32process.GetWindowThreadProcessId(hwnd)
            if process_id is not None and int(pid) != int(process_id):
                return True
            try:
                rect = list(self.win32gui.GetWindowRect(hwnd))
            except Exception:
                rect = None
            minimized = bool(self.win32gui.IsIconic(hwnd))
            windows.append(
                {
                    "hwnd": int(hwnd),
                    "pid": int(pid),
                    "title": title,
                    "class_name": self.win32gui.GetClassName(hwnd),
                    "visible": visible,
                    "minimized": minimized,
                    "window_status": "minimized" if minimized else ("normal" if visible else "hidden"),
                    "rect": rect,
                }
            )
            return True

        self.win32gui.EnumWindows(collect, None)
        return windows

    def validate_window(self, hwnd, pid):
        if not self.win32gui.IsWindow(int(hwnd)):
            return False
        _, actual_pid = self.win32process.GetWindowThreadProcessId(int(hwnd))
        return int(actual_pid) == int(pid)

    def is_minimized(self, hwnd):
        return bool(self.win32gui.IsIconic(int(hwnd)))

    @staticmethod
    def _safe(control, attribute, default=None):
        try:
            return getattr(control, attribute)
        except Exception:
            return default

    @staticmethod
    def _rect(value):
        if value is None:
            return None
        if all(hasattr(value, name) for name in ("left", "top", "right", "bottom")):
            return [int(value.left), int(value.top), int(value.right), int(value.bottom)]
        if all(hasattr(value, name) for name in ("x", "y", "width", "height")):
            return [
                int(value.x),
                int(value.y),
                int(value.x + value.width),
                int(value.y + value.height),
            ]
        return None

    @_uia_thread
    def snapshot(self, hwnd, *, max_elements=120):
        root = self.uia.ControlFromHandle(int(hwnd))
        for key in list(self._elements):
            if key[0] == int(hwnd):
                del self._elements[key]
        elements = []
        if root is not None:
            for index, (control, depth) in enumerate(
                self.uia.WalkControl(root, includeTop=False, maxDepth=12)
            ):
                if index >= max_elements:
                    break
                name = str(self._safe(control, "Name", "") or "")
                automation_id = str(self._safe(control, "AutomationId", "") or "")
                class_name = str(self._safe(control, "ClassName", "") or "")
                control_type = str(self._safe(control, "ControlTypeName", control.__class__.__name__) or "")
                native_hwnd = int(self._safe(control, "NativeWindowHandle", 0) or 0)
                native_text = ""
                if native_hwnd and not self._safe(control, "IsPassword", False):
                    try:
                        if class_name.casefold() == "edit":
                            style = self.win32gui.GetWindowLong(native_hwnd, self.win32con.GWL_STYLE)
                            if not (style & 0x20):
                                native_text = self._read_native_text(native_hwnd, 32769)
                        else:
                            native_text = self.win32gui.GetWindowText(native_hwnd) or ""
                    except Exception:
                        native_text = ""
                try:
                    runtime_id = tuple(control.GetRuntimeId())
                except Exception:
                    runtime_id = ()
                fingerprint = hashlib.sha256(
                    f"{hwnd}\0{runtime_id}\0{index}\0{depth}\0{name}\0{automation_id}\0{class_name}\0{control_type}".encode(
                        "utf-8"
                    )
                ).hexdigest()[:20]
                element_id = f"bfe_{fingerprint}"
                self._elements[(int(hwnd), element_id)] = {"runtime_id": runtime_id}
                elements.append(
                    {
                        "element_id": element_id,
                        "name": name,
                        "automation_id": automation_id,
                        "class_name": class_name,
                        "control_type": control_type,
                        "native_handle": native_hwnd or None,
                        "native_text": native_text,
                        "enabled": bool(self._safe(control, "IsEnabled", False)),
                        "focusable": bool(self._safe(control, "IsKeyboardFocusable", False)),
                        "rect": self._rect(self._safe(control, "BoundingRectangle")),
                        "depth": int(depth),
                    }
                )
        self._append_common_dialog_native_children(int(hwnd), elements, max_elements)
        return {"elements": elements, "element_count": len(elements)}

    def _append_common_dialog_native_children(self, hwnd, elements, max_elements):
        try:
            if self.win32gui.GetClassName(hwnd).casefold() != "#32770":
                return
        except Exception:
            return
        seen = {int(item.get("native_handle") or 0) for item in elements}

        def collect(child, _):
            if len(elements) >= max_elements:
                return False
            child = int(child)
            if child in seen:
                return True
            try:
                class_name = self.win32gui.GetClassName(child)
                kind = class_name.casefold()
                if kind not in {"button", "edit"} or not self.win32gui.IsWindowVisible(child):
                    return True
                name = self.win32gui.GetWindowText(child) or ""
                control_id = int(self.win32gui.GetDlgCtrlID(child))
                rect = list(self.win32gui.GetWindowRect(child))
                enabled = bool(self.win32gui.IsWindowEnabled(child))
            except Exception:
                return True
            fingerprint = hashlib.sha256(
                f"{hwnd}\0native\0{child}\0{control_id}\0{name}\0{class_name}".encode("utf-8")
            ).hexdigest()[:20]
            element_id = f"bfe_{fingerprint}"
            self._elements[(hwnd, element_id)] = {
                "native_hwnd": child,
                "class_name": class_name,
                "name": name,
            }
            native_text = ""
            if kind == "edit":
                try:
                    style = self.win32gui.GetWindowLong(child, self.win32con.GWL_STYLE)
                    if not (style & 0x20):
                        native_text = self._read_native_text(child, 32769)
                except Exception:
                    native_text = ""
            elements.append({
                "element_id": element_id,
                "name": name,
                "automation_id": f"native:{control_id}" if control_id >= 0 else "",
                "class_name": class_name,
                "control_type": "ButtonControl" if kind == "button" else "EditControl",
                "native_handle": child,
                "native_text": native_text,
                "enabled": enabled,
                "focusable": True,
                "rect": rect,
                "depth": 1,
            })
            seen.add(child)
            return True

        try:
            self.win32gui.EnumChildWindows(hwnd, collect, None)
        except Exception:
            return

    def _element(self, hwnd, element_id):
        if not element_id:
            return None
        identity = self._elements.get((int(hwnd), element_id))
        if not identity:
            raise LookupError(
                "element_id is not available; refresh WindowSnapshot before controlling it"
            )
        native = int(identity.get("native_hwnd") or 0)
        if native:
            try:
                if (not self.win32gui.IsWindow(native)
                        or not self.win32gui.IsChild(int(hwnd), native)
                        or self.win32gui.GetClassName(native) != identity["class_name"]):
                    raise LookupError("element_id is stale; refresh WindowSnapshot")
                return SimpleNamespace(
                    NativeWindowHandle=native,
                    Name=identity.get("name", ""),
                    ClassName=identity["class_name"],
                    IsEnabled=bool(self.win32gui.IsWindowEnabled(native)),
                    IsKeyboardFocusable=True,
                )
            except LookupError:
                raise
            except Exception:
                raise LookupError("element_id is stale; refresh WindowSnapshot") from None
        if not identity.get("runtime_id"):
            raise LookupError(
                "element_id is not available; refresh WindowSnapshot before controlling it"
            )
        # Reacquire COM objects in this request's initialized thread. Never use
        # a Control/Pattern object created by another FastMCP worker thread.
        root = self.uia.ControlFromHandle(int(hwnd))
        if root is not None:
            for index, (control, _) in enumerate(self.uia.WalkControl(root, includeTop=False, maxDepth=12)):
                if index >= 200:
                    break
                if tuple(control.GetRuntimeId()) == identity["runtime_id"]:
                    return control
        raise LookupError("element_id is stale; refresh WindowSnapshot")

    def _pattern_call(self, control, getter, method, *args):
        try:
            pattern = getattr(control, getter)()
        except Exception:
            return False
        if pattern is None:
            return False
        try:
            getattr(pattern, method)(*args)
        except Exception as exc:
            raise RuntimeError("UIA action outcome unknown; inspect state before retrying") from exc
        return True

    @staticmethod
    def _read_native_text(hwnd, capacity):
        """Bounded WM_GETTEXT reads Edit content, unlike cached GetWindowText."""
        from ctypes import wintypes

        send = ctypes.WinDLL("user32", use_last_error=True).SendMessageTimeoutW
        send.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                         wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
        send.restype = wintypes.LPARAM
        buffer = ctypes.create_unicode_buffer(max(2, min(int(capacity), 32769)))
        result = ctypes.c_size_t()
        if not send(int(hwnd), 13, len(buffer), ctypes.addressof(buffer), 2, 2000, ctypes.byref(result)):
            raise OSError(ctypes.get_last_error(), "WM_GETTEXT timed out")
        return buffer.value

    def _standard_background_action(self, hwnd, control, action, text, clear):
        """Avoid focus-changing UIA proxies for verified standard Win32 controls.

        Limit this to standard push buttons/Edit children; do not guess semantics
        for owner-drawn, checkbox, browser, Qt or other custom controls.
        """
        native = int(self._safe(control, "NativeWindowHandle", 0) or 0)
        if not native or not self.win32gui.IsChild(hwnd, native):
            return None
        name = self.win32gui.GetClassName(native).casefold()
        if name not in {"button", "edit"}:
            return None
        try:
            style = self.win32gui.GetWindowLong(native, self.win32con.GWL_STYLE)
            parent = self.win32gui.GetParent(native)
            enabled = self.win32gui.IsWindowEnabled(native)
        except AttributeError:
            return None
        if not enabled:
            raise BackgroundControlUnavailable("disabled native control refuses background action")
        if name == "button" and action in {"invoke", "click"} and (style & 15) in {0, 1} and parent:
            control_id = self.win32gui.GetDlgCtrlID(native)
            if not 0 <= control_id <= 65535:
                return None
            self.win32gui.SendMessageTimeout(parent, self.win32con.WM_COMMAND,
                                            control_id | (self.win32con.BN_CLICKED << 16), native,
                                            self.win32con.SMTO_ABORTIFHUNG, 2000)
            return {"accepted": True, "mode": "win32_command", "verified": False,
                    "verification_required": True}
        if name == "edit" and action in {"set_value", "type"}:
            if style & 0x800:  # ES_READONLY
                raise BackgroundControlUnavailable("read-only Edit refuses background value changes")
            value = "" if clear and text is None else (text or "")
            self.win32gui.SendMessageTimeout(native, self.win32con.WM_SETTEXT, 0, value,
                                            self.win32con.SMTO_ABORTIFHUNG, 2000)
            verified = False
            if not (style & 0x20):  # ES_PASSWORD: never read back passwords.
                try:
                    units = len(value.encode("utf-16-le")) // 2
                    # Reserve both a possible extra character and the NUL so
                    # a truncated matching prefix cannot count as verified.
                    if units < 32768:
                        verified = self._read_native_text(native, units + 2) == value
                except OSError:
                    verified = False
            return {"accepted": True, "mode": "win32_value", "verified": verified,
                    "verification_required": not verified}
        return None

    @_uia_thread
    def control(
        self,
        hwnd,
        *,
        action,
        element,
        text,
        clear,
        loc,
        button,
        clicks,
        shortcut=None,
    ):
        hwnd = int(hwnd)
        control = self._element(hwnd, element)

        if control is not None:
            native_result = self._standard_background_action(hwnd, control, action, text, clear)
            if native_result is not None:
                return native_result

        if action == "shortcut":
            if not isinstance(shortcut, str) or not shortcut.strip():
                raise ValueError("shortcut is required for shortcut action")
            raise BackgroundControlUnavailable(
                "keyboard shortcut requires explicit foreground fallback"
            )

        if action in {"invoke", "click"} and control is not None:
            if self._pattern_call(control, "GetInvokePattern", "Invoke"):
                return {"accepted": True, "mode": "uia_invoke", "verified": False,
                        "verification_required": True}
            native = int(self._safe(control, "NativeWindowHandle", 0) or 0)
            native_class = self.win32gui.GetClassName(native).casefold() if native else ""
            if native and (native_class == "button" or native_class.startswith("windowsforms10.button")):
                self.win32gui.SendMessageTimeout(native, self.win32con.BM_CLICK, 0, 0,
                                                self.win32con.SMTO_ABORTIFHUNG, 2000)
                return {"accepted": True, "mode": "win32_message", "verified": False,
                        "verification_required": True}
            raise BackgroundControlUnavailable("element does not support background invoke")

        if action in {"set_value", "type"} and control is not None:
            value = "" if clear and text is None else (text or "")
            if self._pattern_call(control, "GetValuePattern", "SetValue", value):
                try:
                    verified = control.GetValuePattern().Value == value
                except Exception:
                    verified = False
                return {"accepted": True, "mode": "uia_value", "verified": verified,
                        "verification_required": not verified}
            native = int(self._safe(control, "NativeWindowHandle", 0) or 0)
            native_class = self.win32gui.GetClassName(native).casefold() if native else ""
            if native and (native_class == "edit" or native_class.startswith(("richedit", "windowsforms10.edit"))):
                self.win32gui.SendMessageTimeout(native, self.win32con.WM_SETTEXT, 0, value,
                                                self.win32con.SMTO_ABORTIFHUNG, 2000)
                verified = self.win32gui.GetWindowText(native) == value
                return {"accepted": True, "mode": "win32_value", "verified": verified,
                        "verification_required": not verified}
            raise BackgroundControlUnavailable("element does not support background value")

        if action == "toggle" and control is not None:
            if self._pattern_call(control, "GetTogglePattern", "Toggle"):
                return {"accepted": True, "mode": "uia_toggle", "verified": False, "verification_required": True}
            raise BackgroundControlUnavailable("element does not support toggle")

        if action == "select" and control is not None:
            if self._pattern_call(control, "GetSelectionItemPattern", "Select"):
                return {"accepted": True, "mode": "uia_select", "verified": False, "verification_required": True}
            raise BackgroundControlUnavailable("element does not support selection")

        if action == "scroll_into_view" and control is not None:
            if self._pattern_call(control, "GetScrollItemPattern", "ScrollIntoView"):
                return {"accepted": True, "mode": "uia_scroll", "verified": False, "verification_required": True}
            raise BackgroundControlUnavailable("element does not support scroll-into-view")

        if action == "click" and loc is not None:
            if self.win32gui.GetClassName(hwnd).casefold().startswith("tk"):
                raise BackgroundControlUnavailable("Tk buttons require explicit foreground fallback")
            if self.win32gui.IsIconic(hwnd):
                raise BackgroundControlUnavailable("minimized window requires explicit foreground fallback")
            if len(loc) != 2:
                raise ValueError("loc must contain [x, y] in window-client coordinates")
            x, y = int(loc[0]), int(loc[1])
            left, top, right, bottom = self.win32gui.GetClientRect(hwnd)
            if not left <= x < right or not top <= y < bottom:
                raise ValueError("click point must be inside the window client area")
            screen_point = self.win32gui.ClientToScreen(hwnd, (x, y))
            target = hwnd
            for _ in range(32):
                point = self.win32gui.ScreenToClient(target, screen_point)
                child = self.win32gui.ChildWindowFromPointEx(target, point, 7)
                if not child or child == target:
                    break
                if not self.win32gui.IsChild(hwnd, child):
                    raise BackgroundControlUnavailable("click target no longer belongs to the window")
                target = child
            else:
                raise BackgroundControlUnavailable("click target nesting exceeds the supported limit")
            if not self.win32gui.IsWindowEnabled(target):
                raise BackgroundControlUnavailable("click target is disabled")
            x, y = self.win32gui.ScreenToClient(target, screen_point)
            message_down = {
                "left": self.win32con.WM_LBUTTONDOWN,
                "right": self.win32con.WM_RBUTTONDOWN,
                "middle": self.win32con.WM_MBUTTONDOWN,
            }[button]
            message_up = {
                "left": self.win32con.WM_LBUTTONUP,
                "right": self.win32con.WM_RBUTTONUP,
                "middle": self.win32con.WM_MBUTTONUP,
            }[button]
            flag = {
                "left": self.win32con.MK_LBUTTON,
                "right": self.win32con.MK_RBUTTON,
                "middle": self.win32con.MK_MBUTTON,
            }[button]
            lparam = (y << 16) | (x & 0xFFFF)
            self.win32gui.PostMessage(target, self.win32con.WM_MOUSEMOVE, 0, lparam)
            for _ in range(int(clicks)):
                self.win32gui.PostMessage(target, message_down, flag, lparam)
                self.win32gui.PostMessage(target, message_up, 0, lparam)
            return {"accepted": True, "mode": "win32_message", "verified": False}

        raise BackgroundControlUnavailable(
            f"{action} requires an element/background-capable control"
        )

    def _activate(self, hwnd):
        return WindowCaptureBackend._restore_foreground(self, hwnd)

    @_uia_thread
    def foreground_control(self, hwnd, **kwargs):
        hwnd = int(hwnd)
        foreground = self.win32gui.GetForegroundWindow()
        placement = self.win32gui.GetWindowPlacement(hwnd)
        result = None
        restored = False
        placement_restored = False
        try:
            if self.win32gui.IsIconic(hwnd):
                self.win32gui.ShowWindow(hwnd, self.win32con.SW_RESTORE)
            if not self._activate(hwnd):
                raise BackgroundControlUnavailable("target window could not acquire foreground focus")
            action = kwargs["action"]
            element = self._element(hwnd, kwargs.get("element"))
            loc = kwargs.get("loc")
            if element is not None:
                try:
                    element.SetFocus()
                except Exception:
                    pass
            if self.win32gui.GetForegroundWindow() != hwnd:
                raise BackgroundControlUnavailable("foreground focus changed before input dispatch")
            if action in {"invoke", "click"}:
                button, clicks = kwargs.get("button", "left"), kwargs.get("clicks", 1)
                if button not in {"left", "right", "middle"} or type(clicks) is not int or not 1 <= clicks <= 3:
                    raise ValueError("invalid mouse variant")
                if (element is not None and button == "left" and clicks == 1
                        and self._pattern_call(element, "GetInvokePattern", "Invoke")):
                    pass
                elif loc is not None:
                    left, top, right, bottom = self.win32gui.GetClientRect(hwnd)
                    if not left <= loc[0] < right or not top <= loc[1] < bottom:
                        raise ValueError("click must be inside the target client area")
                    point = self.win32gui.ClientToScreen(hwnd, (int(loc[0]), int(loc[1])))
                    mouse = {"left": self.uia.Click, "right": self.uia.RightClick,
                             "middle": self.uia.MiddleClick}[button]
                    for _ in range(clicks):
                        mouse(*point, waitTime=0.05)
                else:
                    raise BackgroundControlUnavailable("foreground click requires element or loc")
            elif action in {"type", "set_value"}:
                text = kwargs.get("text") or ""
                if kwargs.get("clear"):
                    self.uia.SendKeys("{Ctrl}a{Back}")
                encoded = text.encode("utf-16-le")
                for offset in range(0, len(encoded), 2):
                    char = chr(int.from_bytes(encoded[offset:offset + 2], "little"))
                    if self.uia.SendUnicodeChar(char) != 2:
                        raise RuntimeError("text input outcome unknown")
            elif action == "shortcut":
                shortcut = kwargs.get("shortcut")
                if not isinstance(shortcut, str) or not shortcut.strip():
                    raise ValueError("shortcut is required for shortcut action")
                aliases = {
                    "backspace": "Back",
                    "capslock": "Capital",
                    "scrolllock": "Scroll",
                    "windows": "Win",
                    "command": "Win",
                    "option": "Alt",
                }
                sendkeys = ""
                for key in shortcut.split("+"):
                    key = key.strip()
                    if not key:
                        raise ValueError("shortcut contains an empty key")
                    if len(key) == 1:
                        sendkeys += key
                    else:
                        sendkeys += "{" + aliases.get(key.casefold(), key) + "}"
                self.uia.SendKeys(sendkeys, interval=0.01)
            else:
                raise BackgroundControlUnavailable(f"foreground fallback not implemented for {action}")
            result = {
                "accepted": True,
                "mode": "foreground_fallback",
                "verified": False,
                "foreground_preserved": foreground == hwnd,
            }
        finally:
            try:
                self.win32gui.SetWindowPlacement(hwnd, placement)
                placement_restored = self.win32gui.GetWindowPlacement(hwnd) == placement
            except Exception:
                pass
            if foreground and self.win32gui.IsWindow(foreground):
                try:
                    restored = self._activate(foreground)
                except Exception:
                    restored = False
        if result is None:
            raise BackgroundControlUnavailable("foreground fallback did not complete")
        result["foreground_restored"] = restored
        result["placement_restored"] = placement_restored
        return result


class WindowCaptureBackend:
    def __init__(self):
        import cv2
        import win32con
        import win32gui
        import win32process
        import win32ui
        from windows_capture import WindowsCapture

        self.cv2 = cv2
        self.win32con = win32con
        self.win32gui = win32gui
        self.win32process = win32process
        self.win32ui = win32ui
        self.WindowsCapture = WindowsCapture

    def _restore_foreground(self, desired_hwnd: int) -> bool:
        if not desired_hwnd or not self.win32gui.IsWindow(desired_hwnd):
            return False
        if self.win32gui.GetForegroundWindow() == desired_hwnd:
            return True
        current_tid = int(ctypes.windll.kernel32.GetCurrentThreadId())
        current_foreground = self.win32gui.GetForegroundWindow()
        thread_ids = []
        for hwnd in (current_foreground, desired_hwnd):
            if not hwnd or not self.win32gui.IsWindow(hwnd):
                continue
            thread_id, _ = self.win32process.GetWindowThreadProcessId(hwnd)
            if thread_id and thread_id != current_tid and thread_id not in thread_ids:
                thread_ids.append(thread_id)
        attached = []
        try:
            ctypes.windll.user32.AllowSetForegroundWindow(-1)
            for thread_id in thread_ids:
                try:
                    self.win32process.AttachThreadInput(current_tid, thread_id, True)
                    attached.append(thread_id)
                except Exception:
                    pass
            try:
                self.win32gui.SetForegroundWindow(desired_hwnd)
                self.win32gui.BringWindowToTop(desired_hwnd)
            except Exception:
                return False
            return self.win32gui.GetForegroundWindow() == desired_hwnd
        finally:
            for thread_id in reversed(attached):
                try:
                    self.win32process.AttachThreadInput(current_tid, thread_id, False)
                except Exception:
                    pass

    def _wgc(self, hwnd: int, timeout: float = 3.0) -> dict:
        done = threading.Event()
        state: dict[str, Any] = {}
        capture = self.WindowsCapture(
            cursor_capture=False,
            draw_border=False,
            window_hwnd=int(hwnd),
        )

        @capture.event
        def on_frame_arrived(frame, control):
            if "frame" not in state:
                state["frame"] = frame.frame_buffer.copy()
                state["width"] = int(frame.width)
                state["height"] = int(frame.height)
            control.stop()
            done.set()

        @capture.event
        def on_closed():
            done.set()

        control = capture.start_free_threaded()
        if not done.wait(timeout):
            control.stop()
            raise TimeoutError("WGC capture timed out")
        try:
            control.wait()
        except Exception:
            pass
        frame = state.get("frame")
        if frame is None:
            raise RuntimeError("WGC closed before delivering a frame")
        ok, encoded = self.cv2.imencode(".png", frame)
        if not ok:
            raise RuntimeError("failed to encode WGC frame")
        return {
            "backend": "wgc_d3d11",
            "capture_mode": "wgc_d3d11",
            "width": state["width"],
            "height": state["height"],
            "png_bytes": encoded.tobytes(),
            "staging_used": False,
        }

    def _print_window(self, hwnd: int) -> dict:
        left, top, right, bottom = self.win32gui.GetWindowRect(int(hwnd))
        width = max(1, right - left)
        height = max(1, bottom - top)
        window_dc = self.win32gui.GetWindowDC(int(hwnd))
        source_dc = self.win32ui.CreateDCFromHandle(window_dc)
        memory_dc = source_dc.CreateCompatibleDC()
        bitmap = self.win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(source_dc, width, height)
        memory_dc.SelectObject(bitmap)
        try:
            result = int(ctypes.windll.user32.PrintWindow(int(hwnd), memory_dc.GetSafeHdc(), 2))
            if result != 1:
                raise RuntimeError("PrintWindow returned failure")
            info = bitmap.GetInfo()
            bits = bitmap.GetBitmapBits(True)
            from PIL import Image

            image = Image.frombuffer(
                "RGB",
                (info["bmWidth"], info["bmHeight"]),
                bits,
                "raw",
                "BGRX",
                0,
                1,
            )
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            return {
                "backend": "print_window",
                "capture_mode": "print_window",
                "width": width,
                "height": height,
                "png_bytes": buffer.getvalue(),
                "staging_used": False,
            }
        finally:
            self.win32gui.DeleteObject(bitmap.GetHandle())
            memory_dc.DeleteDC()
            source_dc.DeleteDC()
            self.win32gui.ReleaseDC(int(hwnd), window_dc)

    def _stage_minimized(self, hwnd: int) -> dict:
        placement = self.win32gui.GetWindowPlacement(int(hwnd))
        foreground = self.win32gui.GetForegroundWindow()
        normal = placement[4]
        width = max(64, int(normal[2] - normal[0]))
        height = max(64, int(normal[3] - normal[1]))
        try:
            self.win32gui.ShowWindow(int(hwnd), self.win32con.SW_SHOWNOACTIVATE)
            self.win32gui.SetWindowPos(
                int(hwnd),
                self.win32con.HWND_BOTTOM,
                -20000,
                -20000,
                width,
                height,
                self.win32con.SWP_NOACTIVATE | self.win32con.SWP_SHOWWINDOW,
            )
            time.sleep(0.15)
            result = self._wgc(int(hwnd), timeout=3.0)
            result["backend"] = "staging_wgc_d3d11"
            result["capture_mode"] = "staging_wgc_d3d11"
            result["staging_used"] = True
            return result
        finally:
            try:
                self.win32gui.SetWindowPlacement(int(hwnd), placement)
            finally:
                self._restore_foreground(foreground)

    def capture(self, hwnd, *, allow_staging=True):
        hwnd = int(hwnd)
        if self.win32gui.IsIconic(hwnd):
            if not allow_staging:
                raise RuntimeError("minimized capture requires staging")
            return self._stage_minimized(hwnd)
        failures = []
        try:
            return self._wgc(hwnd)
        except Exception as exc:
            failures.append(f"wgc_d3d11:{type(exc).__name__}:{exc}")
        try:
            result = self._print_window(hwnd)
            result["fallback_chain"] = failures + ["print_window:ok"]
            return result
        except Exception as exc:
            failures.append(f"print_window:{type(exc).__name__}:{exc}")
        raise RuntimeError("window capture failed; " + " | ".join(failures))
