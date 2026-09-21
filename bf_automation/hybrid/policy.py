"""Strict hybrid application profile loading."""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..browser.policy import (
    PolicyError,
    build_web_profile,
    load_web_profile,
    origin,
)
from ..hybrid.models import HybridLaunchProfile, HybridProfile, HybridSettings
from ..launch_guard import validate_launch_target


class HybridPolicyError(ValueError):
    """Hybrid profile errors never echo private endpoint or process details."""


def _profile_row(apps_dir: Path, application_id: str):
    if not isinstance(application_id, str) or not re.fullmatch(r'[a-z0-9_-]{1,64}', application_id):
        raise HybridPolicyError('INVALID_HYBRID_PROFILE')
    apps_dir = Path(apps_dir).resolve()
    path = apps_dir / (application_id + '.json')
    if not path.resolve().is_relative_to(apps_dir) or not path.is_file() or path.stat().st_size > 32768:
        raise HybridPolicyError('INVALID_HYBRID_PROFILE')
    try:
        row = json.loads(path.read_text(encoding='utf-8-sig'))
    except (ValueError, OSError):
        raise HybridPolicyError('INVALID_HYBRID_PROFILE') from None
    if not isinstance(row, dict) or row.get('id') != application_id:
        raise HybridPolicyError('INVALID_HYBRID_PROFILE')
    return row


def load_browser_profile(apps_dir: Path, application_id: str, allowed_root: Path):
    apps_dir = Path(apps_dir).resolve()
    row = _profile_row(apps_dir, application_id)
    if row.get('kind') == 'web':
        return load_web_profile(apps_dir, application_id, allowed_root)
    try:
        required = {'version', 'id', 'kind', 'adapter', 'project_root', 'launcher', 'window', 'hybrid', 'web'}
        if set(row) != required or row['version'] != 1 or row['kind'] != 'hybrid-webview2':
            raise ValueError()
        if row['adapter'] != 'hybrid-webview2':
            raise ValueError()
        launcher = row['launcher']
        if (not isinstance(launcher, dict)
                or set(launcher) != {'executable', 'args', 'cwd', 'reuse_existing'}):
            raise ValueError()
        executable = validate_launch_target(apps_dir / launcher['executable'])
        args = launcher['args']
        if (not isinstance(args, list) or len(args) > 64
                or any(not isinstance(value, str) or len(value) > 4096 for value in args)):
            raise ValueError()
        cwd = (apps_dir / launcher['cwd']).resolve(strict=True) if launcher['cwd'] is not None else None
        if cwd is not None and not cwd.is_dir():
            raise ValueError()
        if type(launcher['reuse_existing']) is not bool:
            raise ValueError()
        window = row['window']
        if (not isinstance(window, dict) or set(window) != {'title_contains'}
                or not isinstance(window['title_contains'], str)
                or not 1 <= len(window['title_contains']) <= 512):
            raise ValueError()
        hybrid = row['hybrid']
        if (not isinstance(hybrid, dict)
                or set(hybrid) != {'engine', 'ownership', 'debug_mode', 'max_instances'}
                or hybrid['engine'] != 'webview2'
                or hybrid['ownership'] not in {'managed', 'attached'}
                or type(hybrid['max_instances']) is not int or hybrid['max_instances'] != 2):
            raise ValueError()
        managed = hybrid['ownership'] == 'managed'
        if managed and (hybrid['debug_mode'] != 'managed-loopback' or launcher['reuse_existing']):
            raise ValueError()
        if not managed and (hybrid['debug_mode'] != 'existing-proven' or not launcher['reuse_existing']):
            raise ValueError()
        web = row['web']
        if (not isinstance(web, dict)
                or set(web) != {'navigation_origins', 'resource_origins', 'upload_roots'}):
            raise ValueError()
        navigation_origins = web['navigation_origins']
        if not isinstance(navigation_origins, list) or not navigation_origins:
            raise ValueError()
        entry_url = origin(navigation_origins[0]) + '/'
        web_profile = build_web_profile(
            application_id, apps_dir / row['project_root'], entry_url,
            navigation_origins, web['resource_origins'], web['upload_roots'], allowed_root,
        )
        return HybridProfile(
            application_id=application_id, kind='hybrid-webview2',
            project_root=web_profile.project_root,
            launcher=HybridLaunchProfile(executable, tuple(args), cwd, launcher['reuse_existing']),
            title_contains=window['title_contains'],
            hybrid=HybridSettings(
                engine='webview2', ownership=hybrid['ownership'],
                debug_mode=hybrid['debug_mode'], max_instances=2,
            ),
            web=web_profile,
        )
    except (ValueError, TypeError, OSError, KeyError, PolicyError):
        raise HybridPolicyError('INVALID_HYBRID_PROFILE') from None
