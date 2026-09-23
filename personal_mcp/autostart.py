"""Validate a portable installation before changing this user's login task."""

import ast
import hashlib
import json
import os
import re
import socket
import subprocess
import tempfile
import time
import uuid
import urllib.request
from contextlib import contextmanager
from functools import wraps
from pathlib import Path, PurePosixPath

import psutil

from bf_automation.runtime import application_environment
from .config import load_config
from .protection import InstanceLock, prepare_private_directory
from .windows_jobs import OwnedJob

TASK_NAME = "UnifiedPersonalMCP-AutoConnect"
# Keep ownership compatible with the previously installed login task.
TASK_DESCRIPTION = "Unified Personal MCP automatic connection (managed by install-autostart.ps1)."
VERSION = re.compile(r"\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?")


class _NoControlRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Both credentials must stay on the original loopback endpoint.
        return None


def _read_json(path, limit=4 * 1024 * 1024):
    if path.stat().st_size > limit:
        raise ValueError("INSTALLATION_FILE_TOO_LARGE")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def package_version(bundle):
    """Read version literals without importing code from an unvalidated bundle."""
    path = Path(bundle) / "app/personal_mcp/__init__.py"
    if path.stat().st_size > 16384:
        raise ValueError("PACKAGE_VERSION_INVALID")
    module = ast.parse(path.read_text(encoding="utf-8-sig"))
    for statement in module.body:
        if isinstance(statement, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "__version__" for target in statement.targets):
            value = ast.literal_eval(statement.value)
            if isinstance(value, str) and VERSION.fullmatch(value):
                return value
    raise ValueError("PACKAGE_VERSION_INVALID")


def bundle_directory(assets_root=None):
    root = Path(assets_root).resolve().parent if assets_root else Path(__file__).resolve().parents[2]
    if not (root / "manifest-sha256.json").is_file():
        raise ValueError("PORTABLE_PACKAGE_REQUIRED")
    return root


def validate_package(bundle, expected_version):
    if not isinstance(expected_version, str) or not VERSION.fullmatch(expected_version):
        raise ValueError("PACKAGE_VERSION_REQUIRED")
    bundle = Path(bundle).resolve(strict=True)
    manifest_path = bundle / "manifest-sha256.json"
    try:
        manifest = _read_json(manifest_path)
        if not isinstance(manifest, dict) or not manifest or len(manifest) > 50000:
            raise ValueError("PACKAGE_MANIFEST_INVALID")
        names = set()
        for relative, expected in manifest.items():
            if (not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative
                    or relative.startswith("/") or any(p in {"", ".", ".."} for p in relative.split("/"))
                    or relative.casefold() == "manifest-sha256.json"
                    or relative.casefold() in names):
                raise ValueError("PACKAGE_MANIFEST_PATH_INVALID")
            if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
                raise ValueError("PACKAGE_MANIFEST_INVALID")
            names.add(relative.casefold())
            path = bundle.joinpath(*PurePosixPath(relative).parts)
            if not path.resolve(strict=True).is_relative_to(bundle) or not path.is_file():
                raise ValueError("PACKAGE_MANIFEST_PATH_INVALID")
            for part in (path, *path.parents):
                if part == bundle:
                    break
                if part.is_symlink() or part.is_junction():
                    raise ValueError("PACKAGE_MANIFEST_PATH_INVALID")
            with path.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                    raise ValueError("PACKAGE_HASH_MISMATCH")
        actual = {p.relative_to(bundle).as_posix().casefold() for p in bundle.rglob("*") if p.is_file()}
        if actual != names | {"manifest-sha256.json"}:
            raise ValueError("PACKAGE_FILE_SET_MISMATCH")
        required = {"unifiedpersonalmcp.exe", "resources/python/pythonw.exe", "resources/python/python.exe",
                    "app/run.py", "app/personal_mcp/__init__.py", "package-metadata.json",
                    "scripts/install-autostart.ps1"}
        if not required <= names:
            raise ValueError("PACKAGE_INCOMPLETE")
        metadata = _read_json(bundle / "package-metadata.json", 16384)
        if (metadata.get("name") != "unified-personal-mcp" or metadata.get("version") != expected_version
                or package_version(bundle) != expected_version):
            raise ValueError("PACKAGE_VERSION_MISMATCH")
    except (OSError, json.JSONDecodeError, SyntaxError, TypeError, AttributeError) as exc:
        raise ValueError("PACKAGE_INVALID") from exc
    return {"bundle": str(bundle), "version": expected_version, "files": len(manifest)}


def isolated_startup_check(bundle, config, expected_version):
    """Use no live project, credentials, task or tunnel; own the test child tree."""
    parent = config.data_root / "autostart" / "validation"
    prepare_private_directory(parent)
    with tempfile.TemporaryDirectory(prefix="startup-", dir=parent) as temporary:
        root = Path(temporary)
        workspace = root / "projects"
        workspace.mkdir()
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        raw = {"schema_version": 1, "workspace_root": str(workspace), "data_root": str(root / "data"),
               "host": "127.0.0.1", "port": port, "permission_mode": config.permission_mode,
               "device_label": "Installation check", "tunnel": {"id": "tunnel_" + "0" * 32,
                   "key_env": "UPM_INSTALLATION_TEST_KEY_UNUSED"},
               **{name: getattr(config, name) for name in ("workflow_idle_seconds", "browser_sessions",
                                                         "webview2_instances", "search_sessions")}}
        fixture, output = root / "settings.local.json", root / "result.json"
        fixture.write_text(json.dumps(raw), encoding="utf-8")
        job = OwnedJob()
        try:
            process = job.spawn([str(bundle / "resources/python/pythonw.exe"), "-B", "-s",
                str(bundle / "app/run.py"), "install-smoke", "--config", str(fixture), "--assets",
                str(bundle / "resources"), "--expected-version", expected_version, "--result", str(output)],
                cwd=bundle, env=application_environment(dict(os.environ)), stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if process.wait(timeout=120) != 0:
                raise RuntimeError("ISOLATED_START_FAILED")
            result = _read_json(output, 16384)
            if not result.get("ok") or result.get("version") != expected_version or result.get("tools", 0) < 1:
                raise RuntimeError("ISOLATED_START_FAILED")
            return result
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError("ISOLATED_START_FAILED") from exc
        finally:
            job.close()


def _same_path(left, right):
    return os.path.normcase(str(Path(left).resolve())) == os.path.normcase(str(Path(right).resolve()))


def service_process_status(saved, config):
    if not saved.get("running") or type(saved.get("pid")) is not int:
        return {"running": False}
    try:
        process = psutil.Process(saved["pid"])
        created = process.create_time()
        if float(saved.get("updated", 0)) < created - 2:
            return {"running": False}
        argv = process.cmdline()
        if not {'serve', 'gui'}.intersection(argv):
            return {"running": False}
        if '--config' in argv:
            index = argv.index('--config')
            if index + 1 >= len(argv):
                return {'running': False}
            config_path = argv[index + 1]
        else:
            from .__main__ import default_config_path
            config_path = next((value.split('=', 1)[1] for value in argv
                                if value.startswith('--config=')), default_config_path())
        if not _same_path(config_path, config.path):
            return {"running": False}
        version = saved.get("version")
        if not isinstance(version, str) or not VERSION.fullmatch(version):
            version = None
            for argument in argv:
                candidate = Path(argument)
                if candidate.name == "run.py" and candidate.parent.name == "app":
                    version = package_version(candidate.parent.parent)
                    break
        return {"running": True, "running_version": version, "pid": process.pid,
                "status_fresh": time.time() - float(saved.get("updated", 0)) < 30}
    except psutil.NoSuchProcess:
        return {"running": False}
    except (psutil.AccessDenied, OSError, ValueError, TypeError):
        return {"running": False, "running_unknown": True, "error": "SERVICE_STATUS_UNAVAILABLE"}


def _argv(command):
    import ctypes
    from ctypes import wintypes

    count = ctypes.c_int()
    split = ctypes.windll.shell32.CommandLineToArgvW
    split.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    split.restype = ctypes.POINTER(wintypes.LPWSTR)
    values = split("placeholder " + command, ctypes.byref(count))
    if not values:
        raise ValueError("AUTOSTART_ARGUMENTS_INVALID")
    try:
        return [values[index] for index in range(1, count.value)]
    finally:
        ctypes.windll.kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
        ctypes.windll.kernel32.LocalFree(values)


def _com_operation(function):
    """The operation's entire frame must release COM objects before CoUninitialize."""
    @wraps(function)
    def operation(*args, **kwargs):
        import pythoncom

        pythoncom.CoInitialize()
        failure = None
        try:
            result = function(*args, **kwargs)
        except Exception as exc:
            # Do not retain a traceback whose operation frame still owns COM proxies.
            failure = RuntimeError(str(exc))
        finally:
            pythoncom.CoUninitialize()
        if failure is not None:
            raise failure
        return result
    return operation


class WindowsTaskScheduler:
    @contextmanager
    def _folder(self):
        import win32com.client

        service = win32com.client.Dispatch("Schedule.Service")
        service.Connect()
        yield service, service.GetFolder("\\")

    @staticmethod
    def _sid():
        import win32api
        import win32security

        token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY)
        try:
            return win32security.ConvertSidToStringSid(win32security.GetTokenInformation(token, win32security.TokenUser)[0])
        finally:
            token.Close()

    def _is_current_user(self, principal):
        import pywintypes
        import win32security

        try:
            if principal.startswith("S-1-"):
                sid = win32security.ConvertStringSidToSid(principal)
            else:
                sid, _, _ = win32security.LookupAccountName(None, principal)
            return win32security.ConvertSidToStringSid(sid) == self._sid()
        except (pywintypes.error, AttributeError):
            return False

    @_com_operation
    def status(self, name):
        import pywintypes

        with self._folder() as (_, folder):
            try:
                task = folder.GetTask(name)
            except pywintypes.com_error as exc:
                if exc.hresult in {-2147024894, -2147216625}:
                    return {"exists": False, "enabled": False, "xml": None}
                raise RuntimeError("AUTOSTART_STATUS_UNAVAILABLE") from exc
            definition = task.Definition
            managed = (definition.RegistrationInfo.Description == TASK_DESCRIPTION
                       and self._is_current_user(definition.Principal.UserId))
            result = {"exists": True, "enabled": bool(task.Enabled), "xml": task.Xml,
                      "managed": managed, "state": int(task.State)}
            if managed and definition.Actions.Count == 1:
                action = definition.Actions.Item(1)
                argv = _argv(action.Arguments)
                bundle = Path(action.Path).parent.parent.parent
                if (_same_path(action.Path, bundle / "resources/python/pythonw.exe") and "--config" in argv):
                    index = argv.index("--config")
                    if index + 1 < len(argv):
                        result.update(bundle=str(bundle), config=argv[index + 1])
            return result

    @_com_operation
    def register(self, name, bundle, config):
        with self._folder() as (service, folder):
            definition = service.NewTask(0)
            definition.RegistrationInfo.Description = TASK_DESCRIPTION
            sid = self._sid()
            definition.Principal.UserId = sid
            definition.Principal.LogonType = 3  # Interactive token; no password, desktop remains accessible.
            definition.Principal.RunLevel = 0
            trigger = definition.Triggers.Create(9)
            trigger.UserId, trigger.Delay = sid, "PT20S"
            action = definition.Actions.Create(0)
            action.Path = str(bundle / "resources/python/pythonw.exe")
            action.Arguments = subprocess.list2cmdline(["-B", "-s", str(bundle / "app/run.py"), "serve",
                "--config", str(config), "--assets", str(bundle / "resources"), "--connect"])
            action.WorkingDirectory = str(bundle)
            settings = definition.Settings
            settings.Enabled, settings.StartWhenAvailable = True, True
            settings.MultipleInstances, settings.ExecutionTimeLimit = 2, "PT0S"
            settings.RestartCount, settings.RestartInterval = 999, "PT1M"
            settings.DisallowStartIfOnBatteries, settings.StopIfGoingOnBatteries = False, False
            registered = folder.RegisterTaskDefinition(name, definition, 6, sid, None, 3)
            return {"xml": str(registered.Xml)}

    @_com_operation
    def restore(self, name, previous):
        import pywintypes

        with self._folder() as (_, folder):
            if previous.get("exists"):
                folder.RegisterTask(name, previous["xml"], 6, self._sid(), None, 3)
            else:
                try:
                    folder.DeleteTask(name, 0)
                except pywintypes.com_error as exc:
                    if exc.hresult not in {-2147024894, -2147216625}:
                        raise

    @_com_operation
    def disable(self, name):
        with self._folder() as (_, folder):
            folder.GetTask(name).Enabled = False


class AutostartManager:
    def __init__(self, config_path, *, task_name=TASK_NAME, scheduler=None, smoke=None, process_probe=None):
        if not re.fullmatch(r"UnifiedPersonalMCP-[A-Za-z0-9_-]+", task_name):
            raise ValueError("AUTOSTART_TASK_NAME_INVALID")
        self.config = load_config(config_path)
        self.task_name = task_name
        self.scheduler = scheduler or WindowsTaskScheduler()
        self.smoke = smoke or isolated_startup_check
        self.process_probe = process_probe or service_process_status

    def status(self):
        task = self.scheduler.status(self.task_name)
        result = {"task_name": self.task_name, "enabled": bool(task.get("enabled")),
                  "managed": task.get("managed", not task.get("exists")),
                  "pending_version": None, "running_version": None, "running": False,
                  "tunnel_connected": False, "started_now": False}
        try:
            self._owned(task)
        except RuntimeError as exc:
            result.update(enabled=False, error=str(exc))
        if task.get("bundle") and "error" not in result:
            try:
                result["pending_version"] = package_version(task["bundle"])
                result["pending_bundle"] = task["bundle"]
            except (OSError, ValueError, SyntaxError):
                result["error"] = "AUTOSTART_TARGET_UNAVAILABLE"
        result.update(self._service_status())
        return result

    def _service_status(self):
        try:
            saved = _read_json(self.config.data_root / "service-status.json", 65536)
        except (OSError, ValueError):
            saved = {}
        if not isinstance(saved, dict):
            saved = {}
        result = self.process_probe(saved, self.config)
        if result.get("running"):
            connected = bool(saved.get("tunnel_connected")) if result.get("status_fresh", True) else None
            result.update(tools=saved.get("tools"), tunnel_connected=connected,
                          service_error=saved.get("error"))
            if result.get("status_fresh", True) and isinstance(saved.get("health"), dict):
                result["health"] = saved["health"]
            result['retry_available'] = (not result.get('running_unknown')
                and (self.config.data_root / 'control.key').is_file()
                and (self.config.data_root / 'backend.key').is_file())
            if not result['retry_available']:
                result['retry_error'] = 'LOCAL_RETRY_UNAVAILABLE'
        return result

    def retry_tunnel(self):
        service = self._service_status()
        if not service.get('running') or service.get('running_unknown'):
            raise RuntimeError('LOCAL_SERVICE_NOT_RUNNING')
        if not service.get('retry_available'):
            raise RuntimeError('LOCAL_RETRY_UNAVAILABLE')
        keys = []
        try:
            for name in ('backend.key', 'control.key'):
                path = self.config.data_root / name
                if path.stat().st_size > 1024:
                    raise ValueError('invalid credential')
                value = path.read_text(encoding='utf-8').strip()
                if (len(value) < 32 or not value.isascii()
                        or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value)):
                    raise ValueError('invalid credential')
                keys.append(value)
        except (OSError, ValueError):
            raise RuntimeError('LOCAL_CONTROL_KEY_INVALID') from None
        request_id = uuid.uuid4().hex
        body = json.dumps({'jsonrpc': '2.0', 'id': request_id,
                           'method': 'unified/tunnel/retry', 'params': {}}).encode('utf-8')
        request = urllib.request.Request(f'http://127.0.0.1:{self.config.port}/mcp', body,
            {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + keys[0],
             'X-Unified-Control-Key': keys[1]})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoControlRedirect())
        try:
            with opener.open(request, timeout=5) as response:
                payload = response.read(65537)
            if len(payload) > 65536:
                raise ValueError('invalid response')
            message = json.loads(payload)
            result = message.get('result')
            if (message.get('jsonrpc') != '2.0' or message.get('id') != request_id
                    or not isinstance(result, dict) or result.get('ok') is not True
                    or not isinstance(result.get('tunnel'), dict)):
                raise ValueError('invalid response')
        except (OSError, ValueError, AttributeError):
            raise RuntimeError('LOCAL_RETRY_FAILED') from None
        return {'ok': True, 'tunnel': result['tunnel']}

    def _owned(self, task):
        if task.get("exists") and not task.get("managed"):
            raise RuntimeError("AUTOSTART_TASK_NOT_OWNED")
        if task.get("config") and not _same_path(task["config"], self.config.path):
            raise RuntimeError("AUTOSTART_CONFIGURATION_CONFLICT")

    def _backup(self, previous):
        directory = self.config.data_root / "autostart" / "backups"
        prepare_private_directory(directory)
        target = directory / (time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8] + ".json")
        target.write_text(json.dumps({"task_name": self.task_name, "created": time.time(), "previous": previous},
                                   ensure_ascii=False, indent=2), encoding="utf-8")
        return target

    def enable(self, bundle, *, expected_version):
        bundle = Path(bundle).resolve(strict=True)
        guard = InstanceLock("autostart:" + self.task_name)
        try:
            previous = self.scheduler.status(self.task_name)
            self._owned(previous)
            validate_package(bundle, expected_version)
            checked = self.smoke(bundle, self.config, expected_version)
            if not checked.get("ok") or checked.get("tools", 0) < 1:
                raise RuntimeError("ISOLATED_START_FAILED")
            if checked.get("version") != expected_version:
                raise RuntimeError("ISOLATED_VERSION_MISMATCH")
            # Revalidate after executing the candidate so registration cannot silently
            # accept a package that changed during its startup check.
            validate_package(bundle, expected_version)
            current = self.scheduler.status(self.task_name)
            if current.get("xml") != previous.get("xml"):
                raise RuntimeError("AUTOSTART_CHANGED_DURING_VALIDATION")
            backup = self._backup(previous)
            receipt = None
            try:
                receipt = self.scheduler.register(self.task_name, bundle, self.config.path)
                installed = self.scheduler.status(self.task_name)
                if (not installed.get("enabled") or not installed.get("managed")
                        or not receipt or installed.get('xml') != receipt.get('xml')
                        or not _same_path(installed.get("bundle", ""), bundle)
                        or not _same_path(installed.get("config", ""), self.config.path)):
                    raise RuntimeError("AUTOSTART_REGISTRATION_MISMATCH")
            except Exception as exc:
                # A failed write or later status read can be ambiguous. Do not
                # replace another editor's update with our older backup.
                try:
                    latest = self.scheduler.status(self.task_name)
                except Exception:
                    raise RuntimeError('AUTOSTART_UPDATE_CONFLICT') from None
                if latest.get('xml') == previous.get('xml'):
                    raise RuntimeError('AUTOSTART_UPDATE_FAILED') from exc
                if (not receipt or not receipt.get('xml') or latest.get('xml') != receipt['xml']
                        or not latest.get('managed')
                        or not _same_path(latest.get('bundle', ''), bundle)
                        or not _same_path(latest.get('config', ''), self.config.path)):
                    raise RuntimeError('AUTOSTART_UPDATE_CONFLICT') from None
                try:
                    self.scheduler.restore(self.task_name, previous)
                except Exception as restore_error:
                    raise RuntimeError("AUTOSTART_ROLLBACK_FAILED") from restore_error
                raise RuntimeError("AUTOSTART_UPDATE_FAILED") from exc
            result = self.status()
            return {**result, "backup": str(backup), "validated_version": expected_version, "started_now": False}
        finally:
            guard.close()

    def disable(self):
        guard = InstanceLock("autostart:" + self.task_name)
        try:
            previous = self.scheduler.status(self.task_name)
            self._owned(previous)
            if previous.get("exists") and previous.get("enabled"):
                self._backup(previous)
                self.scheduler.disable(self.task_name)
            return self.status()
        finally:
            guard.close()
