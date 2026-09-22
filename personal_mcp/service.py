"""Start and stop one installation without touching another program's services."""

import http.server
import json
import os
import secrets
import sys
import threading
import time
from pathlib import Path

from coding_tools_mcp.protocol import jsonrpc_error, response_id
from coding_tools_mcp.server import MCPHandler
from . import __version__
from .bf.bootstrap import build_mcp
from .host import UnifiedRuntime
from .protection import InstanceLock, prepare_private_directory
from .tunnel_supervisor import TunnelSupervisor


class PersonalMCPHandler(MCPHandler):
    def handle_rpc(self, request, *, transport_protocol_version=None):
        if request.get('method') != 'unified/tunnel/retry' or 'id' not in request:
            return super().handle_rpc(request, transport_protocol_version=transport_protocol_version)
        request_id = response_id(request)
        # do_POST has already applied backend authentication and the complete
        # transport/envelope checks. This capability is never sent to the tunnel.
        credentials = self.headers.get_all('X-Unified-Control-Key') or []
        if (len(credentials) != 1 or not secrets.compare_digest(
                credentials[0].encode('utf-8'), self.server.control_key.encode('utf-8'))):
            return jsonrpc_error(request_id, -32001, 'LOCAL_CONTROL_DENIED')
        if request.get('params') != {}:
            return jsonrpc_error(request_id, -32602, 'LOCAL_CONTROL_PARAMS_MUST_BE_EMPTY_OBJECT')
        try:
            snapshot = self.server.tunnel_retry()
        except Exception:
            return jsonrpc_error(request_id, -32000, 'LOCAL_SERVICE_NOT_RUNNING')
        return {'jsonrpc': '2.0', 'id': request_id, 'result': {'ok': True, 'tunnel': snapshot}}


class ServiceHTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, runtime, *, control_key, tunnel_retry):
        self.runtime = runtime
        self.control_key, self.tunnel_retry = control_key, tunnel_retry
        super().__init__(address, PersonalMCPHandler)


class LocalService:
    def __init__(self, config, *, assets_root=None):
        self.config = config
        self.assets = Path(assets_root or Path(__file__).resolve().parents[1] / "resources").resolve()
        self.guard = self.runtime = self.server = self.tunnel = None
        self.http_thread = self.maintenance_thread = None
        self.stopping = threading.Event()
        self.failure = None
        self._tunnel_lock = threading.Lock()

    def start(self, *, connect_tunnel=False):
        if self.guard is not None:
            raise RuntimeError("INSTANCE_ALREADY_RUNNING")
        self.guard = InstanceLock("installation:" + str(self.config.data_root.resolve()))
        self.stopping.clear()
        self.failure = None
        try:
            prepare_private_directory(self.config.data_root)
            key_path = self.config.data_root / "backend.key"
            if not key_path.exists():
                key_path.write_text(secrets.token_urlsafe(48), encoding="utf-8")
            if key_path.stat().st_size > 1024:
                raise ValueError("BACKEND_KEY_INVALID")
            key = key_path.read_text(encoding="utf-8").strip()
            if len(key) < 32 or any(c.isspace() for c in key):
                raise ValueError("BACKEND_KEY_INVALID")
            control_path = self.config.data_root / 'control.key'
            if not control_path.exists():
                control_path.write_text(secrets.token_urlsafe(48), encoding='utf-8')
            if control_path.stat().st_size > 1024:
                raise ValueError('CONTROL_KEY_INVALID')
            control_key = control_path.read_text(encoding='utf-8').strip()
            if (len(control_key) < 32 or not control_key.isascii()
                    or any(c.isspace() or ord(c) < 32 for c in control_key)
                    or secrets.compare_digest(control_key, key)):
                raise ValueError('CONTROL_KEY_INVALID')
            python = self.assets / "python" / "python.exe"
            if not python.is_file():
                python = Path(sys.executable)
            browser_config = self.config.data_root / "browser.local.json"
            browser_config.write_text(json.dumps({"enabled": True, "version": 1, "mode": "headless",
                "max_sessions": self.config.browser_sessions, "max_pages_per_session": 4, "python": str(python),
                "browsers_path": str(self.assets / "browsers"),
                "actions_enabled": True, "transfers_enabled": True}), encoding="utf-8")
            bf = build_mcp(state_root=self.config.data_root / "bf", allowed_root=self.config.workspace_root,
                           apps_dir=self.config.data_root / "apps", browser_config_path=browser_config,
                           full_control=self.config.full_control,
                           max_hybrid_instances=self.config.webview2_instances)
            rg = self.assets / "bin" / "rg.exe"
            self.runtime = UnifiedRuntime(self.config, auth_token=key, bf_server=bf,
                                          rg_path=rg if rg.is_file() else None)
            self.runtime.health_provider = self.health_snapshot
            self.server = ServiceHTTPServer((self.config.host, self.config.port), self.runtime,
                control_key=control_key, tunnel_retry=self.connect_tunnel)
            self.http_thread = threading.Thread(target=self.server.serve_forever, daemon=True, name="personal-http")
            self.http_thread.start()
            if connect_tunnel:
                self.connect_tunnel()
            self._save_status()
            self.maintenance_thread = threading.Thread(target=self._maintain, daemon=True, name="personal-maintenance")
            self.maintenance_thread.start()
            return self.status()
        except BaseException:
            self.stop()
            raise

    def connect_tunnel(self):
        with self._tunnel_lock:
            if self.stopping.is_set() or self.runtime is None or self.server is None:
                raise RuntimeError("LOCAL_SERVICE_NOT_RUNNING")
            if self.tunnel is None:
                self.tunnel = TunnelSupervisor(self.config,
                    self.assets / "tunnel-client" / "tunnel-client.exe", self.runtime.auth_token)
                return self.tunnel.start()
            return self.tunnel.retry()

    def health_snapshot(self):
        tunnel = self.tunnel
        component = tunnel.snapshot() if tunnel else {
            'status': 'disabled', 'last_success': None, 'error_code': None,
            'recovery_attempts': 0, 'next_retry': None}
        running = bool(self.server and self.http_thread and self.http_thread.is_alive())
        status = component['status'] if component['status'] != 'disabled' else 'healthy'
        return {'status': status if running else 'stopped', 'tunnel': component}

    def status(self):
        health = self.health_snapshot()
        return {"running": bool(self.server and self.http_thread and self.http_thread.is_alive()),
                "device_label": self.config.device_label, "port": self.config.port,
                "tools": len(self.runtime.catalog) if self.runtime else 0,
                "tunnel_connected": health['tunnel']['status'] == 'healthy',
                "version": __version__, "health": health,
                "recovery": self.runtime.recovery_status if self.runtime else None,
                "error": self.failure}

    def _save_status(self):
        path = self.config.data_root / "service-status.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({**self.status(), "pid": os.getpid(), "updated": time.time()}), encoding="utf-8")
        temporary.replace(path)

    def _maintain(self):
        while not self.stopping.wait(1):
            try:
                self.runtime.registry.expire()
                self._save_status()
                if self.failure == "STATE_MAINTENANCE_FAILED":
                    self.failure = None
            except Exception as exc:
                self.failure = "STATE_MAINTENANCE_FAILED"
                try:
                    (self.config.data_root / 'maintenance-error.json').write_text(
                        json.dumps({'type': type(exc).__name__, 'time': time.time()}), encoding='utf-8')
                except OSError:
                    pass

    def stop(self):
        with self._tunnel_lock:
            self.stopping.set()
            tunnel = self.tunnel
        # Disable reconnect before any possibly slow workflow maintenance cleanup.
        errors = []
        if tunnel is not None:
            try:
                tunnel.close()
                self.tunnel = None
            except Exception as exc:
                errors.append(exc)
        if self.maintenance_thread is not None:
            self.maintenance_thread.join(timeout=3)
            if self.maintenance_thread.is_alive():
                raise RuntimeError("MAINTENANCE_SHUTDOWN_INCOMPLETE")
            self.maintenance_thread = None
        if self.server is not None:
            if self.http_thread and self.http_thread.is_alive():
                self.server.shutdown()
                self.http_thread.join(timeout=3)
            self.server.server_close()
            self.server = None
        if self.runtime is not None:
            try:
                self.runtime.close()
                self.runtime = None
            except Exception as exc:
                errors.append(exc)
        if errors:
            self.failure = "SERVICE_CLEANUP_INCOMPLETE"
            raise RuntimeError(self.failure) from errors[0]
        if self.guard is not None:
            try:
                self._save_status()
            except OSError:
                self.failure = "FINAL_STATUS_WRITE_FAILED"
            finally:
                self.guard.close()
                self.guard = None
