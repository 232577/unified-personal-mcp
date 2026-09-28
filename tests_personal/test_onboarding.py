import json
import subprocess
from pathlib import Path

import pytest

from personal_mcp.config import load_config
from personal_mcp import __version__
from personal_mcp.onboarding import configure_new_device, launch_background_service
from personal_mcp.protection import read_tunnel_key
from personal_mcp.settings import save_settings
from tests_personal.test_autostart import packaged


TUNNEL_ID = "tunnel_" + "a" * 32
KEY = "fixture-new-device-secret-123456789"


@pytest.mark.parametrize("tunnel_id,key", [
    pytest.param("invalid", KEY, id="invalid-id"),
    pytest.param(TUNNEL_ID, "short", id="short-key"),
    pytest.param(TUNNEL_ID, "fixture-secret-with space-123456", id="key-with-space"),
    pytest.param(TUNNEL_ID, KEY + "\nline", id="key-with-newline"),
    pytest.param(TUNNEL_ID, "é" * 10000, id="key-exceeds-file-bytes"),
])
def test_invalid_first_submission_leaves_no_configuration_or_key(tmp_path, tunnel_id, key):
    path = tmp_path / "portable" / "settings.local.json"
    with pytest.raises(ValueError) as error:
        configure_new_device(path, tunnel_id, key)
    assert key not in str(error.value)
    assert not path.exists()
    assert not (path.parent / "data" / "tunnel.key").exists()


@pytest.mark.parametrize("full_control,mode", [(True, "full_control"), (False, "trusted")])
def test_first_device_is_ready_and_secret_stays_out_of_json(tmp_path, full_control, mode):
    path = tmp_path / "portable" / "settings.local.json"
    cfg = configure_new_device(path, TUNNEL_ID, KEY, full_control=full_control)
    loaded = load_config(path)
    assert cfg == loaded
    assert loaded.permission_mode == mode
    assert loaded.workspace_root == path.parent / "projects"
    assert (loaded.workspace_root / "default").is_dir()
    assert loaded.project("default") == loaded.workspace_root / "default"
    assert loaded.data_root == path.parent / "data"
    assert loaded.host == "127.0.0.1" and loaded.port == 28776
    assert (loaded.browser_sessions, loaded.webview2_instances, loaded.search_sessions,
            loaded.workflow_idle_seconds) == (8, 4, 16, 1800)
    assert read_tunnel_key(loaded) == KEY
    assert KEY not in path.read_text(encoding="utf-8")
    assert json.loads(path.read_text(encoding="utf-8"))["tunnel"]["key_file"] == "data/tunnel.key"


def test_existing_configuration_and_key_cannot_be_overwritten(tmp_path):
    path = tmp_path / "portable" / "settings.local.json"
    configure_new_device(path, TUNNEL_ID, KEY)
    before = path.read_bytes(), (path.parent / "data" / "tunnel.key").read_bytes()
    with pytest.raises(ValueError, match="CONFIGURATION_ALREADY_EXISTS"):
        configure_new_device(path, "tunnel_" + "b" * 32, "fixture-second-secret-123456789")
    assert (path.read_bytes(), (path.parent / "data" / "tunnel.key").read_bytes()) == before


def test_literal_key_rolls_back_when_configuration_commit_fails(tmp_path, monkeypatch):
    path = tmp_path / "portable" / "settings.local.json"
    path.parent.mkdir()
    (path.parent / "projects" / "default").mkdir(parents=True)
    raw = {"schema_version": 1, "workspace_root": "projects", "data_root": "data",
           "host": "127.0.0.1", "port": 28776, "permission_mode": "trusted",
           "tunnel": {"id": TUNNEL_ID, "key_file": "data/tunnel.key"}}
    link = __import__("os").link

    def fail_config_link(source, target, **kwargs):
        if Path(target) == path:
            raise PermissionError("fixture config locked")
        return link(source, target, **kwargs)

    monkeypatch.setattr("personal_mcp.settings.os.link", fail_config_link)
    with pytest.raises(PermissionError):
        save_settings(path, raw, key_value=KEY, create_only=True)
    assert not path.exists()
    assert not (path.parent / "data" / "tunnel.key").exists()


def test_key_import_and_literal_are_exclusive(tmp_path):
    path = tmp_path / "portable" / "settings.local.json"
    path.parent.mkdir()
    (path.parent / "projects" / "default").mkdir(parents=True)
    raw = {"schema_version": 1, "workspace_root": "projects", "data_root": "data",
           "tunnel": {"id": TUNNEL_ID, "key_file": "data/tunnel.key"}}
    source = path.parent / "source.key"
    source.write_text(KEY, encoding="utf-8")
    with pytest.raises(ValueError, match="KEY_SOURCE_CONFLICT"):
        save_settings(path, raw, key_import=source, key_value=KEY)
    assert not path.exists()
    assert not (path.parent / "data" / "tunnel.key").exists()


def test_racing_configuration_writer_is_not_replaced(tmp_path, monkeypatch):
    path = tmp_path / "portable" / "settings.local.json"
    path.parent.mkdir()
    (path.parent / "projects" / "default").mkdir(parents=True)
    raw = {"schema_version": 1, "workspace_root": "projects", "data_root": "data",
           "tunnel": {"id": TUNNEL_ID, "key_file": "data/tunnel.key"}}
    winner = b'{"already":"written"}'

    def raced_link(source, target, **kwargs):
        Path(target).write_bytes(winner)
        raise FileExistsError("fixture winner")

    monkeypatch.setattr("personal_mcp.settings.os.link", raced_link)
    with pytest.raises(ValueError, match="CONFIGURATION_ALREADY_EXISTS"):
        save_settings(path, raw, key_value=KEY, create_only=True)
    assert path.read_bytes() == winner
    assert not (path.parent / "data" / "tunnel.key").exists()


def test_background_launch_requires_complete_package(tmp_path):
    path = tmp_path / "settings.local.json"
    cfg = configure_new_device(path, TUNNEL_ID, KEY)
    with pytest.raises(ValueError, match="PORTABLE_PACKAGE_REQUIRED"):
        launch_background_service(cfg, tmp_path / "resources")


def test_background_launch_uses_portable_python_and_private_log(tmp_path, monkeypatch):
    bundle = packaged(tmp_path, version=__version__)
    path = tmp_path / "user" / "settings.local.json"
    cfg = configure_new_device(path, TUNNEL_ID, KEY)
    captured = {}
    sentinel = object()

    def fake_popen(argv, **kwargs):
        captured.update(argv=argv, options=kwargs)
        return sentinel

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    assert launch_background_service(cfg, bundle / "resources") is sentinel
    argv = captured["argv"]
    assert argv == [str(bundle / "resources/python/pythonw.exe"), "-B", "-s",
                    str(bundle / "app/run.py"), "serve", "--config", str(path),
                    "--assets", str(bundle / "resources"), "--connect"]
    assert KEY not in " ".join(argv)
    options = captured["options"]
    assert options["stdin"] == subprocess.DEVNULL
    assert options["creationflags"] & subprocess.DETACHED_PROCESS
    assert options["stdout"] is options["stderr"]
    assert Path(options["stdout"].name).parent == cfg.data_root
    assert KEY not in Path(options["stdout"].name).read_text(encoding="utf-8")


def test_background_launch_can_skip_tunnel_for_isolated_check(tmp_path, monkeypatch):
    bundle = packaged(tmp_path, version=__version__)
    cfg = configure_new_device(tmp_path / "user" / "settings.local.json", TUNNEL_ID, KEY)
    observed = {}
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kwargs: observed.update(argv=argv) or object())
    launch_background_service(cfg, bundle / "resources", connect_tunnel=False)
    assert "--connect" not in observed["argv"]


def test_background_spawn_error_cannot_echo_secret(tmp_path, monkeypatch):
    bundle = packaged(tmp_path, version=__version__)
    cfg = configure_new_device(tmp_path / "user" / "settings.local.json", TUNNEL_ID, KEY)

    def fail_spawn(*args, **kwargs):
        raise OSError(KEY)

    monkeypatch.setattr(subprocess, "Popen", fail_spawn)
    with pytest.raises(RuntimeError, match="BACKGROUND_START_FAILED") as error:
        launch_background_service(cfg, bundle / "resources", connect_tunnel=False)
    assert KEY not in str(error.value)

