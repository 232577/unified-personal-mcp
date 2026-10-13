"""Reviewed display metadata; no permission, dispatch, or result transformations."""


TOOL_TITLES = {
    'server_info': 'Inspect server capabilities',
    'check_exec_environment': 'Inspect command environment',
    'read_file': 'Read file text',
    'list_dir': 'List directory entries',
    'list_files': 'Find files by pattern',
    'search_text': 'Search file contents',
    'apply_patch': 'Apply file patch',
    'exec_command': 'Run a command',
    'write_stdin': 'Interact with a command',
    'kill_command': 'Terminate an owned command',
    'read_output': 'Read retained command output',
    'git_status': 'Inspect Git working tree',
    'git_diff': 'Read Git changes',
    'git_log': 'Read Git commit history',
    'git_show': 'Read a Git revision',
    'git_blame': 'Read Git line attribution',
    'request_permissions': 'Inspect local permission status',
    'view_image': 'Read an existing image',
    'App': 'Switch or resize a window',
    'Click': 'Click the foreground desktop',
    'Clipboard': 'Read or replace shared clipboard',
    'DisplayInventory': 'List connected displays',
    'Move': 'Move or drag the pointer',
    'Notification': 'Send Windows notification',
    'Screenshot': 'Capture the desktop image',
    'Scroll': 'Scroll the foreground desktop',
    'Shortcut': 'Send a keyboard shortcut',
    'Snapshot': 'Inspect desktop state',
    'Type': 'Type into the foreground control',
    'Wait': 'Wait for a duration',
    'WaitFor': 'Wait for a desktop condition',
    'prepare_browser_session': 'Prepare a private browser profile',
    'WindowInventory': 'List application windows',
    'WindowScreenshot': 'Capture an application window',
    'WindowSnapshot': 'Inspect an application window',
    'WindowControl': 'Control a selected window',
    'LaunchApplication': 'Launch a registered application',
    'BrowserAction': 'Act on a browser element',
    'BrowserWaitFor': 'Wait for a browser condition',
    'BrowserUpload': 'Select a file for browser upload',
    'BrowserDownload': 'Capture a browser download',
    'BrowserSession': 'Manage a private browser session',
    'BrowserPages': 'Manage owned browser pages',
    'BrowserNavigate': 'Navigate an owned browser page',
    'BrowserSnapshot': 'Inspect an owned browser page',
    'BrowserScreenshot': 'Capture an owned browser page',
    'BrowserActionStatus': 'Read a browser action result',
    'UnifiedTask': 'Manage workflow (legacy compatibility)',
    'workflow_begin': 'Reserve a project workflow',
    'workflow_activate': 'Activate a reserved workflow',
    'workflow_resume': 'Rotate an owned workflow credential',
    'workflow_end': 'End an owned workflow',
    'workflow_release_idle': 'Release an idle owned workflow',
    'workflow_list': 'List owned workflows',
    'workflow_status': 'Inspect an owned workflow',
    'command_status': 'Observe an owned command',
    'OperationStatus': 'Read a submitted operation result',
    'SearchSession': 'Manage a progressive file search',
}

FILE_PURPOSES = {
    'read_file': 'Read a bounded UTF-8 text slice from an existing file.',
    'list_dir': 'List directory entries with explicit depth and result limits.',
    'list_files': 'Find files using glob filters and explicit result limits.',
    'search_text': 'Search UTF-8 file contents for literal or regular-expression matches.',
    'apply_patch': ('Stage, validate, and atomically apply an explicit file patch. '
                    'The patch can add, overwrite, or delete files; dry_run validates without applying it.'),
    'view_image': 'Return an existing image as MCP image content; this does not capture the desktop.',
}

FIELD_HELP = {
    'workflow_id': ('Current secret workflow credential returned by workflow_begin or workflow_resume '
                    '(legacy UnifiedTask begin/resume is also supported). '
                    'Use the actual returned value; do not infer it from a workflow_ref.'),
    'workflow_ref': ('Non-secret reference returned by workflow_list or UnifiedTask list. '
                     'It identifies an owned workflow but is not an execution credential.'),
    'expected_generation': ('Use the actual credential_generation from the latest workflow list. '
                            'A mismatched generation is refused; do not guess.'),
    'command_id': ('Exact retained command handle returned by exec_command or workflow_status. '
                   'It identifies a command, not an arbitrary operating-system PID.'),
    'output_ref': 'Exact stdout or stderr reference returned for an owned command.',
    'session_id': 'Exact owned browser session identifier returned by BrowserSession.',
    'page_id': 'Exact page identifier returned for the selected owned browser session.',
    'snapshot_version': 'Use the version returned by a fresh snapshot of the target page.',
}


def describe_inputs(schema, tool_name):
    """Annotate actual schema nodes, never examples/defaults or caller data.

    A homogeneous string enum already excludes other types. Making its type
    explicit helps form/signature renderers without changing accepted values.
    """
    if not isinstance(schema, dict):
        return
    values = schema.get('enum')
    if 'type' not in schema and isinstance(values, list) and values and all(isinstance(v, str) for v in values):
        schema['type'] = 'string'
    for name, child in schema.get('properties', {}).items():
        if not isinstance(child, dict):
            continue
        if name in FIELD_HELP:
            child['description'] = FIELD_HELP[name]
        elif name == 'request_id':
            if tool_name == 'workflow_begin':
                child['description'] = ('Unique ID for a new reservation. The credential is returned only once. '
                                        'A duplicate reports prior acceptance without revealing it again; '
                                        'a lost first reply requires waiting for reservation expiry, not guessing a credential.')
            elif tool_name in {'OperationStatus', 'BrowserActionStatus'}:
                child['description'] = 'The original request_id of the submitted operation; do not create a new ID for this lookup.'
            else:
                child['description'] = ('Unique ID for a new operation. Reuse it only to recover the exact '
                                        'same submitted request with identical parameters; unknown effects are not replayed.')
        elif name in {'workdir', 'cwd'} and tool_name == 'exec_command':
            child['description'] = ('Working directory for this command only; relative paths use the workflow project. '
                                    'Does not change later calls. When supplied together, cwd and workdir must agree.')
    for keyword in ('properties', 'patternProperties', '$defs', 'definitions', 'dependentSchemas'):
        for child in schema.get(keyword, {}).values():
            describe_inputs(child, tool_name)
    for keyword in ('anyOf', 'oneOf', 'allOf', 'prefixItems'):
        for child in schema.get(keyword, []):
            describe_inputs(child, tool_name)
    for keyword in ('items', 'contains', 'additionalProperties', 'unevaluatedProperties',
                    'propertyNames', 'not', 'if', 'then', 'else'):
        describe_inputs(schema.get(keyword), tool_name)


def apply_tool_presentation(tools, *, full_control):
    """Apply reviewed presentation only to this instance's copied catalogue."""
    for name, tool in tools.items():
        if name not in TOOL_TITLES:
            raise ValueError('CATALOG_CONTRACT_INVALID')
        tool['title'] = TOOL_TITLES[name]
        tool['annotations']['title'] = tool['title']
        if name in FILE_PURPOSES:
            scope = ('This installation accepts absolute paths outside the workflow project, '
                     'subject to the Windows user\'s access and the configured policy.' if full_control else
                     'Paths are restricted by the configured policy to the workflow project.')
            tool['description'] = 'Requires an active owned workflow. ' + FILE_PURPOSES[name] + ' ' + scope
            if name == 'apply_patch':
                tool['description'] += (' Requires a write workflow and a unique request_id; '
                                        'an unknown previous outcome must not be executed again.')
        elif name == 'exec_command':
            scope = (' This installation accepts absolute paths outside the workflow project '
                     'with the Windows user\'s access.' if full_control else '')
            tool['description'] += scope + (' Set workdir explicitly for this command. '
                'Commands may modify files, start processes, or contact external systems, subject to policy. '
                'Use command_status to observe an existing command without running it again.')
        elif name == 'request_permissions':
            tool['description'] = ('Inspect whether the existing local installation policy permits a requested '
                'exec_command or apply_patch capability. Requires an active owned workflow. '
                'It does not display an approval dialog, change configuration, or override platform safety. '
                'A local granted result describes existing configuration, not user approval of this operation; '
                'unsupported elicitation remains an explicit error.')
        elif name == 'Notification':
            tool['description'] += (' This is a notification, not an approval dialog; '
                                    'it does not return a user decision or grant permission.')
        describe_inputs(tool['inputSchema'], name)
