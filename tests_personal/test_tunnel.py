import json

import pytest

from personal_mcp.config import load_config
from personal_mcp.tunnel import assert_tunnel_available, tunnel_profile, tunnel_environment, verify_client
from tests_personal.test_config import installation


class ClientProcess:
    def __init__(self, profile):
        self.profile = profile

    def name(self):
        return "tunnel-client.exe"

    def cmdline(self):
        return ["tunnel-client.exe", "run", "--profile-file", str(self.profile)]


def test_existing_tunnel_is_refused_before_any_launch(tmp_path):
    cfg = load_config(installation(tmp_path))
    profile = tmp_path / "active.yaml"
    profile.write_text(json.dumps({"control_plane": {"tunnel_id": cfg.tunnel_id}}))
    with pytest.raises(RuntimeError, match="TUNNEL_ALREADY_IN_USE"):
        assert_tunnel_available(cfg.tunnel_id, processes=[ClientProcess(profile)])
    assert_tunnel_available("tunnel_" + "b" * 32, processes=[ClientProcess(profile)])


def test_unknown_running_client_is_not_assumed_safe(tmp_path):
    with pytest.raises(RuntimeError, match="TUNNEL_OWNERSHIP_UNCERTAIN"):
        assert_tunnel_available("tunnel_" + "b" * 32, processes=[ClientProcess(tmp_path / "absent")])


def test_profile_and_environment_separate_backend_and_cloud_credentials(tmp_path, monkeypatch):
    cfg = load_config(installation(tmp_path))
    monkeypatch.setenv("CLOUDFLARED_TUNNEL_TOKEN", "unrelated-cloudflared-value")
    monkeypatch.setenv("OPENAI_API_KEY", "unrelated-openai-value")
    monkeypatch.setenv("MCP_SERVER_URL", "url=http://unexpected.test")
    env = tunnel_environment("fixture-openai-key-123456789", "fixture-backend-key-123456789")
    profile = json.dumps(tunnel_profile(cfg))
    assert env["UPM_TUNNEL_RUN_KEY"] == "fixture-openai-key-123456789"
    assert env["UPM_BACKEND_AUTHORIZATION"] == "Bearer fixture-backend-key-123456789"
    assert "fixture-" not in profile
    assert "http://127.0.0.1:28776/mcp" in profile
    assert "unrelated" not in json.dumps(env)
    assert not ({"CLOUDFLARED_TUNNEL_TOKEN", "MCP_SERVER_URL", "OPENAI_API_KEY"} & env.keys())


def test_unpinned_client_cannot_run(tmp_path):
    binary = tmp_path / "tunnel-client.exe"
    binary.write_bytes(b"unexpected binary")
    with pytest.raises(ValueError, match="TUNNEL_CLIENT_HASH_MISMATCH"):
        verify_client(binary)
