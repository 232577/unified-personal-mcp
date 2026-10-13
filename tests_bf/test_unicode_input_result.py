from types import SimpleNamespace

import pytest

from bf_automation.native_input import send_unicode_char


@pytest.mark.parametrize('results,expected', [([1, 1], 2), ([0, 1], 0), ([1, 0], 0)])
def test_each_unicode_event_is_checked_even_when_provider_returns_only_last_count(results, expected):
    calls = []
    def send(*events):
        calls.append(events)
        return results.pop(0)
    uia = SimpleNamespace(KeyboardEventFlag=SimpleNamespace(KeyUnicode=4, KeyDown=0, KeyUp=2),
                          KeyboardInput=lambda vk, scan, flag: (vk, scan, flag), SendInput=send)
    assert send_unicode_char(uia, '中') == expected
    assert calls == [((0, ord('中'), 4),), ((0, ord('中'), 6),)]
