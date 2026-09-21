"""U1 origin policy must reject before a network action is dispatched."""

import importlib
import json
from pathlib import Path

import pytest


def policy():
    assert (Path(__file__).parents[2] / 'bf_automation/browser/policy.py').is_file()
    return importlib.import_module('bf_automation.browser.policy')


def profile(tmp_path):
    module = policy()
    return module.WebProfile(application_id='fixture', entry_url='http://127.0.0.1:65533/app',
                             navigation_origins=('http://127.0.0.1:65533',),
                             resource_origins=('http://127.0.0.1:65533',), project_root=tmp_path)


@pytest.mark.parametrize('url', [
    'http://127.0.0.1:65534/private', 'https://127.0.0.1:65533/app',
    'http://127.0.0.1:65533.evil.test/app', 'http://user:secret@127.0.0.1:65533/app',
    'file:///D:/run/secrets.txt', 'javascript:alert(1)', 'data:text/html,secret',
    'http://127.0.0.1:65533\\@evil.test/', 'http://127.0.0.1:65533/\nattack',
    'http://127.0.0.1:65533/%0d%0aInjected', 'http://127.0.0.1:99999/',
])
def test_navigation_denied(tmp_path, url):
    module = policy()
    with pytest.raises(module.PolicyError):
        module.validate_navigation(profile(tmp_path), url)


def test_normalization_is_origin_based_not_string_prefix(tmp_path):
    module = policy()
    assert module.origin('HTTPS://Example.COM:443/a') == 'https://example.com'
    assert module.validate_navigation(profile(tmp_path), 'http://127.0.0.1:65533/app?q=1')
    with pytest.raises(module.PolicyError):
        module.validate_navigation(profile(tmp_path), 'http://127.0.0.1:655330/app')


def test_redirect_and_resource_policy_are_separate(tmp_path):
    module = policy()
    p = profile(tmp_path)
    assert module.redirect_target(p, p.entry_url, '/app2', True).endswith('/app2')
    with pytest.raises(module.PolicyError):
        module.redirect_target(p, p.entry_url, '//127.0.0.1:65534/private', False)


def test_profile_loading_retains_project_boundary(tmp_path):
    module = policy()
    apps, project = tmp_path/'apps', tmp_path/'project'
    apps.mkdir()
    project.mkdir()
    row = {'version': 1, 'id': 'fixture', 'kind': 'web', 'project_root': str(project),
           'entry_url': 'http://127.0.0.1:65533/app',
           'navigation_origins': ['http://127.0.0.1:65533'],
           'resource_origins': ['http://127.0.0.1:65533']}
    path = apps/'fixture.json'
    path.write_text(json.dumps(row))
    loaded = module.load_web_profile(apps, 'fixture', tmp_path)
    assert loaded.project_root == project.resolve()
    with pytest.raises(module.PolicyError):
        module.load_web_profile(apps, '../fixture', tmp_path)
    for change in [{'project_root': str(tmp_path.parent)}, {'navigation_origins': ['*']},
                   {'id':'other'}, {'version':2}, {'unexpected':True}, {'kind':'desktop'}]:
        path.write_text(json.dumps({**row, **change}))
        with pytest.raises(module.PolicyError):
            module.load_web_profile(apps, 'fixture', tmp_path)
