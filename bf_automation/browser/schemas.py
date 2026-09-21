"""Explicit U1 baseline plus finite U2.1 contracts; no arbitrary script or file paths."""

from copy import deepcopy

from ..browser.action_schemas import ACTION_INPUTS, ACTION_RESULT, WAIT_RESULT
from ..browser.transfer_schemas import (
    DOWNLOAD_RESULT,
    TRANSFER_INPUTS,
    UPLOAD_RESULT,
)
from ..tool_schemas import (
    BOOL,
    ERROR,
    HASH,
    NONNEG_INT,
    POSITIVE_INT,
    STR,
    TASK_KEY,
    TOKEN,
    array,
    enum,
    nullable,
    obj,
)

VERSION = '2026-09-17.U1'
REQUEST = {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,128}$'}
SESSION_ID = {'type': 'string', 'pattern': '^bfb_[0-9a-f]{32}$'}
HYBRID_INSTANCE_ID = {'type': 'string', 'pattern': '^bfh_[0-9a-f]{32}$'}
WINDOW_ID = {'type': 'string', 'pattern': '^bfw_[0-9a-f]{24}$'}
PAGE_ID = {'type': 'string', 'pattern': '^bfp_[0-9a-f]{32}$'}
APPLICATION_ID = {'type': 'string', 'pattern': '^[a-z0-9_-]{1,64}$'}
URL = {'type': 'string', 'minLength': 1, 'maxLength': 8192}
NUMBER = {'type': 'number'}
CHROMIUM_SESSION = obj({
    'engine': enum('chromium'), 'version': STR, 'mode': enum('headless'),
    'worker_pid': POSITIVE_INT, 'console_visible': BOOL, 'session_id': SESSION_ID,
    'task_key': TASK_KEY, 'application_id': APPLICATION_ID, 'state': enum('active'), 'max_pages': {'const': 4},
})
WEBVIEW2_SESSION = obj({
    'engine': enum('webview2'), 'version': STR, 'mode': enum('hybrid'),
    'ownership': enum('managed', 'attached'),
    'hybrid_instance_id': HYBRID_INSTANCE_ID, 'shell_window_id': WINDOW_ID,
    'worker_pid': POSITIVE_INT, 'console_visible': BOOL, 'session_id': SESSION_ID,
    'task_key': TASK_KEY, 'application_id': APPLICATION_ID, 'state': enum('active'), 'max_pages': {'const': 4},
})
SESSION = {'oneOf': [CHROMIUM_SESSION, WEBVIEW2_SESSION]}
CLEANUP = obj({'released': BOOL, 'remaining': array(POSITIVE_INT), 'deadline_exceeded': BOOL})
NAVIGATION = obj({'page_id': PAGE_ID, 'url': STR, 'title': STR, 'http_status': nullable(NONNEG_INT)})
OP_RESULT = {'anyOf': [
    obj({'session': SESSION}), CLEANUP, obj({'page_id': PAGE_ID}),
    obj({'page_id': PAGE_ID, 'closed': BOOL}), NAVIGATION,
    obj({'error_code': STR}), {'type': 'null'}, ACTION_RESULT,
]}
OPERATION = obj({'request_id': REQUEST, 'state': enum('queued', 'dispatched', 'completed', 'failed', 'unknown'),
                 'verification': enum('not_requested', 'unknown'), 'result': OP_RESULT})
FRAME = obj({'frame_id': STR, 'url': STR})
ELEMENT = obj({'element_id': STR, 'frame_id': STR, 'role': STR, 'name': STR, 'test_id': STR,
               'disabled': BOOL, 'bounds': obj({k: NUMBER for k in ('x', 'y', 'width', 'height')})})
SNAPSHOT = obj({'page_id': PAGE_ID, 'url': STR, 'title': STR, 'snapshot_version': STR,
                'frames': array(FRAME, maxItems=10), 'elements': array(ELEMENT, maxItems=200),
                'element_count': NONNEG_INT, 'truncated': BOOL, 'coordinate_space': enum('frame_css_pixels')})
CAPTURE = obj({'page_id': PAGE_ID, 'width': POSITIVE_INT, 'height': POSITIVE_INT,
               'viewport': obj({'width': POSITIVE_INT, 'height': POSITIVE_INT}),
               'scroll': obj({k: NUMBER for k in ('x', 'y', 'dpr')}), 'sha256': HASH,
               'coordinate_space': enum('viewport_image_pixels'), 'private_fields_masked': BOOL, 'path': STR})


def when(action, required):
    return {'if': {'properties': {'action': {'const': action}}, 'required': ['action']},
            'then': {'required': list(required), 'properties': required}}


INPUTS = {
    'BrowserSession': obj({'bf_task_id': TOKEN, 'action': enum('start', 'status', 'close'),
                          'application_id': nullable(APPLICATION_ID), 'session_id': nullable(SESSION_ID),
                          'request_id': nullable(REQUEST)}, ['bf_task_id', 'action']),
    'BrowserPages': obj({'bf_task_id': TOKEN, 'session_id': SESSION_ID, 'action': enum('list', 'new', 'close'),
                        'page_id': nullable(PAGE_ID), 'request_id': nullable(REQUEST)},
                       ['bf_task_id', 'session_id', 'action']),
    'BrowserNavigate': obj({'bf_task_id': TOKEN, 'session_id': SESSION_ID,
                           'page_id': PAGE_ID, 'url': URL, 'request_id': REQUEST}),
    'BrowserSnapshot': obj({'bf_task_id': TOKEN, 'session_id': SESSION_ID, 'page_id': PAGE_ID,
                           'max_elements': {'type': 'integer', 'minimum': 1, 'maximum': 200, 'default': 120}},
                          ['bf_task_id', 'session_id', 'page_id']),
    'BrowserScreenshot': obj({'bf_task_id': TOKEN, 'session_id': SESSION_ID, 'page_id': PAGE_ID}),
    'BrowserActionStatus': obj({'bf_task_id': TOKEN, 'request_id': REQUEST}),
}
INPUTS['BrowserSession']['allOf'] = [
    when('start', {'application_id': APPLICATION_ID, 'request_id': REQUEST}),
    when('status', {'session_id': SESSION_ID}),
    when('close', {'session_id': SESSION_ID, 'request_id': REQUEST}),
]
INPUTS['BrowserPages']['allOf'] = [when('new', {'request_id': REQUEST}),
                                    when('close', {'page_id': PAGE_ID, 'request_id': REQUEST})]
OUTPUTS = {
    'BrowserSession': {'anyOf': [obj({'operation': OPERATION}), obj({'session': SESSION})]},
    'BrowserPages': {'anyOf': [obj({'operation': OPERATION}),
                             obj({'pages': array(obj({'page_id': PAGE_ID, 'url': STR}), maxItems=4)})]},
    'BrowserNavigate': obj({'operation': OPERATION}),
    'BrowserSnapshot': obj({'snapshot': SNAPSHOT}),
    'BrowserScreenshot': obj({'capture': CAPTURE}),
    'BrowserActionStatus': obj({'operation': nullable(OPERATION)}),
}

# Candidate extension; this only defines contracts. The manager flag controls
# registration, so a disabled action channel does not publish placeholder tools.
U1_INPUTS = deepcopy(INPUTS)
INPUTS.update(ACTION_INPUTS)
OUTPUTS.update({'BrowserAction': obj({'operation': OPERATION}),
                'BrowserWaitFor': obj({'observation': WAIT_RESULT})})
U2_1_INPUTS = deepcopy(INPUTS)
U2_1_OUTPUTS = deepcopy(OUTPUTS)
INPUTS.update(TRANSFER_INPUTS)

TRANSFER_OP_RESULT = {'anyOf': [*deepcopy(OP_RESULT['anyOf']), UPLOAD_RESULT, DOWNLOAD_RESULT]}
TRANSFER_OPERATION = obj({
    'request_id': REQUEST,
    'state': enum('queued', 'dispatched', 'completed', 'failed', 'unknown'),
    'verification': enum('not_requested', 'unknown'),
    'result': TRANSFER_OP_RESULT,
})
TRANSFER_OUTPUTS = {
    'BrowserUpload': obj({'operation': TRANSFER_OPERATION}),
    'BrowserDownload': obj({'operation': TRANSFER_OPERATION}),
}


def output_schema(name, transfers_enabled=False):
    if name in TRANSFER_OUTPUTS:
        if not transfers_enabled:
            raise ValueError('TRANSFER_CONTRACT_DISABLED')
        success = TRANSFER_OUTPUTS[name]
    elif name == 'BrowserActionStatus' and transfers_enabled:
        success = obj({'operation': nullable(TRANSFER_OPERATION)})
    else:
        success = OUTPUTS[name]
    return {'type': 'object', 'anyOf': [deepcopy(success), deepcopy(ERROR)],
            'description': 'BF U1: completed is tool completion, not business success. unknown must not be replayed.'}
