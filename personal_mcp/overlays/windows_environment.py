"""Child-only PATHEXT repair adapted from DesktopCommanderMCP 0.2.50.

Upstream: src/terminal-manager.ts, getRepairedPathExt, commit
a781f5a4b8cfebac6638bc6fcbd38fca6326be53 (MIT; see LICENSE).
Copyright (c) 2024-2025 Eduard Ruzga and Desktop Commander Contributors.
Adaptation: explicit already-filtered environment; never read process globals.
This isolated probe is not imported by the running coding service.
"""

from collections.abc import Mapping

_STANDARD_PATHEXT = ".COM;.EXE;.BAT;.CMD;.VBS;.VBE;.JS;.JSE;.WSF;.WSH;.MSC"


def repair_windows_pathext(filtered_env: Mapping[str, str]) -> dict[str, str]:
    """Preserve healthy custom policy; repair missing EXE only in a copy."""
    result = dict(filtered_env)
    current = None
    for key in list(result):
        if key.upper() == "PATHEXT":
            current = result.pop(key)
    extensions = [part.strip().upper() for part in (current or "").split(";") if part.strip()]
    if ".EXE" in extensions:
        result["PATHEXT"] = current
    else:
        result["PATHEXT"] = ";".join(dict.fromkeys([*_STANDARD_PATHEXT.split(";"), *extensions]))
    return result
