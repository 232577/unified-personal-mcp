"""Validated configuration saves and read-only installation checks."""

import json
import os
import shutil
import uuid
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path

from .config import load_config
from .protection import InstanceLock, prepare_private_directory, read_tunnel_key
from .tunnel import assert_tunnel_available, verify_client


def save_settings(path, raw, *, key_import=None):
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.parent / (".settings-" + uuid.uuid4().hex + ".local.json")
    with ExitStack() as guards:
        guards.callback(InstanceLock('settings:' + str(path)).close)
        try:
            pending.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
            cfg = load_config(pending)
            roots = {cfg.data_root}
            if path.is_file():
                try:
                    roots.add(load_config(path).data_root)
                except (ValueError, OSError):
                    pass  # A stopped installation can replace an invalid configuration.
            for root in sorted(roots):
                guards.callback(InstanceLock('installation:' + str(root.resolve())).close)
            return _save_validated(path, pending, cfg, key_import)
        finally:
            pending.unlink(missing_ok=True)


def _save_validated(path, pending, cfg, key_import):
    key_backup = key_pending = None
    imported = False
    try:
        if key_import is not None:
            if cfg.tunnel_key_file is None:
                raise ValueError("KEY_IMPORT_REQUIRES_FILE_SOURCE")
            value = read_tunnel_key(replace(cfg, tunnel_key_file=Path(key_import).resolve(), tunnel_key_env=None))
            prepare_private_directory(cfg.data_root)
            cfg.tunnel_key_file.parent.mkdir(parents=True, exist_ok=True)
            key_pending = cfg.tunnel_key_file.with_name(".import-" + uuid.uuid4().hex + ".key")
            key_pending.write_text(value, encoding="utf-8")
            if cfg.tunnel_key_file.exists():
                key_backup = cfg.tunnel_key_file.with_name('.previous-' + uuid.uuid4().hex + '.key')
                cfg.tunnel_key_file.replace(key_backup)
            key_pending.replace(cfg.tunnel_key_file)
            imported = True
        pending.replace(path)
    except BaseException:
        if key_backup is not None and key_backup.exists():
            key_backup.replace(cfg.tunnel_key_file)
        elif imported:
            cfg.tunnel_key_file.unlink(missing_ok=True)
        raise
    finally:
        if key_pending is not None:
            key_pending.unlink(missing_ok=True)
    if key_backup is not None:
        try:
            key_backup.unlink(missing_ok=True)
        except OSError:
            pass  # Both files are committed; a private backup can be removed later.
    return replace(cfg, path=path)


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
