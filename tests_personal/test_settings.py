import json
from pathlib import Path

import pytest

from personal_mcp.settings import save_settings, doctor
from tests_personal.test_config import installation


def test_invalid_settings_never_replace_working_configuration(tmp_path):
    path = installation(tmp_path)
    original = path.read_bytes()
    changed = json.loads(original)
    changed["host"] = "0.0.0.0"
    with pytest.raises(ValueError):
        save_settings(path, changed)
    assert path.read_bytes() == original


def test_imported_key_is_private_and_not_embedded_in_settings(tmp_path):
    path = installation(tmp_path)
    source = tmp_path / "provided.key"
    source.write_text("fixture-provided-key-123456789")
    cfg = save_settings(path, json.loads(path.read_text()), key_import=source)
    assert cfg.tunnel_key_file.read_text() == source.read_text()
    assert source.read_text() not in path.read_text()
    assert source.is_file()


def test_doctor_reports_missing_dependencies_without_starting_services(tmp_path):
    path = installation(tmp_path)
    result = doctor(path, assets_root=tmp_path / "missing")
    assert not result["ok"]
    assert all({"name", "ok", "code"} <= set(row) for row in result["checks"])
    assert not (path.parent / "private" / "backend.key").exists()
    assert not any(Path(path.parent).glob("**/service-status.json"))


@pytest.mark.parametrize('move_data', [False, True])
def test_save_refuses_in_use_installation_even_when_data_root_changes(tmp_path, move_data):
    from personal_mcp.protection import InstanceLock
    path = installation(tmp_path)
    original = path.read_bytes()
    raw = json.loads(original)
    raw['device_label'] = 'changed configuration'
    if move_data:
        raw['data_root'] = 'other-private'
        raw['tunnel']['key_file'] = 'other-private/tunnel.key'
    guard = InstanceLock('installation:' + str((path.parent / 'private').resolve()))
    try:
        with pytest.raises(RuntimeError, match='INSTANCE_ALREADY_RUNNING'):
            save_settings(path, raw)
        assert path.read_bytes() == original
    finally:
        guard.close()


def test_failed_configuration_replace_restores_imported_key(tmp_path, monkeypatch):
    path = installation(tmp_path)
    raw = json.loads(path.read_text())
    private = path.parent / 'private'
    private.mkdir()
    key = private / 'tunnel.key'
    key.write_text('fixture-previous-key-123456789')
    provided = tmp_path / 'provided.key'
    provided.write_text('fixture-replacement-key-123456789')
    original = path.read_bytes(), key.read_bytes()
    replace = Path.replace

    def fail_settings_replace(source, target):
        if Path(target) == path:
            raise PermissionError('fixture settings file locked')
        return replace(source, target)

    monkeypatch.setattr(Path, 'replace', fail_settings_replace)
    with pytest.raises(PermissionError):
        save_settings(path, raw, key_import=provided)
    assert (path.read_bytes(), key.read_bytes()) == original


def test_committed_settings_stay_successful_when_private_backup_cleanup_fails(tmp_path, monkeypatch):
    path = installation(tmp_path)
    raw = json.loads(path.read_text())
    raw['device_label'] = 'updated installation'
    private = path.parent / 'private'
    private.mkdir()
    key = private / 'tunnel.key'
    key.write_text('fixture-previous-key-123456789')
    provided = tmp_path / 'provided.key'
    provided.write_text('fixture-replacement-key-123456789')
    unlink = Path.unlink

    def locked_private_backup(target, *args, **kwargs):
        if target.name.startswith('.previous-'):
            raise PermissionError('fixture private backup locked')
        return unlink(target, *args, **kwargs)

    monkeypatch.setattr(Path, 'unlink', locked_private_backup)
    cfg = save_settings(path, raw, key_import=provided)
    assert cfg.device_label == 'updated installation'
    assert json.loads(path.read_text())['device_label'] == 'updated installation'
    assert key.read_bytes() == provided.read_bytes()
    assert len(list(private.glob('.previous-*.key'))) == 1
