"""Command line and Windows configuration window entry point."""

import argparse
import json
import os
import time
from pathlib import Path


def default_config_path():
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "UnifiedPersonalMCP" / "settings.local.json"


def main():
    parser = argparse.ArgumentParser(description="Unified Personal MCP")
    parser.add_argument("action", choices=("gui", "serve", "doctor"), nargs="?", default="gui")
    parser.add_argument("--config", type=Path, default=default_config_path())
    parser.add_argument("--assets", type=Path)
    parser.add_argument("--connect", action="store_true", help="Connect the configured OpenAI tunnel when serving")
    args = parser.parse_args()
    if args.action == "gui":
        from .gui import run
        run(args.config, assets_root=args.assets)
        return 0
    if args.action == "doctor":
        from .settings import doctor
        result = doctor(args.config, assets_root=args.assets)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["ok"] else 1
    from .config import load_config
    from .service import LocalService
    service = LocalService(load_config(args.config), assets_root=args.assets)
    try:
        print(json.dumps(service.start(connect_tunnel=args.connect), ensure_ascii=False), flush=True)
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        return 0
    finally:
        service.stop()


if __name__ == "__main__":
    raise SystemExit(main())
