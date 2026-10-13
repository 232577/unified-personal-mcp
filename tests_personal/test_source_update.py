"""源码升级必须验证输入，并把旧安装完整留在原处。"""
import hashlib
import json
from importlib import import_module

import pytest


def manifest(root):
    rows = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file() and p.name != 'manifest-sha256.json'}
    (root / 'manifest-sha256.json').write_text(json.dumps(rows), encoding='utf-8')


def fixtures(tmp_path):
    base, update = tmp_path / 'old', tmp_path / 'update'
    for root, version in ((base, '0.1.10'), (update, '0.1.13')):
        files = {'app/run.py': 'pass', 'app/personal_mcp/__init__.py': f'__version__ = "{version}"',
                 'scripts/install-autostart.ps1': 'install', 'requirements-windows.lock': 'same-lock',
                 'package-metadata.json': json.dumps({'name': 'unified-personal-mcp', 'version': version})}
        if root == base:
            files.update({'UnifiedPersonalMCP.exe': 'launcher', 'resources/python/python.exe': 'python',
                          'resources/python/pythonw.exe': 'pythonw', 'resources/bin/rg.exe': 'resource',
                          'app/personal_mcp/removed.py': 'obsolete'})
        else:
            files.update({'app/bf_automation/__init__.py': '', 'app/coding_tools_mcp/__init__.py': '',
                          'scripts/prepare_update.py': 'prepare', 'update.cmd': 'wrapper',
                          'LICENSE': 'license', 'NOTICE': 'notice'})
        for relative, content in files.items():
            p = root / relative
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding='utf-8')
    spec = {'format': 'unified-source-update-v1', 'version': '0.1.13',
            'base_versions': ['0.1.10', '0.1.11', '0.1.12', '0.1.13'],
            'lock_sha256': hashlib.sha256(b'same-lock').hexdigest(),
            'resources': {'bin/rg.exe': hashlib.sha256(b'resource').hexdigest()}}
    (update / 'update-metadata.json').write_text(json.dumps(spec), encoding='utf-8')
    manifest(base)
    manifest(update)
    return base, update


def prepare(base, update, destination):
    return import_module('scripts.prepare_update').prepare(base, update, destination)


def test_prepare_new_installation_preserves_old_and_removes_obsolete_code(tmp_path):
    base, update = fixtures(tmp_path)
    old = {p.relative_to(base): p.read_bytes() for p in base.rglob('*') if p.is_file()}
    result = prepare(base, update, tmp_path / 'new')
    assert result['version'] == '0.1.13'
    assert (tmp_path / 'new/resources/python/python.exe').read_text() == 'python'
    assert not (tmp_path / 'new/app/personal_mcp/removed.py').exists()
    assert old == {p.relative_to(base): p.read_bytes() for p in base.rglob('*') if p.is_file()}


@pytest.mark.parametrize('which', ['base', 'update'])
def test_tampering_is_rejected_before_destination_is_created(tmp_path, which):
    base, update = fixtures(tmp_path)
    ((base if which == 'base' else update) / 'app/run.py').write_text('tampered')
    with pytest.raises(ValueError, match='HASH_MISMATCH'):
        prepare(base, update, tmp_path / 'new')
    assert not (tmp_path / 'new').exists()


@pytest.mark.parametrize('relative', ['../escape.py', 'app/../escape.py', 'C:/escape.py', 'app\\escape.py'])
def test_manifest_path_escape_is_rejected(tmp_path, relative):
    base, update = fixtures(tmp_path)
    rows = json.loads((update / 'manifest-sha256.json').read_text())
    rows[relative] = '0' * 64
    (update / 'manifest-sha256.json').write_text(json.dumps(rows))
    with pytest.raises(ValueError, match='PATH_INVALID'):
        prepare(base, update, tmp_path / 'new')


@pytest.mark.parametrize('target', ['old', 'old/new', 'update/new'])
def test_overlap_or_existing_destination_is_rejected(tmp_path, target):
    base, update = fixtures(tmp_path)
    with pytest.raises(ValueError, match='DESTINATION|OVERLAP'):
        prepare(base, update, tmp_path / target)


def test_unsupported_baseline_and_dependency_change_are_rejected(tmp_path):
    base, update = fixtures(tmp_path)
    (base / 'package-metadata.json').write_text(json.dumps({'name': 'unified-personal-mcp', 'version': '0.1.9'}))
    manifest(base)
    with pytest.raises(ValueError, match='BASE_VERSION'):
        prepare(base, update, tmp_path / 'new')
    base, update = fixtures(tmp_path)
    (base / 'requirements-windows.lock').write_text('changed')
    manifest(base)
    with pytest.raises(ValueError, match='DEPENDENCY'):
        prepare(base, update, tmp_path / 'new')


def test_update_cannot_supply_runtime_binary_even_with_valid_hash(tmp_path):
    base, update = fixtures(tmp_path)
    (update / 'resources').mkdir()
    (update / 'resources/evil.exe').write_text('binary')
    manifest(update)
    with pytest.raises(ValueError, match='PAYLOAD'):
        prepare(base, update, tmp_path / 'new')


def test_extra_unmanifested_file_is_rejected(tmp_path):
    base, update = fixtures(tmp_path)
    (update / 'unlisted.txt').write_text('extra')
    with pytest.raises(ValueError, match='FILE_SET'):
        prepare(base, update, tmp_path / 'new')


def test_resource_change_is_rejected_even_when_base_manifest_is_updated(tmp_path):
    base, update = fixtures(tmp_path)
    (base / 'resources/bin/rg.exe').write_text('new-resource')
    manifest(base)
    with pytest.raises(ValueError, match='RESOURCE_COMPATIBILITY'):
        prepare(base, update, tmp_path / 'new')


def test_incomplete_payload_cannot_remove_existing_app(tmp_path):
    base, update = fixtures(tmp_path)
    (update / 'app/bf_automation/__init__.py').unlink()
    manifest(update)
    with pytest.raises(ValueError, match='UPDATE_INCOMPLETE'):
        prepare(base, update, tmp_path / 'new')
    assert (base / 'app/run.py').is_file()
    assert not (tmp_path / 'new').exists()


def test_symbolic_link_inside_input_is_rejected(tmp_path):
    base, update = fixtures(tmp_path)
    link = update / 'docs'
    try:
        link.symlink_to(base, target_is_directory=True)
    except OSError:
        pytest.skip('Windows 当前账号不允许创建符号链接')
    with pytest.raises(ValueError, match='LINK_PATH'):
        prepare(base, update, tmp_path / 'new')


def test_builder_publishes_only_source_payload_and_matching_hashes(tmp_path):
    build = import_module('scripts.build_update').build
    root = tmp_path / 'source-fixture'
    sources = {
        'personal_mcp/__init__.py': '__version__ = "0.1.13"',
        'bf_automation/__init__.py': '', 'coding_tools_mcp/__init__.py': '',
        'personal_mcp/__pycache__/stale.pyc': 'excluded bytecode',
        'packaging/run.py': 'pass', 'packaging/startup_diagnostics.py': 'pass',
        'packaging/update.cmd': 'update', 'packaging/startup-diagnostics.cmd': 'diagnostic',
        'scripts/prepare_update.py': 'pass', 'scripts/switch_managed_release.py': 'pass',
        'scripts/install-autostart.ps1': 'install', 'scripts/smoke_portable.py': 'pass',
        'LICENSE': 'license', 'NOTICE': 'notice', 'README.md': 'readme',
        'verification.md': 'verified', 'requirements-windows.lock': 'fixture-lock',
        'third_party/README.md': 'component provenance',
        'docs/personal/换机使用说明.txt': 'installation guidance',
        'examples/personal/settings.example.json': '{}',
        'resources/bin/rg.exe': 'fixture search binary',
        'resources/browsers/chromium-fixture/chrome.exe': 'fixture browser binary',
        'resources/browsers/chromium-fixture/debug.log': 'excluded runtime log',
        'resources/tunnel-client/tunnel-client.exe': 'fixture tunnel binary',
    }
    for relative, content in sources.items():
        source = root / relative
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(content, encoding='utf-8')
    result = build(tmp_path / 'source-update', root=root, zip_output=True)
    update = tmp_path / 'source-update'
    rows = import_module('scripts.prepare_update').validate_manifest(update)
    assert result['third_party_binaries'] is False
    assert (tmp_path / 'source-update.zip').is_file()
    assert all(import_module('scripts.prepare_update').payload_allowed(name) for name in rows)
    assert not any(name.startswith('resources/') or name.endswith(('.pyc', '.exe', '.dll', '.pyd')) for name in rows)
    assert all((update / f'app/{name}/__init__.py').is_file()
               for name in ('personal_mcp', 'bf_automation', 'coding_tools_mcp'))
    resources = json.loads((update / 'update-metadata.json').read_text())['resources']
    assert resources == {relative.removeprefix('resources/'): hashlib.sha256(content.encode()).hexdigest()
                         for relative, content in sources.items()
                         if relative.startswith('resources/') and not relative.endswith('debug.log')}


@pytest.mark.parametrize('task', [
    {'exists': False},
    {'exists': True, 'managed': False, 'bundle': 'old', 'config': 'config'},
    {'exists': True, 'managed': True, 'bundle': 'another', 'config': 'config'},
])
def test_switch_requires_existing_owned_task_targeting_the_verified_base(tmp_path, task):
    base, update = fixtures(tmp_path)
    result = prepare(base, update, tmp_path / 'new')
    with pytest.raises(ValueError, match='TASK'):
        import_module('scripts.prepare_update').switch_target(task, result)


def test_switch_refuses_missing_config_and_invalid_tool_receipt(tmp_path):
    base, update = fixtures(tmp_path)
    result = prepare(base, update, tmp_path / 'new')
    task = {'exists': True, 'managed': True, 'bundle': str(base)}
    with pytest.raises(ValueError, match='TASK'):
        import_module('scripts.prepare_update').switch_target(task, result)
