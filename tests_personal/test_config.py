import json

import pytest

from personal_mcp.config import load_config


def installation(tmp_path, **overrides):
    root = tmp_path / "installation"
    root.mkdir(exist_ok=True)
    (root / "projects").mkdir(exist_ok=True)
    (root / "projects" / "app").mkdir(exist_ok=True)
    data = {"schema_version": 1, "workspace_root": "projects", "data_root": "private",
            "host": "127.0.0.1", "port": 28776, "permission_mode": "trusted",
            "tunnel": {"id": "tunnel_" + "a" * 32, "key_file": "private/tunnel.key"}}
    data.update(overrides)
    path = root / "settings.local.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_paths_are_relative_to_configuration_not_cwd(tmp_path, monkeypatch):
    path = installation(tmp_path)
    monkeypatch.chdir(tmp_path)
    cfg = load_config(path)
    assert cfg.workspace_root == path.parent / "projects"
    assert cfg.data_root == path.parent / "private"
    assert cfg.tunnel_key_file == path.parent / "private" / "tunnel.key"
    assert cfg.project("app") == path.parent / "projects" / "app"


@pytest.mark.parametrize("project", [".", "..", "../private"])
def test_umbrella_and_outside_projects_are_rejected(tmp_path, project):
    cfg = load_config(installation(tmp_path))
    with pytest.raises(ValueError):
        cfg.project(project)


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "example.com"])
def test_public_listener_configuration_is_rejected(tmp_path, host):
    with pytest.raises(ValueError, match="loopback"):
        load_config(installation(tmp_path, host=host))


def test_private_state_cannot_be_inside_a_project(tmp_path):
    with pytest.raises(ValueError, match="private"):
        load_config(installation(tmp_path, data_root="projects/app/state"))


def test_tunnel_identifier_and_plaintext_secret_are_validated(tmp_path):
    for tunnel in ({"id": "not-a-tunnel", "key_file": "private/tunnel.key"},
                   {"id": "tunnel_" + "a" * 32, "key": "do-not-store-inline"}):
        with pytest.raises(ValueError):
            load_config(installation(tmp_path, tunnel=tunnel))


def test_program_can_use_two_independent_installations(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    ca, cb = load_config(installation(a)), load_config(installation(b))
    assert ca.data_root != cb.data_root
    assert ca.project("app") != cb.project("app")
    assert not ca.data_root.exists(), "loading configuration must not create state"


def test_unknown_settings_do_not_silently_disable_protection(tmp_path):
    with pytest.raises(ValueError, match="unknown"):
        load_config(installation(tmp_path, disable_auth=True))


def test_tunnel_key_can_be_an_environment_reference(tmp_path, monkeypatch):
    monkeypatch.setenv("UPM_TEST_TUNNEL_KEY", "private-test-value")
    path = installation(tmp_path, tunnel={"id": "tunnel_" + "b" * 32,
                                          "key_env": "UPM_TEST_TUNNEL_KEY"})
    cfg = load_config(path)
    assert cfg.tunnel_key_env == "UPM_TEST_TUNNEL_KEY"
    assert cfg.tunnel_key_file is None
    assert "private-test-value" not in repr(cfg)
    assert "private-test-value" not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("reference", ["", "has space", "env:X", "X\nY", "9BAD"])
def test_environment_reference_is_a_variable_name(tmp_path, reference):
    with pytest.raises(ValueError):
        load_config(installation(tmp_path, tunnel={"id": "tunnel_" + "b" * 32,
                                                  "key_env": reference}))


def test_ambiguous_tunnel_key_sources_are_refused(tmp_path):
    with pytest.raises(ValueError):
        load_config(installation(tmp_path, tunnel={"id": "tunnel_" + "b" * 32,
            "key_env": "UPM_TEST_TUNNEL_KEY", "key_file": "private/tunnel.key"}))
