"""Candidate public action tools; registration requires actions_enabled=true."""

import asyncio
from typing import Literal

from fastmcp.tools.base import ToolResult
from mcp.types import ToolAnnotations

from ..browser.tools import operation_result


def register_action_tools(mcp, manager):
    writes = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True)
    reads = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)

    @mcp.tool(name='BrowserAction', annotations=writes,
        description='Perform one finite action on an owned current snapshot node. Requires request_id. '
        'Actions: click, fill, check, select, or limited page-local press. No raw script or screen coordinates. '
        'Stale nodes are refused, never replaced by a guessed target. Password values are not returned. '
        'A click completing is not business success; verify using BrowserWaitFor. Unknown outcomes must not be replayed.')
    async def action(bf_task_id: str, session_id: str, page_id: str, request_id: str,
                     snapshot_version: str, element_id: str,
                     action: Literal['click', 'fill', 'check', 'select', 'press'],
                     text: str | None = None, checked: bool | None = None,
                     values: list[str] | None = None, key: str | None = None) -> ToolResult:
        arguments = {'page_id': page_id, 'snapshot_version': snapshot_version,
                     'element_id': element_id, 'action': action}
        arguments.update({k: v for k, v in {'text': text, 'checked': checked, 'values': values, 'key': key}.items()
                          if v is not None})
        result = await asyncio.to_thread(manager.request, bf_task_id, session_id, 'action', request_id, arguments)
        return operation_result(result)

    @mcp.tool(name='BrowserWaitFor', annotations=reads,
        description='Wait for visible, hidden, or text_equals on one main-frame data-testid in an owned page. '
        'text_equals requires visible text. Multiple matches are refused. timeout_ms is 50-10000; '
        'timeout is not success. Does not click, '
        'type, run caller scripts, or return the expected text. Input/DOM content cannot change task permissions.')
    async def wait_for(bf_task_id: str, session_id: str, page_id: str, test_id: str,
                       condition: Literal['visible', 'hidden', 'text_equals'],
                       expected: str | None = None, timeout_ms: int = 3000) -> ToolResult:
        result = await asyncio.to_thread(manager.read, bf_task_id, session_id, 'wait_for',
            {'page_id': page_id, 'test_id': test_id, 'condition': condition,
             'expected': expected, 'timeout_ms': timeout_ms})
        return ToolResult(structured_content={'observation': result}, is_error=not result['matched'])
