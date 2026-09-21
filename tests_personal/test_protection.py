import json
import os

import pytest

from personal_mcp.config import load_config
from personal_mcp.protection import InstanceLock, read_tunnel_key, prepare_private_directory
from tests_personal.test_config import installation


def test_secret_source_is_explicit_and_never_written_back(tmp_path, monkeypatch):
    path = installation(tmp_path, tunnel={"id": "tunnel_" + "a" * 32, "key_env": "UPM_FIXTURE_KEY"})
    cfg = load_config(path)
    monkeypatch.setenv("OPENAI_API_KEY", "unrelated-secret" * 3)
    monkeypatch.delenv("UPM_FIXTURE_KEY", raising=False)
    with pytest.raises(ValueError, match="TUNNEL_KEY_UNAVAILABLE"):
        read_tunnel_key(cfg)
    sentinel = "fixture-private-value-1234567890"
    monkeypatch.setenv("UPM_FIXTURE_KEY", sentinel)
    assert read_tunnel_key(cfg) == sentinel
    assert sentinel not in json.dumps(json.loads(path.read_text(encoding="utf-8")))


def test_file_key_is_bounded_and_invalid_secret_not_in_error(tmp_path):
    cfg = load_config(installation(tmp_path))
    prepare_private_directory(cfg.data_root)
    for text in ("private value\nwith injection" * 2, "x" * 16385):
        cfg.tunnel_key_file.write_text(text, encoding="utf-8")
        with pytest.raises(ValueError) as error:
            read_tunnel_key(cfg)
        assert text not in str(error.value)
    cfg.tunnel_key_file.write_text("fixture-secret-1234567890\n", encoding="utf-8")
    assert read_tunnel_key(cfg) == "fixture-secret-1234567890"


def test_installation_lock_rejects_second_host_and_releases(tmp_path):
    name = "test-" + str(tmp_path)
    first = InstanceLock(name)
    try:
        with pytest.raises(RuntimeError, match="INSTANCE_ALREADY_RUNNING"):
            InstanceLock(name)
    finally:
        first.close()
    second = InstanceLock(name)
    second.close()
    second.close()


def test_private_directory_disables_inherited_access(tmp_path):
    import win32security
    folder = tmp_path / "private"
    prepare_private_directory(folder)
    descriptor = win32security.GetFileSecurity(str(folder), win32security.DACL_SECURITY_INFORMATION)
    assert descriptor.GetSecurityDescriptorControl()[0] & win32security.SE_DACL_PROTECTED
    assert descriptor.GetSecurityDescriptorDacl().GetAceCount() == 3
    assert os.path.isdir(folder)
