"""Explicit per-installation configuration; loading has no side effects."""

import json
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppConfig:
    path: Path
    workspace_root: Path
    data_root: Path
    host: str
    port: int
    permission_mode: str
    tunnel_id: str
    tunnel_key_file: Path | None
    device_label: str = "My development PC"
    tunnel_key_env: str | None = None

    @property
    def full_control(self) -> bool:
        return self.permission_mode == "full_control"

    @property
    def runtime_permission_mode(self) -> str:
        return "dangerous" if self.full_control else self.permission_mode

    def project(self, value: str) -> Path:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("project must name an existing child directory")
        try:
            target = (self.workspace_root / value).resolve(strict=True)
        except OSError as exc:
            raise ValueError("project must name an existing directory") from exc
        if not target.is_dir():
            raise ValueError("project must name an existing directory")
        if not self.full_control and (target == self.workspace_root
                or not target.is_relative_to(self.workspace_root)):
            raise ValueError("project must be a strict child of workspace_root")
        return target


def _fields(value: dict, allowed: set[str], required: set[str]) -> None:
    if not isinstance(value, dict):
        raise ValueError("configuration must be an object")
    if unknown := value.keys() - allowed:
        raise ValueError(f"unknown settings: {', '.join(sorted(unknown))}")
    if missing := required - value.keys():
        raise ValueError(f"missing settings: {', '.join(sorted(missing))}")


def load_config(path: str | Path) -> AppConfig:
    path = Path(path).resolve(strict=True)
    raw = json.loads(path.read_text(encoding="utf-8"))
    required = {"schema_version", "workspace_root", "data_root", "tunnel"}
    _fields(raw, required | {"host", "port", "permission_mode", "device_label"}, required)
    if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
        raise ValueError("unsupported schema_version")

    def relative(value: str) -> Path:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("paths must be nonempty strings")
        return (path.parent / value).resolve()

    workspace = relative(raw["workspace_root"])
    private = relative(raw["data_root"])
    if not workspace.is_dir():
        raise ValueError("workspace_root must exist")
    if private.is_relative_to(workspace) or workspace.is_relative_to(private):
        raise ValueError("private data_root and workspace_root must not overlap")
    host = raw.get("host", "127.0.0.1")
    if host != "127.0.0.1":
        raise ValueError("only the IPv4 loopback listener 127.0.0.1 is supported")
    port = raw.get("port", 28776)
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535")
    mode = raw.get("permission_mode", "safe")
    if mode not in {"safe", "trusted", "full_control"}:
        raise ValueError("permission_mode must be safe, trusted or full_control")
    tunnel = raw["tunnel"]
    _fields(tunnel, {"id", "key_file", "key_env"}, {"id"})
    if ("key_file" in tunnel) == ("key_env" in tunnel):
        raise ValueError("configure exactly one tunnel key_file or key_env")
    if not isinstance(tunnel["id"], str) or not re.fullmatch(r"tunnel_[0-9a-f]{32}", tunnel["id"]):
        raise ValueError("invalid OpenAI tunnel id")
    key, key_env = None, tunnel.get("key_env")
    if "key_file" in tunnel:
        key = relative(tunnel["key_file"])
        if key == private or not key.is_relative_to(private):
            raise ValueError("tunnel key_file must be inside private data_root")
    elif not isinstance(key_env, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", key_env):
        raise ValueError("tunnel key_env must name an environment variable")
    label = raw.get("device_label", "My development PC")
    if not isinstance(label, str) or not 1 <= len(label.strip()) <= 100:
        raise ValueError("device_label must be 1 to 100 characters")
    return AppConfig(path, workspace, private, host, port, mode, tunnel["id"], key, label.strip(), key_env)
