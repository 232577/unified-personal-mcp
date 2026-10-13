"""本机桌面的物理像素捕获、窗口归属与有界输入。"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import io
import math
import platform
import time
from contextlib import contextmanager
from pathlib import Path

from PIL import Image


class NativeDesktopError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _fail(code, message):
    raise NativeDesktopError(code, message)


def _integer(value, name, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        _fail("INVALID_ARGUMENT", f"{name} must be an integer in [{minimum}, {maximum}].")
    return value


def _layout(displays):
    return sorted((item["id"], tuple(item["rect"]), bool(item["primary"])) for item in displays)


class _Win32:
    """所有产生桌面副作用的调用集中于此，测试替换整个边界。"""

    def __init__(self):
        import win32api
        import win32clipboard
        import win32gui
        import win32process
        import windows_mcp.uia as uia

        self.win32api = win32api
        self.clipboard = win32clipboard
        self.gui = win32gui
        self.process = win32process
        self.uia = uia

    @contextmanager
    def dpi_context(self):
        change = ctypes.WinDLL("user32", use_last_error=True).SetThreadDpiAwarenessContext
        change.argtypes = [ctypes.c_void_p]
        change.restype = ctypes.c_void_p
        previous = change(ctypes.c_void_p(-4))
        if not previous:
            _fail("NATIVE_GEOMETRY_UNAVAILABLE", "Physical DPI coordinates are unavailable.")
        try:
            yield
        finally:
            change(previous)

    def input_context(self):
        return self.uia.UIAutomationInitializerInThread()

    def desktop_ready(self):
        from ctypes import wintypes

        user = ctypes.WinDLL("user32", use_last_error=True)
        opening = user.OpenInputDesktop
        opening.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        opening.restype = wintypes.HANDLE
        closing = user.CloseDesktop
        closing.argtypes = [wintypes.HANDLE]
        name = user.GetUserObjectInformationW
        name.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                         wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        name.restype = wintypes.BOOL
        handle = opening(0, False, 1)
        if not handle:
            return False
        try:
            buffer = ctypes.create_unicode_buffer(256)
            needed = wintypes.DWORD()
            return bool(name(handle, 2, buffer, ctypes.sizeof(buffer), ctypes.byref(needed))) and buffer.value == "Default"
        finally:
            closing(handle)

    def monitors(self):
        output = []
        for monitor, _, _ in self.win32api.EnumDisplayMonitors():
            info = self.win32api.GetMonitorInfo(monitor)
            output.append({"id": info["Device"], "name": info["Device"],
                           "rect": list(info["Monitor"]), "primary": bool(info["Flags"] & 1)})
        return output

    def identity(self, hwnd):
        import psutil

        if not self.gui.IsWindow(hwnd):
            raise LookupError("Window is stale.")
        _, pid = self.process.GetWindowThreadProcessId(hwnd)
        client = self.gui.GetClientRect(hwnd)
        return {"hwnd": int(hwnd), "pid": int(pid), "created": psutil.Process(pid).create_time(),
                "rect": list(self.gui.GetWindowRect(hwnd)),
                "client_origin": list(self.gui.ClientToScreen(hwnd, (0, 0))),
                "client_size": [client[2] - client[0], client[3] - client[1]],
                "visible": bool(self.gui.IsWindowVisible(hwnd)), "minimized": bool(self.gui.IsIconic(hwnd))}

    def foreground(self):
        return int(self.gui.GetForegroundWindow())

    def window_at(self, point):
        return int(self.gui.GetAncestor(self.gui.WindowFromPoint(tuple(point)), 2))

    def activate(self, hwnd):
        # 只能激活已经验证并取得写归属的窗口，不恢复未知的先前前台窗口。
        try:
            self.gui.SetForegroundWindow(hwnd)
        except Exception:
            return False
        return self.foreground() == hwnd

    @staticmethod
    def grab(rect):
        from PIL import ImageGrab

        return ImageGrab.grab(bbox=tuple(rect), all_screens=True)

    def move(self, point):
        self.win32api.SetCursorPos(tuple(point))

    def mouse(self, button, down):
        flags = {"left": (2, 4), "right": (8, 16), "middle": (32, 64)}[button]
        self.win32api.mouse_event(flags[0 if down else 1], 0, 0, 0, 0)

    def wheel(self, axis, steps):
        self.win32api.mouse_event(0x1000 if axis == "x" else 0x800, 0, 0, steps * 120, 0)

    def unicode(self, unit):
        from bf_automation.native_input import send_unicode_char
        if send_unicode_char(self.uia, chr(unit)) != 2:
            raise RuntimeError("Unicode input outcome is unknown.")

    def key(self, code, down):
        self.win32api.keybd_event(code, 0, 0 if down else 2, 0)

    def clipboard_read(self):
        self.clipboard.OpenClipboard()
        try:
            if not self.clipboard.IsClipboardFormatAvailable(13):
                return ""
            text = self.clipboard.GetClipboardData(13)
            if len(text) > 8192:
                _fail("NATIVE_CLIPBOARD_TOO_LARGE", "Clipboard text exceeds 8192 characters.")
            return text
        finally:
            self.clipboard.CloseClipboard()

    def clipboard_write(self, text):
        self.clipboard.OpenClipboard()
        try:
            self.clipboard.EmptyClipboard()
            self.clipboard.SetClipboardText(text, 13)
        finally:
            self.clipboard.CloseClipboard()


class NativeDesktop:
    MAX_SOURCE_PIXELS = 24_000_000
    MAX_PREVIEW_EDGE = 1600
    MAX_JPEG_BYTES = 1024 * 1024
    SHORTCUTS = {"ctrl": 17, "shift": 16, "alt": 18, "enter": 13, "tab": 9, "esc": 27,
                 "escape": 27, "backspace": 8, "delete": 46, "home": 36, "end": 35,
                 "pageup": 33, "pagedown": 34, "left": 37, "up": 38, "right": 39,
                 "down": 40, "space": 32, **{chr(code).lower(): code for code in range(65, 91)},
                 **{str(number): 48 + number for number in range(10)},
                 **{f"f{number}": 111 + number for number in range(1, 13)}}

    def __init__(self, host):
        self.host = host
        self._api = None
        self._sleep = time.sleep
        self._clock = time.monotonic

    @property
    def api(self):
        if self._api is None:
            self._api = _Win32()
        return self._api

    @api.setter
    def api(self, value):
        self._api = value

    @property
    def store(self):
        return self.host.bf_server._bf_store

    def _require(self, resource):
        self.store.require(resource.bf_token)
        if not self.api.desktop_ready():
            _fail("NATIVE_SECURE_DESKTOP", "Secure or unavailable desktop cannot be captured or controlled.")

    def _windows(self, resource):
        result = self.host.bf.call("WindowInventory", {"bf_task_id": resource.bf_token})
        if result.get("isError"):
            _fail("NATIVE_WINDOW_INVENTORY_FAILED", "Window inventory failed.")
        payload = result.get("structuredContent", {})
        output = []
        for item in payload.get("windows", [])[:200]:
            key = item["window_id"]
            record = self.store.resolve_window(resource.bf_token, key)
            try:
                identity = self.api.identity(record["hwnd"])
            except Exception:
                continue
            if identity["pid"] == record["pid"] and identity["visible"] and not identity["minimized"]:
                # 复制几何序列，避免 API 复用列表时悄悄改变本帧的旧库存。
                identity = {**identity, **{key: list(identity[key]) for key in (
                    "rect", "client_origin", "client_size")}}
                output.append({**identity, "window_id": key, "title": item.get("title", "")})
        return output

    def inventory(self, resource):
        with self.api.dpi_context():
            self._require(resource)
            displays = self.api.monitors()
            windows = self._windows(resource)
        return {"device_label": platform.node() or "Windows", "displays": [
            {"id": item["id"], "name": item["name"], "width": item["rect"][2] - item["rect"][0],
             "height": item["rect"][3] - item["rect"][1], "primary": item["primary"]} for item in displays],
            "windows": [{"id": item["window_id"], "title": item["title"]} for item in windows]}

    def _size(self, rect):
        if not isinstance(rect, (list, tuple)) or len(rect) != 4 or any(type(value) is not int for value in rect):
            _fail("NATIVE_GEOMETRY_UNAVAILABLE", "Capture rectangle is unavailable.")
        width, height = rect[2] - rect[0], rect[3] - rect[1]
        if width <= 0 or height <= 0 or width * height > self.MAX_SOURCE_PIXELS:
            _fail("NATIVE_CAPTURE_TOO_LARGE", "Capture source must be positive and no larger than 24 megapixels.")
        return width, height

    def _preview(self, image):
        image = image.convert("RGB")
        image.thumbnail((self.MAX_PREVIEW_EDGE, self.MAX_PREVIEW_EDGE), Image.Resampling.LANCZOS)
        for _ in range(8):
            for quality in (80, 65, 45):
                output = io.BytesIO()
                image.save(output, format="JPEG", quality=quality, optimize=True)
                data = output.getvalue()
                if len(data) <= self.MAX_JPEG_BYTES:
                    return {"data_url": "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii"),
                            "width": image.width, "height": image.height}
            image = image.resize((max(1, image.width * 3 // 4), max(1, image.height * 3 // 4)), Image.Resampling.LANCZOS)
        _fail("NATIVE_CAPTURE_TOO_LARGE", "Preview could not meet the JPEG byte limit.")

    def _capture_windows_stable(self, windows, foreground):
        # 旧库存只用于旧画面；不能把截图后得到的新几何附到旧图上。
        for window in windows:
            try:
                actual = self.api.identity(window["hwnd"])
            except Exception:
                _fail("NATIVE_STALE_GEOMETRY", "A captured window disappeared or became unavailable.")
            if any(actual.get(key) != window.get(key) for key in (
                    "hwnd", "pid", "created", "rect", "client_origin", "client_size", "visible", "minimized")):
                _fail("NATIVE_STALE_GEOMETRY", "Window inventory changed while capturing; refresh the frame.")
        if self.api.foreground() != foreground:
            _fail("NATIVE_STALE_GEOMETRY", "Foreground focus changed while capturing; refresh the frame.")

    def capture(self, resource, target):
        if not isinstance(target, dict) or target.get("kind") not in {"display", "window"} or not isinstance(target.get("id"), str):
            _fail("INVALID_ARGUMENT", "Capture target must identify one display or bound window.")
        with self.api.dpi_context():
            self._require(resource)
            displays = self.api.monitors()
            layout = _layout(displays)
            windows = self._windows(resource)
            foreground = self.api.foreground()
            if target["kind"] == "display":
                display = next((item for item in displays if item["id"] == target["id"]), None)
                if display is None:
                    _fail("NATIVE_TARGET_NOT_FOUND", "Display target no longer exists.")
                rect = list(display["rect"])
                width, height = self._size(rect)
                with self.host.bf_server._bf_lease.hold(resource.bf_token, timeout=2):
                    self._require(resource)
                    self._capture_windows_stable(windows, foreground)
                    image = self.api.grab(rect)
                    self._require(resource)
                    if layout != _layout(self.api.monitors()):
                        _fail("NATIVE_STALE_GEOMETRY", "Display layout changed while capturing.")
                    self._capture_windows_stable(windows, foreground)
                if image.size != (width, height):
                    _fail("NATIVE_GEOMETRY_UNAVAILABLE", "Captured pixels do not match the physical display rectangle.")
            else:
                window = next((item for item in windows if item["window_id"] == target["id"]), None)
                if window is None:
                    _fail("NATIVE_TARGET_NOT_FOUND", "Bound window target is unavailable.")
                rect = window["rect"]
                width, height = self._size(rect)
                image, rect = self._window_image(resource, target["id"], window)
                self._require(resource)
                self._capture_windows_stable(windows, foreground)
            preview = self._preview(image)
            state = {"owner": resource.key, "target": dict(target), "layout": layout,
                     "windows": windows, "foreground": foreground, "capture_rect": list(rect),
                     "source_width": width, "source_height": height,
                     "width": preview["width"], "height": preview["height"]}
            return {**preview, "source_width": width, "source_height": height, "capture_rect": list(rect),
                    "geometry_verified": True, "target": dict(target), "native_state": state}

    def _window_image(self, resource, window_id, window):
        task_dir, _ = self.store.require(resource.bf_token)
        captures = Path(task_dir) / "captures"
        before = set(captures.iterdir()) if captures.is_dir() else set()
        result = self.host.bf.call("WindowScreenshot", {"bf_task_id": resource.bf_token,
            "window_id": window_id, "allow_staging": False, "prefer_coordinate_mapping": True})
        payload = result.get("structuredContent", {}).get("window_capture", {})
        try:
            if result.get("isError") or payload.get("geometry_verified") is not True:
                _fail("NATIVE_GEOMETRY_UNAVAILABLE", "Window capture has no verified physical pixel mapping.")
            rect = payload.get("capture_rect")
            if rect != window["rect"]:
                _fail("NATIVE_STALE_GEOMETRY", "Window capture rectangle differs from the bound window.")
            width, height = self._size(rect)
            if (payload.get("source_width"), payload.get("source_height")) != (width, height):
                _fail("NATIVE_GEOMETRY_UNAVAILABLE", "Window source dimensions do not match its capture rectangle.")
            encoded = next((item.get("data") for item in result.get("content", []) if item.get("type") == "image"), None)
            if not isinstance(encoded, str) or len(encoded) > 128 * 1024 * 1024:
                _fail("NATIVE_CAPTURE_TOO_LARGE", "Window image content is missing or oversized.")
            data = base64.b64decode(encoded, validate=True)
            with Image.open(io.BytesIO(data)) as image:
                if image.size != (width, height):
                    _fail("NATIVE_GEOMETRY_UNAVAILABLE", "Window image dimensions do not match the source mapping.")
                return image.convert("RGB"), rect
        finally:
            raw = payload.get("path")
            if isinstance(raw, str):
                candidate = Path(raw)
                try:
                    resolved = candidate.resolve(strict=True)
                    allowed = (self.host.config.data_root / "bf").resolve()
                    if (candidate not in before and not candidate.is_symlink()
                            and resolved.parent == captures.resolve() and resolved.is_relative_to(allowed)
                            and resolved.name.startswith(window_id + "-") and resolved.suffix == ".png"
                            and resolved.stat().st_size <= 96 * 1024 * 1024):
                        with resolved.open("rb") as stream:
                            digest = hashlib.file_digest(stream, "sha256").hexdigest()
                            identity = resolved.stat().st_ino
                        if digest == payload.get("sha256") and resolved.stat().st_ino == identity:
                            resolved.unlink()
                except (OSError, ValueError):
                    pass

    def _guard(self, resource, state, window=None, point=None, *, focus=False):
        self._require(resource)
        if state.get("owner") != resource.key:
            _fail("NATIVE_TASK_MISMATCH", "Frame belongs to another task.")
        if _layout(self.api.monitors()) != state.get("layout"):
            _fail("NATIVE_STALE_GEOMETRY", "Display layout changed; refresh the frame.")
        if window is None:
            return
        actual = self.api.identity(window["hwnd"])
        if any(actual.get(key) != window.get(key) for key in (
                "hwnd", "pid", "created", "rect", "client_origin", "client_size", "visible", "minimized")):
            _fail("NATIVE_STALE_GEOMETRY", "Window identity or geometry changed; refresh the frame.")
        record = self.store.resolve_window(resource.bf_token, window["window_id"])
        if record["hwnd"] != window["hwnd"] or record["pid"] != window["pid"]:
            _fail("NATIVE_STALE_TARGET", "Bound window identity changed.")
        self.store.claim_window(resource.bf_token, window["window_id"])
        if focus and self.api.foreground() != window["hwnd"]:
            _fail("NATIVE_FOCUS_CHANGED", "Foreground focus changed before input.")
        if point is not None and self.api.window_at(point) != window["hwnd"]:
            _fail("NATIVE_STALE_TARGET", "The coordinate no longer hits the captured window.")

    def _point(self, state, x, y):
        if (isinstance(x, bool) or isinstance(y, bool) or not isinstance(x, (int, float))
                or not isinstance(y, (int, float)) or not math.isfinite(x) or not math.isfinite(y)
                or not 0 <= x < state["width"] or not 0 <= y < state["height"]):
            _fail("INVALID_ARGUMENT", "Coordinates must lie within the current preview image.")
        rect = state["capture_rect"]
        return (rect[0] + int(x * state["source_width"] / state["width"]),
                rect[1] + int(y * state["source_height"] / state["height"]))

    def _hit(self, state, point):
        hwnd = self.api.window_at(point)
        window = next((item for item in state["windows"] if item["hwnd"] == hwnd), None)
        if window is None or (state["target"]["kind"] == "window" and window["window_id"] != state["target"]["id"]):
            _fail("NATIVE_TARGET_NOT_BOUND", "The hit window was not bound in this frame.")
        return window

    @staticmethod
    def _selected_window(state):
        window = next((item for item in state["windows"] if item["window_id"] == state["target"]["id"]), None)
        if window is None:
            _fail("NATIVE_TARGET_NOT_BOUND", "The selected window was not bound in this frame.")
        return window

    def _keyboard_window(self, state):
        if state["target"]["kind"] == "window":
            return self._selected_window(state)
        hwnd = state["foreground"]
        window = next((item for item in state["windows"] if item["hwnd"] == hwnd), None)
        if window is None:
            _fail("NATIVE_TARGET_NOT_BOUND", "The captured foreground window is not bound.")
        return window

    @staticmethod
    def _authorize(callback):
        if callback is None:
            return
        if not callable(callback):
            _fail("INVALID_ARGUMENT", "Internal authorization callback is invalid.")
        try:
            callback()
        except Exception:
            # 不透出回调异常中的工作流、凭据或其他私有信息。
            _fail("NATIVE_AUTHORIZATION_REVOKED", "Control authorization ended; further input was stopped.")

    def _observe_foreground(self, hwnd):
        # 只观察首击后的焦点变化；不借此重试或强制激活窗口。
        deadline = self._clock() + 0.2
        for _ in range(10):
            if self.api.foreground() == hwnd:
                return True
            if self._clock() >= deadline:
                return False
            self._sleep(min(0.02, max(0.0, deadline - self._clock())))
        return self.api.foreground() == hwnd

    def act(self, resource, frame, args):
        action, accepted = args.get("action") if isinstance(args, dict) else None, False
        try:
            if not isinstance(args, dict) or not isinstance(frame, dict):
                _fail("INVALID_ARGUMENT", "Input arguments and frame must be objects.")
            authorize = args.get("_authorize")
            if authorize is not None and not callable(authorize):
                _fail("INVALID_ARGUMENT", "Internal authorization callback is invalid.")
            if not isinstance(action, str) or action not in {"click", "type", "scroll", "shortcut", "drag"}:
                _fail("NOT_SUPPORTED", "Native desktop action is not supported.")
            state = frame.get("native_state")
            if not isinstance(state, dict) or not frame.get("geometry_verified") or frame.get("target") != state.get("target"):
                _fail("NATIVE_GEOMETRY_UNAVAILABLE", "Frame has no verified native state.")
            # 参数必须在窗口激活或任何输入副作用前全部验证。
            if action == "click":
                button = args.get("button", "left")
                if not isinstance(button, str) or button not in {"left", "right", "middle"}:
                    _fail("INVALID_ARGUMENT", "Unsupported mouse button.")
                clicks = _integer(args.get("clicks", 1), "clicks", 1, 2)
            elif action == "type":
                text = args.get("text")
                if not isinstance(text, str) or len(text) > 8192 or "\x00" in text:
                    _fail("INVALID_ARGUMENT", "Literal text must contain at most 8192 characters and no NUL.")
                encoded = text.encode("utf-16-le")
            elif action == "shortcut":
                keys = args.get("keys")
                if (not isinstance(keys, list) or not 1 <= len(keys) <= 4
                        or any(not isinstance(key, str) or key.casefold() not in self.SHORTCUTS for key in keys)):
                    _fail("INVALID_ARGUMENT", "Shortcut must use one to four allowed keys.")
                codes = [self.SHORTCUTS[key.casefold()] for key in keys]
                if (len(set(codes)) != len(codes) or codes[-1] in {16, 17, 18}
                        or any(code not in {16, 17, 18} for code in codes[:-1])):
                    _fail("INVALID_ARGUMENT", "Shortcut requires distinct modifiers followed by one final key.")
            elif action == "scroll":
                dx = _integer(args.get("dx", 0), "dx", -20, 20)
                dy = _integer(args.get("dy", 0), "dy", -20, 20)
            else:
                end = self._point(state, args.get("to_x"), args.get("to_y"))
            with self.api.dpi_context(), self.host.bf_server._bf_lease.hold(resource.bf_token, timeout=2), self.api.input_context():
                self._guard(resource, state)
                point = self._point(state, args.get("x"), args.get("y")) if action in {"click", "drag", "scroll"} else None
                selected = state["target"]["kind"] == "window"
                window = self._selected_window(state) if selected else (
                    self._hit(state, point) if point is not None else self._keyboard_window(state))
                # 后台窗口图来自该窗口表面；先认领所选窗口，再激活并核对遮挡。
                self._guard(resource, state, window, None if selected and action in {"click", "drag"} else point)
                if action == "drag":
                    other = window if selected else self._hit(state, end)
                    self._guard(resource, state, other, None if selected else end)
                    if other["hwnd"] != window["hwnd"]:
                        _fail("NOT_SUPPORTED", "Cross-window dragging is not supported.")
                    origin, size = window["client_origin"], window["client_size"]
                    if any(not origin[0] <= pos[0] < origin[0] + size[0]
                           or not origin[1] <= pos[1] < origin[1] + size[1] for pos in (point, end)):
                        _fail("NOT_SUPPORTED", "Dragging is limited to the same window client area.")
                if action in {"type", "shortcut", "scroll"}:
                    self._guard(resource, state, window, point, focus=True)
                elif self.api.foreground() != window["hwnd"] and (
                        action == "drag" or self.api.window_at(point) != window["hwnd"]):
                    self._authorize(authorize)
                    accepted = True
                    if not self.api.activate(window["hwnd"]):
                        _fail("NATIVE_FOCUS_CHANGED", "Bound window could not acquire foreground focus.")
                if action in {"click", "drag"}:
                    # 激活后仍可能被置顶窗口遮挡，不能省略真实命中与焦点复核。
                    self._guard(resource, state, window, point, focus=action == "drag")
                    if action == "drag":
                        self._guard(resource, state, window, end, focus=True)
                if action == "click":
                    for click_index in range(clicks):
                        if click_index and not self._observe_foreground(window["hwnd"]):
                            _fail("NATIVE_FOCUS_CHANGED", "First click did not acquire foreground focus; second click was refused.")
                        self._guard(resource, state, window, point, focus=click_index > 0)
                        self._authorize(authorize)
                        accepted = True
                        self.api.move(point)
                        self._guard(resource, state, window, point, focus=click_index > 0)
                        self._authorize(authorize)
                        accepted = True
                        try:
                            self.api.mouse(button, True)
                            # 可见目标的首击允许由 Windows 正常取得焦点，释放必须成对。
                            self._guard(resource, state, window, focus=click_index > 0)
                            self._authorize(authorize)
                        finally:
                            # 撤销授权不能阻止已按下按钮的清理释放。
                            self.api.mouse(button, False)
                elif action == "type":
                    for index in range(0, len(encoded), 2):
                        if index % 64 == 0:
                            self._guard(resource, state, window, focus=True)
                        if self.api.foreground() != window["hwnd"]:
                            _fail("NATIVE_FOCUS_CHANGED", "Focus changed during literal typing.")
                        self._authorize(authorize)
                        accepted = True
                        self.api.unicode(int.from_bytes(encoded[index:index + 2], "little"))
                elif action == "shortcut":
                    pressed = []
                    try:
                        for code in codes:
                            self._guard(resource, state, window, focus=True)
                            self._authorize(authorize)
                            accepted = True
                            pressed.append(code)
                            self.api.key(code, True)
                        self._authorize(authorize)
                    finally:
                        for code in reversed(pressed):
                            self.api.key(code, False)
                elif action == "scroll":
                    self._guard(resource, state, window, point, focus=True)
                    self._authorize(authorize)
                    accepted = True
                    self.api.move(point)
                    self._guard(resource, state, window, point, focus=True)
                    for axis, steps in (("x", dx), ("y", dy)):
                        for _ in range(abs(steps)):
                            self._guard(resource, state, window, point, focus=True)
                            self._authorize(authorize)
                            accepted = True
                            self.api.wheel(axis, 1 if steps > 0 else -1)
                else:
                    self._guard(resource, state, window, point, focus=True)
                    self._authorize(authorize)
                    accepted = True
                    self.api.move(point)
                    self._guard(resource, state, window, point, focus=True)
                    self._authorize(authorize)
                    accepted = True
                    try:
                        deadline = self._clock() + 1.0
                        self.api.mouse("left", True)
                        for step in range(1, 13):
                            position = tuple(round(point[axis] + (end[axis] - point[axis]) * step / 12) for axis in (0, 1))
                            self._guard(resource, state, window, position, focus=True)
                            if self._clock() >= deadline:
                                _fail("NATIVE_INPUT_DEADLINE", "Drag exceeded its one-second input deadline.")
                            self._authorize(authorize)
                            self.api.move(position)
                            self._sleep(min(0.04, max(0.0, deadline - self._clock())))
                        self._guard(resource, state, window, end, focus=True)
                        if self._clock() >= deadline:
                            _fail("NATIVE_INPUT_DEADLINE", "Drag exceeded its one-second input deadline.")
                        self._authorize(authorize)
                    finally:
                        self.api.mouse("left", False)
            return {"ok": True, "accepted": accepted, "verified": False, "action": action}
        except Exception as exc:
            code = "NATIVE_INPUT_OUTCOME_UNKNOWN" if accepted else getattr(exc, "code", "NATIVE_INPUT_REFUSED")
            return {"ok": False, "accepted": accepted, "verified": False, "action": action,
                    "error": {"code": code, "message": "Input may have taken effect; refresh and inspect before another action."
                              if accepted else str(exc) if isinstance(exc, NativeDesktopError)
                              else "Input refused; refresh the target or correct the arguments.", "retryable": False,
                              'cause_code': exc.code if isinstance(exc, NativeDesktopError) else type(exc).__name__,
                              **({"outcome": "unknown"} if accepted else {})}}

    def clipboard_read(self, resource):
        with self.host.bf_server._bf_lease.hold(resource.bf_token, timeout=2):
            self._require(resource)
            return self.api.clipboard_read()

    def clipboard_write(self, resource, text, *, authorize=None):
        if not isinstance(text, str) or len(text) > 8192 or "\x00" in text:
            _fail("INVALID_ARGUMENT", "Clipboard text must contain at most 8192 characters and no NUL.")
        with self.host.bf_server._bf_lease.hold(resource.bf_token, timeout=2):
            self._require(resource)
            self._authorize(authorize)
            self.api.clipboard_write(text)
        return {"ok": True, "accepted": True, "verified": False, "action": "clipboard_write"}
