"""明确启动真实夹具，验收侧边操控台的本地认证 HTTP 与原生桌面闭环。"""
import argparse
import base64
import hashlib
import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--bundle', type=Path)
    args = parser.parse_args()
    root = args.bundle.resolve(strict=True) if args.bundle else Path(__file__).resolve().parents[1]
    if args.bundle:
        if Path(sys.executable).resolve() != root / 'resources/python/python.exe':
            raise RuntimeError('BUNDLED_INTERPRETER_REQUIRED')
        sys.path.insert(0, str(root / 'app'))
    from personal_mcp.config import load_config
    from personal_mcp.service import LocalService
    from personal_mcp.windows_jobs import OwnedJob
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    project = output / 'projects' / 'fixture'
    project.mkdir(parents=True)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    config_file = output / 'settings.local.json'
    config_file.write_text(json.dumps({'schema_version': 1, 'workspace_root': 'projects',
        'data_root': 'private', 'host': '127.0.0.1', 'port': port, 'permission_mode': 'full_control',
        'device_label': '桌面验收', 'tunnel': {'id': 'tunnel_' + '0' * 32, 'key_env': 'UNUSED_TEST_TUNNEL'}}), encoding='utf-8')
    service = LocalService(load_config(config_file), assets_root=root / 'resources')
    job = OwnedJob()
    report = {'status': 'RUNNING', 'tunnel_started': False, 'tests': [], 'frame_roundtrip_ms': []}
    token = secret = None
    try:
        receipt = output / 'fixture.json'
        process = job.spawn([sys.executable, '-B', str(Path(__file__).with_name('desktop_fixture.py')),
            '--receipt', str(receipt)], cwd=root, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        service.start(connect_tunnel=False)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        sequence = 0
        def rpc(method, params):
            nonlocal sequence
            sequence += 1
            body = json.dumps({'jsonrpc': '2.0', 'id': sequence, 'method': method, 'params': params}).encode()
            req = urllib.request.Request(f'http://127.0.0.1:{port}/mcp', body,
                {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + service.runtime.auth_token})
            with opener.open(req, timeout=45) as response:
                envelope = json.load(response)
            if 'error' in envelope:
                raise RuntimeError(envelope['error']['message'])
            return envelope['result']
        def call(name, **arguments):
            params = {'name': name, 'arguments': arguments}
            if secret:
                params['_meta'] = {'unified/ui_session': secret}
            value = rpc('tools/call', params)
            if value.get('isError'):
                error = value.get('structuredContent', {}).get('error', {})
                raise RuntimeError(name + ':' + str(error.get('code')) + ':' + str(error.get('cause_code', '')))
            return value
        deadline = time.monotonic() + 15
        while not receipt.exists():
            if process.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError('FIXTURE_NOT_READY')
            time.sleep(.1)
        token = call('workflow_begin', project_path='fixture', request_id='desktop-fixture-begin')['structuredContent']['workflow_id']
        call('workflow_activate', workflow_id=token)
        resources = rpc('resources/list', {})
        html = rpc('resources/read', {'uri': resources['resources'][0]['uri']})['contents'][0]['text']
        assert 'src="desktop.js"' not in html and 'unified/ui_session' in html
        report['tests'].append('authenticated_ui_resource')
        opened = call('DesktopConsole', workflow_id=token)
        secret = opened['_meta']['desktop']['ui_session']
        assert token not in json.dumps(opened)
        inventory = call('DesktopView', action='inventory')['structuredContent']
        window = next(w for w in inventory['windows'] if w['title'] == 'UPM Desktop Acceptance Fixture')
        target = {'kind': 'window', 'id': window['id']}
        def frame():
            start = time.perf_counter()
            value = call('DesktopView', action='frame', target=target)['_meta']['desktop_frame']
            report['frame_roundtrip_ms'].append(round((time.perf_counter() - start) * 1000, 2))
            return value
        def data():
            return json.loads(receipt.read_text(encoding='utf-8'))
        def control(name):
            api = service.runtime.desktop.native.api
            with api.dpi_context():
                rect = api.gui.GetWindowRect(data()['controls'][name]['hwnd'])
            return [(rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2]
        def action(action_name, point=None, **extras):
            value = frame()
            parameters = {'frame_id': value['frame_id'], **extras}
            endpoint = parameters.pop('endpoint', None)
            if endpoint is not None:
                rect = value['capture_rect']
                parameters.update(to_x=int((endpoint[0] - rect[0]) * value['width'] / value['source_width']),
                                  to_y=int((endpoint[1] - rect[1]) * value['height'] / value['source_height']))
            if point:
                x, y = point
                rect = value['capture_rect']
                parameters.update(x=int((x - rect[0]) * value['width'] / value['source_width']),
                                  y=int((y - rect[1]) * value['height'] / value['source_height']))
                api = service.runtime.desktop.native.api
                with api.dpi_context():
                    resource = next(iter(service.runtime.registry.resources.values()))
                    record = service.runtime.bf_server._bf_store.resolve_window(resource.bf_token, target['id'])
                    report['last_point_observation'] = {'point': point, 'capture_rect': rect,
                        'target_hwnd': record['hwnd'], 'hit_hwnd': api.window_at(point),
                        'foreground_hwnd': api.foreground(), 'preview_point': [parameters['x'], parameters['y']]}
            request = 'desktop-' + str(sequence)
            result = call('DesktopAction', action=action_name, request_id=request, **parameters)
            state = call('DesktopStatus', request_id=request)['structuredContent']['state']
            assert state == 'completed'
            time.sleep(.2)
            return result
        first = frame()
        native_api = service.runtime.desktop.native.api
        mouse_dispatch = native_api.mouse
        report['mouse_dispatch_ms'] = []
        def observe_mouse(button, down):
            report['mouse_dispatch_ms'].append([button, down, round(time.perf_counter() * 1000, 2)])
            return mouse_dispatch(button, down)
        native_api.mouse = observe_mouse
        assert first['geometry_verified']
        (output / 'actual-window.jpg').write_bytes(base64.b64decode(first['data_url'].split(',', 1)[1]))
        call('DesktopAction', action='arm', request_id='arm-fixture')
        # Windows 可将非前台窗口的首击只用于激活；先点无业务副作用的标题栏。
        action('click', [first['capture_rect'][0] + 180, first['capture_rect'][1] + 14])
        action('click', control('entry'))
        assert data()['entry_focused'], 'ENTRY_FOCUS_NOT_OBSERVED'
        action('type', text='{Win} 中文输入 014')
        assert data()['text'] == '{Win} 中文输入 014'
        action('click', control('button'))
        assert data()['clicks'] == 1
        action('click', control('target'), button='right')
        assert data()['right_clicks'] == 1
        action('click', control('target'), clicks=2)
        report['double_click_events'] = data()['button_events']
        assert data()['double_clicks'] == 1, 'DOUBLE_CLICK_NOT_OBSERVED'
        report['tests'].extend(['literal_chinese_input', 'left_click', 'right_click', 'double_click', 'action_receipts'])
        action('click', control('entry'))
        action('shortcut', keys=['ctrl', 'a'])
        action('type', text='快捷键替换')
        assert data()['text'] == '快捷键替换'
        start = control('canvas')
        action('drag', start, endpoint=[start[0] + 70, start[1] + 25])
        assert data()['drag'] is not None
        action('click', control('listing'))
        before_scroll = data()['scroll_first']
        action('scroll', control('listing'), dy=-3, dx=0)
        assert data()['scroll_first'] > before_scroll
        report['tests'].extend(['shortcut', 'bounded_drag', 'point_targeted_scroll'])
        call('DesktopAction', action='disarm', request_id='stop-fixture')
        content = b'UPM binary fixture\x00\xff\x01'
        upload = call('DesktopFiles', action='upload_begin', request_id='upload-begin',
                      path='roundtrip.bin', size=len(content), sha256=hashlib.sha256(content).hexdigest())['structuredContent']
        call('DesktopFiles', action='upload_chunk', request_id='upload-chunk', transfer_id=upload['transfer_id'],
             offset=0, data_base64=base64.b64encode(content).decode())
        call('DesktopFiles', action='upload_commit', request_id='upload-commit', transfer_id=upload['transfer_id'])
        down = call('DesktopFiles', action='download_begin', request_id='download-begin', path='roundtrip.bin')['structuredContent']
        block = call('DesktopFiles', action='download_chunk', transfer_id=down['transfer_id'], offset=0, limit=196608)
        assert 'data_base64' not in block['structuredContent']
        assert base64.b64decode(block['_meta']['transfer']['data_base64']) == content
        report['tests'].append('private_binary_roundtrip')
        call('workflow_end', workflow_id=token)
        token = secret = None
        report.update(status='PASS', version=service.runtime.server_identity()['version'],
                      tools=len(service.runtime.catalog), fixture_pid=process.pid)
    except Exception as exc:
        report.update(status='FAIL', error=str(exc), error_type=type(exc).__name__)
        raise
    finally:
        if token:
            service.runtime.registry.end(service.runtime.principal, token)
        service.stop()
        job.close()
        (output / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
