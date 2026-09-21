"""Attach only to an existing registered shell's proven WebView2 debug listener."""

import json
import re
import urllib.request
from pathlib import Path

import psutil


class ExistingWebViewResolver:
    def __init__(self, platform):
        self.platform = platform

    def find_window(self, profile):
        from ..window_control import NativeWinApi
        api = NativeWinApi()
        windows = []
        for process in psutil.process_iter(['pid', 'exe']):
            try:
                if not process.info['exe'] or Path(process.info['exe']).resolve() != profile.launcher.executable:
                    continue
                windows.extend(api.inventory(process_id=process.pid, title_filter=profile.title_contains))
            except (OSError, psutil.Error):
                continue
        return windows[0] if len(windows) == 1 else None

    @staticmethod
    def _command_line(pid):
        return psutil.Process(pid).cmdline()

    @staticmethod
    def _engine_version(port):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f'http://127.0.0.1:{port}/json/version', timeout=2) as response:
            raw = response.read(16385)
        if len(raw) > 16384:
            raise ValueError('DEBUG_VERSION_UNAVAILABLE')
        value = json.loads(raw)['Browser']
        if not isinstance(value, str) or not 1 <= len(value) <= 128:
            raise ValueError('DEBUG_VERSION_UNAVAILABLE')
        match = re.fullmatch(r'(?:Edg|Chrome|Chromium)/([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)', value)
        if match is None:
            raise ValueError('DEBUG_VERSION_UNAVAILABLE')
        return match.group(1)

    def resolve(self, identity, profile, window_record):
        if profile.hybrid.ownership != 'attached':
            return None
        try:
            if not identity.matches(self.platform.process_identity(identity.pid)):
                return None
            candidates = []
            for child in self.platform.descendants(identity.pid):
                if child.executable.name.casefold() != 'msedgewebview2.exe':
                    continue
                args = self._command_line(child.pid)
                values = {value.split('=', 1)[1] for value in args if value.startswith('--remote-debugging-port=')}
                values.update(args[i + 1] for i, value in enumerate(args[:-1]) if value == '--remote-debugging-port')
                if len(values) != 1:
                    continue
                value = values.pop()
                if not value.isascii() or not value.isdigit() or not 1 <= int(value) <= 65535:
                    continue
                port = int(value)
                if child.pid in self.platform.listener_pids(port) and child.matches(self.platform.process_identity(child.pid)):
                    candidates.append((child, port))
            if len(candidates) != 1:
                return None
            child, port = candidates[0]
            version = self._engine_version(port)
            if (not identity.matches(self.platform.process_identity(identity.pid))
                    or not child.matches(self.platform.process_identity(child.pid))):
                return None
            return {'endpoint': f'http://127.0.0.1:{port}', 'webview_pid': child.pid,
                    'webview_created': child.create_time, 'engine_version': version}
        except (OSError, psutil.Error, ValueError, KeyError, TypeError):
            return None
