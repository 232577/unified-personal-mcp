"""Playwright CDP adapter for a WebView2 endpoint already proven by HybridManager."""

from __future__ import annotations

import asyncio
import importlib.metadata
from urllib.parse import urlsplit

from playwright.async_api import async_playwright

from ..browser.policy import PolicyError, validate_navigation
from ..browser.transfers import TransferEngine


class WebView2TransferEngine(TransferEngine):
    def __init__(self, profile, *, hybrid_instance_id, playwright_factory=async_playwright):
        super().__init__(profile)
        self.hybrid_instance_id = hybrid_instance_id
        self.playwright_factory = playwright_factory
        self.main_page_id = None
        self.ownership = None

    @staticmethod
    def _endpoint(value):
        try:
            parsed = urlsplit(value)
            if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1'
                    or parsed.username is not None or parsed.password is not None
                    or parsed.path not in {'', '/'} or parsed.query or parsed.fragment
                    or parsed.port is None):
                raise ValueError()
            return f'http://127.0.0.1:{int(parsed.port)}'
        except (ValueError, TypeError):
            raise ValueError('DEBUG_ENDPOINT_NOT_OWNED') from None

    async def _candidate(self, page, ownership):
        if page.is_closed():
            return False
        try:
            validate_navigation(self.profile, page.url)
        except PolicyError:
            return False
        if ownership == 'managed':
            marker = await page.evaluate(
                '() => document.documentElement.dataset.bfHybridInstance || null'
            )
            if marker != self.hybrid_instance_id:
                return False
        return True

    async def _admit_popup(self, page):
        for _ in range(50):
            if self.closed or page.is_closed():
                return
            if page.url != 'about:blank':
                break
            await asyncio.sleep(0.05)
        if not await self._candidate(page, 'attached'):
            if not page.is_closed():
                await page.close()
            return
        self._bind_page(page)

    async def start_connection(self, connection):
        if self.closed or self._playwright is not None:
            raise RuntimeError('SESSION_ALREADY_STARTED_OR_CLOSED')
        if importlib.metadata.version('playwright') != '1.63.0':
            raise RuntimeError('BROWSER_RUNTIME_VERSION_MISMATCH')
        if not isinstance(connection, dict) or set(connection) != {
            'endpoint', 'hybrid_instance_id', 'engine_version', 'ownership',
        }:
            raise ValueError('INVALID_WEBVIEW2_CONNECTION')
        if connection['hybrid_instance_id'] != self.hybrid_instance_id:
            raise PermissionError('HYBRID_INSTANCE_NOT_OWNED')
        ownership = connection['ownership']
        if ownership not in {'managed', 'attached'}:
            raise ValueError('INVALID_WEBVIEW2_CONNECTION')
        endpoint = self._endpoint(connection['endpoint'])
        expected_version = connection['engine_version']
        if not isinstance(expected_version, str) or not expected_version or len(expected_version) > 128:
            raise ValueError('INVALID_WEBVIEW2_CONNECTION')
        try:
            self._playwright = await self.playwright_factory().start()
            self.browser = await self._playwright.chromium.connect_over_cdp(endpoint)
            if str(self.browser.version) != expected_version:
                raise RuntimeError('WEBVIEW2_RUNTIME_VERSION_MISMATCH')
            candidates = []
            for context in list(self.browser.contexts):
                for page in list(context.pages):
                    if await self._candidate(page, ownership):
                        candidates.append((context, page))
            if not candidates:
                raise LookupError('WEBVIEW_TARGET_NOT_FOUND')
            if len(candidates) != 1:
                raise LookupError('AMBIGUOUS_WEBVIEW_TARGET')
            self.context, page = candidates[0]
            self.context.set_default_timeout(10000)
            await self.context.route('**/*', self._route)

            async def reject_websocket(ws):
                self._block('WEBSOCKET_UNSUPPORTED', 'websocket')
                await ws.close(code=1008, reason='not enabled in U4A')

            await self.context.route_web_socket('**/*', reject_websocket)
            self.context.on('page', lambda popup: self._background(self._admit_popup(popup)))
            page_id = self._bind_page(page)
            if page_id is None:
                raise RuntimeError('WEBVIEW_TARGET_NOT_FOUND')
            self.main_page_id = page_id
            self.ownership = ownership
            return {
                'engine': 'webview2', 'version': expected_version,
                'mode': 'hybrid', 'ownership': ownership,
            }
        except BaseException:
            await self.close()
            raise

    async def new_page(self):
        raise ValueError('WEBVIEW_PAGE_CREATION_UNSUPPORTED')

    async def close_page(self, page_id):
        page = self._page(page_id)
        if page_id == self.main_page_id:
            raise ValueError('WEBVIEW_MAIN_PAGE_CLOSE_UNSUPPORTED')
        await page.close()
        return {'page_id': page_id, 'closed': True}

    async def screenshot(self, page_id):
        png, metadata = await super().screenshot(page_id)
        if metadata.get('viewport') is None:
            page = self._page(page_id)
            viewport = await page.evaluate(
                '() => ({width: Math.trunc(innerWidth), height: Math.trunc(innerHeight)})'
            )
            if (not isinstance(viewport, dict)
                    or type(viewport.get('width')) is not int
                    or type(viewport.get('height')) is not int
                    or viewport['width'] < 1 or viewport['height'] < 1):
                raise RuntimeError('WEBVIEW_VIEWPORT_UNAVAILABLE')
            metadata['viewport'] = viewport
        return png, metadata

    async def close(self):
        if self.closed:
            return
        self.closed = True
        for snapshot in list(self.snapshots.values()):
            await self._dispose(snapshot['nodes'])
        self.snapshots.clear()
        if self._pending:
            await asyncio.gather(*list(self._pending), return_exceptions=True)
        if self._playwright is not None:
            await self._playwright.stop()
        self.pages.clear()
        self._ids.clear()
        self._guards.clear()
        self._pending.clear()
        self.context = None
        self.browser = None
        self._playwright = None
