"""Explicit local filesystem access for the owner-selected full-control mode."""

import os
import shutil
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from coding_tools_mcp.project_context import ProjectContext
from coding_tools_mcp.server import (
    DEFAULT_EXCLUDED_NAMES,
    ResolvedPath,
    ToolFailure,
    Workspace,
    normalize_rel_display,
)


class FullControlWorkspace(Workspace):
    """Keep a stable task root while accepting explicit paths elsewhere."""

    def __init__(self, root):
        self.root = Path(root).expanduser().resolve(strict=True)
        if not self.root.is_dir():
            raise ToolFailure("INVALID_ARGUMENT", "Default directory must exist.", category="validation")
        self.git_path = shutil.which("git")
        self._scan_root = ContextVar("full_control_scan_root", default=self.root)

    def _candidate(self, raw_path):
        if not isinstance(raw_path, str) or not raw_path or "\x00" in raw_path:
            raise ToolFailure("INVALID_ARGUMENT", "Path must be a nonempty string without NUL.", category="validation")
        path = Path(raw_path)
        if os.name == "nt":
            if (path.drive or path.root) and not path.is_absolute():
                raise ToolFailure("INVALID_ARGUMENT", "Use a complete absolute path or a task-relative path.", category="validation")
            parts = path.parts[1:] if path.anchor else path.parts
            if any(any(char in part for char in '<>:"|?*') or any(ord(char) < 32 for char in part)
                   for part in parts):
                raise ToolFailure("INVALID_ARGUMENT", "Path contains invalid Windows filename characters.", category="validation")
            if any(os.path.isreserved(part) for part in parts if part not in {".", ".."}):
                raise ToolFailure("INVALID_ARGUMENT", "Path contains a reserved Windows filename.", category="validation")
        return path if path.is_absolute() else self.root / path

    def _resolve(self, raw_path, *, strict):
        candidate = self._candidate(raw_path)
        try:
            path = candidate.resolve(strict=strict)
            return ResolvedPath(normalize_rel_display(path, self.root), path, path.exists())
        except FileNotFoundError as exc:
            raise ToolFailure("NOT_FOUND", f"Path not found: {raw_path}", category="not_found") from exc
        except (OSError, ValueError, RuntimeError) as exc:
            raise ToolFailure("INVALID_ARGUMENT", "Path could not be resolved.", category="validation") from exc

    def resolve_existing(self, raw_path="."):
        return self._resolve(raw_path, strict=True)

    def resolve_for_write(self, raw_path):
        resolved = self._resolve(raw_path, strict=False)
        if resolved.path.is_dir():
            raise ToolFailure("INVALID_ARGUMENT", "Write target must be a file.", category="validation")
        return resolved

    def reject_write_symlink(self, raw_path):
        if self._candidate(raw_path).is_symlink():
            raise ToolFailure("SYMLINK_ESCAPE", "Writing through a file symlink is denied.", category="security")

    def is_safe_existing_path(self, path):
        try:
            return path.resolve(strict=True).exists()
        except (OSError, ValueError, RuntimeError):
            return False

    @contextmanager
    def scan(self, path):
        scope = path if path.is_dir() else path.parent
        token = self._scan_root.set(scope)
        try:
            yield
        finally:
            self._scan_root.reset(token)

    def is_ignored_path(self, path, *, include_hidden=False, include_ignored=False, git_ignored=None):
        scope = self._scan_root.get()
        try:
            parts = path.relative_to(scope).parts
        except ValueError:
            return True
        if not include_hidden and any(part.startswith(".") for part in parts):
            return True
        if not include_ignored and any(part in DEFAULT_EXCLUDED_NAMES for part in parts):
            return True
        if include_ignored:
            return False
        display = normalize_rel_display(path, self.root)
        ignored = git_ignored if git_ignored is not None else self.git_ignored_paths([display])
        return display in ignored

    def git_ignored_paths(self, rel_paths):
        scope = self._scan_root.get()
        if scope == self.root:
            return super().git_ignored_paths(rel_paths)
        mapping = {}
        for display in rel_paths:
            path = self.root / display
            try:
                mapping[path.relative_to(scope).as_posix()] = display
            except ValueError:
                continue
        # This read-only view scopes git's ignore lookup without changing the
        # workflow root, process cwd, or another concurrent request's scan.
        view = object.__new__(Workspace)
        view.root, view.git_path = scope, self.git_path
        return {mapping[path] for path in view.git_ignored_paths(list(mapping)) if path in mapping}


class FullControlProjectContext(ProjectContext):
    def server_instructions(self):
        instructions = super().server_instructions()
        return instructions.replace(
            "Use these tools only for coding operations inside the configured workspace.",
            "Full control is enabled by the device owner. The workflow project is the default directory, "
            "not a filesystem boundary: absolute local paths and parent-relative paths are allowed. "
            "Read the applicable AGENTS.md/CLAUDE.md rules for each target before editing it. "
            "External writes are not isolated by this workflow's project lease.",
            1,
        )
