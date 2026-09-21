"""Private local state and explicit credentials for the user's Windows account."""

import hashlib
import os
from pathlib import Path

import win32api
import win32event
import win32security


class InstanceLock:
    def __init__(self, identity):
        name = "Local\\UnifiedPersonalMCP-" + hashlib.sha256(str(identity).casefold().encode()).hexdigest()
        self.handle = win32event.CreateMutex(None, False, name)
        if win32api.GetLastError() == 183:
            self.close()
            raise RuntimeError("INSTANCE_ALREADY_RUNNING")

    def close(self):
        if self.handle is not None:
            self.handle.Close()
            self.handle = None


def prepare_private_directory(path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY)
    try:
        user = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    finally:
        token.Close()
    acl = win32security.ACL()
    for sid in (user, win32security.CreateWellKnownSid(win32security.WinLocalSystemSid),
                win32security.CreateWellKnownSid(win32security.WinBuiltinAdministratorsSid)):
        acl.AddAccessAllowedAceEx(win32security.ACL_REVISION_DS,
            win32security.CONTAINER_INHERIT_ACE | win32security.OBJECT_INHERIT_ACE, 0x1F01FF, sid)
    win32security.SetNamedSecurityInfo(str(path), win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
        None, None, acl, None)


def _environment_value(name):
    if name in os.environ:
        return os.environ[name]
    # A GUI launched before setx may have an older environment. Read only the
    # explicitly configured variable; never enumerate or import other secrets.
    import winreg
    for hive, path in ((winreg.HKEY_CURRENT_USER, "Environment"),
                       (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
        try:
            with winreg.OpenKey(hive, path) as key:
                value, kind = winreg.QueryValueEx(key, name)
            if kind in (winreg.REG_SZ, winreg.REG_EXPAND_SZ):
                return value
        except FileNotFoundError:
            continue
    raise ValueError("TUNNEL_KEY_UNAVAILABLE")


def read_tunnel_key(config):
    try:
        if config.tunnel_key_env is not None:
            value = _environment_value(config.tunnel_key_env)
        else:
            with config.tunnel_key_file.open("rb") as source:
                value = source.read(16385)
            if len(value) > 16384:
                raise ValueError("TUNNEL_KEY_INVALID")
            value = value.decode("utf-8-sig")
        value = value.strip()
        if not 20 <= len(value) <= 16384 or any(c.isspace() or ord(c) < 32 for c in value):
            raise ValueError("TUNNEL_KEY_INVALID")
        return value
    except (OSError, UnicodeError, AttributeError):
        raise ValueError("TUNNEL_KEY_UNAVAILABLE") from None
