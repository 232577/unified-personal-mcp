from types import SimpleNamespace

import pytest

from bf_automation.window_control import BackgroundControlUnavailable
from tests_bf.test_native_semantics import api_for


def fixture():
    api = api_for(SimpleNamespace(ClassName="NativeChild"))
    api._element = lambda *args: None
    api.win32con = SimpleNamespace(WM_LBUTTONDOWN=513, WM_LBUTTONUP=514,
        WM_RBUTTONDOWN=516, WM_RBUTTONUP=517, WM_MBUTTONDOWN=519, WM_MBUTTONUP=520,
        MK_LBUTTON=1, MK_RBUTTON=2, MK_MBUTTON=16, WM_MOUSEMOVE=512)
    api.win32gui.GetClientRect = lambda hwnd: (0, 0, 800, 600)
    api.win32gui.IsIconic = lambda hwnd: False
    api.win32gui.ClientToScreen = lambda hwnd, point: (point[0] + 100, point[1] + 100)
    api.win32gui.ScreenToClient = lambda hwnd, point: (point[0] - (100 if hwnd == 100 else 120),
                                                               point[1] - (100 if hwnd == 100 else 140))
    api.win32gui.ChildWindowFromPointEx = lambda hwnd, point, flags: 101 if hwnd == 100 else 101
    api.win32gui.IsWindowEnabled = lambda hwnd: True
    calls = []
    api.win32gui.PostMessage = lambda *args: calls.append(args)
    return api, calls


def click(api, point):
    return api.control(100, action="click", element=None, text=None, clear=False,
                       loc=point, button="left", clicks=1)


def test_background_click_targets_child_with_translated_coordinates():
    api, calls = fixture()
    result = click(api, [50, 70])
    assert calls[-2] == (101, 513, 1, (30 << 16) | 30)
    assert calls[-1] == (101, 514, 0, (30 << 16) | 30)
    assert result["accepted"] and not result["verified"]


@pytest.mark.parametrize("point", [[-1, 50], [801, 0]])
def test_outside_client_clicks_are_refused(point):
    api, calls = fixture()
    with pytest.raises(ValueError):
        click(api, point)
    assert calls == []


def test_child_target_cannot_escape_owning_window():
    api, calls = fixture()
    api.win32gui.IsChild = lambda *args: False
    with pytest.raises(BackgroundControlUnavailable):
        click(api, [50, 70])
    assert calls == []


def test_minimized_window_requires_explicit_foreground_fallback():
    api, calls = fixture()
    api.win32gui.IsIconic = lambda hwnd: True
    with pytest.raises(BackgroundControlUnavailable):
        click(api, [50, 70])
    assert calls == []


def test_tk_message_click_is_refused_before_dispatch():
    api, calls = fixture()
    api.win32gui.GetClassName = lambda hwnd: "TkTopLevel"
    with pytest.raises(BackgroundControlUnavailable, match="Tk"):
        click(api, [50, 70])
    assert calls == []
