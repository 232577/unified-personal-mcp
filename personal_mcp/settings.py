"""Validated configuration saves and read-only installation checks."""

import json
import os
import shutil
import uuid
from dataclasses import replace
from pathlib import Path

from .config import load_config
from .protection import prepare_private_directory, read_tunnel_key
from .tunnel import assert_tunnel_available, verify_client


def save_settings(path, raw, *, key_import=None):
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.parent / (".settings-" + uuid.uuid4().hex + ".local.json")
    try:
        pending.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        cfg = load_config(pending)
        if key_import is not None:
            if cfg.tunnel_key_file is None:
                raise ValueError("KEY_IMPORT_REQUIRES_FILE_SOURCE")
            value = read_tunnel_key(replace(cfg, tunnel_key_file=Path(key_import).resolve(), tunnel_key_env=None))
            prepare_private_directory(cfg.data_root)
            cfg.tunnel_key_file.parent.mkdir(parents=True, exist_ok=True)
            key_pending = cfg.tunnel_key_file.with_name(".import-" + uuid.uuid4().hex + ".key")
            try:
                key_pending.write_text(value, encoding="utf-8")
                key_pending.replace(cfg.tunnel_key_file)
            finally:
                key_pending.unlink(missing_ok=True)
        pending.replace(path)
        return replace(cfg, path=path)
    finally:
        pending.unlink(missing_ok=True)


def doctor(path, *, assets_root=None):
    assets = Path(assets_root or Path(__file__).resolve().parents[1] / "resources")
    checks = []
    def check(name, action):
        try:
            if action() is False:
                raise ValueError("UNAVAILABLE")
            checks.append({"name": name, "ok": True, "code": "OK"})
        except Exception as exc:
            known = {"TUNNEL_ALREADY_IN_USE", "TUNNEL_OWNERSHIP_UNCERTAIN", "TUNNEL_KEY_UNAVAILABLE",
                     "TUNNEL_KEY_INVALID", "TUNNEL_CLIENT_HASH_MISMATCH"}
            checks.append({"name": name, "ok": False, "code": str(exc) if str(exc) in known else "UNAVAILABLE"})
    try:
        cfg = load_config(path)
    except Exception:
        return {"ok": False, "checks": [{"name": "安装配置", "ok": False, "code": "INVALID_CONFIGURATION"}]}
    check("Windows 系统", lambda: os.name == "nt")
    check("浏览器运行文件", lambda: any((assets / "browsers").glob("chromium-*/chrome-win*/chrome.exe")))
    check("代码搜索工具", lambda: (assets / "bin" / "rg.exe").is_file() or shutil.which("rg") is not None)
    check("OpenAI 隧道程序", lambda: verify_client(assets / "tunnel-client" / "tunnel-client.exe"))
    check("隧道运行密钥", lambda: bool(read_tunnel_key(cfg)))
    check("隧道可用性", lambda: assert_tunnel_available(cfg.tunnel_id))
    check("浏览器数据目录长度", lambda: len(str(cfg.data_root)) <= 105)
    return {"ok": all(row["ok"] for row in checks), "checks": checks}
