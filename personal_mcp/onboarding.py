"""First-device configuration and portable background launch."""

import re
import socket
import subprocess
from pathlib import Path

from . import __version__
from .autostart import validate_package
from .config import AppConfig, load_config
from .protection import prepare_private_directory, validate_tunnel_key
from .settings import save_settings


def configure_new_device(path, tunnel_id, key, *, full_control=True) -> AppConfig:
    """Create one installation from a tunnel ID and key, without replacing another."""
    if not isinstance(tunnel_id, str) or re.fullmatch(r"tunnel_[0-9a-f]{32}", tunnel_id) is None:
        raise ValueError("TUNNEL_ID_INVALID")
    key = validate_tunnel_key(key, file_source=True)
    path = Path(path).resolve()
    if path.exists():
        raise ValueError("CONFIGURATION_ALREADY_EXISTS")
    (path.parent / "projects" / "default").mkdir(parents=True, exist_ok=True)
    raw = {"schema_version": 1, "workspace_root": "projects", "data_root": "data",
           "host": "127.0.0.1", "port": 28776,
           "permission_mode": "full_control" if full_control else "trusted",
           "device_label": socket.gethostname().strip()[:100] or "My development PC",
           "workflow_idle_seconds": 1800, "browser_sessions": 8,
           "webview2_instances": 4, "search_sessions": 16,
           "tunnel": {"id": tunnel_id, "key_file": "data/tunnel.key"}}
    return save_settings(path, raw, key_value=key, create_only=True)


def launch_background_service(config: AppConfig, assets_root, *, connect_tunnel=True) -> subprocess.Popen:
    """Start a validated portable host; status is checked separately by the caller."""
    assets = Path(assets_root).resolve()
    bundle = assets.parent
    if assets != bundle / "resources" or not (bundle / "manifest-sha256.json").is_file():
        raise ValueError("PORTABLE_PACKAGE_REQUIRED")
    validate_package(bundle, __version__)
    current = load_config(config.path)
    if current != config:
        raise ValueError("CONFIGURATION_CHANGED")
    prepare_private_directory(config.data_root)
    argv = [str(bundle / "resources/python/pythonw.exe"), "-B", "-s",
            str(bundle / "app/run.py"), "serve", "--config", str(config.path),
            "--assets", str(assets)]
    if connect_tunnel:
        argv.append("--connect")
    log_path = config.data_root / "startup.log"
    with log_path.open("wb") as output:
        try:
            return subprocess.Popen(argv, cwd=bundle, stdin=subprocess.DEVNULL,
                                    stdout=output, stderr=output,
                                    creationflags=subprocess.DETACHED_PROCESS)
        except OSError:
            raise RuntimeError("BACKGROUND_START_FAILED") from None
