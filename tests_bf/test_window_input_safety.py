from types import SimpleNamespace

import pytest

from bf_automation.window_control import BackgroundControlUnavailable
from tests_bf.test_native_semantics import standard_api
from tests_personal.test_foreground_guard import fixture as foreground_fixture
from tests_personal.test_window_hit_target import fixture as background_fixture


@pytest.mark.parametrize("button,clicks", [("right", 1), ("middle", 1), ("left", 2)])
def test_element_mouse_variant_cannot_execute_single_semantic_invoke(button, clicks):
    calls = []
    control = SimpleNamespace(NativeWindowHandle=101, ClassName="Button")
    target = standard_api(control, calls)
    with pytest.raises(BackgroundControlUnavailable):
        target.control(100, action="click", element="button", text=None,
                       clear=False, loc=None, button=button, clicks=clicks)
    assert calls == []


def test_element_right_click_with_coordinates_uses_mouse_message_not_invoke():
    target, calls = background_fixture()
    invokes = []
    control = SimpleNamespace(GetInvokePattern=lambda: SimpleNamespace(Invoke=lambda: invokes.append("invoke")))
    target._element = lambda *args: control
    result = target.control(100, action="click", element="button", text=None,
                            clear=False, loc=[50, 70], button="right", clicks=1)
    assert invokes == []
    assert [call[1] for call in calls] == [512, 516, 517]
    assert result["accepted"]


@pytest.mark.parametrize("button,double_message,down,up", [
    ("left", 515, 513, 514), ("right", 518, 516, 517), ("middle", 521, 519, 520),
])
def test_background_double_click_sends_the_requested_double_click_message(button, double_message, down, up):
    target, calls = background_fixture()
    target.win32con.WM_LBUTTONDBLCLK = 515
    target.win32con.WM_RBUTTONDBLCLK = 518
    target.win32con.WM_MBUTTONDBLCLK = 521
    result = target.control(100, action="click", element=None, text=None,
                            clear=False, loc=[50, 70], button=button, clicks=2)
    assert [call[1] for call in calls] == [512, down, up, double_message, up]
    assert result["accepted"] and not result["verified"]


def test_foreground_second_click_is_refused_after_focus_changes():
    target, calls = foreground_fixture()
    state = {"hwnd": 100}
    target.win32gui.GetForegroundWindow = lambda: state["hwnd"]

    def mouse(*args, **kwargs):
        calls.append(args)
        state["hwnd"] = 200

    target.uia.Click = mouse
    with pytest.raises(RuntimeError, match="focus"):
        target.foreground_control(100, action="click", loc=[50, 50], button="left", clicks=2)
    assert calls == [(50, 50)]


def test_foreground_text_stops_after_focus_changes():
    target, _ = foreground_fixture()
    state, calls = {"hwnd": 100}, []
    target.win32gui.GetForegroundWindow = lambda: state["hwnd"]

    def send(char):
        calls.append(char)
        state["hwnd"] = 200
        return 2

    target.uia.SendUnicodeChar = send
    with pytest.raises(RuntimeError, match="focus"):
        target.foreground_control(100, action="type", text="abc", clear=False)
    assert calls == ["a"]


def test_foreground_failed_element_focus_refuses_all_text_input():
    target, _ = foreground_fixture()
    calls = []

    def failed_focus():
        raise OSError("fixture focus refused")

    target._element = lambda *args: SimpleNamespace(SetFocus=failed_focus)
    target.uia.SendUnicodeChar = lambda char: calls.append(char) or 2
    with pytest.raises(BackgroundControlUnavailable, match="focus"):
        target.foreground_control(100, action="type", element="field", text="abc", clear=False)
    assert calls == []


def test_foreground_clear_stops_before_backspace_after_focus_changes():
    target, _ = foreground_fixture()
    state, calls = {"hwnd": 100}, []
    target.win32gui.GetForegroundWindow = lambda: state["hwnd"]

    def keys(value):
        calls.append(value)
        state["hwnd"] = 200

    target.uia.SendKeys = keys
    target.uia.SendUnicodeChar = lambda char: calls.append(char) or 2
    with pytest.raises(RuntimeError, match="focus"):
        target.foreground_control(100, action="type", text="abc", clear=True)
    assert calls == ["{Ctrl}a"]
