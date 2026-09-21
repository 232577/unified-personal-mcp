"""Small JSON-line protocol on private anonymous pipes; pictures travel via owned files."""

import json

VERSION = 1
MAX_LINE = 262144


def encode(value):
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8') + b'\n'
    if len(raw) > MAX_LINE:
        raise ValueError('PROTOCOL_MESSAGE_TOO_LARGE')
    return raw


def _unique(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError('DUPLICATE_JSON_KEY')
        result[name] = value
    return result


def decode(raw):
    if not raw or len(raw) > MAX_LINE or not raw.endswith(b'\n'):
        raise ValueError('INVALID_PROTOCOL_FRAME')
    def bad_constant(_):
        raise ValueError('NONFINITE_JSON')
    value = json.loads(raw, object_pairs_hook=_unique, parse_constant=bad_constant)
    if not isinstance(value, dict):
        raise ValueError('PROTOCOL_OBJECT_REQUIRED')
    return value
