import importlib
from pathlib import Path

import pytest


def protocol():
    assert Path('bf_automation/browser/protocol.py').is_file(), 'bounded worker protocol missing'
    return importlib.import_module('bf_automation.browser.protocol')


def test_protocol_roundtrip():
    p = protocol()
    value = {'version': 1, 'id': 'x', 'operation': 'pages', 'arguments': {}}
    assert p.decode(p.encode(value)) == value


@pytest.mark.parametrize('raw', [b'[]\n', b'{"a":NaN}\n', b'{"a":1,"a":2}\n', b'not-json\n', b'{}'])
def test_bad_worker_messages_refused(raw):
    with pytest.raises(ValueError):
        protocol().decode(raw)


def test_long_worker_messages_refused():
    p = protocol()
    with pytest.raises(ValueError):
        p.decode(b'x' * (p.MAX_LINE + 1))
    with pytest.raises(ValueError):
        p.encode({'x': 'a' * p.MAX_LINE})
