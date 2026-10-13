"""Published hints must never grant workflow access or bypass request receipts."""

from tests_personal.test_host import call, host as host, workflow


def test_read_workflow_exec_denial_does_not_depend_on_published_hint(host):
    token = workflow(host, access='read')
    host.catalog['exec_command']['annotations']['readOnlyHint'] = True
    result = call(host, 'exec_command', workflow_id=token,
                  request_id='must-remain-denied', cmd='echo policy-fixture', yield_time_ms=1000)
    assert result['isError'] is True
    assert result['structuredContent']['error']['code'] == 'READ_ONLY_WORKFLOW'
    resource = next(iter(host.registry.resources.values()))
    assert resource.coding.command_inventory() == []


def test_file_read_permission_does_not_depend_on_published_hint(host):
    token = workflow(host, access='read')
    host.catalog['read_file']['annotations']['readOnlyHint'] = False
    result = call(host, 'read_file', workflow_id=token, path='note.txt')
    assert result['isError'] is False
    assert result['structuredContent']['content'] == 'hello unified'


def test_command_receipt_and_deduplication_do_not_depend_on_published_hint(host):
    token = workflow(host)
    host.catalog['exec_command']['annotations']['readOnlyHint'] = True
    arguments = {'workflow_id': token, 'request_id': 'one-command',
                 'cmd': 'echo policy-fixture', 'yield_time_ms': 1000}
    first = call(host, 'exec_command', **arguments)
    assert first['isError'] is False, first
    assert first['structuredContent']['exit_code'] == 0
    assert call(host, 'exec_command', **arguments) == first
    receipt = call(host, 'OperationStatus', workflow_id=token, request_id='one-command')
    assert receipt['structuredContent']['result'] == first
    resource = next(iter(host.registry.resources.values()))
    assert len(resource.coding.command_inventory()) == 1
