"""Application profiles used by semantic launch."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .launch_guard import validate_launch_target


def project_profiles(store, token, fallback, app_id):
    if not isinstance(app_id, str) or not re.fullmatch(r'[a-z0-9_-]{1,64}', app_id):
        raise ValueError('INVALID_APPLICATION_ID')
    _, task = store.require(token)
    project = Path(task['project_path']).resolve()
    directory = project / '.bf' / 'apps'
    candidate = directory / (app_id + '.json')
    if not directory.resolve().is_relative_to(project) or not candidate.resolve().is_relative_to(project):
        raise ValueError('PROFILE_PATH_OUTSIDE_PROJECT')
    return directory if candidate.is_file() else Path(fallback).resolve()


@dataclass(frozen=True)
class ApplicationProfile:
    app_id: str
    kind: str
    adapter: str
    executable: Path
    args: list[str]
    cwd: Path | None
    title_contains: str | None
    reuse_existing: bool = False


class ApplicationRegistry:
    def __init__(self, apps_dir: str | Path):
        self.apps_dir = Path(apps_dir).resolve()

    def get(self, app_id: str) -> ApplicationProfile:
        if not isinstance(app_id, str) or not re.fullmatch(r'[a-z0-9_-]{1,64}', app_id):
            raise ValueError("invalid application id")
        path = self.apps_dir / f"{app_id}.json"
        if not path.is_file() or not path.resolve().is_relative_to(self.apps_dir) or path.stat().st_size > 32768:
            raise KeyError(f"unknown application: {app_id}")
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        if raw.get("id") != app_id:
            raise ValueError("application profile id does not match filename")
        launcher = raw.get("launcher") or {}
        reuse_existing = launcher.get("reuse_existing", False)
        if not isinstance(reuse_existing, bool):
            raise ValueError("launcher.reuse_existing must be a boolean")
        executable = validate_launch_target(self.apps_dir / launcher.get("executable", ""))
        args = launcher.get("args") or []
        if not isinstance(args, list) or not all(isinstance(value, str) for value in args):
            raise ValueError("launcher.args must be a list of strings")
        cwd_value = launcher.get("cwd")
        cwd = (self.apps_dir / cwd_value).resolve() if cwd_value else None
        if cwd is not None and not cwd.is_dir():
            raise ValueError(f"launcher cwd does not exist: {cwd}")
        window = raw.get("window") or {}
        title_contains = window.get("title_contains")
        if title_contains is not None and not isinstance(title_contains, str):
            raise ValueError("window.title_contains must be a string")
        return ApplicationProfile(
            app_id=app_id,
            kind=str(raw.get("kind") or "desktop"),
            adapter=str(raw.get("adapter") or "window"),
            executable=executable,
            args=list(args),
            cwd=cwd,
            title_contains=title_contains,
            reuse_existing=reuse_existing,
        )
