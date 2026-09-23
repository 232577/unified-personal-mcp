# Imported pytest fixtures intentionally share names with test parameters.
# ruff: noqa: F811
import pytest
from jsonschema import Draft202012Validator

from tests_personal.test_host import host  # noqa: F401


@pytest.mark.parametrize('arguments', [
    {'action': 'begin', 'project_path': 'app', 'request_id': 'new', 'access': 'read', 'ttl': 60},
    {'action': 'activate', 'workflow_id': 'wf_' + 'x' * 43},
    {'action': 'status', 'workflow_id': 'wf_' + 'x' * 43},
    {'action': 'end', 'workflow_id': 'wf_' + 'x' * 43},
    {'action': 'list'},
    {'action': 'release_idle', 'workflow_ref': 'wfr_' + 'a' * 64},
    {'action': 'resume', 'workflow_ref': 'wfr_' + 'a' * 64,
     'request_id': 'recover', 'expected_generation': 0},
])
def test_union_branches_describe_complete_client_arguments(host, arguments):
    schema = host.catalog['UnifiedTask']['inputSchema']
    branch = next(item for item in schema['oneOf']
                  if arguments['action'] in item['properties']['action']['enum'])
    # Clients render each union arm as a distinct object; inherited field
    # declarations must not be needed to construct its callable signature.
    assert set(arguments) <= branch['properties'].keys()
    assert set(branch['required']) <= branch['properties'].keys()
    Draft202012Validator(branch).validate(arguments)
    Draft202012Validator(schema).validate(arguments)
    assert not Draft202012Validator(branch).is_valid({**arguments, 'unexpected': True})
