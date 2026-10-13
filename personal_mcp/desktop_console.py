"""经现有工作流授权的侧边操控台；画面和短期凭据只进入组件元数据。"""
import hashlib
import json
import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

from coding_tools_mcp.errors import JsonRpcError
from .file_transfer import FileTransferManager
from .workflows import WorkflowError

RESOURCE_URI = 'ui://unified/desktop-console/v1.html'
RESOURCE_MIME = 'text/html;profile=mcp-app'
UI_TOOLS = frozenset({'DesktopView', 'DesktopAction', 'DesktopStatus', 'DesktopFiles'})


def result(public, private=None):
    output = {'content': [{'type': 'text', 'text': json.dumps(public, ensure_ascii=False)}],
              'structuredContent': public, 'isError': public.get('ok') is False}
    if private is not None:
        output['_meta'] = private
    return output


class DesktopError(ValueError):
    pass


@dataclass
class Session:
    owner: str
    workflow: str = field(repr=False)
    expires: float
    armed: bool = False
    arm_expires: float = 0
    control_epoch: int = 0
    closed: bool = False
    frames: OrderedDict = field(default_factory=OrderedDict, repr=False)
    lock: object = field(default_factory=threading.RLock, repr=False)
    state_lock: object = field(default_factory=threading.RLock, repr=False)


class DesktopConsoleManager:
    def __init__(self, host, *, clock=None):
        self.host = host
        self.clock = clock or time.monotonic
        self.lock = threading.RLock()
        self.sessions = {}
        self.native = None
        self.files = FileTransferManager(host.config.data_root / 'desktop-transfers')
        self.ttl = 900
        self.frame_ttl = 8

    def _native(self):
        if self.native is None:
            from .desktop_native import NativeDesktop
            self.native = NativeDesktop(self.host)
        return self.native

    def open(self, workflow):
        with self.host.registry.use(self.host.principal, workflow, write=True) as resource:
            with self.lock:
                self._prune()
                previous = [key for key, value in self.sessions.items() if value.owner == resource.key]
                for key in previous:
                    self._stop(self.sessions[key], close=True)
                    del self.sessions[key]
                if len(self.sessions) >= 8:
                    raise DesktopError('UI_SESSION_CAPACITY')
                secret = secrets.token_urlsafe(32)
                self.sessions[self._hash(secret)] = Session(resource.key, workflow, self.clock() + self.ttl)
        return result({'ok': True, 'mode': 'observe', 'device_label': self.host.config.device_label,
                       'message': '操控台已打开，当前仅查看。'}, {'desktop': {
                           'ui_session': secret, 'expires_at': time.time() + self.ttl,
                           'device_label': self.host.config.device_label,
                           'max_frame_age_ms': self.frame_ttl * 1000, 'refresh_interval_ms': 1200}})

    @staticmethod
    def _hash(value):
        return hashlib.sha256(value.encode()).hexdigest()

    def _prune(self):
        for key, session in list(self.sessions.items()):
            if session.expires <= self.clock() or session.closed:
                self._stop(session, close=True)
                del self.sessions[key]

    def _session(self, context):
        secret = getattr(context, 'ui_session', None)
        if not isinstance(secret, str) or not 20 <= len(secret) <= 128:
            raise DesktopError('UI_SESSION_REQUIRED')
        with self.lock:
            self._prune()
            session = self.sessions.get(self._hash(secret))
            if session is None:
                raise DesktopError('UI_SESSION_EXPIRED')
            with session.state_lock:
                if session.armed and session.arm_expires <= self.clock():
                    self._stop(session)
            return session

    @staticmethod
    def _stop(session, *, close=False):
        with session.state_lock:
            session.armed = False
            session.arm_expires = 0
            session.control_epoch += 1
            session.closed = session.closed or close

    def _authorize(self, session, epoch, *, arm=False):
        with session.state_lock:
            if session.closed or session.expires <= self.clock():
                raise DesktopError('UI_SESSION_EXPIRED')
            if session.control_epoch != epoch:
                raise DesktopError('DESKTOP_CONTROL_REVOKED')
            if not arm and (not session.armed or session.arm_expires <= self.clock()):
                self._stop(session)
                raise DesktopError('DESKTOP_NOT_ARMED')

    def _frame(self, session, args):
        frame = session.frames.get(args.get('frame_id'))
        if frame is None or self.clock() - frame['_captured'] > self.frame_ttl:
            raise DesktopError('DESKTOP_FRAME_STALE')
        if not frame.get('geometry_verified'):
            raise DesktopError('COORDINATE_MAPPING_UNAVAILABLE')
        if args['action'] in {'click', 'drag', 'scroll'}:
            points = [('x', 'y')]
            if args['action'] == 'drag':
                points.append(('to_x', 'to_y'))
            for x, y in points:
                if (type(args.get(x)) not in (int, float) or type(args.get(y)) not in (int, float)
                        or not 0 <= args[x] < frame['width'] or not 0 <= args[y] < frame['height']):
                    raise DesktopError('DESKTOP_COORDINATE_INVALID')
        return frame

    def _admit(self, resource):
        try:
            self.host.bf.drain(resource.bf_token, timeout=0)
        except TimeoutError:
            raise DesktopError('WORKFLOW_WRITE_PENDING') from None

    def handle(self, name, arguments, context):
        session = None
        try:
            session = self._session(context)
            with session.state_lock:
                epoch = session.control_epoch
            action = arguments.get('action')
            observation = (name in {'DesktopView', 'DesktopStatus'} or
                           name == 'DesktopFiles' and action in {'download_chunk', 'status'})
            stopping = name == 'DesktopAction' and action == 'disarm'
            with self.host.registry.use(self.host.principal, session.workflow,
                    write=not observation and not stopping, control=observation or stopping,
                    touch=name != 'DesktopStatus') as resource:
                if session.closed:
                    raise DesktopError('UI_SESSION_EXPIRED')
                if name == 'DesktopStatus':
                    return result(self.host.operations.status(resource.key, arguments['request_id']))
                if stopping:
                    # 停止授权不等待在途截图/动作锁；已发出的有界输入由原动作收敛。
                    self._stop(session)
                    return self.host.operations.run(resource.key, arguments['request_id'], 'DesktopAction',
                        {'action': 'disarm'}, lambda: result({'ok': True, 'armed': False}))
                if not session.lock.acquire(blocking=False):
                    raise DesktopError('DESKTOP_BUSY')
                try:
                    if name == 'DesktopView':
                        return self._view(session, resource, arguments)
                    if name == 'DesktopAction':
                        return self._action(session, resource, arguments, epoch)
                    if name == 'DesktopFiles':
                        return self._files(resource, arguments)
                    raise DesktopError('INVALID_ARGUMENT')
                finally:
                    session.lock.release()
        except DesktopError as exc:
            return result({'ok': False, 'error': {'code': str(exc)}})
        except WorkflowError:
            if session is not None:
                self._stop(session, close=True)
            raise
        except (TimeoutError, RuntimeError, LookupError, PermissionError, OSError) as exc:
            if session is not None:
                self._stop(session)
            known = {'DESKTOP_LAYOUT_CHANGED', 'DESKTOP_TARGET_CHANGED', 'DESKTOP_FOCUS_CHANGED',
                     'COORDINATE_MAPPING_UNAVAILABLE', 'DESKTOP_NOT_SUPPORTED', 'DESKTOP_IMAGE_TOO_LARGE',
                     'DESKTOP_SECURE_INPUT_DENIED', 'DESKTOP_BUSY', 'OPERATION_RUNNING', 'OUTCOME_UNKNOWN'}
            if type(exc).__module__ == 'personal_mcp.desktop_native' and hasattr(exc, 'code'):
                code = exc.code
            else:
                code = str(exc) if str(exc) in known else 'DESKTOP_OBSERVATION_REQUIRED'
            return result({'ok': False, 'error': {'code': code, 'outcome': 'unknown',
                           'next_action': '检查当前画面；查询原 request_id，不自动重做操作。'}})

    def _view(self, session, resource, args):
        native = self._native()
        if args['action'] == 'inventory':
            return result({'ok': True, 'armed': session.armed, **native.inventory(resource),
                           'device_label': self.host.config.device_label})
        if args['action'] == 'clipboard':
            return result({'ok': True}, {'clipboard_text': native.clipboard_read(resource)})
        target = args.get('target')
        if not isinstance(target, dict):
            raise DesktopError('DESKTOP_TARGET_REQUIRED')
        capture_started = self.clock()
        capture_wall_time = time.time()
        frame = native.capture(resource, target)
        data_url = frame.pop('data_url')
        if not isinstance(data_url, str) or len(data_url) > 1500000:
            raise DesktopError('DESKTOP_IMAGE_TOO_LARGE')
        # 采集、等待与编码耗时计入帧龄，不能在返回时把旧画面重新标为新鲜。
        if self.clock() - capture_started > self.frame_ttl:
            raise DesktopError('DESKTOP_FRAME_STALE')
        frame.update(frame_id=secrets.token_hex(16), captured_at=capture_wall_time, _captured=capture_started)
        session.frames[frame['frame_id']] = frame
        while len(session.frames) > 2:
            session.frames.popitem(last=False)
        visible = {key: value for key, value in frame.items() if key not in {'native_state', '_captured'}}
        return result({'ok': True, 'armed': session.armed, 'frame_id': frame['frame_id'],
                       'captured_at': frame['captured_at'], 'geometry_verified': frame['geometry_verified']},
                      {'desktop_frame': {**visible, 'data_url': data_url}})

    def _action(self, session, resource, args, epoch):
        action = args['action']
        operation = {key: value for key, value in args.items() if key != 'request_id'}

        def admit():
            self._authorize(session, epoch, arm=action == 'arm')
            if action != 'disarm':
                self._admit(resource)
            if action not in {'arm', 'disarm'}:
                if action != 'clipboard_write':
                    self._frame(session, args)

        def execute():
            if action in {'arm', 'disarm'}:
                with session.state_lock:
                    self._authorize(session, epoch, arm=True)
                    session.armed = True
                    session.arm_expires = self.clock() + 30
                    return result({'ok': True, 'armed': True, 'control_idle_seconds': 30})
            def authorize():
                self._authorize(session, epoch)
            authorize()
            guard = getattr(self.host.bf_server, '_bf_desktop_guard', None)
            if guard is not None:
                guard.invalidate()
            if action == 'clipboard_write':
                value = self._native().clipboard_write(resource, args.get('text', ''), authorize=authorize)
            else:
                value = self._native().act(resource, self._frame(session, args), {**args, '_authorize': authorize})
            if value.get('ok') is False:
                self._stop(session)
            else:
                with session.state_lock:
                    if session.armed and session.control_epoch == epoch:
                        session.arm_expires = self.clock() + 30
            return result(value)

        return self.host.operations.run(resource.key, args['request_id'], 'DesktopAction',
                                        operation, execute, before_dispatch=admit)

    def _files(self, resource, args):
        def execute():
            payload = self.files.handle(resource.key, resource.coding, args)
            if 'data_base64' in payload:
                public = {key: value for key, value in payload.items() if key != 'data_base64'}
                return result(public, {'transfer': payload})
            return result(payload, {'transfer': payload} if payload.get('ok') else None)
        if args['action'] in {'download_chunk', 'status'}:
            return execute()
        if not args.get('request_id'):
            raise DesktopError('REQUEST_ID_REQUIRED')
        return self.host.operations.run(resource.key, args['request_id'], 'DesktopFiles',
            {key: value for key, value in args.items() if key != 'request_id'}, execute,
            before_dispatch=lambda: self._admit(resource))

    def revoke_owner(self, owner):
        with self.lock:
            for session in self.sessions.values():
                if session.owner == owner:
                    self._stop(session, close=True)
            self._prune()

    def close_owner(self, owner):
        self.revoke_owner(owner)
        self.files.close_owner(owner)

    def close(self):
        self.revoke_all()
        self.files.close()

    def revoke_all(self):
        with self.lock:
            for session in self.sessions.values():
                self._stop(session, close=True)
            self.sessions.clear()


def resources_list():
    return {'resources': [{'uri': RESOURCE_URI, 'name': 'desktop-console',
                          'title': '远程桌面', 'mimeType': RESOURCE_MIME}]}


def resource_read(uri):
    if uri != RESOURCE_URI:
        raise JsonRpcError(-32602, 'Unknown UI resource')
    directory = Path(__file__).parent / 'ui'
    html = (directory / 'desktop.html').read_text(encoding='utf-8')
    css = (directory / 'desktop.css').read_text(encoding='utf-8')
    js = (directory / 'desktop.js').read_text(encoding='utf-8').replace('</script', '<\\/script')
    html = html.replace('<link rel="stylesheet" href="desktop.css">', '<style>' + css + '</style>')
    html = html.replace('<script type="module" src="desktop.js"></script>', '<script type="module">' + js + '</script>')
    html = html.replace('<script src="desktop.js" defer></script>', '<script>' + js + '</script>')
    return {'contents': [{'uri': RESOURCE_URI, 'mimeType': RESOURCE_MIME, 'text': html,
        '_meta': {'ui': {'csp': {'connectDomains': [], 'resourceDomains': []}},
                  'openai/widgetDescription': '显示并操作已授权设备的桌面。'}}]}
