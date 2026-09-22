"""Pinned OpenAI Secure MCP Tunnel runner with isolated child credentials."""

import hashlib
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import psutil
import yaml

from bf_automation.runtime import application_environment
from .protection import InstanceLock, prepare_private_directory, read_tunnel_key
from .windows_jobs import OwnedJob

CLIENT_VERSION = "0.0.14+0f870e50a973fa820d4c409000059e181e8d242b"
CLIENT_SHA256 = "fcc85a69ec0ad82518e4f8964f60c45e31787957782a0fc9c1b0c44e82d61b9b"


def verify_client(binary):
    path = Path(binary).resolve(strict=True)
    with path.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    if digest != CLIENT_SHA256:
        raise ValueError("TUNNEL_CLIENT_HASH_MISMATCH")
    return path


def _option(argv, option):
    for i, arg in enumerate(argv):
        if arg.startswith(option + "="):
            return arg.split("=", 1)[1]
        if arg == option and i + 1 < len(argv):
            return argv[i + 1]
    return None


def assert_tunnel_available(tunnel_id, *, processes=None):
    for process in psutil.process_iter() if processes is None else processes:
        try:
            if process.name().casefold() not in {"tunnel-client", "tunnel-client.exe"}:
                continue
            argv = process.cmdline()
            if "run" not in argv:
                continue
            current = _option(argv, "--control-plane.tunnel-id")
            if not current:
                profile = _option(argv, "--profile-file") or _option(argv, "--config")
                if not profile:
                    raise ValueError("unresolved profile")
                path = Path(profile)
                if not path.is_absolute() or path.stat().st_size > 65536:
                    raise ValueError("unresolved profile")
                current = yaml.safe_load(path.read_text(encoding="utf-8-sig"))["control_plane"]["tunnel_id"]
            if current == tunnel_id:
                raise RuntimeError("TUNNEL_ALREADY_IN_USE")
        except psutil.NoSuchProcess:
            continue
        except (OSError, psutil.AccessDenied, ValueError, KeyError, TypeError, yaml.YAMLError):
            raise RuntimeError("TUNNEL_OWNERSHIP_UNCERTAIN") from None


def tunnel_profile(config):
    return {"config_version": 1,
        "control_plane": {"base_url": "https://api.openai.com", "tunnel_id": config.tunnel_id,
                          "api_key": "env:UPM_TUNNEL_RUN_KEY"},
        "health": {"listen_addr": "127.0.0.1:0", "url_file": str(config.data_root / "tunnel-health.url")},
        "admin_ui": {"open_browser": False}, "log": {"level": "info", "format": "json"},
        "mcp": {"extra_headers": {"Authorization": "env:UPM_BACKEND_AUTHORIZATION"},
                "discovery_extra_headers": {"Authorization": "env:UPM_BACKEND_AUTHORIZATION"},
                "server_urls": [{"channel": "main", "url": f"http://127.0.0.1:{config.port}/mcp"}]}}


def tunnel_environment(cloud_key, backend_key):
    env = application_environment(dict(os.environ))
    env["UPM_TUNNEL_RUN_KEY"] = cloud_key
    env["UPM_BACKEND_AUTHORIZATION"] = "Bearer " + backend_key
    return env


class TunnelRunner:
    def __init__(self, config, binary, backend_key):
        self.config, self.binary, self.backend_key = config, binary, backend_key
        self.job = self.lock = self.process = None

    def start(self):
        if self.process is not None:
            raise RuntimeError("TUNNEL_ALREADY_STARTED")
        binary = verify_client(self.binary)
        assert_tunnel_available(self.config.tunnel_id)
        self.lock = InstanceLock("tunnel:" + self.config.tunnel_id)
        try:
            prepare_private_directory(self.config.data_root)
            profile = self.config.data_root / "tunnel.local.yaml"
            profile.write_text(yaml.safe_dump(tunnel_profile(self.config)), encoding="utf-8")
            health = self.config.data_root / "tunnel-health.url"
            health.unlink(missing_ok=True)
            env = tunnel_environment(read_tunnel_key(self.config), self.backend_key)
            self.job = OwnedJob()
            self.process = self.job.spawn([str(binary), "run", "--profile-file", str(profile),
                "--mcp.extra-headers", "Authorization: env:UPM_BACKEND_AUTHORIZATION",
                "--mcp.discovery-extra-headers", "Authorization: env:UPM_BACKEND_AUTHORIZATION",
                "--health.url-file", str(health),
                "--log.file", str(self.config.data_root / "tunnel.log"),
                "--pid.file", str(self.config.data_root / "tunnel.pid")],
                env=env, cwd=self.config.data_root, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return {"started": True, "pid": self.process.pid}
        except BaseException:
            self.close()
            raise

    def is_alive(self):
        return self.process is not None and self.process.poll() is None

    def ready(self):
        if not self.is_alive():
            return False
        try:
            path = self.config.data_root / "tunnel-health.url"
            if path.stat().st_size > 256:
                return False
            url = path.read_text(encoding="utf-8").strip()
            parsed = urllib.parse.urlsplit(url)
            if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port
                    or parsed.username or parsed.password or parsed.path not in ("", "/")
                    or parsed.query or parsed.fragment):
                return False
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(url.rstrip("/") + "/readyz", timeout=1) as response:
                return response.status == 200
        except (OSError, ValueError, urllib.error.URLError):
            return False

    def close(self):
        if self.job is not None:
            self.job.close()
            self.job = None
        if self.process is not None:
            self.process.wait(timeout=3)
            self.process = None
        if self.lock is not None:
            self.lock.close()
            self.lock = None
