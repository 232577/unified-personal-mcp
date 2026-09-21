"""One task's async browser worker; no public network listener or arbitrary eval."""

from __future__ import annotations

import asyncio
import ctypes
import json
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if not __package__:
    sys.path.insert(0, str(ROOT))
    __package__ = 'bf_automation.browser'
from ..browser.models import WebProfile  # noqa: E402 -- direct worker entry point sets package root first
from ..browser.protocol import MAX_LINE, VERSION, decode, encode  # noqa: E402


async def main():
    engine = None
    directory = None
    try:
        while True:
            raw = await asyncio.to_thread(sys.stdin.buffer.readline, MAX_LINE + 1)
            if not raw:
                break
            message = decode(raw)
            if set(message) != {'version', 'id', 'operation', 'arguments'} or message['version'] != VERSION:
                raise ValueError('PROTOCOL_VERSION_OR_KEYS')
            request_id, operation, args = message['id'], message['operation'], message['arguments']
            if not isinstance(request_id, str) or not isinstance(args, dict):
                raise ValueError('PROTOCOL_TYPES')
            try:
                if operation == 'boot':
                    if engine is not None:
                        raise ValueError('ALREADY_BOOTED')
                    directory = Path(args['directory']).resolve(strict=True)
                    profile = args['profile']
                    profile['project_root'] = Path(profile['project_root'])
                    profile['navigation_origins'] = tuple(profile['navigation_origins'])
                    profile['resource_origins'] = tuple(profile['resource_origins'])
                    profile['upload_roots'] = tuple(profile.get('upload_roots', ()))
                    engine_mode = args.get('engine_mode', 'chromium')
                    if engine_mode == 'webview2':
                        from ..browser.webview2 import WebView2TransferEngine
                        connection = args.get('connection')
                        if not isinstance(connection, dict):
                            raise ValueError('INVALID_WEBVIEW2_CONNECTION')
                        engine = WebView2TransferEngine(
                            WebProfile(**profile),
                            hybrid_instance_id=connection.get('hybrid_instance_id'),
                        )
                        engine.max_pages = args['max_pages']
                        result = await engine.start_connection(connection)
                    elif engine_mode == 'chromium':
                        from ..browser.engine import ObservationEngine
                        engine_type = ObservationEngine
                        if args.get('actions_enabled') is True:
                            from ..browser.actions import ActionEngine
                            engine_type = ActionEngine
                        if args.get('transfers_enabled') is True:
                            from ..browser.transfers import TransferEngine
                            engine_type = TransferEngine
                        engine = engine_type(WebProfile(**profile))
                        engine.max_pages = args['max_pages']
                        result = await engine.start()
                    else:
                        raise ValueError('INVALID_ENGINE_MODE')
                    ctypes.windll.kernel32.GetConsoleWindow.restype = ctypes.c_void_p
                    handle = ctypes.windll.kernel32.GetConsoleWindow()
                    result.update(worker_pid=os.getpid(), console_visible=bool(
                        handle and ctypes.windll.user32.IsWindowVisible(ctypes.c_void_p(handle))))
                elif engine is None:
                    raise ValueError('WORKER_NOT_BOOTED')
                elif operation == 'health':
                    if engine.closed or engine.browser is None or not engine.browser.is_connected():
                        raise LookupError('BROWSER_DISCONNECTED')
                    result = {'alive': True}
                elif operation == 'new_page':
                    result = {'page_id': await engine.new_page()}
                elif operation == 'pages':
                    from ..browser.engine import public_url
                    result = {'pages': [{'page_id': pid, 'url': public_url(page.url)}
                                        for pid, page in list(engine.pages.items()) if not page.is_closed()]}
                elif operation == 'navigate':
                    result = await engine.navigate(args['page_id'], args['url'])
                elif operation == 'close_page':
                    if hasattr(engine, 'close_page'):
                        result = await engine.close_page(args['page_id'])
                    else:
                        await engine._page(args['page_id']).close()
                        result = {'page_id': args['page_id'], 'closed': True}
                elif operation == 'snapshot':
                    result = await engine.snapshot(args['page_id'], args.get('max_elements', 120))
                elif operation == 'action' and hasattr(engine, 'action'):
                    result = await engine.action(**args)
                elif operation == 'upload' and hasattr(engine, 'upload'):
                    result = await engine.upload(**args)
                elif operation == 'download' and hasattr(engine, 'download'):
                    result = await engine.download(**args)
                elif operation == 'wait_for' and hasattr(engine, 'wait_for'):
                    result = await engine.wait_for(**args)
                elif operation == 'screenshot':
                    png, result = await engine.screenshot(args['page_id'])
                    name = 'capture-' + uuid.uuid4().hex + '.png'
                    if len(png) > 8 * 1024 * 1024:
                        raise ValueError('IMAGE_TOO_LARGE')
                    (directory / name).write_bytes(png)
                    result['file'] = name
                elif operation == 'close':
                    await engine.close()
                    result = {'closed': True}
                else:
                    raise ValueError('UNKNOWN_WORKER_OPERATION')
                reply = {'version': VERSION, 'id': request_id, 'ok': True, 'result': result}
            except Exception as exc:
                # Provider exceptions may contain URLs, passwords and page text.
                code = ('INVALID_ARGUMENT' if isinstance(exc, ValueError) else
                        'STALE_TARGET' if isinstance(exc, LookupError) else 'BROWSER_OPERATION_FAILED')
                print(json.dumps({'operation': operation, 'error_type': type(exc).__name__}), file=sys.stderr, flush=True)
                reply = {'version': VERSION, 'id': request_id, 'ok': False, 'error': code}
            sys.stdout.buffer.write(encode(reply))
            sys.stdout.buffer.flush()
            if operation == 'close':
                break
    finally:
        if engine is not None:
            await engine.close()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except Exception as error:
        print(json.dumps({'fatal': type(error).__name__}), file=sys.stderr, flush=True)
        raise SystemExit(2) from None
