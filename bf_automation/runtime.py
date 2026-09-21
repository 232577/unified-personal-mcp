"""Portable BF defaults with no process-wide environment mutation."""

import os
import re
from pathlib import Path


def safe_upstream_tools():
    return ("App", "Click", "Clipboard", "DisplayInventory", "Move", "Notification",
            "Screenshot", "Scroll", "Shortcut", "Snapshot", "Type", "Wait", "WaitFor")


def _windows_user_folders():
    import win32api
    import win32profile
    import win32security

    token = win32security.OpenProcessToken(
        win32api.GetCurrentProcess(), win32security.TOKEN_QUERY | win32security.TOKEN_DUPLICATE)
    try:
        block = win32profile.CreateEnvironmentBlock(token, False)
        block = {key.upper(): value for key, value in block.items()}
        names = ("USERPROFILE", "LOCALAPPDATA", "APPDATA", "SystemDrive", "ProgramData")
        result = {name: str(block.get(name.upper()) or "") for name in names}
        result["SystemDrive"] = Path(win32api.GetWindowsDirectory()).drive
        result["ProgramData"] = re.sub(r"%SystemDrive%", lambda match: result["SystemDrive"],
                                        result["ProgramData"], flags=re.IGNORECASE)
    finally:
        token.Close()
    if not all(result.values()):
        raise RuntimeError("Windows standard user directories are unavailable")
    return result


def application_environment(base):
    allowed = {"SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "PATHEXT", "COMPUTERNAME",
               "PROCESSOR_ARCHITECTURE", "TEMP", "TMP", "LANG", "PYTHONUTF8"}
    result = {k: v for k, v in base.items() if k.upper() in allowed}
    if os.name == "nt":
        result.update(_windows_user_folders())
        result["HOME"] = result["USERPROFILE"]
    return result
