"""Finite snapshot-bound actions. Never exposes script, coordinates or stored input.

Exact snapshot node references deliberately fail on detachment/navigation rather
than selecting a replacement. Dynamic result waiting uses a strict Locator.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import suppress

from ..browser.engine import ObservationEngine, public_url
from ..browser.policy import validate_navigation

SELECTOR = 'button,input,select,textarea,a[href],[role],[data-testid]'
METADATA = r'''e => {
  if (!e.isConnected || !e.getClientRects().length || getComputedStyle(e).visibility==='hidden'
      || e.closest('[data-bf-private]') || e.type==='hidden') return null;
  const clean = node => {if(!node) return ''; const c=node.cloneNode(true);
    c.querySelectorAll('[data-bf-private],input,textarea').forEach(x=>x.remove());
    return (c.textContent||'').trim();};
  const tag=e.tagName.toLowerCase(), r=e.getBoundingClientRect();
  const role=e.getAttribute('role') || (tag==='input' && ['checkbox','radio'].includes(e.type) ? e.type :
    {button:'button',input:'textbox',textarea:'textbox',select:'combobox',a:'link'}[tag] || tag);
  return {role, name:(e.getAttribute('aria-label') || clean(e.labels&&e.labels[0]) ||
    (['input','textarea'].includes(tag) ? '' : clean(e))).slice(0,160),
    test_id:(e.getAttribute('data-testid')||'').slice(0,80), disabled:!!e.disabled,
    bounds:{x:r.x,y:r.y,width:r.width,height:r.height}};
}'''
KEYS = {'Enter', 'Tab', 'Escape', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'}


class ActionEngine(ObservationEngine):
    def __init__(self, profile):
        super().__init__(profile)
        self.snapshots = {}
        self.revisions = {}

    async def _dispose(self, references):
        for handle in references.values():
            with suppress(Exception):
                await handle.dispose()

    def _invalidate(self, page_id):
        self.revisions[page_id] = self.revisions.get(page_id, 0) + 1
        old = self.snapshots.pop(page_id, None)
        if old:
            self._background(self._dispose(old['nodes']))

    def _bind_page(self, page):
        existed = page in self._ids
        page_id = super()._bind_page(page)
        if page_id and not existed:
            page.on('framenavigated', lambda _: self._invalidate(page_id))
            page.on('close', lambda: self._invalidate(page_id))
        return page_id

    async def snapshot(self, page_id, max_elements=120):
        if type(max_elements) is not int or not 1 <= max_elements <= 200:
            raise ValueError('MAX_ELEMENTS_OUT_OF_RANGE')
        page = self._page(page_id)
        if page.url != 'about:blank':
            validate_navigation(self.profile, page.url)
        self._invalidate(page_id)
        revision = self.revisions[page_id]
        version = 'bfs_' + uuid.uuid4().hex
        frames, elements, nodes = [], [], {}
        truncated = len(page.frames) > 10
        try:
            for index, frame in enumerate(page.frames[:10]):
                frame_id = version + '_' + str(index)
                frames.append({'frame_id': frame_id, 'url': public_url(frame.url)})
                group = await frame.evaluate_handle(
                    '(s) => Array.from(document.querySelectorAll(s)).filter(e => '
                    'e.getClientRects().length && !e.closest("[data-bf-private]")).slice(0,201)', SELECTOR)
                properties = await group.get_properties()
                await group.dispose()
                for value in properties.values():
                    handle = value.as_element()
                    if handle is None:
                        await value.dispose()
                        continue
                    if len(elements) >= max_elements:
                        truncated = True
                        await handle.dispose()
                        continue
                    item = await handle.evaluate(METADATA)
                    if item is None:
                        await handle.dispose()
                        continue
                    element_id = version + '_' + str(len(elements))
                    item.update(element_id=element_id, frame_id=frame_id)
                    nodes[element_id] = handle
                    elements.append(item)
            if revision != self.revisions[page_id]:
                raise LookupError('SNAPSHOT_NAVIGATION_RACE')
            self.snapshots[page_id] = {'version': version, 'nodes': nodes}
            return {'page_id': page_id, 'url': public_url(page.url), 'title': await page.title(),
                'snapshot_version': version, 'frames': frames, 'elements': elements,
                'element_count': len(elements), 'truncated': truncated, 'coordinate_space': 'frame_css_pixels'}
        except BaseException:
            await self._dispose(nodes)
            raise

    async def action(self, page_id, snapshot_version, element_id, action,
                     text=None, checked=None, values=None, key=None):
        result = {'page_id': page_id, 'action': action, 'accepted': False,
                  'verified': False, 'verification_required': False, 'reason': ''}

        def refused(reason):
            return {**result, 'reason': reason}

        expected = {'click': set(), 'fill': {'text'}, 'check': {'checked'}, 'select': {'values'}, 'press': {'key'}}
        actual = {k for k, v in {'text': text, 'checked': checked, 'values': values, 'key': key}.items() if v is not None}
        if (action not in expected or actual != expected[action]
                or (action == 'fill' and (not isinstance(text, str) or len(text) > 65536))
                or (action == 'check' and type(checked) is not bool)
                or (action == 'select' and (not isinstance(values, list) or not 1 <= len(values) <= 32
                    or any(not isinstance(v, str) or len(v) > 1024 for v in values)))
                or (action == 'press' and key not in KEYS)):
            return refused('INVALID_ACTION_ARGUMENTS')
        self._page(page_id)
        snapshot = self.snapshots.get(page_id)
        if snapshot is None or snapshot['version'] != snapshot_version:
            return refused('STALE_SNAPSHOT')
        handle = snapshot['nodes'].get(element_id)
        if handle is None:
            return refused('STALE_ELEMENT')
        try:
            connected = await handle.evaluate('e => e.isConnected')
            if not connected:
                return refused('STALE_ELEMENT')
            if await handle.is_disabled():
                return refused('DISABLED_TARGET')
            kind = await handle.evaluate('e => ({tag:e.tagName, type:e.type, readOnly:!!e.readOnly})')
            if action == 'fill' and (kind['tag'] not in {'INPUT', 'TEXTAREA'} or kind['readOnly']):
                return refused('INVALID_TARGET_TYPE')
            if action == 'check' and kind.get('type') not in {'checkbox', 'radio'}:
                return refused('INVALID_TARGET_TYPE')
            if action == 'select' and kind['tag'] != 'SELECT':
                return refused('INVALID_TARGET_TYPE')
        except Exception:
            return refused('STALE_ELEMENT')
        # From here on, provider failures may follow an actual event. The worker
        # reports uncertainty; the manager never repeats a dispatched request.
        verified = False
        if action == 'fill':
            await handle.fill(text, timeout=5000)
            verified = await handle.input_value() == text
        elif action == 'click':
            await handle.click(timeout=5000)
        elif action == 'check':
            await handle.set_checked(checked, timeout=5000)
            verified = await handle.is_checked() == checked
        elif action == 'select':
            selected = await handle.select_option(values, timeout=5000)
            verified = sorted(selected) == sorted(values)
        elif action == 'press':
            await handle.press(key, timeout=5000)
        return {**result, 'accepted': True, 'verified': verified,
                'verification_required': not verified, 'reason': 'ACTION_COMPLETED'}

    async def wait_for(self, page_id, test_id, condition, expected=None, timeout_ms=3000):
        if (not isinstance(test_id, str) or not 1 <= len(test_id) <= 128
                or condition not in {'visible', 'hidden', 'text_equals'}
                or type(timeout_ms) is not int or not 50 <= timeout_ms <= 10000
                or (condition == 'text_equals' and (not isinstance(expected, str) or len(expected) > 65536))):
            raise ValueError('INVALID_WAIT_ARGUMENTS')
        page = self._page(page_id)
        target = page.get_by_test_id(test_id)
        until = time.monotonic() + timeout_ms / 1000
        count = 0
        while True:
            count = await target.count()
            if count > 1:
                return {'page_id': page_id, 'matched': False, 'condition': condition,
                        'match_count': count, 'reason': 'AMBIGUOUS_TARGET'}
            matched = count == 0 and condition == 'hidden'
            if count == 1:
                if condition == 'text_equals':
                    matched = (await target.is_visible()
                               and await target.inner_text(timeout=500) == expected)
                else:
                    visible = await target.is_visible()
                    matched = visible if condition == 'visible' else not visible
            if matched or time.monotonic() >= until:
                return {'page_id': page_id, 'matched': matched, 'condition': condition,
                        'match_count': count, 'reason': 'MATCHED' if matched else 'TIMEOUT'}
            await asyncio.sleep(.05)

    async def close(self):
        for snapshot in list(self.snapshots.values()):
            await self._dispose(snapshot['nodes'])
        self.snapshots.clear()
        await super().close()
