"""U2.1 only: finite snapshot actions and bounded read-only result waits."""

from ..tool_schemas import BOOL, NONNEG_INT, TOKEN, array, enum, nullable, obj

PAGE = {'type': 'string', 'pattern': '^bfp_[0-9a-f]{32}$'}
SESSION = {'type': 'string', 'pattern': '^bfb_[0-9a-f]{32}$'}
REQUEST = {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,128}$'}
VERSION = {'type': 'string', 'pattern': '^bfs_[0-9a-f]{32}$'}
ELEMENT = {'type': 'string', 'pattern': '^bfs_[0-9a-f]{32}_[0-9]+$'}
TEXT = {'type': 'string', 'maxLength': 65536}
ACTIONS = enum('click', 'fill', 'check', 'select', 'press')
KEYS = enum('Enter', 'Tab', 'Escape', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight')
OPTIONS = array({'type': 'string', 'maxLength': 1024}, minItems=1, maxItems=32)
ACTION_RESULT = obj({'page_id': PAGE, 'action': ACTIONS, 'accepted': BOOL,
    'verified': BOOL, 'verification_required': BOOL,
    'reason': enum('ACTION_COMPLETED', 'STALE_SNAPSHOT', 'STALE_ELEMENT',
                   'DISABLED_TARGET', 'INVALID_TARGET_TYPE', 'INVALID_ACTION_ARGUMENTS')})
WAIT_RESULT = obj({'page_id': PAGE, 'matched': BOOL, 'condition': enum('visible', 'hidden', 'text_equals'),
                  'match_count': NONNEG_INT, 'reason': enum('MATCHED', 'TIMEOUT', 'AMBIGUOUS_TARGET')})

ACTION_INPUTS = {
    'BrowserAction': obj({'bf_task_id': TOKEN, 'session_id': SESSION, 'page_id': PAGE,
        'request_id': REQUEST, 'snapshot_version': VERSION, 'element_id': ELEMENT, 'action': ACTIONS,
        'text': nullable(TEXT), 'checked': nullable(BOOL), 'values': nullable(OPTIONS), 'key': nullable(KEYS)},
        ['bf_task_id', 'session_id', 'page_id', 'request_id', 'snapshot_version', 'element_id', 'action']),
    'BrowserWaitFor': obj({'bf_task_id': TOKEN, 'session_id': SESSION, 'page_id': PAGE,
        'test_id': {'type': 'string', 'minLength': 1, 'maxLength': 128},
        'condition': enum('visible', 'hidden', 'text_equals'), 'expected': nullable(TEXT),
        'timeout_ms': {'type': 'integer', 'minimum': 50, 'maximum': 10000, 'default': 3000}},
        ['bf_task_id', 'session_id', 'page_id', 'test_id', 'condition']),
}
action_conditions = []
for action, value in {'click': None, 'fill': 'text', 'check': 'checked', 'select': 'values', 'press': 'key'}.items():
    fields = {name: {'type': 'null'} for name in ('text', 'checked', 'values', 'key') if name != value}
    branch = {'properties': fields}
    if value:
        fields[value] = {'text': TEXT, 'checked': BOOL, 'values': OPTIONS, 'key': KEYS}[value]
        branch['required'] = [value]
    action_conditions.append({'if': {'properties': {'action': {'const': action}}, 'required': ['action']},
                              'then': branch})
ACTION_INPUTS['BrowserAction']['allOf'] = action_conditions
ACTION_INPUTS['BrowserWaitFor']['allOf'] = [{
    'if': {'properties': {'condition': {'const': 'text_equals'}}, 'required': ['condition']},
    'then': {'properties': {'expected': TEXT}, 'required': ['expected']},
    'else': {'properties': {'expected': {'type': 'null'}}},
}]
