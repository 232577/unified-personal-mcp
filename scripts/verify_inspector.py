"""Opt-in independent protocol smoke with a pinned, separately installed Inspector."""

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--inspector', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--node', type=Path, default=shutil.which('node'), help='Explicit Node runtime for Inspector')
    args = parser.parse_args()
    if args.node is None:
        raise ValueError('Node.js is required for Inspector')
    node = Path(args.node).resolve(strict=True)
    node_version = subprocess.check_output([str(node), '--version'], text=True,
        creationflags=subprocess.CREATE_NO_WINDOW).strip()
    inspector = args.inspector.resolve(strict=True)
    package = json.loads((inspector.parents[3] / 'package.json').read_text(encoding='utf-8'))
    if package.get('name') != '@modelcontextprotocol/inspector' or package.get('version') != '2.7.0':
        raise ValueError('Inspector 2.7.0 is required for this pinned smoke')
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from bf_automation.runtime import application_environment
    from personal_mcp.config import load_config
    from personal_mcp.service import LocalService

    projects = root / 'projects'
    (projects / 'fixture').mkdir(parents=True)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    config = root / 'settings.local.json'
    config.write_text(json.dumps({'schema_version': 1, 'workspace_root': str(projects),
        'data_root': str(root / 'private'), 'host': '127.0.0.1', 'port': port,
        'permission_mode': 'trusted', 'tunnel': {'id': 'tunnel_' + '0' * 32,
                                              'key_env': 'UNUSED_INSPECTOR_TEST_KEY'}}), encoding='utf-8')
    env = application_environment(dict(os.environ))
    storage = root / 'inspector-store'
    storage.mkdir()
    env.update(MCP_STORAGE_DIR=str(storage), MCP_INSPECTOR_OAUTH_STATE_PATH=str(storage / 'oauth.json'),
               MCP_CATALOG_PATH=str(storage / 'catalog.json'), MCP_CLIENT_CONFIG_PATH=str(storage / 'client.json'),
               MCP_AUTO_OPEN_ENABLED='false')
    service = LocalService(load_config(config))
    report = {'status': 'RUNNING', 'inspector_version': package['version'], 'node_version': node_version,
              'production_tunnel': 'not used', 'checks': []}
    try:
        service.start()

        def invoke(method, tool=None, arguments=None, *, auth=True, expected_exit=0):
            command = [str(node), str(inspector), '--cli', '--transport', 'http',
                '--server-url', f'http://127.0.0.1:{port}/mcp', '--protocol-era', 'legacy',
                '--stored-auth-only', '--connect-timeout', '10000', '--format', 'json', '--method', method]
            if auth:
                command += ['--header', 'Authorization: Bearer ' + service.runtime.auth_token]
            if tool:
                command += ['--tool-name', tool, '--tool-args-json', json.dumps(arguments or {})]
            result = subprocess.run(command, env=env, capture_output=True, text=True, encoding='utf-8',
                                    timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode != expected_exit:
                # Persist diagnostics from this isolated fixture only, never the invocation/header.
                (root / 'failure.stderr.txt').write_text(result.stderr, encoding='utf-8')
                raise AssertionError(f'Inspector {method}/{tool}: exit {result.returncode}, expected {expected_exit}')
            report['checks'].append({'method': method, 'tool': tool, 'exit_code': result.returncode})
            return json.loads(result.stdout)['result'] if result.stdout.strip() else {}

        initialize = invoke('initialize')
        report['server_version'] = initialize['serverInfo']['version']
        invoke('initialize', auth=False, expected_exit=3)
        tools = invoke('tools/list')['tools']
        assert len(tools) == 50 and 'OperationStatus' in {tool['name'] for tool in tools}
        info = invoke('tools/call', 'server_info')['structuredContent']
        assert info['health']['bf']['status'] == 'healthy'
        begun = invoke('tools/call', 'UnifiedTask', {'action': 'begin', 'project_path': 'fixture',
                                                  'request_id': 'begin'})['structuredContent']
        token = begun['workflow_id']
        active = invoke('tools/call', 'UnifiedTask', {'action': 'activate', 'workflow_id': token})['structuredContent']
        executed = invoke('tools/call', 'exec_command', {'workflow_id': token, 'request_id': 'execute',
            'cmd': 'echo inspector-fixture', 'yield_time_ms': 1000})
        assert executed['structuredContent']['exit_code'] == 0
        outcome = invoke('tools/call', 'OperationStatus', {'workflow_id': token, 'request_id': 'execute'})
        assert outcome['structuredContent']['result'] == executed
        resume_args = {'action': 'resume', 'workflow_ref': active['workflow_ref'],
                       'request_id': 'resume', 'expected_generation': active['credential_generation']}
        resumed = invoke('tools/call', 'UnifiedTask', resume_args)['structuredContent']
        assert invoke('tools/call', 'UnifiedTask', resume_args)['structuredContent'] == resumed
        rejected = invoke('tools/call', 'UnifiedTask', {'action': 'status', 'workflow_id': token}, expected_exit=5)
        assert rejected['structuredContent']['error']['code'] == 'WORKFLOW_CREDENTIAL_REPLACED'
        ended = invoke('tools/call', 'UnifiedTask', {'action': 'end',
            'workflow_id': resumed['workflow_id']})['structuredContent']
        assert ended['state'] == 'ENDED'
        report.update(status='PASS', tools=len(tools), catalog_revision=info['catalog_revision'])
    except BaseException as exc:
        report.update(status='FAIL', error_type=type(exc).__name__)
        raise
    finally:
        service.stop()
        (root / 'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
