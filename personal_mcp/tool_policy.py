"""Server-owned dispatch policy. Client-facing annotations never grant access.

New tools require an explicit policy review here before catalogue publication.
These sets preserve the existing workflow checks and receipt behavior; resource
ownership, runtime permissions and argument validation remain separate checks.
"""

from types import MappingProxyType


WORKFLOW_ACTIONS = MappingProxyType({
    'workflow_begin': 'begin',
    'workflow_activate': 'activate',
    'workflow_resume': 'resume',
    'workflow_end': 'end',
    'workflow_release_idle': 'release_idle',
})


READ_ONLY_DESCRIPTORS = frozenset({
    'server_info', 'check_exec_environment', 'read_file', 'list_dir', 'list_files',
    'search_text', 'read_output', 'git_status', 'git_diff', 'git_log', 'git_show',
    'git_blame', 'request_permissions', 'view_image', 'DisplayInventory',
    'Screenshot', 'Snapshot', 'Wait', 'WaitFor', 'WindowInventory',
    'WindowScreenshot', 'WindowSnapshot', 'BrowserWaitFor', 'BrowserSnapshot',
    'BrowserScreenshot', 'BrowserActionStatus', 'workflow_list', 'workflow_status',
    'OperationStatus', 'command_status', 'DesktopView', 'DesktopStatus',
})

WRITE_DESCRIPTORS = frozenset({
    'apply_patch', 'exec_command', 'write_stdin', 'kill_command', 'App', 'Click',
    'Clipboard', 'Move', 'Notification', 'Scroll', 'Shortcut', 'Type',
    'prepare_browser_session', 'WindowControl', 'LaunchApplication',
    'BrowserAction', 'BrowserUpload', 'BrowserDownload', 'BrowserSession',
    'BrowserPages', 'BrowserNavigate', 'UnifiedTask', 'SearchSession',
    'DesktopConsole', 'DesktopAction', 'DesktopFiles',
}) | frozenset(WORKFLOW_ACTIONS)

KNOWN_TOOLS = READ_ONLY_DESCRIPTORS | WRITE_DESCRIPTORS

UNBOUNDED_FILE_TOOLS = frozenset({
    'read_file', 'list_dir', 'list_files', 'search_text', 'apply_patch', 'view_image',
})


def reviewed_effects(name: str, *, full_control: bool = False) -> dict[str, bool]:
    """Effects that the unified adapter must not understate in its descriptor.

Stdin can submit any interaction to an already-running command. Clipboard set
overwrites shared contents. Full-control file tools accept paths outside the
project (including destinations subject to the Windows user's access).
"""
    if name == 'write_stdin':
        return {'destructiveHint': True, 'openWorldHint': True}
    if name == 'Clipboard':
        return {'destructiveHint': True}
    if full_control and name in UNBOUNDED_FILE_TOOLS:
        return {'openWorldHint': True}
    return {}


def requires_workflow_write(name: str) -> bool:
    """Preserve the existing, explicit task-private search control exception.

SearchSession has stateful search lifecycle actions and is NOT advertised as
read-only. Its legacy access rule allows searches in a read workflow; ownership
and retained-result limits are enforced by SearchManager. This is not inferred
from a hint and does not grant project file or desktop write access.
"""
    if name not in KNOWN_TOOLS:
        raise ValueError('TOOL_POLICY_UNKNOWN')
    return name in WRITE_DESCRIPTORS and name != 'SearchSession'
