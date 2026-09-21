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
