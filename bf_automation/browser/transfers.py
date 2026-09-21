"""U2.2 task-private file transfers bound to exact snapshot elements."""

from __future__ import annotations

import hashlib
import re
import uuid
from pathlib import Path

from ..browser.actions import ActionEngine

MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024


def safe_download_name(value: str) -> str:
    name = str(value or '').replace('\\', '/').rsplit('/', 1)[-1].strip().rstrip('. ')
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name)[:160]
    while '..' in name:
        name = name.replace('..', '_')
    if not name or name in {'.', '..'}:
        name = 'download.bin'
    reserved = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}
    if Path(name).stem.upper() in reserved:
        name = '_' + name
    return name


class TransferEngine(ActionEngine):
    ACCEPT_DOWNLOADS = True

    async def _transfer_target(self, page_id, snapshot_version, element_id):
        self._page(page_id)
        snapshot = self.snapshots.get(page_id)
        if snapshot is None or snapshot['version'] != snapshot_version:
            return None, None, 'STALE_SNAPSHOT'
        handle = snapshot['nodes'].get(element_id)
        if handle is None:
            return None, None, 'STALE_ELEMENT'
        try:
            if not await handle.evaluate('e => e.isConnected'):
                return None, None, 'STALE_ELEMENT'
            if await handle.is_disabled():
                return None, None, 'DISABLED_TARGET'
            kind = await handle.evaluate('e => ({tag:e.tagName, type:e.type || ""})')
        except Exception:
            return None, None, 'STALE_ELEMENT'
        return handle, kind, None

    async def upload(self, page_id, snapshot_version, element_id, file_path, filename, size, sha256):
        base = {'page_id': page_id, 'accepted': False, 'verified': False,
                'filename': filename, 'size': size, 'sha256': sha256}
        handle, kind, error = await self._transfer_target(page_id, snapshot_version, element_id)
        if error:
            return {**base, 'reason': error}
        if kind != {'tag': 'INPUT', 'type': 'file'}:
            return {**base, 'reason': 'INVALID_TARGET_TYPE'}
        source = Path(file_path).resolve(strict=True)
        data = source.read_bytes()
        if len(data) != size or hashlib.sha256(data).hexdigest() != sha256:
            return {**base, 'reason': 'UPLOAD_SOURCE_CHANGED'}
        await handle.set_input_files({
            'name': filename,
            'mimeType': 'application/octet-stream',
            'buffer': data,
        }, timeout=5000)
        files = await handle.evaluate('e => Array.from(e.files || []).map(f => ({name:f.name,size:f.size}))')
        verified = files == [{'name': filename, 'size': size}]
        return {**base, 'accepted': True, 'verified': verified,
                'reason': 'UPLOAD_READY' if verified else 'UPLOAD_VERIFICATION_FAILED'}

    async def download(self, page_id, snapshot_version, element_id, download_dir, timeout_ms):
        base = {'page_id': page_id, 'accepted': False, 'verified': False}
        handle, _, error = await self._transfer_target(page_id, snapshot_version, element_id)
        if error:
            return {**base, 'reason': error}
        page = self._page(page_id)
        directory = Path(download_dir).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        async with page.expect_download(timeout=timeout_ms) as pending:
            await handle.click(timeout=min(timeout_ms, 5000))
        download = await pending.value
        filename = safe_download_name(download.suggested_filename)
        target = directory / filename
        if target.exists():
            target = directory / f'{Path(filename).stem}-{uuid.uuid4().hex[:8]}{Path(filename).suffix}'
        await download.save_as(str(target))
        failure = await download.failure()
        if failure:
            target.unlink(missing_ok=True)
            raise RuntimeError('DOWNLOAD_PROVIDER_FAILURE')
        size = target.stat().st_size
        if size > MAX_DOWNLOAD_BYTES:
            target.unlink(missing_ok=True)
            return {**base, 'filename': target.name, 'size': size, 'sha256': '0' * 64,
                    'file': 'downloads/' + target.name, 'reason': 'DOWNLOAD_TOO_LARGE'}
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        return {**base, 'accepted': True, 'verified': True, 'filename': target.name,
                'size': size, 'sha256': digest, 'file': 'downloads/' + target.name,
                'reason': 'DOWNLOAD_SAVED'}
