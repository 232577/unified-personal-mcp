"""通过伪造 Win32 边界验证物理映射、任务归属和输入中断。"""

import base64
import io
from contextlib import nullcontext
from types import SimpleNamespace as NS

import pytest
from PIL import Image

from bf_automation.lease import DesktopLease


class FakeApi:
    def __init__(self):
        self.displays = [{"id": "display-a", "name": "fixture display", "rect": [-100, 0, 300, 200], "primary": True}]
        self.window = {"hwnd": 100, "pid": 20, "created": 1.5, "rect": [-100, 0, 300, 200],
                       "client_origin": [-100, 0], "client_size": [400, 200], "visible": True, "minimized": False}
        self.focus = 100
        self.hit = 100
        self.events = []
        self.secure = False
        self.on_event = None
        self.clipboard = "fixture clipboard"

    def dpi_context(self):
        return nullcontext()

    def input_context(self):
        return nullcontext()

    def desktop_ready(self):
        return not self.secure

    def monitors(self):
        return [dict(item) for item in self.displays]

    def identity(self, hwnd):
        if hwnd != 100:
            raise LookupError("missing fixture window")
        return dict(self.window)

    def foreground(self):
        return self.focus

    def window_at(self, point):
        return self.hit

    def activate(self, hwnd):
        self.events.append(("activate", hwnd))
        self.focus = hwnd
        return True

    def grab(self, rect):
        self.events.append(("grab", tuple(rect)))
        return Image.new("RGB", (rect[2] - rect[0], rect[3] - rect[1]), "red")

    def _event(self, *event):
        self.events.append(event)
        if self.on_event:
            self.on_event(event)

    def move(self, point):
        self._event("move", tuple(point))

    def mouse(self, button, down):
        self._event("mouse", button, down)

    def wheel(self, axis, steps):
        self._event("wheel", axis, steps)

    def unicode(self, unit):
        self._event("unicode", unit)

    def key(self, code, down):
        self._event("key", code, down)

    def clipboard_read(self):
        return self.clipboard

    def clipboard_write(self, text):
        self.clipboard = text


class FakeStore:
    def __init__(self, root):
        self.root = root
        self.root.mkdir(parents=True)
        self.claims = []
        self.denied = False

    def require(self, token):
        assert token == "fixture-token"
        return self.root, {}

    def resolve_window(self, token, window_id):
        self.require(token)
        assert window_id == "window-a"
        return {"hwnd": 100, "pid": 20, "title": "fixture window"}

    def claim_window(self, token, window_id):
        self.require(token)
        if self.denied:
            raise PermissionError("another workflow owns fixture window")
        self.claims.append(window_id)


@pytest.fixture
def native(tmp_path):
    from personal_mcp.desktop_native import NativeDesktop

    store = FakeStore(tmp_path / "bf" / "task")
    host = NS(config=NS(data_root=tmp_path), bf_server=NS(_bf_store=store, _bf_lease=DesktopLease()))
    host.bf = NS(call=lambda name, args: {"structuredContent": {"windows": [
        {"window_id": "window-a", "title": "fixture window", "pid": 20}]}})
    resource = NS(key="owner-a", bf_token="fixture-token", coding=object())
    service = NativeDesktop(host)
    service.api = FakeApi()
    service._sleep = lambda seconds: None
    return service, resource, store


def captured(native):
    service, resource, _ = native
    return service.capture(resource, {"kind": "display", "id": "display-a"})


def test_display_capture_keeps_negative_physical_origin_and_owner(native):
    service, resource, _ = native
    frame = captured(native)
    assert frame["geometry_verified"]
    assert frame["capture_rect"] == [-100, 0, 300, 200]
    assert frame["source_width"] == 400 and frame["source_height"] == 200
    assert frame["width"] == 400 and frame["height"] == 200
    assert frame["native_state"]["owner"] == "owner-a"
    assert "frame_id" not in frame
    with Image.open(io.BytesIO(base64.b64decode(frame["data_url"].split(",", 1)[1]))) as image:
        assert image.size == (400, 200)
    assert service.api.events == [("grab", (-100, 0, 300, 200))]


def test_inventory_returns_displays_and_task_scoped_windows(native):
    service, resource, _ = native
    output = service.inventory(resource)
    assert output["displays"] == [{"id": "display-a", "name": "fixture display", "width": 400,
                                    "height": 200, "primary": True}]
    assert output["windows"] == [{"id": "window-a", "title": "fixture window"}]
    assert output["device_label"]


def test_click_maps_preview_pixels_and_claims_hit_window(native):
    service, resource, store = native
    frame = captured(native)
    result = service.act(resource, frame, {"action": "click", "x": 50, "y": 60, "button": "right", "clicks": 2})
    assert result["ok"] and result["accepted"] and result["verified"] is False
    assert ("move", (-50, 60)) in service.api.events
    assert [event for event in service.api.events if event[0] == "mouse"] == [
        ("mouse", "right", True), ("mouse", "right", False),
        ("mouse", "right", True), ("mouse", "right", False)]
    assert store.claims


@pytest.mark.parametrize("mutation", ["owner", "layout", "pid", "created", "geometry", "secure", "unknown", "claim"])
def test_stale_or_unowned_target_never_receives_input(native, mutation):
    service, resource, store = native
    frame = captured(native)
    service.api.events.clear()
    if mutation == "owner":
        resource = NS(key="owner-b", bf_token="fixture-token")
    elif mutation == "layout":
        service.api.displays[0]["rect"] = [0, 0, 400, 200]
    elif mutation in {"pid", "created"}:
        service.api.window[mutation] += 1
    elif mutation == "geometry":
        service.api.window["rect"] = [0, 0, 400, 200]
    elif mutation == "secure":
        service.api.secure = True
    elif mutation == "unknown":
        service.api.hit = 999
    else:
        store.denied = True
    result = service.act(resource, frame, {"action": "click", "x": 50, "y": 60})
    assert not result["ok"] and not result["accepted"]
    assert not service.api.events


def test_focus_change_stops_second_click_and_releases_first(native):
    service, resource, _ = native
    frame = captured(native)
    service.api.events.clear()
    service.api.on_event = lambda event: setattr(service.api, "focus", 999) if event == ("mouse", "left", True) else None
    result = service.act(resource, frame, {"action": "click", "x": 50, "y": 60, "clicks": 2})
    assert not result["ok"] and result["accepted"] and not result["verified"]
    assert result["error"]["code"] == "NATIVE_INPUT_OUTCOME_UNKNOWN"
    assert [event for event in service.api.events if event[0] == "mouse"] == [
        ("mouse", "left", True), ("mouse", "left", False)]


def test_typing_is_literal_and_stops_after_focus_changes(native):
    service, resource, _ = native
    frame = captured(native)
    service.api.events.clear()
    result = service.act(resource, frame, {"action": "type", "text": "{Win}😀"})
    assert result["ok"]
    raw = "{Win}😀".encode("utf-16-le")
    assert [event[1] for event in service.api.events if event[0] == "unicode"] == [
        int.from_bytes(raw[index:index + 2], "little") for index in range(0, len(raw), 2)]
    service.api.events.clear()
    service.api.on_event = lambda event: setattr(service.api, "focus", 999) if event[0] == "unicode" else None
    result = service.act(resource, frame, {"action": "type", "text": "abcd"})
    assert result["error"]["code"] == "NATIVE_INPUT_OUTCOME_UNKNOWN"
    assert len(service.api.events) == 1


def test_shortcut_releases_modifiers_when_focus_changes(native):
    service, resource, _ = native
    frame = captured(native)
    service.api.events.clear()
    service.api.on_event = lambda event: setattr(service.api, "focus", 999) if event[0] == "key" and event[2] else None
    result = service.act(resource, frame, {"action": "shortcut", "keys": ["Ctrl", "A"]})
    assert result["error"]["code"] == "NATIVE_INPUT_OUTCOME_UNKNOWN"
    assert service.api.events == [("key", 17, True), ("key", 17, False)]


def test_scroll_is_bounded_and_checks_focus_between_steps(native):
    service, resource, _ = native
    frame = captured(native)
    service.api.events.clear()
    assert not service.act(resource, frame, {"action": "scroll", "dy": 21, "dx": 0})["ok"]
    assert service.api.events == []
    service.api.on_event = lambda event: setattr(service.api, "focus", 999) if event[0] == "wheel" else None
    result = service.act(resource, frame, {"action": "scroll", "dy": 3, "dx": 0, "x": 10, "y": 10})
    assert result["error"]["code"] == "NATIVE_INPUT_OUTCOME_UNKNOWN"
    assert service.api.events == [("move", (-90, 10)), ("wheel", "y", 1)]


def test_drag_releases_button_when_geometry_changes(native):
    service, resource, _ = native
    frame = captured(native)
    service.api.events.clear()

    def changed(event):
        if event == ("mouse", "left", True):
            service.api.window["rect"] = [0, 0, 400, 200]

    service.api.on_event = changed
    result = service.act(resource, frame, {"action": "drag", "x": 50, "y": 60, "to_x": 150, "to_y": 80})
    assert result["error"]["code"] == "NATIVE_INPUT_OUTCOME_UNKNOWN"
    assert [event for event in service.api.events if event[0] == "mouse"] == [
        ("mouse", "left", True), ("mouse", "left", False)]


def test_clipboard_changes_only_explicitly_and_uses_lease(native):
    service, resource, _ = native
    frame = captured(native)
    assert service.api.clipboard == "fixture clipboard"
    assert service.clipboard_read(resource) == "fixture clipboard"
    assert service.clipboard_write(resource, "literal {Win}")["accepted"]
    assert service.api.clipboard == "literal {Win}"
    assert frame["native_state"]["owner"] == resource.key


@pytest.mark.parametrize("args", [
    {"action": "click", "x": 1, "y": 1, "clicks": 3},
    {"action": "click", "x": 1, "y": 1, "button": "unknown"},
    {"action": "drag", "x": 1, "y": 1, "to_x": 999, "to_y": 1},
    {"action": "shortcut", "keys": ["A", "Ctrl"]},
])
def test_invalid_action_has_no_activation_or_input(native, args):
    service, resource, _ = native
    frame = captured(native)
    service.api.focus = 999
    service.api.events.clear()
    assert not service.act(resource, frame, args)["ok"]
    assert service.api.events == []


def screenshot_fixture(service, store, *, verified=True, old=False, outside=False):
    from pathlib import Path
    import hashlib

    captures = store.root / "captures"
    captures.mkdir()
    raw = io.BytesIO()
    Image.new("RGB", (400, 200), "green").save(raw, format="PNG")
    path = (service.host.config.data_root / "retained.png") if outside else captures / "window-a-123.png"
    if old:
        path.write_bytes(raw.getvalue())

    def caller(name, args):
        if name == "WindowInventory":
            return {"structuredContent": {"windows": [{"window_id": "window-a", "title": "fixture window"}]}}
        assert name == "WindowScreenshot"
        assert args["allow_staging"] is False and args["prefer_coordinate_mapping"] is True
        Path(path).write_bytes(raw.getvalue())
        return {"structuredContent": {"window_capture": {
            "geometry_verified": verified, "capture_rect": [-100, 0, 300, 200],
            "source_width": 400, "source_height": 200, "client_origin": [-100, 0],
            "path": str(path), "sha256": hashlib.sha256(raw.getvalue()).hexdigest()}},
            "content": [{"type": "image", "data": base64.b64encode(raw.getvalue()).decode("ascii")}]}

    service.host.bf.call = caller
    return path


def test_window_capture_only_removes_new_task_scoped_screenshot(native):
    service, resource, store = native
    path = screenshot_fixture(service, store)
    unrelated = path.parent / "retain.png"
    unrelated.write_bytes(b"retained")
    frame = service.capture(resource, {"kind": "window", "id": "window-a"})
    assert frame["geometry_verified"] and frame["source_width"] == 400
    assert not path.exists()
    assert unrelated.read_bytes() == b"retained"


@pytest.mark.parametrize("old,outside", [(True, False), (False, True)])
def test_window_capture_retains_preexisting_or_outside_path(native, old, outside):
    service, resource, store = native
    path = screenshot_fixture(service, store, old=old, outside=outside)
    assert service.capture(resource, {"kind": "window", "id": "window-a"})["geometry_verified"]
    assert path.exists()


def test_unverified_window_image_is_refused_and_new_task_file_cleaned(native):
    from personal_mcp.desktop_native import NativeDesktopError

    service, resource, store = native
    path = screenshot_fixture(service, store, verified=False)
    with pytest.raises(NativeDesktopError, match="verified physical"):
        service.capture(resource, {"kind": "window", "id": "window-a"})
    assert not path.exists()


def test_display_pixel_limit_is_checked_before_grab(native):
    from personal_mcp.desktop_native import NativeDesktopError

    service, resource, _ = native
    service.api.displays[0]["rect"] = [0, 0, 10000, 10000]
    with pytest.raises(NativeDesktopError) as error:
        captured(native)
    assert error.value.code == "NATIVE_CAPTURE_TOO_LARGE"
    assert service.api.events == []


def test_preview_is_bounded_and_mapping_uses_original_source_pixels(native):
    service, resource, _ = native
    service.api.displays[0]["rect"] = [-100, 0, 3100, 1600]
    service.api.window.update(rect=[-100, 0, 3100, 1600], client_size=[3200, 1600])
    frame = captured(native)
    assert (frame["width"], frame["height"]) == (1600, 800)
    assert len(base64.b64decode(frame["data_url"].split(",", 1)[1])) <= 1024 * 1024
    assert service.act(resource, frame, {"action": "click", "x": 25, "y": 30})["ok"]
    assert ("move", (-50, 60)) in service.api.events


def test_drag_has_twelve_segments_and_releases_button(native):
    service, resource, _ = native
    frame = captured(native)
    service.api.events.clear()
    duration = []
    service._sleep = duration.append
    result = service.act(resource, frame, {"action": "drag", "x": 10, "y": 10, "to_x": 100, "to_y": 100})
    assert result["ok"] and result["accepted"] and result["verified"] is False
    moves = [event for event in service.api.events if event[0] == "move"]
    assert len(moves) == 13 and moves[-1] == ("move", (0, 100))
    assert sum(duration) <= 1
    assert service.api.events[-1] == ("mouse", "left", False)


def test_drag_deadline_stops_motion_and_releases_button(native):
    service, resource, _ = native
    frame = captured(native)
    service.api.events.clear()
    now = [0.0]
    service._clock = lambda: now[0]
    service._sleep = lambda seconds: now.__setitem__(0, now[0] + 0.6)
    result = service.act(resource, frame, {"action": "drag", "x": 10, "y": 10, "to_x": 100, "to_y": 100})
    assert result["error"]["code"] == "NATIVE_INPUT_OUTCOME_UNKNOWN"
    assert service.api.events[-1] == ("mouse", "left", False)
    assert len([item for item in service.api.events if item[0] == "move"]) < 13


def test_typing_revalidates_identity_before_later_segment(native):
    service, resource, _ = native
    frame = captured(native)
    service.api.events.clear()

    def changed(event):
        if event[0] == "unicode":
            service.api.window["created"] = 99.0

    service.api.on_event = changed
    result = service.act(resource, frame, {"action": "type", "text": "a" * 100})
    assert result["error"]["code"] == "NATIVE_INPUT_OUTCOME_UNKNOWN"
    assert len(service.api.events) == 32


@pytest.mark.parametrize("mode", ["read", "write"])
def test_win32_clipboard_always_closes_after_open(mode):
    from personal_mcp.desktop_native import _Win32

    api = _Win32.__new__(_Win32)
    events = []

    def failed(*args):
        events.append("failed")
        raise OSError("fixture clipboard failure")

    api.clipboard = NS(OpenClipboard=lambda: events.append("open"),
                       CloseClipboard=lambda: events.append("close"),
                       IsClipboardFormatAvailable=lambda value: True,
                       GetClipboardData=failed, EmptyClipboard=lambda: events.append("empty"),
                       SetClipboardText=failed)
    with pytest.raises(OSError):
        api.clipboard_read() if mode == "read" else api.clipboard_write("text")
    assert events[0] == "open" and events[-1] == "close"


def test_native_input_uses_existing_exclusive_lease(native):
    service, resource, _ = native
    frame = captured(native)
    service.api.events.clear()
    seen = []
    service.api.on_event = lambda event: seen.append(service.host.bf_server._bf_lease.status(resource.bf_token)["desktop_lease"])
    assert service.act(resource, frame, {"action": "click", "x": 10, "y": 10})["ok"]
    assert seen and all(value == "owned_by_task" for value in seen)
    assert service.host.bf_server._bf_lease.status()["desktop_lease"] == "available"


def test_secure_desktop_refuses_capture_and_clipboard(native):
    from personal_mcp.desktop_native import NativeDesktopError

    service, resource, _ = native
    service.api.secure = True
    with pytest.raises(NativeDesktopError):
        captured(native)
    with pytest.raises(NativeDesktopError):
        service.clipboard_read(resource)
    with pytest.raises(NativeDesktopError):
        service.clipboard_write(resource, "replacement")
    assert service.api.events == [] and service.api.clipboard == "fixture clipboard"


@pytest.mark.parametrize("name,read_ok,expected", [("Default", True, True), ("Winlogon", True, False), ("Default", False, False)])
def test_secure_desktop_win32_handle_is_always_closed(monkeypatch, name, read_ok, expected):
    from personal_mcp.desktop_native import _Win32
    import ctypes

    calls = []

    def opening(*args):
        calls.append("open")
        return 123

    def closing(handle):
        assert handle == 123
        calls.append("close")

    def information(handle, index, buffer, size, needed):
        assert handle == 123 and index == 2
        buffer.value = name
        return read_ok

    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: NS(
        OpenInputDesktop=opening, CloseDesktop=closing, GetUserObjectInformationW=information))
    assert _Win32.__new__(_Win32).desktop_ready() is expected
    assert calls == ["open", "close"]


def test_dpi_context_restores_previous_thread_context_on_failure(monkeypatch):
    from personal_mcp.desktop_native import _Win32
    import ctypes

    contexts = []

    def change(value):
        contexts.append(getattr(value, "value", value))
        return 123

    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: NS(SetThreadDpiAwarenessContext=change))
    with pytest.raises(RuntimeError):
        with _Win32.__new__(_Win32).dpi_context():
            raise RuntimeError("fixture failure")
    assert contexts[-1] == 123 and len(contexts) == 2


def test_shortcut_order_is_validated_before_any_input(native):
    service, resource, _ = native
    frame = captured(native)
    service.api.events.clear()
    result = service.act(resource, frame, {"action": "shortcut", "keys": ["A", "Ctrl"]})
    assert result["error"]["code"] == "INVALID_ARGUMENT"
    assert not result["accepted"] and service.api.events == []


def test_window_capture_revalidates_creation_time_after_image(native):
    from personal_mcp.desktop_native import NativeDesktopError

    service, resource, store = native
    path = screenshot_fixture(service, store)
    caller = service.host.bf.call

    def changed(name, args):
        result = caller(name, args)
        if name == "WindowScreenshot":
            service.api.window["created"] = 99.0
        return result

    service.host.bf.call = changed
    with pytest.raises(NativeDesktopError) as error:
        service.capture(resource, {"kind": "window", "id": "window-a"})
    assert error.value.code == "NATIVE_STALE_GEOMETRY"
    assert not path.exists()


@pytest.mark.parametrize("field,value", [
    ("rect", [0, 0, 400, 200]),
    ("client_origin", [0, 0]),
    ("client_size", [300, 200]),
    ("pid", 21),
    ("created", 99.0),
    ("visible", False),
    ("minimized", True),
])
def test_display_capture_refuses_window_changed_at_grab(native, field, value):
    from personal_mcp.desktop_native import NativeDesktopError

    service, resource, _ = native
    grab = service.api.grab

    def changed(rect):
        image = grab(rect)
        service.api.window[field] = value
        return image

    service.api.grab = changed
    with pytest.raises(NativeDesktopError) as error:
        captured(native)
    assert error.value.code == "NATIVE_STALE_GEOMETRY"


def test_display_capture_rejects_moved_image_before_window_can_return(native):
    from personal_mcp.desktop_native import NativeDesktopError

    service, resource, _ = native
    grab = service.api.grab
    preview = service._preview

    def changed(rect):
        service.api.window["rect"] = [0, 0, 400, 200]
        return grab(rect)

    def returned(image):
        service.api.window["rect"] = [-100, 0, 300, 200]
        return preview(image)

    service.api.grab = changed
    service._preview = returned
    with pytest.raises(NativeDesktopError) as error:
        captured(native)
    assert error.value.code == "NATIVE_STALE_GEOMETRY"
    service.api.window["rect"] = [-100, 0, 300, 200]


def test_display_capture_refuses_foreground_change_during_grab(native):
    from personal_mcp.desktop_native import NativeDesktopError

    service, resource, _ = native
    grab = service.api.grab

    def changed(rect):
        image = grab(rect)
        service.api.focus = 999
        return image

    service.api.grab = changed
    with pytest.raises(NativeDesktopError) as error:
        captured(native)
    assert error.value.code == "NATIVE_STALE_GEOMETRY"


def test_partial_input_explicitly_marks_unknown_outcome(native):
    service, resource, _ = native
    frame = captured(native)
    service.api.on_event = lambda event: setattr(service.api, "focus", 999) if event[0] == "unicode" else None
    result = service.act(resource, frame, {"action": "type", "text": "ab"})
    assert result["ok"] is False and result["accepted"] is True
    assert result["error"]["outcome"] == "unknown"


def test_display_capture_freezes_geometry_even_if_api_reuses_mutable_lists(native):
    from personal_mcp.desktop_native import NativeDesktopError

    service, resource, _ = native
    grab = service.api.grab

    def changed(rect):
        image = grab(rect)
        service.api.window["rect"][0] += 100
        return image

    service.api.grab = changed
    with pytest.raises(NativeDesktopError) as error:
        captured(native)
    assert error.value.code == "NATIVE_STALE_GEOMETRY"


def test_display_capture_freezes_monitor_layout_before_grab(native):
    from personal_mcp.desktop_native import NativeDesktopError

    service, resource, _ = native
    grab = service.api.grab

    def changed(rect):
        image = grab(rect)
        service.api.displays[0]["rect"][0] -= 100
        return image

    service.api.grab = changed
    with pytest.raises(NativeDesktopError) as error:
        captured(native)
    assert error.value.code == "NATIVE_STALE_GEOMETRY"


def test_internal_authorization_revocation_stops_unicode_and_hides_exception(native):
    service, resource, _ = native
    frame = captured(native)
    service.api.events.clear()

    def authorize():
        if service.api.events:
            raise RuntimeError("private authorization detail")

    result = service.act(resource, frame, {"action": "type", "text": "abcd", "_authorize": authorize})
    assert result["ok"] is False and result["accepted"] is True
    assert result["error"]["outcome"] == "unknown"
    assert service.api.events == [("unicode", ord("a"))]
    assert "private authorization detail" not in str(result)


def test_internal_revocation_stops_second_click_and_always_releases(native):
    service, resource, _ = native
    frame = captured(native)
    service.api.events.clear()

    def authorize():
        if ("mouse", "left", True) in service.api.events:
            raise RuntimeError("revoked")

    result = service.act(resource, frame, {"action": "click", "x": 10, "y": 10,
                                          "clicks": 2, "_authorize": authorize})
    assert result["error"]["outcome"] == "unknown"
    assert [event for event in service.api.events if event[0] == "mouse"] == [
        ("mouse", "left", True), ("mouse", "left", False)]


def test_internal_revocation_releases_shortcut_modifier_without_final_key(native):
    service, resource, _ = native
    frame = captured(native)
    service.api.events.clear()

    def authorize():
        if ("key", 17, True) in service.api.events:
            raise RuntimeError("revoked")

    result = service.act(resource, frame, {"action": "shortcut", "keys": ["Ctrl", "A"], "_authorize": authorize})
    assert result["error"]["outcome"] == "unknown"
    assert service.api.events == [("key", 17, True), ("key", 17, False)]


def test_internal_revocation_stops_drag_segments_and_releases_button(native):
    service, resource, _ = native
    frame = captured(native)
    service.api.events.clear()

    def authorize():
        if ("mouse", "left", True) in service.api.events:
            raise RuntimeError("revoked")

    result = service.act(resource, frame, {"action": "drag", "x": 10, "y": 10, "to_x": 100,
                                          "to_y": 100, "_authorize": authorize})
    assert result["error"]["outcome"] == "unknown"
    assert service.api.events == [("move", (-90, 10)), ("mouse", "left", True), ("mouse", "left", False)]


def test_internal_revocation_before_activation_causes_no_side_effect(native):
    service, resource, _ = native
    frame = captured(native)
    service.api.focus = 999
    service.api.events.clear()

    def authorize():
        raise RuntimeError("private authorization detail")

    result = service.act(resource, frame, {"action": "click", "x": 10, "y": 10, "_authorize": authorize})
    assert result["ok"] is False and result["accepted"] is False
    assert result["error"]["code"] == "NATIVE_AUTHORIZATION_REVOKED"
    assert service.api.events == [] and service.api.focus == 999
    assert "private authorization detail" not in str(result)


def test_internal_authorization_checks_each_wheel_step(native):
    service, resource, _ = native
    frame = captured(native)
    service.api.events.clear()

    def authorize():
        if any(event[0] == "wheel" for event in service.api.events):
            raise RuntimeError("revoked")

    result = service.act(resource, frame, {"action": "scroll", "dy": 3, "x": 10, "y": 10, "_authorize": authorize})
    assert result["error"]["outcome"] == "unknown"
    assert service.api.events == [("move", (-90, 10)), ("wheel", "y", 1)]


def test_internal_clipboard_authorization_is_under_lease_and_before_replace(native):
    from personal_mcp.desktop_native import NativeDesktopError

    service, resource, _ = native
    checked = []

    def authorize():
        checked.append(service.host.bf_server._bf_lease.status(resource.bf_token)["desktop_lease"])
        raise RuntimeError("private authorization detail")

    with pytest.raises(NativeDesktopError) as error:
        service.clipboard_write(resource, "new clipboard", authorize=authorize)
    assert error.value.code == "NATIVE_AUTHORIZATION_REVOKED"
    assert checked == ["owned_by_task"]
    assert service.api.clipboard == "fixture clipboard"
    assert "private authorization detail" not in str(error.value)


def selected_window_frame(native):
    frame = captured(native)
    target = {"kind": "window", "id": "window-a"}
    frame["target"] = target
    frame["native_state"]["target"] = dict(target)
    return frame


@pytest.mark.parametrize("action", ["click", "drag"])
def test_selected_background_window_activates_after_claim_before_hit_test(native, action):
    service, resource, store = native
    frame = selected_window_frame(native)
    service.api.focus = 999
    service.api.hit = 999
    service.api.events.clear()
    activate = service.api.activate

    def bring_selected_to_front(hwnd):
        assert store.claims and store.claims[-1] == "window-a"
        result = activate(hwnd)
        service.api.hit = hwnd
        return result

    service.api.activate = bring_selected_to_front
    args = {"action": action, "x": 10, "y": 10}
    if action == "drag":
        args.update(to_x=100, to_y=100)
    result = service.act(resource, frame, args)
    assert result["ok"] is True, result
    assert service.api.events[0] == ("activate", 100)
    assert ("mouse", "left", True) in service.api.events
    assert service.api.events[-1] == ("mouse", "left", False)


def test_selected_window_always_on_top_occlusion_still_refuses_mouse(native):
    service, resource, store = native
    frame = selected_window_frame(native)
    service.api.focus = 999
    service.api.hit = 999
    service.api.events.clear()
    result = service.act(resource, frame, {"action": "click", "x": 10, "y": 10})
    assert result["ok"] is False and result["error"]["outcome"] == "unknown"
    assert store.claims
    assert service.api.events == [("activate", 100)]


@pytest.mark.parametrize("args", [
    {"action": "type", "text": "a"},
    {"action": "shortcut", "keys": ["Ctrl", "A"]},
    {"action": "scroll", "dy": 1, "x": 10, "y": 10},
])
def test_selected_background_window_keyboard_and_scroll_do_not_activate(native, args):
    service, resource, _ = native
    frame = selected_window_frame(native)
    service.api.focus = 999
    service.api.hit = 999
    if args["action"] == "scroll":
        service.api.hit = 100
    service.api.events.clear()
    result = service.act(resource, frame, args)
    assert result["ok"] is False and result["accepted"] is False
    assert result["error"]["code"] == "NATIVE_FOCUS_CHANGED"
    assert service.api.events == []


def test_display_target_never_activates_uncaptured_hit_window(native):
    service, resource, _ = native
    frame = captured(native)
    service.api.focus = 999
    service.api.hit = 999
    service.api.events.clear()
    result = service.act(resource, frame, {"action": "click", "x": 10, "y": 10})
    assert result["error"]["code"] == "NATIVE_TARGET_NOT_BOUND"
    assert result["accepted"] is False and service.api.events == []


def test_selected_background_window_claim_denial_prevents_activation(native):
    service, resource, store = native
    frame = selected_window_frame(native)
    service.api.focus = 999
    service.api.hit = 999
    service.api.events.clear()
    store.denied = True
    result = service.act(resource, frame, {"action": "click", "x": 10, "y": 10})
    assert result["ok"] is False and result["accepted"] is False
    assert service.api.events == []


def test_selected_drag_checks_occluded_end_before_mouse_down(native):
    service, resource, _ = native
    frame = selected_window_frame(native)
    service.api.events.clear()
    service.api.window_at = lambda point: 999 if tuple(point) == (0, 100) else 100
    result = service.act(resource, frame, {"action": "drag", "x": 10, "y": 10, "to_x": 100, "to_y": 100})
    assert result["ok"] is False and result["accepted"] is False
    assert service.api.events == []


def test_selected_window_activation_geometry_change_prevents_mouse_down(native):
    service, resource, _ = native
    frame = selected_window_frame(native)
    service.api.focus = 999
    service.api.hit = 999
    service.api.events.clear()
    activate = service.api.activate

    def moved_on_activation(hwnd):
        result = activate(hwnd)
        service.api.hit = hwnd
        service.api.window["rect"] = [0, 0, 400, 200]
        return result

    service.api.activate = moved_on_activation
    result = service.act(resource, frame, {"action": "click", "x": 10, "y": 10})
    assert result["ok"] is False and result["accepted"] is True
    assert result["error"]["outcome"] == "unknown"
    assert service.api.events == [("activate", 100)]


@pytest.mark.parametrize("kind", ["display", "window"])
def test_visible_background_target_first_click_uses_normal_mouse_focus(native, kind):
    service, resource, _ = native
    frame = selected_window_frame(native) if kind == "window" else captured(native)
    service.api.focus = 999
    service.api.hit = 100
    service.api.events.clear()
    service.api.on_event = lambda event: setattr(service.api, "focus", 100) if event == ("mouse", "left", True) else None
    result = service.act(resource, frame, {"action": "click", "x": 10, "y": 10})
    assert result["ok"] is True and result["accepted"] is True and result["verified"] is False
    assert service.api.events == [("move", (-90, 10)), ("mouse", "left", True), ("mouse", "left", False)]


def test_single_visible_click_is_acknowledged_even_when_windows_keeps_old_focus(native):
    service, resource, _ = native
    frame = selected_window_frame(native)
    service.api.focus = 999
    service.api.hit = 100
    service.api.events.clear()
    result = service.act(resource, frame, {"action": "click", "x": 10, "y": 10})
    assert result["ok"] is True and result["accepted"] is True and result["verified"] is False
    assert service.api.focus == 999
    assert not [event for event in service.api.events if event[0] == "activate"]
    assert service.api.events[-1] == ("mouse", "left", False)


def test_visible_double_click_waits_for_normal_first_click_focus_before_second(native):
    service, resource, _ = native
    frame = selected_window_frame(native)
    service.api.focus = 999
    service.api.hit = 100
    service.api.events.clear()
    service.api.on_event = lambda event: setattr(service.api, "focus", 100) if event == ("mouse", "left", True) else None
    result = service.act(resource, frame, {"action": "click", "x": 10, "y": 10, "clicks": 2})
    assert result["ok"] is True
    assert [event for event in service.api.events if event[0] == "mouse"] == [
        ("mouse", "left", True), ("mouse", "left", False),
        ("mouse", "left", True), ("mouse", "left", False)]
    assert not [event for event in service.api.events if event[0] == "activate"]


def test_visible_double_click_refuses_second_when_windows_keeps_old_focus(native):
    service, resource, _ = native
    frame = selected_window_frame(native)
    service.api.focus = 999
    service.api.hit = 100
    service.api.events.clear()
    waits = []
    service._sleep = waits.append
    result = service.act(resource, frame, {"action": "click", "x": 10, "y": 10, "clicks": 2})
    assert result["ok"] is False and result["accepted"] is True
    assert result["error"]["outcome"] == "unknown"
    assert [event for event in service.api.events if event[0] == "mouse"] == [
        ("mouse", "left", True), ("mouse", "left", False)]
    assert sum(waits) <= 0.21 and len(waits) <= 10
    assert not [event for event in service.api.events if event[0] == "activate"]


def test_scroll_positions_pointer_before_each_bounded_wheel_sequence(native):
    service, resource, _ = native
    frame = captured(native)
    service.api.events.clear()
    result = service.act(resource, frame, {"action": "scroll", "x": 75, "y": 60, "dy": -2})
    assert result["ok"] is True
    assert service.api.events == [("move", (-25, 60)), ("wheel", "y", -1), ("wheel", "y", -1)]


def test_scroll_missing_coordinates_is_invalid_and_sends_nothing(native):
    service, resource, _ = native
    frame = captured(native)
    service.api.events.clear()
    result = service.act(resource, frame, {"action": "scroll", "dy": 1})
    assert result["error"]["code"] == "INVALID_ARGUMENT"
    assert result["accepted"] is False and service.api.events == []


def test_scroll_rechecks_hit_target_after_pointer_move(native):
    service, resource, _ = native
    frame = captured(native)
    service.api.events.clear()
    service.api.on_event = lambda event: setattr(service.api, "hit", 999) if event[0] == "move" else None
    result = service.act(resource, frame, {"action": "scroll", "x": 75, "y": 60, "dy": 2})
    assert result["ok"] is False and result["accepted"] is True
    assert result["error"]["outcome"] == "unknown"
    assert service.api.events == [("move", (-25, 60))]
