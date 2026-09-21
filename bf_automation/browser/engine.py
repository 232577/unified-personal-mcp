"""Async browser observation engine for an isolated worker environment.

No public eval, file association, personal profile or shared browser instance.
The BF manager/MCP bridge is a separate U1 task; this module does not publish tools.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
import struct
import uuid
from urllib.parse import urlsplit, urlunsplit

from playwright.async_api import async_playwright

from ..browser.models import WebProfile
from ..browser.policy import PolicyError, validate_navigation, validate_resource

PRIVATE_SELECTOR = 'input[type="password"], [autocomplete*="password"], [data-bf-private]'

ELEMENT_QUERY = """limit => {
  const nodes = [...document.querySelectorAll('button,input,select,textarea,a[href],[role],[data-testid]')];
  const visible = nodes.filter(e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden'
      && !e.closest('[data-bf-private]') && !e.matches('input[type=password],[autocomplete*=password]'));
  return {total:visible.length, elements:visible.slice(0,limit).map(e => {
    const rect=e.getBoundingClientRect();
    const type=e.tagName.toLowerCase();
    return {role:e.getAttribute('role') || ({button:'button',input:'textbox',select:'combobox',
      textarea:'textbox',a:'link'}[type] || type),
      name:(e.getAttribute('aria-label') || (e.labels && e.labels[0] && e.labels[0].innerText)
        || (['input','textarea'].includes(type) ? '' : e.innerText) || '').slice(0,160),
      test_id:(e.getAttribute('data-testid') || '').slice(0,80), disabled:!!e.disabled,
      bounds:{x:rect.x,y:rect.y,width:rect.width,height:rect.height}};
  })};
}"""


def public_url(url):
    p = urlsplit(url)
    # Query/fragment can contain credentials. They are used internally but not recorded as evidence.
    return urlunsplit((p.scheme, p.netloc, p.path, '', ''))


class ObservationEngine:
    ACCEPT_DOWNLOADS = False
    """One browser and ephemeral context, owned by one future BF worker."""

    def __init__(self, profile: WebProfile):
        self.profile = profile
        self._playwright = self.browser = self.context = None
        self.pages = {}
        self._ids = {}
        self._pending = set()
        self._guards = {}
        self.blocked_requests = []
        self.closed = False
        self.max_pages = 4

    def _background(self, coro):
        task = asyncio.create_task(coro)
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    def _block(self, code, request_type):
        if len(self.blocked_requests) < 50:
            self.blocked_requests.append({'code': code, 'type': request_type})

    def _bind_page(self, page):
        if page in self._ids:
            return self._ids[page]
        if self.closed or len(self.pages) >= self.max_pages:
            self._background(page.close())
            return None
        page_id = 'bfp_' + uuid.uuid4().hex
        self.pages[page_id] = page
        self._ids[page] = page_id
        page.on('dialog', lambda dialog: self._background(dialog.dismiss()))
        def unbind():
            self.pages.pop(page_id, None)
            self._ids.pop(page, None)
        page.on('close', unbind)
        return page_id

    async def start(self):
        if self.closed or self._playwright is not None:
            raise RuntimeError('SESSION_ALREADY_STARTED_OR_CLOSED')
        if importlib.metadata.version('playwright') != '1.63.0':
            raise RuntimeError('BROWSER_RUNTIME_VERSION_MISMATCH')
        validate_navigation(self.profile, self.profile.entry_url)
        try:
            self._playwright = await async_playwright().start()
            self.browser = await self._playwright.chromium.launch(headless=True, chromium_sandbox=True)
            if self.browser.version != '153.0.8010.12':
                raise RuntimeError('CHROMIUM_VERSION_MISMATCH')
            self.context = await self.browser.new_context(
                viewport={'width':1280,'height':800}, device_scale_factor=1,
                service_workers='block', accept_downloads=self.ACCEPT_DOWNLOADS, permissions=[],
                ignore_https_errors=False)
            self.context.set_default_timeout(10000)
            await self.context.route('**/*', self._route)

            async def reject_websocket(ws):
                self._block('WEBSOCKET_UNSUPPORTED', 'websocket')
                await ws.close(code=1008, reason='not enabled in U1')

            await self.context.route_web_socket('**/*', reject_websocket)
            self.context.on('page', self._bind_page)
            return {'engine':'chromium','version':self.browser.version,'mode':'headless'}
        except BaseException:
            await self.close()
            raise

    async def _install_guard(self, page):
        cdp = await self.context.new_cdp_session(page)

        async def paused(event):
            request_id = event['requestId']
            try:
                url = event['request']['url']
                if event.get('resourceType') == 'Document':
                    validate_navigation(self.profile, url)
                else:
                    validate_resource(self.profile, url)
                await cdp.send('Fetch.continueRequest', {'requestId':request_id})
            except PolicyError as exc:
                self._block(str(exc), event.get('resourceType', 'unknown'))
                await cdp.send('Fetch.failRequest', {'requestId':request_id, 'errorReason':'BlockedByClient'})
            except Exception:
                if not self.closed and not page.is_closed():
                    self._block('CDP_GUARD_FAILED', 'unknown')
                    await page.close()

        cdp.on('Fetch.requestPaused', lambda event: self._background(paused(event)))
        await cdp.send('Fetch.enable', {'patterns':[{'urlPattern':'*','requestStage':'Request'}]})
        return cdp

    async def _ensure_guard(self, page):
        task = self._guards.get(page)
        if task is None:
            task = asyncio.create_task(self._install_guard(page))
            self._guards[page] = task
        return await task

    async def _route(self, route):
        request = route.request
        try:
            is_navigation = request.is_navigation_request()
            if is_navigation:
                validate_navigation(self.profile, request.url)
            else:
                validate_resource(self.profile, request.url)
            # Playwright routes do not guard every redirect hop. Chromium Fetch
            # pauses the actual browser request at each hop, without replacing
            # the response body or changing the browser's final URL.
            await self._ensure_guard(request.frame.page)
            await route.continue_()
        except PolicyError as exc:
            self._block(str(exc), request.resource_type)
            await route.abort('blockedbyclient')
        except Exception:
            self._block('REQUEST_FAILED', request.resource_type)
            try:
                await route.abort('failed')
            except Exception:
                pass  # Context may already be closing.

    def _page(self, page_id):
        page = self.pages.get(page_id)
        if self.closed or page is None or page.is_closed():
            raise LookupError('PAGE_NOT_OWNED_OR_CLOSED')
        return page

    async def new_page(self):
        if self.closed or self.context is None:
            raise LookupError('SESSION_NOT_ACTIVE')
        if len(self.pages) >= self.max_pages:
            raise ValueError('PAGE_LIMIT_REACHED')
        page = await self.context.new_page()
        await self._ensure_guard(page)
        return self._bind_page(page)

    async def navigate(self, page_id, url):
        normalized = validate_navigation(self.profile, url)
        page = self._page(page_id)
        try:
            response = await page.goto(normalized, wait_until='domcontentloaded', timeout=10000)
            validate_navigation(self.profile, page.url)
        except Exception:
            # A failed/denied redirect can still be settling after goto rejects.
            # Invalidate this owned observation page rather than racing a retry on it.
            await page.close()
            self.pages.pop(page_id, None)
            raise
        return {'page_id':page_id,'url':public_url(page.url),'title':await page.title(),
                'http_status':response.status if response else None}

    async def snapshot(self, page_id, max_elements=120):
        if type(max_elements) is not int or not 1 <= max_elements <= 200:
            raise ValueError('MAX_ELEMENTS_OUT_OF_RANGE')
        page = self._page(page_id)
        if page.url != 'about:blank':
            validate_navigation(self.profile, page.url)
        version = 'bfs_' + uuid.uuid4().hex
        frames, elements = [], []
        truncated = len(page.frames) > 10
        for index, frame in enumerate(page.frames[:10]):
            frame_id = version + '_' + str(index)
            remaining = max_elements - len(elements)
            if remaining <= 0:
                truncated = True
                break
            observed = await frame.evaluate(ELEMENT_QUERY, remaining)
            truncated |= observed['total'] > len(observed['elements'])
            for item in observed['elements']:
                item.update(element_id=version+'_'+str(len(elements)), frame_id=frame_id)
                elements.append(item)
            frames.append({'frame_id':frame_id,'url':public_url(frame.url)})
        return {'page_id':page_id,'url':public_url(page.url),'title':await page.title(),
                'snapshot_version':version,'frames':frames,'elements':elements,
                'element_count':len(elements),'truncated':truncated,'coordinate_space':'frame_css_pixels'}

    async def screenshot(self, page_id):
        page = self._page(page_id)
        if page.url != 'about:blank':
            validate_navigation(self.profile, page.url)
        # A page-only locator does not cover passwords/private sections inside frames.
        # Frame replacement during capture fails rather than silently dropping a mask.
        masks = [frame.locator(PRIVATE_SELECTOR) for frame in page.frames]
        png = await page.screenshot(type='png', full_page=False, mask=masks, timeout=10000)
        width, height = struct.unpack('>II', png[16:24])
        position = await page.evaluate('() => ({x:scrollX,y:scrollY,dpr:devicePixelRatio})')
        return png, {'page_id':page_id,'width':width,'height':height,'viewport':page.viewport_size,
                     'scroll':position,'sha256':hashlib.sha256(png).hexdigest(),
                     'coordinate_space':'viewport_image_pixels','private_fields_masked':True}

    async def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            if self.context is not None:
                await self.context.close()
            if self.browser is not None:
                await self.browser.close()
        finally:
            if self._playwright is not None:
                await self._playwright.stop()
            self.pages.clear()
            self._ids.clear()
            self._guards.clear()
            if self._pending:
                await asyncio.gather(*self._pending, return_exceptions=True)
