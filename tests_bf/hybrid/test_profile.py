import importlib.util
import json

import pytest


def test_hybrid_profile_module_exists():
    assert importlib.util.find_spec('bf_automation.hybrid.policy') is not None


def write_hybrid_profile(tmp_path, *, ownership='managed', debug_mode=None,
                         max_instances=2, reuse_existing=None):
    project = tmp_path / 'project'
    project.mkdir(exist_ok=True)
    (project / 'uploads').mkdir(exist_ok=True)
    executable = project / 'fixture.exe'
    executable.write_bytes(b'fixture')
    apps = tmp_path / 'apps'
    apps.mkdir(exist_ok=True)
    if debug_mode is None:
        debug_mode = 'managed-loopback' if ownership == 'managed' else 'existing-proven'
    if reuse_existing is None:
        reuse_existing = ownership == 'attached'
    row = {
        'version': 1,
        'id': 'u4a-fixture',
        'kind': 'hybrid-webview2',
        'adapter': 'hybrid-webview2',
        'project_root': str(project),
        'launcher': {
            'executable': str(executable),
            'args': ['--fixture'],
            'cwd': str(project),
            'reuse_existing': reuse_existing,
        },
        'window': {'title_contains': 'U4A Fixture'},
        'hybrid': {
            'engine': 'webview2',
            'ownership': ownership,
            'debug_mode': debug_mode,
            'max_instances': max_instances,
        },
        'web': {
            'navigation_origins': ['http://127.0.0.1:65533'],
            'resource_origins': ['http://127.0.0.1:65533'],
            'upload_roots': ['uploads'],
        },
    }
    path = apps / 'u4a-fixture.json'
    path.write_text(json.dumps(row), encoding='utf-8')
    return path, project


def mutate(path, section, field, value):
    row = json.loads(path.read_text(encoding='utf-8'))
    row[section][field] = value
    path.write_text(json.dumps(row), encoding='utf-8')


def test_managed_hybrid_profile_loads_strict_identity_policy(tmp_path):
    from bf_automation.hybrid.policy import load_browser_profile

    path, project = write_hybrid_profile(tmp_path)
    profile = load_browser_profile(path.parent, 'u4a-fixture', tmp_path)
    assert profile.kind == 'hybrid-webview2'
    assert profile.project_root == project.resolve()
    assert profile.launcher.executable == (project / 'fixture.exe').resolve()
    assert profile.launcher.args == ('--fixture',)
    assert profile.launcher.reuse_existing is False
    assert profile.hybrid.engine == 'webview2'
    assert profile.hybrid.ownership == 'managed'
    assert profile.hybrid.debug_mode == 'managed-loopback'
    assert profile.hybrid.max_instances == 2
    assert profile.web.navigation_origins == ('http://127.0.0.1:65533',)
    assert profile.web.resource_origins == ('http://127.0.0.1:65533',)
    assert profile.web.upload_roots == ('uploads',)
    assert profile.web.entry_url == 'http://127.0.0.1:65533/'


@pytest.mark.parametrize(('section', 'field', 'value'), [
    ('hybrid', 'engine', 'chromium'),
    ('hybrid', 'ownership', 'unknown'),
    ('hybrid', 'debug_mode', 'scan-ports'),
    ('hybrid', 'max_instances', 3),
    ('launcher', 'reuse_existing', True),
])
def test_invalid_managed_hybrid_modes_are_rejected(tmp_path, section, field, value):
    from bf_automation.hybrid.policy import HybridPolicyError, load_browser_profile

    path, _ = write_hybrid_profile(tmp_path)
    mutate(path, section, field, value)
    with pytest.raises(HybridPolicyError, match='INVALID_HYBRID_PROFILE'):
        load_browser_profile(path.parent, 'u4a-fixture', tmp_path)


def test_attached_profile_requires_reuse_existing_and_proven_mode(tmp_path):
    from bf_automation.hybrid.policy import HybridPolicyError, load_browser_profile

    path, _ = write_hybrid_profile(tmp_path, ownership='attached')
    profile = load_browser_profile(path.parent, 'u4a-fixture', tmp_path)
    assert profile.hybrid.ownership == 'attached'
    assert profile.hybrid.debug_mode == 'existing-proven'
    assert profile.launcher.reuse_existing is True

    mutate(path, 'launcher', 'reuse_existing', False)
    with pytest.raises(HybridPolicyError, match='INVALID_HYBRID_PROFILE'):
        load_browser_profile(path.parent, 'u4a-fixture', tmp_path)


def test_existing_web_profile_loader_is_unchanged(tmp_path):
    from bf_automation.browser.policy import load_web_profile
    from bf_automation.hybrid.policy import load_browser_profile

    project = tmp_path / 'project'
    project.mkdir()
    apps = tmp_path / 'apps'
    apps.mkdir()
    row = {
        'version': 1, 'id': 'web-fixture', 'kind': 'web',
        'project_root': str(project),
        'entry_url': 'http://127.0.0.1:65533/app',
        'navigation_origins': ['http://127.0.0.1:65533'],
        'resource_origins': ['http://127.0.0.1:65533'],
        'transfer': {'upload_roots': []},
    }
    (apps / 'web-fixture.json').write_text(json.dumps(row), encoding='utf-8')
    expected = load_web_profile(apps, 'web-fixture', tmp_path)
    actual = load_browser_profile(apps, 'web-fixture', tmp_path)
    assert actual == expected
