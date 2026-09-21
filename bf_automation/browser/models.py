"""Browser profile data shared by the future manager and its worker."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WebProfile:
    application_id: str
    entry_url: str
    navigation_origins: tuple[str, ...]
    resource_origins: tuple[str, ...]
    project_root: Path
    upload_roots: tuple[str, ...] = ()
