"""Load only explicit instruction roots without scanning adjacent projects."""

from __future__ import annotations

from pathlib import Path

from coding_tools_mcp.project_context import (
    CONTEXT_FILE_NAMES,
    MAX_CONTEXT_FILE_BYTES,
    MAX_ROOT_CONTEXT_BYTES,
    LoadedContextFile,
    ProjectContext,
    _decode_utf8_prefix,
)

NESTED_CONTEXT_WARNING = (
    "Nested project-context discovery disabled for umbrella workspace; "
    "read the target project's AGENTS.md/CLAUDE.md explicitly before modifications."
)


def load_workspace_context(root: Path) -> ProjectContext:
    resolved_root = root.expanduser().resolve(strict=True)
    loaded: list[LoadedContextFile] = []
    warnings: list[str] = []
    remaining = MAX_ROOT_CONTEXT_BYTES
    seen_paths: set[Path] = set()

    for name in sorted(CONTEXT_FILE_NAMES):
        path = resolved_root / name
        if not path.is_file():
            continue
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(resolved_root)
        except (OSError, ValueError):
            warnings.append(f"Skipped unsafe root instruction path: {name}")
            continue
        normalized = Path(str(resolved).casefold())
        if normalized in seen_paths:
            continue
        seen_paths.add(normalized)
        if remaining <= 0:
            warnings.append("Root instruction byte limit reached.")
            break
        budget = min(MAX_CONTEXT_FILE_BYTES, remaining)
        try:
            with resolved.open("rb") as handle:
                data = handle.read(budget + 1)
            content = _decode_utf8_prefix(data[:budget])
        except UnicodeDecodeError:
            warnings.append(f"Skipped non-UTF-8 instruction file: {name}")
            continue
        except OSError as exc:
            warnings.append(f"Could not read {name}: {exc}")
            continue
        truncated = len(data) > budget
        display_name = "AGENTS.md" if name.casefold() == "agents.md" else "CLAUDE.md"
        loaded.append(LoadedContextFile(display_name, content, truncated))
        remaining -= len(content.encode("utf-8"))

    warnings.append(NESTED_CONTEXT_WARNING)
    return ProjectContext(tuple(loaded), (), tuple(warnings))
