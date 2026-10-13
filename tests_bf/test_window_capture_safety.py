from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from bf_automation.window_control import WindowCaptureBackend, WindowController
import bf_automation.window_control as capture_module


class Store:
    state_root = Path("isolated-fixture-state")

    def resolve_window(self, token, window_id):
        return {"hwnd": 100, "pid": 10, "title": "fixture"}

    def claim_window(self, token, window_id):
        pass


@pytest.mark.parametrize("operation", ["screenshot", "snapshot", "control"])
def test_window_identity_is_rechecked_after_waiting_for_operation_lock(operation):
    state, calls = {"valid": True}, []
    api = SimpleNamespace(validate_window=lambda *args: state["valid"],
                          snapshot=lambda *args, **kwargs: calls.append("snapshot") or {},
                          control=lambda *args, **kwargs: calls.append("control") or {})
    backend = SimpleNamespace(capture=lambda *args, **kwargs: calls.append("capture") or {})
    controller = WindowController(Store(), win_api=api, capture_backend=backend)

    @contextmanager
    def lock(record):
        state["valid"] = False
        yield

    controller._operation_lock = lock
    kwargs = {"action": "invoke"} if operation == "control" else {}
    with pytest.raises(LookupError, match="stale"):
        getattr(controller, operation)("task", "window", **kwargs)
    assert calls == []


def test_snapshot_tree_and_capture_share_one_operation_lock():
    events = []
    api = SimpleNamespace(validate_window=lambda *args: True,
                          snapshot=lambda *args, **kwargs: events.append("tree") or {})
    backend = SimpleNamespace(capture=lambda *args, **kwargs: events.append("capture") or {})
    controller = WindowController(Store(), win_api=api, capture_backend=backend)

    @contextmanager
    def lock(record):
        events.append("enter")
        yield
        events.append("exit")

    controller._operation_lock = lock
    controller.snapshot("task", "window")
    assert events == ["enter", "tree", "capture", "exit"]


def test_snapshot_refuses_image_if_window_identity_changes_during_tree_read():
    state, calls = {"valid": True}, []

    def tree(*args, **kwargs):
        state["valid"] = False
        return {}

    api = SimpleNamespace(validate_window=lambda *args: state["valid"], snapshot=tree)
    backend = SimpleNamespace(capture=lambda *args, **kwargs: calls.append("capture") or {})
    controller = WindowController(Store(), win_api=api, capture_backend=backend)
    with pytest.raises(LookupError, match="stale"):
        controller.snapshot("task", "window")
    assert calls == []


def geometry():
    return {"window_rect": [-500, 80, -300, 230], "client_origin": [-492, 111],
            "client_size": [184, 111], "physical_coordinates": True, "minimized": False}


def backend(mode="print_window", *, shifted=False, sized=False):
    result = WindowCaptureBackend.__new__(WindowCaptureBackend)
    result.win32gui = SimpleNamespace(IsIconic=lambda hwnd: False)
    calls = []

    def observed(hwnd):
        record = geometry()
        if calls and shifted:
            record["window_rect"] = [-499, 80, -299, 230]
        calls.append("geometry")
        return record

    result._window_geometry = observed
    capture = {"backend": mode, "capture_mode": mode, "width": 199 if sized else 200,
               "height": 150, "staging_used": False}
    if mode == "print_window":
        capture["capture_rect"] = geometry()["window_rect"]
        result._wgc = lambda hwnd: (_ for _ in ()).throw(RuntimeError("fixture WGC failure"))
    else:
        result._wgc = lambda hwnd: dict(capture)
    result._print_window = lambda hwnd: dict(capture)
    return result


def test_print_window_exact_geometry_maps_negative_screen_origin():
    result = backend().capture(100)
    assert result["capture_rect"] == [-500, 80, -300, 230]
    assert result["client_origin"] == [-492, 111]
    assert result["client_size"] == [184, 111]
    assert result["source_width"] == 200 and result["source_height"] == 150
    assert result["geometry_verified"] is True
    assert result["coordinate_space"] == "window_surface_pixels"


@pytest.mark.parametrize("shifted,sized", [(True, False), (False, True)])
def test_print_window_changed_or_mismatched_geometry_refuses_pixel_mapping(shifted, sized):
    result = backend(shifted=shifted, sized=sized).capture(100)
    assert result["capture_rect"] is None
    assert result["geometry_verified"] is False


def test_wgc_does_not_guess_capture_origin_from_matching_dimensions():
    result = backend("wgc_d3d11").capture(100)
    assert result["capture_rect"] is None
    assert result["geometry_verified"] is False
    assert result["client_origin"] == [-492, 111]
    assert result["source_width"] == 200


def test_non_physical_thread_geometry_refuses_pixel_mapping():
    target = backend()
    record = geometry()
    record["physical_coordinates"] = False
    target._window_geometry = lambda hwnd: dict(record)
    result = target.capture(100)
    assert result["capture_rect"] is None and not result["geometry_verified"]


def test_coordinate_mapping_request_uses_only_print_window():
    target = backend()
    calls = []
    target._wgc = lambda hwnd: calls.append("wgc") or {}
    result = target.capture(100, prefer_coordinate_mapping=True)
    assert calls == []
    assert result["geometry_verified"]


@pytest.mark.parametrize("mode", ["changed", "sized", "failed", "minimized"])
def test_coordinate_mapping_request_never_falls_back_to_unverified_wgc(mode):
    target = backend(shifted=mode == "changed", sized=mode == "sized")
    calls = []
    target._wgc = lambda hwnd: calls.append("wgc") or {}
    if mode == "failed":
        target._print_window = lambda hwnd: (_ for _ in ()).throw(OSError("fixture print failure"))
    if mode == "minimized":
        target.win32gui.IsIconic = lambda hwnd: True
    with pytest.raises(RuntimeError, match="COORDINATE_MAPPING_UNAVAILABLE"):
        target.capture(100, prefer_coordinate_mapping=True)
    assert calls == []


def test_controller_passes_explicit_mapping_request_to_capture_backend():
    calls = []
    api = SimpleNamespace(validate_window=lambda *args: True)
    backend = SimpleNamespace(capture=lambda *args, **kwargs: calls.append(kwargs) or {})
    controller = WindowController(Store(), win_api=api, capture_backend=backend)
    controller.screenshot("task", "window", prefer_coordinate_mapping=True)
    assert calls == [{"allow_staging": True, "prefer_coordinate_mapping": True}]


@pytest.mark.parametrize("failure", [False, True])
def test_print_window_releases_gdi_objects_in_safe_order_even_if_bitmap_setup_fails(monkeypatch, failure):
    events = []
    bitmap = SimpleNamespace(GetHandle=lambda: 40,
                             GetInfo=lambda: {"bmWidth": 200, "bmHeight": 150},
                             GetBitmapBits=lambda raw: bytes(200 * 150 * 4))

    def compatible(*args):
        if failure:
            raise OSError("fixture bitmap setup failed")

    bitmap.CreateCompatibleBitmap = compatible
    memory = SimpleNamespace(GetSafeHdc=lambda: 30, DeleteDC=lambda: events.append("memory-delete"),
                             SelectObject=lambda selected: events.append("restore" if selected == "old-bitmap" else "select") or "old-bitmap")
    source = SimpleNamespace(CreateCompatibleDC=lambda: memory, DeleteDC=lambda: events.append("source-delete"))
    target = WindowCaptureBackend.__new__(WindowCaptureBackend)
    target.win32gui = SimpleNamespace(GetWindowRect=lambda hwnd: (-500, 80, -300, 230),
                                    GetWindowDC=lambda hwnd: 20,
                                    DeleteObject=lambda handle: events.append("bitmap-delete"),
                                    ReleaseDC=lambda *args: events.append("release"))
    target.win32ui = SimpleNamespace(CreateDCFromHandle=lambda dc: source, CreateBitmap=lambda: bitmap)
    target._native_print_window = lambda *args: 1
    monkeypatch.setattr(capture_module.ctypes, "windll", SimpleNamespace(user32=SimpleNamespace(PrintWindow=lambda *args: 1)), raising=False)
    if failure:
        with pytest.raises(OSError, match="fixture bitmap"):
            target._print_window(100)
        assert events == ["bitmap-delete", "memory-delete", "source-delete", "release"]
    else:
        target._print_window(100)
        assert events == ["select", "restore", "bitmap-delete", "memory-delete", "source-delete", "release"]
