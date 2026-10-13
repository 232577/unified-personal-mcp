"""Bounded local diagnostics, never a request journal or an authorization gate.

Only explicit metadata fields are accepted. Arguments, results, headers, URLs,
workflow credentials and caller-supplied request IDs are deliberately absent.
"""

import json
import threading
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path


_TRACE = ContextVar('personal_mcp_diagnostic_trace', default=None)
_STAGES = frozenset({'http_received', 'http_finished', 'http_failed',
                     'tool_received', 'tool_validated', 'tool_returned', 'tool_raised'})
_ERROR_CODES = frozenset({
    'INVALID_ARGUMENT', 'READ_ONLY_WORKFLOW', 'WORKFLOW_DENIED', 'WORKFLOW_INACTIVE',
    'WORKFLOW_NOT_FOUND', 'WORKFLOW_NOT_IDLE', 'WORKFLOW_CREDENTIAL_REPLACED',
    'WORKFLOW_WRITE_PENDING', 'PROJECT_BUSY', 'REQUEST_CONFLICT', 'REQUEST_REFUSED',
    'OPERATION_NOT_FOUND', 'RESULT_EXPIRED', 'OPERATION_CAPACITY', 'OPERATION_RUNNING',
    'OUTCOME_UNKNOWN', 'EXECUTION_ERROR', 'BF_EXECUTOR_CLOSED', 'BF_RECOVERING',
    'BF_CONNECTION_UNAVAILABLE', 'BF_CALL_OUTCOME_UNKNOWN', 'BF_OPERATIONS_STILL_RUNNING',
})


@contextmanager
def trace_scope():
    """Use a server-generated correlation ID; never copy a caller's identifier."""
    existing = _TRACE.get()
    token = None
    if existing is None:
        existing = uuid.uuid4().hex
        token = _TRACE.set(existing)
    try:
        yield existing
    finally:
        if token is not None:
            _TRACE.reset(token)


def result_metadata(result):
    """Describe delivery of a tool result, not the success of a business action."""
    if not isinstance(result, dict):
        return {'outcome': 'unknown'}
    metadata = {'outcome': 'error' if result.get('isError') else 'ok'}
    payload = result.get('structuredContent')
    if not isinstance(payload, dict):
        return metadata
    error = payload.get('error')
    code = error.get('code') if isinstance(error, dict) else None
    if isinstance(code, str) and code in _ERROR_CODES:
        metadata['error_code'] = code
    if type(payload.get('exit_code')) is int:
        metadata['exit_code'] = payload['exit_code']
    if payload.get('status') in ('running', 'exited'):
        metadata['command_status'] = payload['status']
    return metadata


class CallDiagnostics:
    """Instance-local rotating JSONL with visible, sticky loss accounting."""

    def __init__(self, data_root, catalog, revision, version, *, max_bytes=1024 * 1024,
                 backups=3):
        if not 1024 <= max_bytes <= 16 * 1024 * 1024 or not 1 <= backups <= 8:
            raise ValueError('INVALID_DIAGNOSTIC_RETENTION')
        self.root = Path(data_root) / 'diagnostics'
        self.path = self.root / 'calls.jsonl'
        self.catalog_revision, self.version = revision, version
        self.tool_names = frozenset(catalog)
        self.actions = {name: tuple(tool.get('inputSchema', {}).get('properties', {})
                                    .get('action', {}).get('enum', ()))
                        for name, tool in catalog.items()}
        self.instance_id = uuid.uuid4().hex
        self.max_bytes, self.backups = max_bytes, backups
        self.lock = threading.RLock()
        self.sequence = self.dropped_events = self.rotations = 0
        self.catalog_available = False
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            snapshot = {'catalog_revision': revision,
                        'revision_format': 'complete-descriptors-v2',
                        'server_version': version, 'tools': list(catalog.values())}
            temporary = self.root / 'catalog.json.tmp'
            temporary.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + '\n',
                                 encoding='utf-8')
            temporary.replace(self.root / 'catalog.json')
            self.catalog_available = True
        except OSError:
            # Observability must not convert an accepted operation into a failure
            # which could tempt a caller to replay it. Health exposes the loss.
            pass

    def _append(self, data):
        if self.path.exists() and self.path.stat().st_size + len(data) > self.max_bytes:
            for index in range(self.backups, 0, -1):
                source = self.path if index == 1 else self.root / f'calls.{index - 1}.jsonl'
                destination = self.root / f'calls.{index}.jsonl'
                if source.exists():
                    source.replace(destination)
            self.rotations += 1
        with self.path.open('ab') as stream:
            stream.write(data)

    def emit(self, stage, trace_id, *, tool=None, action=None, outcome=None, error_code=None,
             duration_ms=None, http_status=None, exit_code=None, command_status=None):
        with self.lock:
            self.sequence += 1
            try:
                if stage not in _STAGES:
                    raise ValueError('INVALID_DIAGNOSTIC_STAGE')
                # IDs are generated here, not accepted as arbitrary log text.
                if not isinstance(trace_id, str) or len(trace_id) != 32:
                    raise ValueError('INVALID_DIAGNOSTIC_TRACE')
                int(trace_id, 16)
                record = {'time': datetime.now(timezone.utc).isoformat(),
                          'instance_id': self.instance_id, 'sequence': self.sequence,
                          'trace_id': trace_id, 'stage': stage,
                          'catalog_revision': self.catalog_revision, 'version': self.version}
                if tool is not None:
                    record['tool'] = tool if tool in self.tool_names else '(unknown)'
                if isinstance(action, str) and action in self.actions.get(tool, ()):
                    record['action'] = action
                if outcome in ('ok', 'error', 'unknown'):
                    record['outcome'] = outcome
                if error_code in _ERROR_CODES:
                    record['error_code'] = error_code
                if type(duration_ms) in (int, float):
                    record['duration_ms'] = round(duration_ms, 3)
                if type(http_status) is int:
                    record['http_status'] = http_status
                if type(exit_code) is int:
                    record['exit_code'] = exit_code
                if command_status in ('running', 'exited'):
                    record['command_status'] = command_status
                data = (json.dumps(record, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')
                if len(data) > self.max_bytes:
                    raise ValueError('DIAGNOSTIC_EVENT_TOO_LARGE')
                self._append(data)
            except Exception:
                self.dropped_events += 1

    def snapshot(self):
        with self.lock:
            return {'status': 'healthy' if self.catalog_available and not self.dropped_events else 'degraded',
                    'instance_id': self.instance_id, 'catalog_available': self.catalog_available,
                    'attempted_events': self.sequence, 'dropped_events': self.dropped_events,
                    'rotations': self.rotations, 'max_bytes_per_file': self.max_bytes,
                    'retained_files': self.backups + 1,
                    'coverage': 'http_post_after_headers_and_runtime_tool_calls',
                    'client_receipt_verified': False}


def elapsed_ms(start):
    return (time.monotonic() - start) * 1000
