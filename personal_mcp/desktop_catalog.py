"""远程桌面组件的明确工具契约。"""
from .desktop_console import RESOURCE_URI


def desktop_tools():
    text = {'type': 'string', 'minLength': 1, 'maxLength': 128}
    target = {'type': 'object', 'additionalProperties': False, 'required': ['kind', 'id'],
              'properties': {'kind': {'enum': ['display', 'window']}, 'id': text}}
    coordinate = {'type': 'number', 'minimum': 0, 'maximum': 16384}
    definitions = {
        'DesktopConsole': ('Open the remote desktop side panel',
            'Open a private remote desktop panel for this active write workflow. Starts in observation mode. '
            'The user enables control in the panel. No mouse or keyboard input is sent by opening it.',
            {'workflow_id': text}, ['workflow_id'], False),
        'DesktopView': ('Observe the selected desktop surface',
            'Component-only observation of displays, windows, bounded live frames or explicitly requested clipboard text. '
            'Requires the private UI capability in request metadata; never activates control.',
            {'action': {'enum': ['inventory', 'frame', 'clipboard']}, 'target': target}, ['action'], True),
        'DesktopAction': ('Control the selected desktop surface',
            'Component-only explicit user control. Requires a private UI capability, active write workflow, '
            'armed session and a fresh frame for input. Unknown effects must not be replayed. '
            'Typing, keys, clicks and clipboard writes may submit, modify or delete data.',
            {'request_id': text, 'action': {'enum': ['arm', 'disarm', 'click', 'drag', 'scroll', 'type',
                                                   'shortcut', 'clipboard_write']},
             'frame_id': text, 'x': coordinate, 'y': coordinate, 'to_x': coordinate, 'to_y': coordinate,
             'button': {'enum': ['left', 'right', 'middle']}, 'clicks': {'type': 'integer', 'minimum': 1, 'maximum': 2},
             'dx': {'type': 'integer', 'minimum': -10, 'maximum': 10},
             'dy': {'type': 'integer', 'minimum': -10, 'maximum': 10},
             'text': {'type': 'string', 'maxLength': 8192},
             'keys': {'type': 'array', 'minItems': 1, 'maxItems': 4,
                      'items': {'type': 'string', 'maxLength': 24}}}, ['action', 'request_id'], False),
        'DesktopStatus': ('Observe a desktop action receipt',
            'Component-only lookup of an original request_id. Does not repeat the action or authorize a new action.',
            {'request_id': text}, ['request_id'], True),
        'DesktopFiles': ('Transfer files for the selected workflow',
            'Component-only bounded binary file transfer. Uploads create new files and never overwrite. '
            'Downloads read an explicit file snapshot. All transfers are owner-scoped and expire. '
            'Mutating transfer steps require unique request_id values; files are never executed.',
            {'action': {'enum': ['upload_begin', 'upload_chunk', 'upload_commit', 'download_begin',
                                'download_chunk', 'status', 'release']}, 'request_id': text, 'transfer_id': text,
             'path': {'type': 'string', 'minLength': 1, 'maxLength': 4096},
             'size': {'type': 'integer', 'minimum': 0, 'maximum': 134217728},
             'sha256': {'type': 'string', 'pattern': '^[0-9a-f]{64}$'},
             'offset': {'type': 'integer', 'minimum': 0},
             'limit': {'type': 'integer', 'minimum': 1, 'maximum': 196608},
             'data_base64': {'type': 'string', 'maxLength': 262144}}, ['action'], False),
    }
    tools = {}
    for name, (title, description, properties, required, readonly) in definitions.items():
        tools[name] = {'name': name, 'title': title, 'description': description,
            'inputSchema': {'type': 'object', 'properties': properties, 'required': required,
                            'additionalProperties': False},
            'outputSchema': {'type': 'object', 'required': ['ok'], 'properties': {'ok': {'type': 'boolean'}}},
            'annotations': {'title': title, 'readOnlyHint': readonly,
                            'destructiveHint': name == 'DesktopAction',
                            'idempotentHint': False, 'openWorldHint': True},
            '_meta': {'ui': {'visibility': ['app']}}}
    tools['DesktopConsole']['_meta'] = {'ui': {'resourceUri': RESOURCE_URI},
        'openai/ui': {'entrypoints': [{'type': 'thread'}]}, 'openai/outputTemplate': RESOURCE_URI}
    return tools
