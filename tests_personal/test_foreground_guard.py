from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from bf_automation.window_control import NativeWinApi, BackgroundControlUnavailable


def fixture():
    api = NativeWinApi.__new__(NativeWinApi)
    calls = []
    api.uia = SimpleNamespace(UIAutomationInitializerInThread=nullcontext,
        Click=lambda *args, **kwargs: calls.append(args), RightClick=lambda *args, **kwargs: None,
        MiddleClick=lambda *args, **kwargs: None)
    api._element = lambda *args: None
    api._activate = lambda hwnd: True
    api.win32gui = SimpleNamespace(GetForegroundWindow=lambda: 100, GetWindowPlacement=lambda hwnd: (0, 1, (0, 0), (0, 0), (0, 0, 500, 400)),
        IsIconic=lambda hwnd: False, SetForegroundWindow=lambda hwnd: None, SetWindowPlacement=lambda *args: None,
        IsWindow=lambda hwnd: True, ClientToScreen=lambda hwnd, point: point,
        GetClientRect=lambda hwnd: (0, 0, 500, 400))
    return api, calls


def test_no_input_when_target_activation_cannot_be_proven():
    api, calls = fixture()
    api._activate = lambda hwnd: False
    with pytest.raises(BackgroundControlUnavailable):
        api.foreground_control(100, action="click", loc=[50, 50])
    assert calls == []


def test_failed_placement_restore_is_not_reported_successful():
    api, calls = fixture()
    def failed(*args):
        raise OSError("fixture placement failure")
    api.win32gui.SetWindowPlacement = failed
    result = api.foreground_control(100, action="click", loc=[50, 50])
    assert calls == [(50, 50)]
    assert not result["placement_restored"]
