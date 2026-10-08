"""Pinned OpenAI Secure MCP Tunnel runner with isolated child credentials."""

import hashlib
import ipaddress
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import psutil
import yaml

from bf_automation.runtime import application_environment
from .protection import InstanceLock, prepare_private_directory, read_tunnel_key
from .tunnel_diagnostics import latest_poller_state
from .windows_jobs import OwnedJob

CLIENT_VERSION = "0.0.14+0f870e50a973fa820d4c409000059e181e8d242b"
CLIENT_SHA256 = "fcc85a69ec0ad82518e4f8964f60c45e31787957782a0fc9c1b0c44e82d61b9b"


class _NoHealthRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


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


def _explicit_http_proxy(value):
    if (not isinstance(value, str) or not value or any(c.isspace() for c in value)
            or any(c in value for c in '\\?#')):
        return None
    if '=' in value:
        entries = {}
        for entry in value.split(';'):
            name, separator, address = entry.partition('=')
            name = name.casefold()
            if not separator or name in entries:
                return None
            entries[name] = address
        value = entries.get('https', entries.get('http', ''))
    try:
        parsed = urllib.parse.urlsplit(value if '://' in value else 'http://' + value)
        if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or not parsed.port
                or parsed.username is not None or parsed.password is not None
                or parsed.path or parsed.query or parsed.fragment):
            return None
        host = parsed.hostname
        if ':' in host:
            host = '[' + str(ipaddress.IPv6Address(host)) + ']'
        elif len(host) > 253 or any(not re.fullmatch(
                r'[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?', label)
                for label in host.rstrip('.').split('.')):
            return None
        return f'{parsed.scheme}://{host}:{parsed.port}'
    except ValueError:
        return None


def system_http_proxy():
    """Read only the currently enabled, explicit WinInet HTTP proxy; never fetch PAC."""
    if sys.platform != 'win32':
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                r'Software\Microsoft\Windows\CurrentVersion\Internet Settings') as key:
            enabled, kind = winreg.QueryValueEx(key, 'ProxyEnable')
            if kind != winreg.REG_DWORD or type(enabled) is not int or enabled != 1:
                return None
            value, kind = winreg.QueryValueEx(key, 'ProxyServer')
            if kind != winreg.REG_SZ:
                return None
        return _explicit_http_proxy(value)
    except (ImportError, OSError, ValueError, TypeError):
        return None


def _connection_digest(cloud_key, http_proxy):
    # Length-prefix the key so changes in either setting cannot alias each other.
    key = cloud_key.encode('utf-8')
    return hashlib.sha256(len(key).to_bytes(8, 'big') + key + (http_proxy or '').encode('utf-8')).digest()


def tunnel_profile(config, *, http_proxy=None):
    control_plane = {"base_url": "https://api.openai.com", "tunnel_id": config.tunnel_id,
                     "api_key": "env:UPM_TUNNEL_RUN_KEY"}
    if http_proxy is not None:
        control_plane['http_proxy'] = http_proxy
    return {"config_version": 1,
        "control_plane": control_plane,
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
        self._credential_digest = None
        self._started_at = 0.0
        self._polling_state = None
        self.readiness_error = None

    def start(self):
        if self.process is not None:
            raise RuntimeError("TUNNEL_ALREADY_STARTED")
        binary = verify_client(self.binary)
        assert_tunnel_available(self.config.tunnel_id)
        self.lock = InstanceLock("tunnel:" + self.config.tunnel_id)
        try:
            prepare_private_directory(self.config.data_root)
            http_proxy = system_http_proxy()
            profile = self.config.data_root / "tunnel.local.yaml"
            profile.write_text(yaml.safe_dump(tunnel_profile(self.config, http_proxy=http_proxy)), encoding="utf-8")
            health = self.config.data_root / "tunnel-health.url"
            health.unlink(missing_ok=True)
            cloud_key = read_tunnel_key(self.config)
            env = tunnel_environment(cloud_key, self.backend_key)
            self._credential_digest = _connection_digest(cloud_key, http_proxy)
            self.job = OwnedJob()
            self._started_at = time.time()
            self._polling_state = None
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

    def credentials_changed(self):
        value = read_tunnel_key(self.config)
        return _connection_digest(value, system_http_proxy()) != self._credential_digest

    def ready(self):
        self.readiness_error = None
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
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoHealthRedirect())
            with opener.open(url.rstrip("/") + "/readyz", timeout=1) as response:
                if response.status != 200:
                    return False
            polling = latest_poller_state(self.config.data_root / 'tunnel.log', since=self._started_at)
            if polling is not None and (self._polling_state is None
                    or polling['observed_at'] >= self._polling_state['observed_at']):
                self._polling_state = polling
            if self._polling_state is not None and not self._polling_state['connected']:
                self.readiness_error = 'TUNNEL_POLL_FAILED'
                return False
            return True
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
