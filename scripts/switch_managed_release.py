"""Explicit, one-shot switch of the owner's existing managed plugin task.

Run --arm only after validating the candidate. It creates a demand-only task,
waits for the caller's workflow to end, and restarts ONLY the registered plugin
service. No arbitrary PID termination, host reboot or alternate remote channel.
"""

import argparse
import json
import re
import sqlite3
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener

import psutil

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / 'app' if (ROOT / 'app/personal_mcp').is_dir() else ROOT
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from personal_mcp.autostart import (  # noqa: E402
    TASK_DESCRIPTION, AutostartManager, _com_operation, _NoControlRedirect,
    _same_path, validate_package,
)
from personal_mcp.protection import InstanceLock, prepare_private_directory  # noqa: E402

SELF_DESCRIPTION = 'Unified Personal MCP explicitly requested one-shot release switch.'
SAFE_FAILURE_CODES = frozenset({
    'MANAGED_TASK_REQUIRED', 'OLD_TASK_TARGET_CHANGED', 'SEPARATE_CANDIDATE_REQUIRED',
    'OLD_SERVICE_NOT_VERIFIED', 'OLD_PROCESS_IDENTITY_CHANGED', 'OTHER_WORKFLOW_PRESENT',
    'WORKFLOWS_NOT_IDLE', 'WORKFLOW_APPEARED_BEFORE_STOP', 'CONCURRENT_TASK_CHANGE',
    'TASK_OWNERSHIP_CHANGED', 'TASK_INSTANCE_POLICY_CONFLICT', 'TASK_INSTANCE_CONFLICT',
    'SERVICE_IDENTITY_UNKNOWN', 'UNEXPECTED_SERVICE_PROCESS', 'TASK_STOP_NOT_CONFIRMED',
    'NEW_SERVICE_NOT_READY', 'ROLLBACK_TARGET_MISMATCH', 'AUTOSTART_CONFIGURATION_CONFLICT',
    'AUTOSTART_TASK_NOT_OWNED', 'ISOLATED_START_FAILED', 'ISOLATED_VERSION_MISMATCH',
    'PACKAGE_HASH_MISMATCH', 'PACKAGE_FILE_SET_MISMATCH', 'PACKAGE_VERSION_MISMATCH',
    'PACKAGE_INCOMPLETE', 'PACKAGE_INVALID', 'PACKAGE_MANIFEST_PATH_INVALID',
})


def failure_code(exc):
    # 只记录审核过的错误码，不把任意异常文字或凭据写入升级回执。
    message = str(exc)
    return message if message in SAFE_FAILURE_CODES else 'RELEASE_SWITCH_FAILED'


def switch_release(backend):
    backend.preflight()
    backend.wait_idle()
    previous = backend.task()
    installed = None
    try:
        installed = backend.install()  # Keep the verified XML receipt before another status read.
        if backend.task()['xml'] != installed['xml']:
            raise RuntimeError('CONCURRENT_TASK_CHANGE')
        backend.wait_idle()
        with backend.quiescent():
            backend.stop(installed)
            backend.wait_stopped()
        backend.start(installed)
        return {'status': 'LOCAL_READY', 'running': backend.ready()}
    except Exception as exc:
        if installed is None:
            # Installer failures either restore their own receipt or report an
            # ambiguous update without guessing that it is safe to overwrite.
            raise
        running = backend.rollback(previous, installed)
        return {'status': 'ROLLED_BACK', 'cause_type': type(exc).__name__,
                'cause_code': failure_code(exc), 'running': running}


class ManagedRelease:
    def __init__(self, args):
        self.args = args
        self.manager = AutostartManager(args.config)
        self.waiting_process = None
        self.output = args.output.resolve()
        prepare_private_directory(self.output)

    def record(self, stage, **fields):
        value = {'stage': stage, 'time': time.time(), **fields}
        with (self.output / 'events.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(value, ensure_ascii=False) + '\n')

    def task(self):
        task = self.manager.scheduler.status(self.manager.task_name)
        self.manager._owned(task)
        if not task.get('exists') or not task.get('enabled') or not task.get('managed'):
            raise RuntimeError('MANAGED_TASK_REQUIRED')
        return task

    def rpc(self, name, arguments):
        key_path = self.manager.config.data_root / 'backend.key'
        if key_path.stat().st_size > 1024:
            raise RuntimeError('LOCAL_CREDENTIAL_INVALID')
        key = key_path.read_text(encoding='utf-8').strip()
        if len(key) < 32 or not key.isascii() or any(c.isspace() for c in key):
            raise RuntimeError('LOCAL_CREDENTIAL_INVALID')
        body = json.dumps({'jsonrpc': '2.0', 'id': 'release-observe', 'method': 'tools/call',
                           'params': {'name': name, 'arguments': arguments}}).encode()
        request = Request(f'http://127.0.0.1:{self.manager.config.port}/mcp', body,
            {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key})
        opener = build_opener(ProxyHandler({}), _NoControlRedirect())
        with opener.open(request, timeout=5) as response:
            data = response.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024:
            raise RuntimeError('LOCAL_RESPONSE_TOO_LARGE')
        result = json.loads(data)['result']
        if result.get('isError'):
            raise RuntimeError('LOCAL_OBSERVATION_FAILED')
        return result['structuredContent']

    def preflight(self):
        self.record('preflight')
        task = self.task()
        if not _same_path(task.get('bundle', ''), self.args.old_bundle):
            raise RuntimeError('OLD_TASK_TARGET_CHANGED')
        if _same_path(self.args.old_bundle, self.args.candidate):
            raise RuntimeError('SEPARATE_CANDIDATE_REQUIRED')
        validate_package(self.args.old_bundle, self.args.old_version)
        validate_package(self.args.candidate, self.args.version)
        info = self.rpc('server_info', {})
        state = self.manager._service_status()
        if (not state.get('running') or not state.get('status_fresh')
                or info.get('version') != self.args.old_version):
            raise RuntimeError('OLD_SERVICE_NOT_VERIFIED')
        process = psutil.Process(state['pid'])
        if not _same_path(process.exe(), self.args.old_bundle / 'resources/python/pythonw.exe'):
            raise RuntimeError('OLD_PROCESS_IDENTITY_CHANGED')
        self.old_count = info['tool_count']
        self.record('preflight_passed', old_version=info['version'], old_tools=self.old_count)

    def wait_idle(self):
        deadline = time.monotonic() + 120
        while True:
            rows = self.rpc('UnifiedTask', {'action': 'list'})['workflows']
            if any(row['workflow_ref'] != self.args.allow_workflow_ref for row in rows):
                raise RuntimeError('OTHER_WORKFLOW_PRESENT')
            info = self.rpc('server_info', {})
            usage = info['resource_usage']
            zero = ('inflight', 'commands', 'coding_job_active_processes',
                    'browser_sessions', 'webview2_instances', 'search_sessions', 'resources')
            if not rows and not usage.get('stale', True) and all(usage.get(k) == 0 for k in zero):
                return
            if time.monotonic() >= deadline:
                raise RuntimeError('WORKFLOWS_NOT_IDLE')
            time.sleep(2)

    def install(self):
        self.record('installing_candidate')
        receipt = self.manager.enable(self.args.candidate, expected_version=self.args.version)
        return receipt['registered_task']

    @contextmanager
    def quiescent(self):
        # Prevent a new workflow reservation racing the last idle observation.
        path = self.manager.config.data_root / 'workflows.sqlite3'
        db = sqlite3.connect(path.resolve().as_uri() + '?mode=rw', uri=True, timeout=5)
        try:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT COUNT(*) FROM workflows WHERE state NOT IN ('ENDED','EXPIRED')").fetchone()[0]:
                raise RuntimeError('WORKFLOW_APPEARED_BEFORE_STOP')
            yield
        finally:
            db.rollback()
            db.close()

    @_com_operation
    def _control(self, expected, action):
        with self.manager.scheduler._folder() as (_, folder):
            task = folder.GetTask(self.manager.task_name)
            if str(task.Xml) != expected['xml']:
                raise RuntimeError('CONCURRENT_TASK_CHANGE')
            definition = task.Definition
            if (definition.RegistrationInfo.Description != TASK_DESCRIPTION
                    or not self.manager.scheduler._is_current_user(definition.Principal.UserId)):
                raise RuntimeError('TASK_OWNERSHIP_CHANGED')
            # The managed installer uses IGNORE_NEW, so this task cannot run
            # multiple instances. Read fresh state in this COM operation; no
            # instance collection or COM proxy escapes the operation boundary.
            if int(definition.Settings.MultipleInstances) != 2:
                raise RuntimeError('TASK_INSTANCE_POLICY_CONFLICT')
            state = int(task.State)
            if state not in {3, 4} or (action == 'start' and state != 3):
                raise RuntimeError('TASK_INSTANCE_CONFLICT')
            if action == 'stop':
                task.Stop(0)
            elif action == 'start':
                task.Run('')
            else:
                raise ValueError('INVALID_TASK_ACTION')

    def stop(self, expected):
        state = self.manager._service_status()
        if state.get('running_unknown'):
            raise RuntimeError('SERVICE_IDENTITY_UNKNOWN')
        self.waiting_process = None
        if state.get('running'):
            process = psutil.Process(state['pid'])
            if not any(_same_path(process.exe(), bundle / 'resources/python/pythonw.exe')
                       for bundle in (self.args.old_bundle, self.args.candidate)):
                raise RuntimeError('UNEXPECTED_SERVICE_PROCESS')
            self.waiting_process = process
        self.record('stopping_owned_task')
        self._control(expected, 'stop')

    def wait_stopped(self):
        if self.waiting_process is not None:
            self.waiting_process.wait(timeout=20)
        deadline = time.monotonic() + 10
        while self.manager.scheduler.status(self.manager.task_name).get('state') == 4:
            if time.monotonic() >= deadline:
                raise RuntimeError('TASK_STOP_NOT_CONFIRMED')
            time.sleep(0.2)

    def start(self, expected):
        self.record('starting_owned_task')
        self._control(expected, 'start')

    def ready(self, *, old=False):
        version = self.args.old_version if old else self.args.version
        count = self.old_count if old else self.args.tools
        bundle = self.args.old_bundle if old else self.args.candidate
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                state = self.manager._service_status()
                info = self.rpc('server_info', {})
                if (state.get('status_fresh') and state.get('running')
                        and info.get('version') == version and info.get('tool_count') == count
                        and info.get('health', {}).get('status') == 'healthy'
                        and info['health'].get('tunnel', {}).get('status') == 'healthy'
                        and _same_path(psutil.Process(state['pid']).exe(),
                                       bundle / 'resources/python/pythonw.exe')):
                    return {'version': version, 'tool_count': count, 'pid': state['pid'],
                            'catalog_revision': info.get('catalog_revision'),
                            'diagnostics': info.get('diagnostics'), 'platform_call_verified': False}
            except (OSError, ValueError, KeyError, RuntimeError, psutil.Error):
                pass  # Observation only; never repeat a write or restart here.
            time.sleep(2)
        raise RuntimeError('NEW_SERVICE_NOT_READY')

    def rollback(self, previous, installed):
        self.record('rollback_requested')
        if self.task()['xml'] != installed['xml']:
            raise RuntimeError('CONCURRENT_TASK_CHANGE')
        state = self.manager._service_status()
        running_old = state.get('running') and state.get('running_version') == self.args.old_version
        if running_old:
            # Failure before stopping: restore the pending target, keep the old process and its work.
            self.manager.scheduler.restore(self.manager.task_name, previous, expected_xml=installed['xml'])
            return self.ready(old=True)
        with self.quiescent():
            self.stop(installed)
            self.wait_stopped()
        self.manager.scheduler.restore(self.manager.task_name, previous, expected_xml=installed['xml'])
        restored = self.task()
        if not _same_path(restored.get('bundle', ''), self.args.old_bundle):
            raise RuntimeError('ROLLBACK_TARGET_MISMATCH')
        self.start(restored)
        return self.ready(old=True)


@_com_operation
def arm(args):
    backend = ManagedRelease(args)
    backend.preflight()
    name = 'UnifiedPersonalMCP-Release-' + uuid.uuid4().hex[:12]
    with backend.manager.scheduler._folder() as (service, folder):
        definition = service.NewTask(0)
        definition.RegistrationInfo.Description = SELF_DESCRIPTION
        sid = backend.manager.scheduler._sid()
        definition.Principal.UserId = sid
        definition.Principal.LogonType = 3
        definition.Principal.RunLevel = 0
        action = definition.Actions.Create(0)
        interpreter = Path(sys.executable).with_name('pythonw.exe')
        if not interpreter.is_file():
            raise RuntimeError('WINDOWLESS_INTERPRETER_MISSING')
        action.Path = str(interpreter)
        argv = ['-B', '-X', 'utf8', str(Path(__file__).resolve()), '--run',
                '--config', str(args.config), '--candidate', str(args.candidate),
                '--old-bundle', str(args.old_bundle), '--version', args.version,
                '--old-version', args.old_version, '--tools', str(args.tools),
                '--allow-workflow-ref', args.allow_workflow_ref,
                '--output', str(args.output), '--self-task', name]
        action.Arguments = subprocess.list2cmdline(argv)
        action.WorkingDirectory = str(ROOT)
        definition.Settings.Enabled = True
        definition.Settings.AllowDemandStart = True
        definition.Settings.MultipleInstances = 2
        definition.Settings.ExecutionTimeLimit = 'PT10M'
        definition.Settings.DisallowStartIfOnBatteries = False
        definition.Settings.StopIfGoingOnBatteries = False
        # Create only: never replace an existing task, and no login/recurring trigger.
        registered = folder.RegisterTaskDefinition(name, definition, 2, sid, None, 3)
        try:
            registered.Run('')
        except Exception:
            folder.DeleteTask(name, 0)
            raise
    return {'status': 'ARMED', 'task': name, 'output': str(args.output),
            'waiting_for_workflow_end': args.allow_workflow_ref}


@_com_operation
def remove_own_task(args):
    if not re.fullmatch(r'UnifiedPersonalMCP-Release-[0-9a-f]{12}', args.self_task or ''):
        raise RuntimeError('INVALID_RELEASE_TASK')
    manager = AutostartManager(args.config)
    with manager.scheduler._folder() as (_, folder):
        task = folder.GetTask(args.self_task)
        definition = task.Definition
        if (definition.RegistrationInfo.Description != SELF_DESCRIPTION
                or not manager.scheduler._is_current_user(definition.Principal.UserId)
                or definition.Actions.Count != 1
                or not _same_path(definition.Actions.Item(1).Path, sys.executable)
                or str(Path(__file__).resolve()) not in definition.Actions.Item(1).Arguments):
            raise RuntimeError('RELEASE_TASK_OWNERSHIP_CHANGED')
        folder.DeleteTask(args.self_task, 0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--arm', action='store_true')
    mode.add_argument('--run', action='store_true')
    for field in ('config', 'candidate', 'old-bundle', 'output'):
        parser.add_argument('--' + field, type=Path, required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--old-version', required=True)
    parser.add_argument('--tools', type=int, required=True)
    parser.add_argument('--allow-workflow-ref')
    parser.add_argument('--self-task')
    args = parser.parse_args()
    if ((args.arm and args.allow_workflow_ref is None)
            or (args.allow_workflow_ref is not None
                and not re.fullmatch(r'wfr_[0-9a-f]{64}', args.allow_workflow_ref))):
        parser.error('--arm requires a valid --allow-workflow-ref; omit it for strict idle --run')
    for field in ('config', 'candidate', 'old_bundle', 'output'):
        setattr(args, field, getattr(args, field).resolve())
    if args.arm:
        print(json.dumps(arm(args)))
        return 0
    backend = None
    result = {'status': 'FAILED_BEFORE_SWITCH'}
    guard = None
    try:
        backend = ManagedRelease(args)
        guard = InstanceLock('release-switch:' + str(args.config))
        result = switch_release(backend)
    except Exception as exc:
        result = {'status': 'NOT_COMPLETED', 'error_type': type(exc).__name__,
                  'error_code': failure_code(exc)}
        if backend is not None:
            backend.record('not_completed', error_type=type(exc).__name__, error_code=failure_code(exc))
    finally:
        if guard:
            guard.close()
        result['temporary_task_removed'] = None
        if args.self_task:
            try:
                remove_own_task(args)
                result['temporary_task_removed'] = True
            except Exception:
                result['temporary_task_removed'] = False
        output = backend.output if backend is not None else args.output
        prepare_private_directory(output)
        (output / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    return 0 if result['status'] == 'LOCAL_READY' else 1


if __name__ == '__main__':
    raise SystemExit(main())
