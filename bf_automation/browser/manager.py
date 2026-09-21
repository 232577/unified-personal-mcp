"""Task ownership, request journaling and one independently isolated worker per task."""

from __future__ import annotations

import atexit
import hashlib
import json
import shutil
import threading
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..browser.operation_store import OperationStore
from ..browser.policy import validate_navigation
from ..browser.process import WorkerProcess, WorkerReplyError
from ..hybrid.models import HybridProfile
from ..hybrid.policy import load_browser_profile


@dataclass
class Session:
    session_id: str
    owner: str
    application_id: str
    directory: Path
    profile: object
    task_token: str | None = field(default=None, repr=False)
    hybrid_instance_id: str | None = None
    worker: object = None
    lock: object = field(default_factory=threading.RLock)
    info: dict = field(default_factory=dict)
    state: str = 'starting'


class BrowserManager:
    def __init__(self, store, config_path, apps_dir, *, source_root, hybrid_manager=None):
        self.store = store
        self.source_root = Path(source_root).resolve()
        self.apps_dir = Path(apps_dir).resolve()
        self.config = json.loads(Path(config_path).read_text(encoding='utf-8'))
        self.actions_enabled = self.config.get('actions_enabled', False)
        self.transfers_enabled = self.config.get('transfers_enabled', False)
        if type(self.actions_enabled) is not bool:
            raise ValueError('INVALID_ACTIONS_FLAG')
        if type(self.transfers_enabled) is not bool:
            raise ValueError('INVALID_TRANSFERS_FLAG')
        if self.transfers_enabled and not self.actions_enabled:
            raise ValueError('TRANSFERS_REQUIRE_ACTIONS')
        max_sessions = self.config.get('max_sessions')
        max_pages = self.config.get('max_pages_per_session')
        if (self.config.get('version') != 1 or self.config.get('mode') != 'headless'
                or type(max_sessions) is not int or not 1 <= max_sessions <= 4
                or type(max_pages) is not int or max_pages != 4):
            raise ValueError('UNVERIFIED_BROWSER_CONFIG')
        self.max_sessions = max_sessions
        self.max_pages_per_session = max_pages
        self.hybrid_manager = hybrid_manager
        self.python = Path(self.config['python']).resolve(strict=True)
        self.browsers_path = Path(self.config['browsers_path']).resolve(strict=True)
        self.sessions = {}
        self.lock = threading.RLock()
        self.journal = OperationStore(store.state_root / 'browser-operations.sqlite3')
        self.journal.recover()
        self.ending = set()
        self.store.add_end_hook(self.end_task)
        atexit.register(self.shutdown)

    def _task(self, token):
        directory, row = self.store.require(token)
        if row['task_key'] in self.ending:
            raise PermissionError('TASK_ENDING')
        return directory, row

    def _session(self, token, session_id):
        _, task = self._task(token)
        with self.lock:
            session = self.sessions.get(session_id)
        if session is None or session.owner != task['task_key']:
            raise PermissionError('SESSION_NOT_OWNED_OR_CLOSED')
        return session

    def _once(self, owner, request_id, operation, args, execute):
        old, created = self.journal.reserve(owner, request_id, operation, args)
        if not created:
            return old
        self.journal.dispatch(owner, request_id)
        try:
            result = execute()
        except WorkerReplyError as exc:
            # Provider failure can happen after navigation reached the server.
            # Only explicit precondition failures are known not to have run.
            code = str(exc)
            state = 'failed' if code in {'INVALID_ARGUMENT', 'STALE_TARGET'} else 'unknown'
            return self.journal.finish(owner, request_id, state, {'error_code': code})
        except (EOFError, TimeoutError, BrokenPipeError, OSError):
            return self.journal.finish(owner, request_id, 'unknown', None)
        except Exception:
            return self.journal.finish(owner, request_id, 'unknown', {'error_code': 'OUTCOME_UNKNOWN'})
        failed_result = operation in {'action', 'upload', 'download'} and result.get('accepted') is False
        failed_verify = operation == 'upload' and result.get('verified') is not True
        state = 'failed' if failed_result or failed_verify else 'completed'
        return self.journal.finish(owner, request_id, state, result)

    def start(self, token, application_id, request_id):
        directory, task = self._task(token)
        from ..application_registry import project_profiles
        profiles = project_profiles(self.store, token, self.apps_dir, application_id)
        profile_scope = (Path(task['project_path']).resolve().parent
                         if self.store.allow_external_projects else self.store.allowed_root)
        registered = load_browser_profile(profiles, application_id, profile_scope)
        hybrid_profile = registered if isinstance(registered, HybridProfile) else None
        profile = hybrid_profile.web if hybrid_profile is not None else registered
        if profile.project_root != Path(task['project_path']).resolve():
            raise PermissionError('APPLICATION_PROJECT_MISMATCH')
        if hybrid_profile is not None and self.hybrid_manager is None:
            raise RuntimeError('HYBRID_ADAPTER_DISABLED')
        with self.lock:
            # Reserve and dispatch only once even for simultaneous starts.
            old = self.journal.get(task['task_key'], request_id)
            if old:
                return self.journal.reserve(task['task_key'], request_id, 'start', {'application_id': application_id})[0]
            if (len(self.sessions) >= self.max_sessions
                    or any(s.owner == task['task_key'] for s in self.sessions.values())):
                raise ValueError('SESSION_LIMIT_REACHED')
            sid = 'bfb_' + uuid.uuid4().hex
            owned_dir = directory / 'browser-workers' / sid
            owned_dir.mkdir(parents=True, exist_ok=False)
            session = Session(
                sid, task['task_key'], application_id, owned_dir, profile,
                task_token=token,
            )
            self.sessions[sid] = session
            session.lock.acquire()
        try:
            self._task(token)
            def launch():
                try:
                    hybrid_instance = None
                    connection = None
                    if hybrid_profile is not None:
                        if hybrid_profile.hybrid.ownership == 'managed':
                            hybrid_instance = self.hybrid_manager.prepare_managed(
                                token, hybrid_profile,
                            )
                        else:
                            hybrid_instance = self.hybrid_manager.prepare_attached(
                                token, hybrid_profile,
                            )
                        session.hybrid_instance_id = hybrid_instance.hybrid_instance_id
                        connection = self.hybrid_manager.connection_spec(
                            token, hybrid_instance.hybrid_instance_id,
                        )
                    session.worker = WorkerProcess(self.python, self.source_root / 'bf_automation/browser/worker.py',
                                                   owned_dir, self.browsers_path)
                    self._task(token)
                    args = asdict(profile)
                    args['project_root'] = str(profile.project_root)
                    boot = {
                        'profile': args,
                        'directory': str(owned_dir),
                        'max_pages': self.max_pages_per_session,
                        'actions_enabled': self.actions_enabled,
                        'transfers_enabled': self.transfers_enabled,
                    }
                    if hybrid_instance is not None:
                        boot.update(engine_mode='webview2', connection=connection)
                    info = session.worker.call('boot', boot)
                    if hybrid_instance is not None:
                        info = {**info, **hybrid_instance.public_identity()}
                    session.info = {**info, 'session_id': sid, 'task_key': session.owner,
                                    'application_id': application_id, 'state': 'active',
                                    'max_pages': self.max_pages_per_session}
                    session.state = 'active'
                    (owned_dir / 'owner.json').write_text(json.dumps({
                        'session_id': sid, 'task_key': session.owner, 'worker_pid': info['worker_pid'],
                        'hybrid_instance_id': session.hybrid_instance_id,
                    }), encoding='utf-8')
                    return {'session': dict(session.info)}
                except BaseException:
                    self._close(session)
                    raise
            return self._once(session.owner, request_id, 'start', {'application_id': application_id}, launch)
        finally:
            session.lock.release()

    def request(self, token, session_id, operation, request_id, arguments):
        # Checking a previous request after close is allowed for its task only.
        _, task = self._task(token)
        key_args = {'session_id': session_id, **arguments}
        old = self.journal.get(task['task_key'], request_id)
        if old:
            return self.journal.reserve(task['task_key'], request_id, operation, key_args)[0]
        session = self._session(token, session_id)
        permitted = {'new_page', 'close_page', 'navigate', 'close'}
        if self.actions_enabled:
            permitted.add('action')
        if self.transfers_enabled:
            permitted |= {'upload', 'download'}
        if operation not in permitted:
            raise ValueError('UNKNOWN_BROWSER_OPERATION')
        if operation == 'navigate':
            validate_navigation(session.profile, arguments['url'])
        upload = None
        if operation == 'upload':
            upload = self._prepare_upload(task, session.profile, arguments['source_path'])
        with session.lock:
            self._task(token)
            def execute():
                if operation == 'close':
                    return self._close(session)
                if session.state != 'active':
                    raise LookupError('SESSION_CLOSED')
                try:
                    worker_arguments = dict(arguments)
                    if operation == 'upload':
                        worker_arguments.pop('source_path', None)
                        staged = self._stage_upload(session, upload)
                        worker_arguments.update(file_path=str(staged), filename=upload['filename'],
                                                size=upload['size'], sha256=upload['sha256'])
                    elif operation == 'download':
                        worker_arguments['download_dir'] = str(session.directory / 'downloads')
                    result = session.worker.call(operation, worker_arguments)
                    if operation == 'download' and 'file' in result:
                        relative = result.pop('file')
                        path = (session.directory / relative).resolve(strict=True)
                        downloads = (session.directory / 'downloads').resolve()
                        if not path.is_relative_to(downloads) or path.stat().st_size > 64 * 1024 * 1024:
                            raise ValueError('INVALID_DOWNLOAD_PATH')
                        if hashlib.sha256(path.read_bytes()).hexdigest() != result.get('sha256'):
                            raise ValueError('DOWNLOAD_HASH_MISMATCH')
                        result['path'] = str(path)
                    return result
                except (EOFError, TimeoutError, BrokenPipeError, OSError):
                    self._close(session)
                    raise
            return self._once(session.owner, request_id, operation, key_args, execute)

    @staticmethod
    def _file_hash(path):
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
        return digest.hexdigest()

    def _prepare_upload(self, task, profile, source_path):
        if not isinstance(source_path, str) or not source_path or len(source_path) > 4096:
            raise ValueError('INVALID_UPLOAD_SOURCE')
        project = Path(task['project_path']).resolve()
        requested = Path(source_path)
        candidate = requested if requested.is_absolute() else project / requested
        try:
            source = candidate.resolve(strict=True)
        except OSError:
            raise ValueError('UPLOAD_SOURCE_UNAVAILABLE') from None
        if not source.is_relative_to(project):
            raise PermissionError('UPLOAD_PATH_OUTSIDE_PROJECT')
        roots = []
        for relative in profile.upload_roots:
            root = (project / Path(relative)).resolve()
            if root.is_relative_to(project) and root.is_dir():
                roots.append(root)
        if not roots or not any(source.is_relative_to(root) for root in roots):
            raise PermissionError('UPLOAD_ROOT_NOT_ALLOWED')
        if not source.is_file():
            raise ValueError('UPLOAD_SOURCE_NOT_FILE')
        size = source.stat().st_size
        if size > 32 * 1024 * 1024:
            raise ValueError('UPLOAD_TOO_LARGE')
        return {'source': source, 'filename': source.name, 'size': size,
                'sha256': self._file_hash(source)}

    def _stage_upload(self, session, upload):
        source = upload['source']
        if source.stat().st_size != upload['size'] or self._file_hash(source) != upload['sha256']:
            raise ValueError('UPLOAD_SOURCE_CHANGED')
        directory = session.directory / 'uploads'
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / (upload['sha256'] + '.bin')
        if not target.exists():
            shutil.copyfile(source, target)
        if target.stat().st_size != upload['size'] or self._file_hash(target) != upload['sha256']:
            raise ValueError('UPLOAD_STAGE_VERIFY_FAILED')
        return target

    def read(self, token, session_id, operation, arguments=None):
        session = self._session(token, session_id)
        with session.lock:
            self._task(token)
            if session.state != 'active':
                raise LookupError('SESSION_CLOSED')
            if operation == 'status':
                try:
                    health = session.worker.call('health', {})
                    if health.get('alive') is not True:
                        raise EOFError('INVALID_HEALTH_REPLY')
                except (EOFError, TimeoutError, BrokenPipeError, OSError, WorkerReplyError):
                    self._close(session)
                    raise LookupError('WORKER_NOT_AVAILABLE') from None
                return {'session': dict(session.info)}
            if operation not in {'pages', 'snapshot', 'screenshot'} | ({'wait_for'} if self.actions_enabled else set()):
                raise ValueError('UNKNOWN_BROWSER_OBSERVATION')
            try:
                result = session.worker.call(operation, arguments or {})
            except (EOFError, TimeoutError, BrokenPipeError, OSError):
                self._close(session)
                raise
            if operation == 'screenshot':
                filename = result.pop('file')
                path = (session.directory / filename).resolve(strict=True)
                if not path.is_relative_to(session.directory) or path.suffix != '.png' or path.stat().st_size > 8*1024*1024:
                    raise ValueError('INVALID_CAPTURE_PATH')
                if hashlib.sha256(path.read_bytes()).hexdigest() != result['sha256']:
                    raise ValueError('CAPTURE_HASH_MISMATCH')
                result['path'] = str(path)
            return result

    def operation_status(self, token, request_id):
        _, task = self._task(token)
        return self.journal.get(task['task_key'], request_id)

    def _close(self, session):
        result = (session.worker.close() if session.worker is not None else
                  {'released': True, 'remaining': [], 'deadline_exceeded': False})
        if not result['released']:
            session.state = 'cleanup_failed'
            raise RuntimeError('BROWSER_CLEANUP_INCOMPLETE')
        if session.hybrid_instance_id is not None:
            if self.hybrid_manager is None or session.task_token is None:
                session.state = 'cleanup_failed'
                raise RuntimeError('HYBRID_CLEANUP_INCOMPLETE')
            hybrid = self.hybrid_manager.release(
                session.task_token, session.hybrid_instance_id,
            )
            if hybrid.get('remaining'):
                session.state = 'cleanup_failed'
                raise RuntimeError('HYBRID_CLEANUP_INCOMPLETE')
            session.hybrid_instance_id = None
        session.state = 'closed'
        with self.lock:
            self.sessions.pop(session.session_id, None)
        return result

    def end_task(self, token):
        # TaskStore calls hooks while the token is still active.
        _, task = self.store.require(token)
        with self.lock:
            self.ending.add(task['task_key'])
            sessions = [s for s in self.sessions.values() if s.owner == task['task_key']]
        for session in sessions:
            # Unblock an in-flight request before acquiring its serialization lock.
            if session.worker is not None:
                session.worker.close()
            with session.lock:
                self._close(session)
        if self.hybrid_manager is not None:
            # Failed preparation has no browser session. Retry its owned cleanup
            # only after every browser worker above has released its resources.
            self.hybrid_manager.end_task(token)
        # Completed and unknown records remain private evidence after task end.

    def shutdown(self):
        with self.lock:
            sessions = list(self.sessions.values())
        for session in sessions:
            with session.lock:
                self._close(session)
