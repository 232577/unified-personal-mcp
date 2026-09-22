"""Task-owned lifecycle manager for managed and attached WebView2 shells."""

from __future__ import annotations

import os
import re
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from ..hybrid.process import HybridPlatform, ProcessIdentity
from ..runtime import application_environment

HYBRID_ID_RE = re.compile(r'^bfh_[0-9a-f]{32}$')


def validate_hybrid_instance_id(value: str) -> str:
    if not isinstance(value, str) or not HYBRID_ID_RE.fullmatch(value):
        raise ValueError('INVALID_HYBRID_INSTANCE_ID')
    return value


@dataclass
class HybridInstance:
    hybrid_instance_id: str
    owner: str
    application_id: str
    ownership: str
    directory: Path
    user_data_dir: Path | None
    ready_file: Path | None
    debug_port: int
    shell: ProcessIdentity
    shell_hwnd: int
    shell_window_id: str
    webview: ProcessIdentity
    engine_version: str
    state: str = 'active'
    launch_root: ProcessIdentity | None = None

    def public_identity(self):
        return {
            'hybrid_instance_id': self.hybrid_instance_id,
            'ownership': self.ownership,
            'shell_window_id': self.shell_window_id,
        }


class HybridManager:
    def __init__(self, store, *, platform=None, attach_resolver=None, max_managed=2,
                 register_end_hook=True):
        self.store = store
        self.platform = platform or HybridPlatform()
        from .attach import ExistingWebViewResolver
        self.attach_resolver = attach_resolver or ExistingWebViewResolver(self.platform)
        if type(max_managed) is not int or not 1 <= max_managed <= 16:
            raise ValueError('UNVERIFIED_HYBRID_LIMIT')
        self.max_managed = max_managed
        self.instances = {}
        self._pending = {}
        self._failed_launches = {}
        self._lock = threading.RLock()
        if register_end_hook:
            self.store.add_end_hook(self.end_task)

    def _task(self, token):
        return self.store.require(token)

    def _owned(self, token, hybrid_instance_id):
        validate_hybrid_instance_id(hybrid_instance_id)
        _, task = self._task(token)
        with self._lock:
            instance = self.instances.get(hybrid_instance_id)
        if instance is None or instance.owner != task['task_key']:
            raise PermissionError('HYBRID_INSTANCE_NOT_OWNED')
        return instance

    @staticmethod
    def _endpoint_port(endpoint):
        try:
            parsed = urlsplit(endpoint)
            if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1'
                    or parsed.username is not None or parsed.password is not None
                    or parsed.path not in {'', '/'} or parsed.query or parsed.fragment
                    or parsed.port is None):
                raise ValueError()
            return int(parsed.port)
        except (ValueError, TypeError):
            raise RuntimeError('DEBUG_ENDPOINT_NOT_OWNED') from None

    def _reserve_managed(self, owner):
        with self._lock:
            live = sum(1 for item in self.instances.values() if item.ownership == 'managed')
            pending = len(self._pending) + len(self._failed_launches)
            if live + pending >= self.max_managed:
                raise ValueError('HYBRID_INSTANCE_LIMIT_REACHED')
            if (any(item.owner == owner for item in self.instances.values())
                    or owner in self._pending.values()
                    or any(failed_owner == owner for failed_owner, _ in self._failed_launches.values())):
                raise ValueError('HYBRID_INSTANCE_LIMIT_REACHED')
            instance_id = 'bfh_' + uuid.uuid4().hex
            self._pending[instance_id] = owner
            return instance_id

    def prepare_managed(self, token, profile, *, timeout_seconds=10):
        task_dir, task = self._task(token)
        if profile.hybrid.ownership != 'managed' or profile.launcher.reuse_existing:
            raise ValueError('INVALID_HYBRID_PROFILE')
        instance_id = self._reserve_managed(task['task_key'])
        launch_root = None
        try:
            directory = task_dir / 'hybrid' / instance_id
            user_data = directory / 'user-data'
            ready_file = directory / 'ready.json'
            directory.mkdir(parents=True, exist_ok=False)
            user_data.mkdir()
            port = self.platform.reserve_loopback_port()
            env = application_environment(dict(os.environ))
            env.update({
                'WEBVIEW2_USER_DATA_FOLDER': str(user_data),
                'WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS': f'--remote-debugging-port={port}',
                'BF_HYBRID_INSTANCE': instance_id,
                'BF_HYBRID_READY_FILE': str(ready_file),
            })
            launch_root = self.platform.launch(
                executable=profile.launcher.executable, args=profile.launcher.args,
                cwd=profile.launcher.cwd, env=env,
                stdout_path=directory / 'stdout.log', stderr_path=directory / 'stderr.log',
                owner=token, store=self.store,
            )
            if not launch_root.matches(self.platform.process_identity(launch_root.pid)):
                raise RuntimeError('HYBRID_INSTANCE_STALE')
            if launch_root.executable.resolve() != profile.launcher.executable.resolve():
                raise RuntimeError('HYBRID_INSTANCE_STALE')
            self.store.add_owned_pid(token, launch_root.pid)
            ready = self.platform.wait_ready(ready_file, timeout_seconds)
            required = {
                'hybrid_instance_id', 'shell_pid', 'shell_created', 'hwnd', 'title',
                'user_data_dir', 'debug_port', 'webview_pid', 'webview_created', 'engine_version',
            }
            if set(ready) != required or ready['hybrid_instance_id'] != instance_id:
                raise RuntimeError('HYBRID_READY_IDENTITY_MISMATCH')
            shell = self.platform.process_identity(int(ready['shell_pid']))
            if float(ready['shell_created']) != shell.create_time:
                raise RuntimeError('HYBRID_READY_IDENTITY_MISMATCH')
            launch_descendants = self.platform.descendants(launch_root.pid)
            if not (shell.matches(launch_root) or any(shell.matches(child) for child in launch_descendants)):
                raise RuntimeError('HYBRID_READY_IDENTITY_MISMATCH')
            if shell.executable.resolve() != profile.launcher.executable.resolve():
                raise RuntimeError('HYBRID_READY_IDENTITY_MISMATCH')
            self.store.add_owned_pid(token, shell.pid)
            if Path(ready['user_data_dir']).resolve() != user_data.resolve():
                raise RuntimeError('HYBRID_READY_IDENTITY_MISMATCH')
            if int(ready['debug_port']) != port:
                raise RuntimeError('DEBUG_ENDPOINT_NOT_OWNED')
            hwnd = int(ready['hwnd'])
            title = str(ready['title'])
            if self.platform.window_pid(hwnd) != shell.pid or profile.title_contains.casefold() not in title.casefold():
                raise RuntimeError('SHELL_WINDOW_STALE')
            webview = self.platform.process_identity(int(ready['webview_pid']))
            if float(ready['webview_created']) != webview.create_time:
                raise RuntimeError('HYBRID_READY_IDENTITY_MISMATCH')
            descendants = self.platform.descendants(shell.pid)
            if not any(webview.matches(child) for child in descendants):
                raise RuntimeError('DEBUG_ENDPOINT_NOT_OWNED')
            if webview.pid not in self.platform.listener_pids(port):
                raise RuntimeError('DEBUG_ENDPOINT_NOT_OWNED')
            for child in descendants:
                self.store.add_owned_pid(token, child.pid)
            for child in launch_descendants:
                self.store.add_owned_pid(token, child.pid)
            window_id = self.store.bind_window(token, hwnd=hwnd, pid=shell.pid, title=title)
            engine_version = str(ready['engine_version'])
            if not engine_version or len(engine_version) > 128:
                raise RuntimeError('WEBVIEW2_RUNTIME_UNAVAILABLE')
            instance = HybridInstance(
                instance_id, task['task_key'], profile.application_id, 'managed', directory,
                user_data, ready_file, port, shell, hwnd, window_id, webview, engine_version,
                launch_root=launch_root,
            )
            with self._lock:
                self.instances[instance_id] = instance
            return instance
        except BaseException:
            if launch_root is not None:
                try:
                    cleanup = self.platform.terminate_tree(launch_root)
                    if cleanup['remaining']:
                        raise RuntimeError('HYBRID_CLEANUP_INCOMPLETE')
                except Exception as exc:
                    with self._lock:
                        self._failed_launches[instance_id] = (task['task_key'], launch_root)
                    raise RuntimeError('HYBRID_CLEANUP_INCOMPLETE') from exc
            raise
        finally:
            with self._lock:
                self._pending.pop(instance_id, None)

    def attach_existing(self, token, profile, window_record):
        task_dir, task = self._task(token)
        if profile.hybrid.ownership != 'attached' or not profile.launcher.reuse_existing:
            raise ValueError('INVALID_HYBRID_PROFILE')
        shell = self.platform.process_identity(int(window_record['pid']))
        if shell.executable.resolve() != profile.launcher.executable.resolve():
            raise PermissionError('HYBRID_INSTANCE_NOT_OWNED')
        hwnd = int(window_record['hwnd'])
        if self.platform.window_pid(hwnd) != shell.pid:
            raise RuntimeError('SHELL_WINDOW_STALE')
        if profile.title_contains.casefold() not in str(window_record['title']).casefold():
            raise RuntimeError('SHELL_WINDOW_STALE')
        proof = self.attach_resolver.resolve(shell, profile, window_record)
        if proof is None:
            raise RuntimeError('DEBUG_CHANNEL_NOT_AVAILABLE')
        port = self._endpoint_port(proof.get('endpoint'))
        webview = self.platform.process_identity(int(proof['webview_pid']))
        if float(proof['webview_created']) != webview.create_time:
            raise RuntimeError('DEBUG_ENDPOINT_NOT_OWNED')
        descendants = self.platform.descendants(shell.pid)
        if not any(webview.matches(child) for child in descendants):
            raise RuntimeError('DEBUG_ENDPOINT_NOT_OWNED')
        if webview.pid not in self.platform.listener_pids(port):
            raise RuntimeError('DEBUG_ENDPOINT_NOT_OWNED')
        with self._lock:
            if any(item.owner == task['task_key'] for item in self.instances.values()):
                raise ValueError('HYBRID_INSTANCE_LIMIT_REACHED')
            instance_id = 'bfh_' + uuid.uuid4().hex
            directory = task_dir / 'hybrid' / instance_id
            directory.mkdir(parents=True, exist_ok=False)
            window_id = window_record.get('window_id') or self.store.bind_window(
                token, hwnd=hwnd, pid=shell.pid, title=str(window_record['title']))
            instance = HybridInstance(
                instance_id, task['task_key'], profile.application_id, 'attached', directory,
                None, None, port, shell, hwnd, window_id, webview, str(proof['engine_version']),
            )
            self.instances[instance_id] = instance
        return instance

    def prepare_attached(self, token, profile):
        if profile.hybrid.ownership != 'attached' or not profile.launcher.reuse_existing:
            raise ValueError('INVALID_HYBRID_PROFILE')
        finder = getattr(self.attach_resolver, 'find_window', None)
        window = finder(profile) if callable(finder) else None
        if not isinstance(window, dict):
            raise RuntimeError('DEBUG_CHANNEL_NOT_AVAILABLE')
        if not {'hwnd', 'pid', 'title'} <= set(window):
            raise RuntimeError('DEBUG_CHANNEL_NOT_AVAILABLE')
        return self.attach_existing(token, profile, window)

    def connection_spec(self, token, hybrid_instance_id):
        instance = self._owned(token, hybrid_instance_id)
        return {
            'endpoint': f'http://127.0.0.1:{instance.debug_port}',
            'hybrid_instance_id': instance.hybrid_instance_id,
            'engine_version': instance.engine_version,
            'ownership': instance.ownership,
        }

    def release(self, token, hybrid_instance_id):
        instance = self._owned(token, hybrid_instance_id)
        if instance.ownership == 'managed':
            termination_identity = instance.launch_root or instance.shell
            owns_job = getattr(self.platform, 'owns_job', lambda value: False)(termination_identity)
            try:
                current = self.platform.process_identity(termination_identity.pid)
            except Exception:
                current = None
            if current is not None and not termination_identity.matches(current):
                current = None
            if current is None and not owns_job and termination_identity.pid != instance.shell.pid:
                try:
                    shell_current = self.platform.process_identity(instance.shell.pid)
                except Exception:
                    shell_current = None
                if shell_current is not None and instance.shell.matches(shell_current):
                    termination_identity = instance.shell
                    current = shell_current
            owns_job = getattr(self.platform, 'owns_job', lambda value: False)(termination_identity)
            cleanup = (self.platform.terminate_tree(termination_identity) if current is not None or owns_job
                       else {'terminated': [], 'remaining': []})
            if cleanup['remaining']:
                raise RuntimeError('HYBRID_CLEANUP_INCOMPLETE')
            terminated = current is not None
        else:
            cleanup = {'terminated': [], 'remaining': []}
            terminated = False
        with self._lock:
            self.instances.pop(instance.hybrid_instance_id, None)
        instance.state = 'closed'
        return {
            'hybrid_instance_id': instance.hybrid_instance_id,
            'ownership': instance.ownership,
            'process_terminated': terminated,
            'remaining': list(cleanup['remaining']),
        }

    def end_task(self, token):
        _, task = self.store.require(token)
        with self._lock:
            owned = [item.hybrid_instance_id for item in self.instances.values()
                     if item.owner == task['task_key']]
        for hybrid_instance_id in owned:
            self.release(token, hybrid_instance_id)
        with self._lock:
            failed = [(key, identity) for key, (owner, identity) in self._failed_launches.items()
                      if owner == task['task_key']]
        for key, identity in failed:
            if self.platform.terminate_tree(identity)['remaining']:
                raise RuntimeError('HYBRID_CLEANUP_INCOMPLETE')
            with self._lock:
                del self._failed_launches[key]
