"""Command line and Windows configuration window entry point."""

import argparse
import json
import os
import time
from pathlib import Path


def default_config_path():
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "UnifiedPersonalMCP" / "settings.local.json"


def installation_smoke(args):
    """A short-lived local-only check executed by the package installer."""
    import urllib.request
    from . import __version__
    from .config import load_config
    from .service import LocalService

    if args.expected_version != __version__ or args.result is None or args.connect:
        raise ValueError("ISOLATED_CHECK_ARGUMENTS_INVALID")
    config = load_config(args.config)
    root = config.path.parent
    if (args.result.resolve().parent != root or config.data_root.parent != root
            or config.workspace_root.parent != root):
        raise ValueError("ISOLATED_CHECK_PATHS_INVALID")
    service = LocalService(config, assets_root=args.assets)
    try:
        service.start(connect_tunnel=False)
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}).encode()
        request = urllib.request.Request(f"http://127.0.0.1:{config.port}/mcp", body,
            {"Content-Type": "application/json", "Authorization": "Bearer " + service.runtime.auth_token})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=10) as response:
            tools = json.load(response)["result"]["tools"]
        if not {"server_info", "UnifiedTask"} <= {tool["name"] for tool in tools}:
            raise ValueError("ISOLATED_CATALOG_INVALID")
        result = {"ok": True, "version": __version__, "tools": len(tools), "tunnel_connected": False}
    finally:
        service.stop()
    args.result.write_text(json.dumps(result), encoding="utf-8")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Unified Personal MCP")
    parser.add_argument("action", choices=("gui", "serve", "doctor", "autostart-status", "autostart-enable",
                                         "autostart-disable", "tunnel-retry", "install-smoke"), nargs="?", default="gui")
    parser.add_argument("--config", type=Path, default=default_config_path())
    parser.add_argument("--assets", type=Path)
    parser.add_argument("--connect", action="store_true", help="Connect the configured OpenAI tunnel when serving")
    parser.add_argument("--bundle", type=Path, help="Complete portable package to use at next login")
    parser.add_argument("--expected-version", help="Required package version when enabling automatic startup")
    parser.add_argument("--task-name", default="UnifiedPersonalMCP-AutoConnect")
    parser.add_argument("--result", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.action == "install-smoke":
        return installation_smoke(args)
    if args.action.startswith("autostart-") or args.action == 'tunnel-retry':
        from .autostart import AutostartManager
        manager = AutostartManager(args.config, task_name=args.task_name)
        if args.action == "autostart-enable":
            if args.bundle is None or args.expected_version is None:
                parser.error("autostart-enable requires --bundle and --expected-version")
            result = manager.enable(args.bundle, expected_version=args.expected_version)
        elif args.action == "autostart-disable":
            result = manager.disable()
        elif args.action == 'tunnel-retry':
            result = manager.retry_tunnel()
        else:
            result = manager.status()
        print(json.dumps(result, ensure_ascii=False))
        return 0
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
