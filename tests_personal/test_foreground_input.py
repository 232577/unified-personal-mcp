from types import SimpleNamespace
from contextlib import nullcontext

import pytest

from bf_automation.window_control import NativeWinApi


def api():
    result = NativeWinApi.__new__(NativeWinApi)
    result.win32gui = SimpleNamespace(GetForegroundWindow=lambda: 100, GetWindowPlacement=lambda hwnd: (),
        IsIconic=lambda hwnd: False, SetWindowPlacement=lambda *args: None, IsWindow=lambda hwnd: True,
        ClientToScreen=lambda hwnd, loc: loc, GetClientRect=lambda hwnd: (0, 0, 200, 200))
    result._activate = lambda hwnd: True
    result._element = lambda *args: None
    return result


@pytest.mark.parametrize("button,clicks", [("left", 2), ("right", 1), ("middle", 1)])
def test_foreground_mouse_preserves_requested_variant(button, clicks):
    target = api()
    calls = []
    target.uia = SimpleNamespace(UIAutomationInitializerInThread=nullcontext,
        Click=lambda *args, **kwargs: calls.append("left"),
        RightClick=lambda *args, **kwargs: calls.append("right"),
        MiddleClick=lambda *args, **kwargs: calls.append("middle"))
    target.foreground_control(100, action="click", loc=[10, 10], button=button, clicks=clicks)
    assert calls == [button] * clicks


def test_foreground_text_is_literal_including_braces_and_non_bmp():
    target = api()
    sent = []
    target.uia = SimpleNamespace(UIAutomationInitializerInThread=nullcontext,
        SendKeys=lambda *args, **kwargs: sent.append("shortcut-parser"),
        SendUnicodeChar=lambda char: (sent.append(ord(char)) or 2))
    target.foreground_control(100, action="type", text="{Win} 中文😀", clear=False)
    raw = "{Win} 中文😀".encode("utf-16-le")
    assert sent == [int.from_bytes(raw[i:i+2], "little") for i in range(0, len(raw), 2)]

