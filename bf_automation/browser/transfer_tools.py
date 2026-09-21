"""Public U2.2 transfer tools. No arbitrary download destination is accepted."""

import asyncio

from mcp.types import ToolAnnotations

from ..browser.tools import operation_result


def register_transfer_tools(mcp, manager):
    writes = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True)

    @mcp.tool(name='BrowserUpload', annotations=writes,
        description='Select one project-owned local file on an exact owned file-input snapshot node. '
        'The manager validates the source inside the BF task project, stages it privately, and never returns '
        'the original path or file contents. request_id deduplicates retries; no raw script or coordinates.')
    async def upload(bf_task_id: str, session_id: str, page_id: str, request_id: str,
                     snapshot_version: str, element_id: str, source_path: str):
        return operation_result(await asyncio.to_thread(
            manager.request, bf_task_id, session_id, 'upload', request_id,
            {'page_id': page_id, 'snapshot_version': snapshot_version,
             'element_id': element_id, 'source_path': source_path}))

    @mcp.tool(name='BrowserDownload', annotations=writes,
        description='Click one exact owned snapshot node and capture the resulting browser download into this '
        'BF task session private downloads directory. No caller-selected output path and no automatic file open. '
        'request_id deduplicates retries; an uncertain dispatched click is not replayed.')
    async def download(bf_task_id: str, session_id: str, page_id: str, request_id: str,
                       snapshot_version: str, element_id: str, timeout_ms: int = 10000):
        return operation_result(await asyncio.to_thread(
            manager.request, bf_task_id, session_id, 'download', request_id,
            {'page_id': page_id, 'snapshot_version': snapshot_version,
             'element_id': element_id, 'timeout_ms': timeout_ms}))
