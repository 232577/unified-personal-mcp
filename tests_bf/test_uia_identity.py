from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from bf_automation.window_control import NativeWinApi


def test_snapshot_caches_identity_not_cross_thread_com_pointer():
    def element(runtime_id):
        return SimpleNamespace(Name="Apply", AutomationId="1002", ClassName="Button",
                               ControlTypeName="ButtonControl", NativeWindowHandle=101,
                               IsEnabled=True, IsKeyboardFocusable=True, IsPassword=False,
                               GetRuntimeId=lambda: runtime_id)

    old = element((42, 1))
    fresh = element((42, 1))
    current = [old]
    api = NativeWinApi.__new__(NativeWinApi)
    api._elements = {}
    api.uia = SimpleNamespace(
        UIAutomationInitializerInThread=nullcontext,
        ControlFromHandle=lambda hwnd: object(),
        WalkControl=lambda root, **kwargs: ((item, 1) for item in current),
    )
    api.win32gui = SimpleNamespace(GetWindowText=lambda hwnd: "Apply")
    snapshot = api.snapshot(100)
    element_id = snapshot["elements"][0]["element_id"]
    cached = api._elements[(100, element_id)]
    assert isinstance(cached, dict), "COM object must not escape the request thread"
    current[:] = [fresh]
    assert api._element(100, element_id) is fresh
    current[:] = [element((42, 2))]
    with pytest.raises(LookupError, match="stale"):
        api._element(100, element_id)


@pytest.mark.parametrize("password", [False, True])
def test_snapshot_edit_value_is_fresh_but_password_is_never_read(password):
    item = SimpleNamespace(Name="Value", AutomationId="1001", ClassName="Edit",
                           ControlTypeName="EditControl", NativeWindowHandle=101,
                           IsPassword=password, GetRuntimeId=lambda: (42, 1))
    api = NativeWinApi.__new__(NativeWinApi)
    api._elements = {}
    api.uia = SimpleNamespace(UIAutomationInitializerInThread=nullcontext,
                              ControlFromHandle=lambda hwnd: object(),
                              WalkControl=lambda root, **kw: [(item, 1)])
    api.win32gui = SimpleNamespace(GetWindowText=lambda hwnd: "INITIAL",
                                  GetWindowLong=lambda hwnd, flag: 0x20 if password else 0)
    api.win32con = SimpleNamespace(GWL_STYLE=-16)
    reads = []
    api._read_native_text = lambda hwnd, capacity: (reads.append(hwnd) or "CURRENT")
    result = api.snapshot(100)["elements"][0]
    assert result["native_text"] == ("" if password else "CURRENT")
    assert reads == ([] if password else [101])
