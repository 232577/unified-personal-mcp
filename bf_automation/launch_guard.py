"""Strict application-launch validation.

BF never launches data/source files through Windows file association.
"""

from __future__ import annotations

from pathlib import Path

EXECUTABLE_SUFFIXES = {".exe", ".com"}


def validate_application_name(name: str | None) -> str:
    """The Start Menu name route is not a path/URL/document launcher."""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("an installed application name is required")
    value = name.strip()
    if any(char in value for char in ("/", "\\", ":", "\x00", "\r", "\n")):
        raise ValueError("use an application name, not a path or URL")
    if Path(value).suffix.casefold() in {
        ".txt", ".md", ".json", ".html", ".htm", ".vue", ".py", ".js", ".ts",
        ".ps1", ".cmd", ".bat", ".url", ".lnk", ".exe", ".com", ".pdf", ".docx", ".xlsx",
    }:
        raise ValueError("use an application name; executable paths require launch_executable")
    return value


def validate_launch_target(path: str | Path) -> Path:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise ValueError(f"application executable does not exist: {resolved}")
    if resolved.suffix.casefold() not in EXECUTABLE_SUFFIXES:
        raise ValueError(f"not an application executable: {resolved}")
    return resolved
