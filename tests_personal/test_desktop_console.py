"""侧边操控台的凭据隔离、画面新鲜度与动作去重。"""
from types import SimpleNamespace

from coding_tools_mcp.protocol import RequestContext, dispatch_rpc
from tests_personal.test_host import call, host as host, workflow


class Surface:
    def __init__(self):
        self.actions = []

    def inventory(self, resource):
        return {'device_label': 'fixture', 'displays': [{'id': 'screen', 'name': '屏幕',
            'width': 100, 'height': 100, 'primary': True}], 'windows': []}

    def capture(self, resource, target):
        return {'data_url': 'data:image/jpeg;base64,/9j/', 'width': 100, 'height': 100,
                'source_width': 100, 'source_height': 100, 'capture_rect': [0, 0, 100, 100],
                'geometry_verified': True, 'target': target, 'native_state': {'owner': resource.key}}

    def act(self, resource, frame, args):
        self.actions.append(args['action'])
        return {'ok': True, 'accepted': True, 'verified': False}


def console(host):
    token = workflow(host)
    opened = call(host, 'DesktopConsole', workflow_id=token)
    assert not opened['isError'], opened
    secret = opened['_meta']['desktop']['ui_session']
    assert secret not in str(opened['structuredContent']) + str(opened['content'])
    assert token not in str(opened)
    return token, secret


def ui(host, secret, name, **arguments):
    return host.call_tool(name, arguments, context=SimpleNamespace(ui_session=secret))


def test_ui_capability_is_private_and_missing_capability_fails(host):
    _, secret = console(host)
    host.desktop.native = Surface()
    denied = call(host, 'DesktopView', action='inventory')
    assert denied['isError'] and denied['structuredContent']['error']['code'] == 'UI_SESSION_REQUIRED'
    assert not ui(host, secret, 'DesktopView', action='inventory')['isError']


def test_actions_require_arm_fresh_frame_and_exact_request_dedup(host):
    _, secret = console(host)
    native = host.desktop.native = Surface()
    frame = ui(host, secret, 'DesktopView', action='frame', target={'kind': 'display', 'id': 'screen'})
    frame_id = frame['_meta']['desktop_frame']['frame_id']
    args = dict(action='click', frame_id=frame_id, x=3, y=4, request_id='click-one')
    assert ui(host, secret, 'DesktopAction', **args)['structuredContent']['error']['code'] == 'DESKTOP_NOT_ARMED'
    ui(host, secret, 'DesktopAction', action='arm', request_id='arm-one')
    args['request_id'] = 'click-two'
    first = ui(host, secret, 'DesktopAction', **args)
    assert not first['isError'], first
    assert ui(host, secret, 'DesktopAction', **args) == first
    assert native.actions == ['click']
    host.desktop.clock = lambda: 10**15
    assert ui(host, secret, 'DesktopAction', **{**args, 'request_id': 'later'})['isError']


def test_rotated_or_ended_workflow_revokes_ui_capability(host):
    token, secret = console(host)
    host.desktop.native = Surface()
    listing = call(host, 'workflow_list')['structuredContent']['workflows'][0]
    call(host, 'workflow_resume', workflow_ref=listing['workflow_ref'],
         expected_generation=listing['credential_generation'], request_id='rotate')
    denied = ui(host, secret, 'DesktopView', action='inventory')
    assert denied['isError']
    assert denied['structuredContent']['error']['code'] == 'UI_SESSION_EXPIRED'


def test_fixed_resource_and_private_rpc_context(host):
    from personal_mcp.desktop_console import RESOURCE_URI
    result = dispatch_rpc(host, {'jsonrpc': '2.0', 'id': 1, 'method': 'resources/list', 'params': {}})
    assert result['result']['resources'][0]['uri'] == RESOURCE_URI
    bad = dispatch_rpc(host, {'jsonrpc': '2.0', 'id': 2, 'method': 'resources/read',
                             'params': {'uri': 'file:///C:/secret'}})
    assert bad['error']['code'] == -32602
    _, secret = console(host)
    host.desktop.native = Surface()
    result = dispatch_rpc(host, {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {
        'name': 'DesktopView', 'arguments': {'action': 'inventory'}, '_meta': {'unified/ui_session': secret}}})
    assert not result['result']['isError']
    assert secret not in repr(RequestContext(ui_session=secret))


def test_ui_tool_contract_has_app_visibility_and_no_argument_credential(host):
    for name in ('DesktopView', 'DesktopAction', 'DesktopStatus', 'DesktopFiles'):
        tool = host.catalog[name]
        assert tool['_meta']['ui']['visibility'] == ['app']
        assert 'workflow_id' not in tool['inputSchema']['properties']
        assert 'ui_session' not in tool['inputSchema']['properties']
    assert host.initialize_result()['capabilities']['resources'] == {'subscribe': False, 'listChanged': False}


def test_slow_capture_does_not_reset_frame_age_when_encoding_finishes(host):
    _, secret = console(host)
    now = [1.0]
    host.desktop.clock = lambda: now[0]
    native = Surface()
    original = native.capture
    def delayed(resource, target):
        now[0] += 9
        return original(resource, target)
    native.capture = delayed
    host.desktop.native = native
    response = ui(host, secret, 'DesktopView', action='frame', target={'kind': 'display', 'id': 'screen'})
    assert response['structuredContent']['error']['code'] == 'DESKTOP_FRAME_STALE'
    assert '_meta' not in response
