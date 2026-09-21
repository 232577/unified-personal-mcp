from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from bf_automation.window_control import BackgroundControlUnavailable, NativeWinApi


def api_for(control):
    api = NativeWinApi.__new__(NativeWinApi)
    api._elements = {(100, "button"): control}
    api._element = lambda hwnd, element_id: control
    api.uia = SimpleNamespace(UIAutomationInitializerInThread=nullcontext)
    api.win32con = SimpleNamespace(BM_CLICK=245, WM_SETTEXT=12, SMTO_ABORTIFHUNG=2)
    api.win32gui = SimpleNamespace(
        SendMessage=lambda *args: 0,
        SendMessageTimeout=lambda *args: 0,
        GetClassName=lambda hwnd: control.ClassName,
        GetWindowText=lambda hwnd: "not changed",
        IsWindow=lambda hwnd: True,
        IsChild=lambda parent, child: True,
    )
    return api


def invoke(api, action="invoke", **extra):
    return api.control(100, action=action, element="button", text="new", clear=True,
                       loc=None, button="left", clicks=1, **extra)


def test_native_handle_alone_does_not_mean_button_or_edit():
    control = SimpleNamespace(NativeWindowHandle=101, ClassName="Chrome_RenderWidgetHostHWND")
    with pytest.raises(BackgroundControlUnavailable):
        invoke(api_for(control))
    with pytest.raises(BackgroundControlUnavailable):
        invoke(api_for(control), "set_value")


def test_invoke_message_delivery_is_not_business_verification():
    control = SimpleNamespace(NativeWindowHandle=101, ClassName="Button")
    result = invoke(api_for(control))
    assert result["accepted"] is True
    assert result["verified"] is False
    assert result["verification_required"] is True


def test_supported_uia_pattern_is_preferred_over_native_message():
    calls = []
    control = SimpleNamespace(
        NativeWindowHandle=101, ClassName="Button",
        GetInvokePattern=lambda: SimpleNamespace(Invoke=lambda: calls.append("uia")),
    )
    result = invoke(api_for(control))
    assert calls == ["uia"]
    assert result["mode"] == "uia_invoke"
    assert result["verified"] is False


def test_set_value_requires_readback_before_verification():
    control = SimpleNamespace(NativeWindowHandle=101, ClassName="Edit")
    result = invoke(api_for(control), "set_value")
    assert result["verified"] is False


def test_pattern_execution_error_does_not_retry_by_native_message():
    def failed():
        raise OSError("provider failed after dispatch")

    control = SimpleNamespace(NativeWindowHandle=101, ClassName="Button",
                              GetInvokePattern=lambda: SimpleNamespace(Invoke=failed))
    with pytest.raises(RuntimeError, match="outcome unknown"):
        invoke(api_for(control))


def standard_api(control, calls):
    api = api_for(control)
    api.win32con.GWL_STYLE = -16
    api.win32con.WM_COMMAND = 273
    api.win32con.BN_CLICKED = 0
    api.win32gui.GetWindowLong = lambda hwnd, flag: 0
    api.win32gui.GetParent = lambda hwnd: 100
    api.win32gui.GetDlgCtrlID = lambda hwnd: 1002
    api.win32gui.IsWindowEnabled = lambda hwnd: True
    api.win32gui.SendMessageTimeout = lambda *args: calls.append(args)
    api._read_native_text = lambda hwnd, capacity: "new"
    return api


def test_standard_win32_pushbutton_uses_nonactivating_parent_notification():
    calls = []
    control = SimpleNamespace(NativeWindowHandle=101, ClassName="Button",
                              GetInvokePattern=lambda: SimpleNamespace(Invoke=lambda: calls.append("uia-focus")))
    result = invoke(standard_api(control, calls))
    assert calls[0][:4] == (100, 273, 1002, 101)
    assert "uia-focus" not in calls
    assert result["mode"] == "win32_command"
    assert result["verified"] is False


def test_standard_win32_edit_uses_nonactivating_settext_with_readback():
    calls = []
    control = SimpleNamespace(NativeWindowHandle=101, ClassName="Edit",
                              GetValuePattern=lambda: SimpleNamespace(SetValue=lambda value: calls.append("uia-focus"), Value="new"))
    result = invoke(standard_api(control, calls), "set_value")
    assert calls[0][:4] == (101, 12, 0, "new")
    assert "uia-focus" not in calls
    assert result["mode"] == "win32_value" and result["verified"] is True


def test_nonactivating_notification_must_not_bypass_disabled_button():
    calls = []
    control = SimpleNamespace(NativeWindowHandle=101, ClassName="Button")
    api = standard_api(control, calls)
    api.win32gui.IsWindowEnabled = lambda hwnd: False
    with pytest.raises(BackgroundControlUnavailable):
        invoke(api)
    assert calls == []


def test_native_readback_can_detect_extra_character_after_expected_value():
    calls = []
    control = SimpleNamespace(NativeWindowHandle=101, ClassName="Edit")
    api = standard_api(control, calls)
    capacities = []
    api._read_native_text = lambda hwnd, capacity: (capacities.append(capacity) or "new-extra"[:capacity - 1])
    result = invoke(api, "set_value")
    assert capacities[0] >= len("new") + 2
    assert result["verified"] is False
