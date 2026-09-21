"""U2.2 transfer contracts: project-scoped upload and task-private download."""

from ..tool_schemas import BOOL, HASH, NONNEG_INT, STR, TOKEN, enum, obj

PAGE = {'type': 'string', 'pattern': '^bfp_[0-9a-f]{32}$'}
SESSION = {'type': 'string', 'pattern': '^bfb_[0-9a-f]{32}$'}
REQUEST = {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{1,128}$'}
VERSION = {'type': 'string', 'pattern': '^bfs_[0-9a-f]{32}$'}
ELEMENT = {'type': 'string', 'pattern': '^bfs_[0-9a-f]{32}_[0-9]+$'}
PATH = {'type': 'string', 'minLength': 1, 'maxLength': 4096}

UPLOAD_RESULT = obj({
    'page_id': PAGE, 'accepted': BOOL, 'verified': BOOL, 'filename': STR,
    'size': NONNEG_INT, 'sha256': HASH,
    'reason': enum('UPLOAD_READY', 'UPLOAD_VERIFICATION_FAILED', 'UPLOAD_SOURCE_CHANGED',
                   'STALE_SNAPSHOT', 'STALE_ELEMENT', 'DISABLED_TARGET', 'INVALID_TARGET_TYPE'),
})
DOWNLOAD_RESULT = obj({
    'page_id': PAGE, 'accepted': BOOL, 'verified': BOOL, 'filename': STR,
    'size': NONNEG_INT, 'sha256': HASH, 'path': STR,
    'reason': enum('DOWNLOAD_SAVED', 'DOWNLOAD_TOO_LARGE', 'STALE_SNAPSHOT',
                   'STALE_ELEMENT', 'DISABLED_TARGET'),
})

TRANSFER_INPUTS = {
    'BrowserUpload': obj({
        'bf_task_id': TOKEN, 'session_id': SESSION, 'page_id': PAGE, 'request_id': REQUEST,
        'snapshot_version': VERSION, 'element_id': ELEMENT, 'source_path': PATH,
    }),
    'BrowserDownload': obj({
        'bf_task_id': TOKEN, 'session_id': SESSION, 'page_id': PAGE, 'request_id': REQUEST,
        'snapshot_version': VERSION, 'element_id': ELEMENT,
        'timeout_ms': {'type': 'integer', 'minimum': 100, 'maximum': 30000, 'default': 10000},
    }),
}
