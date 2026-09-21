from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlencode, urlsplit, urlunsplit

HYBRID_ID_RE = re.compile(r'^bfh_[0-9a-f]{32}$')
DEBUG_RE = re.compile(r'^--remote-debugging-port=([1-9][0-9]{0,4})$')
READY_FIELDS = {
    'hybrid_instance_id', 'shell_pid', 'shell_created', 'hwnd', 'title',
    'user_data_dir', 'debug_port', 'webview_pid', 'webview_created', 'engine_version',
}


class FixtureConfig:
    __slots__ = ('instance_id', 'user_data_dir', 'ready_file', 'debug_port')

    def __init__(self, instance_id, user_data_dir, ready_file, debug_port):
        self.instance_id = instance_id
        self.user_data_dir = user_data_dir
        self.ready_file = ready_file
        self.debug_port = debug_port


def fixture_config_from_env():
    try:
        instance_id = os.environ['BF_HYBRID_INSTANCE']
        user_data = Path(os.environ['WEBVIEW2_USER_DATA_FOLDER']).resolve()
        ready_file = Path(os.environ['BF_HYBRID_READY_FILE']).resolve()
        debug_arg = os.environ['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS']
    except (KeyError, OSError):
        raise ValueError('MANAGED_ENV_REQUIRED') from None
    match = DEBUG_RE.fullmatch(debug_arg)
    if (not HYBRID_ID_RE.fullmatch(instance_id) or match is None
            or not user_data.is_dir() or ready_file.parent != user_data.parent):
        raise ValueError('MANAGED_ENV_REQUIRED')
    port = int(match.group(1))
    if not 1 <= port <= 65535:
        raise ValueError('MANAGED_ENV_REQUIRED')
    return FixtureConfig(instance_id, user_data, ready_file, port)


def write_ready_record(path, value):
    path = Path(path).resolve()
    if not isinstance(value, dict) or set(value) != READY_FIELDS:
        raise ValueError('INVALID_READY_RECORD')
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


class ExportApi:
    def __init__(self, instance_id, output_root):
        self._instance_id = instance_id
        self._output_root = Path(output_root).resolve()
        self._output_root.mkdir(parents=True, exist_ok=True)
        self._window = None
        self._selected_path = None

    def choose_export_path(self):
        if self._window is None:
            raise RuntimeError('WINDOW_NOT_READY')
        import webview
        result = self._window.create_file_dialog(
            webview.FileDialog.SAVE, directory=str(self._output_root),
            save_filename=f'u4a-{self._instance_id[-6:]}.txt',
            file_types=('Text (*.txt)',),
        )
        if not result:
            self._selected_path = None
            return {'cancelled': True}
        selected = Path(result[0]).resolve()
        if not selected.is_relative_to(self._output_root):
            self._selected_path = None
            raise ValueError('EXPORT_PATH_OUTSIDE_TASK')
        self._selected_path = selected
        return {'cancelled': False, 'filename': selected.name, 'path': str(selected)}

    def complete_export(self, path):
        requested = Path(path).resolve()
        if self._selected_path is None or requested != self._selected_path:
            raise ValueError('EXPORT_PATH_NOT_SELECTED')
        if not requested.is_relative_to(self._output_root):
            raise ValueError('EXPORT_PATH_OUTSIDE_TASK')
        requested.write_text(f'U4A export {self._instance_id}\n', encoding='utf-8')
        return {'ok': True, 'filename': requested.name}


def _window_for_pid(pid, title_contains):
    user32 = ctypes.windll.user32
    matches = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def collect(hwnd, _):
        process_id = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        if process_id.value != pid or not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, len(buffer))
        title = buffer.value
        if title_contains in title:
            matches.append((int(hwnd), title))
        return True

    user32.EnumWindows(callback_type(collect), 0)
    if len(matches) != 1:
        raise RuntimeError('FIXTURE_WINDOW_NOT_UNIQUE')
    return matches[0]


def _webview_listener(shell_pid, debug_port):
    import psutil
    shell = psutil.Process(shell_pid)
    descendants = {child.pid: child for child in shell.children(recursive=True)}
    for connection in psutil.net_connections(kind='tcp'):
        if (connection.pid in descendants and connection.status == psutil.CONN_LISTEN
                and connection.laddr and int(connection.laddr.port) == debug_port
                and str(connection.laddr.ip) in {'127.0.0.1', '::1'}):
            process = descendants[connection.pid]
            executable = Path(process.exe()).resolve()
            version = executable.parent.name
            if not re.fullmatch(r'[0-9]+(?:\.[0-9]+){2,3}', version):
                version = 'unknown'
            return process.pid, process.create_time(), version
    raise RuntimeError('WEBVIEW_DEBUG_LISTENER_NOT_READY')


def _wait_identity(config, title, timeout=15):
    import psutil
    shell = psutil.Process(os.getpid())
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() <= deadline:
        try:
            hwnd, actual_title = _window_for_pid(shell.pid, title)
            webview_pid, webview_created, version = _webview_listener(shell.pid, config.debug_port)
            return {
                'hybrid_instance_id': config.instance_id,
                'shell_pid': shell.pid, 'shell_created': shell.create_time(),
                'hwnd': hwnd, 'title': actual_title,
                'user_data_dir': str(config.user_data_dir),
                'debug_port': config.debug_port,
                'webview_pid': webview_pid, 'webview_created': webview_created,
                'engine_version': version,
            }
        except Exception as exc:
            last_error = exc
            time.sleep(0.1)
    raise RuntimeError('FIXTURE_READY_TIMEOUT') from last_error


def _instance_url(base_url, instance_id):
    parsed = urlsplit(base_url)
    if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1':
        raise ValueError('FIXTURE_URL_NOT_LOOPBACK')
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or '/', urlencode({'instance': instance_id}), ''))


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', required=True)
    args = parser.parse_args(argv)
    config = fixture_config_from_env()
    import webview
    webview.settings['REMOTE_DEBUGGING_PORT'] = config.debug_port
    webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER'] = False
    webview.settings['ALLOW_DOWNLOADS'] = True
    title = 'U4A WebView2 Fixture ' + config.instance_id[-6:]
    api = ExportApi(config.instance_id, config.ready_file.parent / 'exports')
    window = webview.create_window(
        title, _instance_url(args.url, config.instance_id), js_api=api,
        width=980, height=720, min_size=(760, 520),
    )
    api._window = window

    def ready():
        write_ready_record(config.ready_file, _wait_identity(config, title))

    webview.start(
        ready, gui='edgechromium', debug=False, private_mode=False,
        storage_path=str(config.user_data_dir),
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())


