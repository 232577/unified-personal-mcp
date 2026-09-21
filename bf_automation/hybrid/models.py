"""Immutable application-profile models for task-owned WebView2 hybrids."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..browser.models import WebProfile


@dataclass(frozen=True)
class HybridLaunchProfile:
    executable: Path
    args: tuple[str, ...]
    cwd: Path | None
    reuse_existing: bool


@dataclass(frozen=True)
class HybridSettings:
    engine: str
    ownership: str
    debug_mode: str
    max_instances: int


@dataclass(frozen=True)
class HybridProfile:
    application_id: str
    kind: str
    project_root: Path
    launcher: HybridLaunchProfile
    title_contains: str
    hybrid: HybridSettings
    web: WebProfile
