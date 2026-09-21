"""Strict origin allowlists. This is application policy, not an OS network sandbox."""

from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath
from urllib.parse import urljoin, urlsplit, urlunsplit

from ..browser.models import WebProfile


class PolicyError(ValueError):
    """No input URLs or credentials are echoed in policy errors."""


def _parts(url: str):
    if (not isinstance(url, str) or not url or len(url) > 8192 or '\\' in url
            or any(ord(c) < 33 or ord(c) == 127 for c in url)
            or re.search(r'%(?:0[0-9a-f]|1[0-9a-f]|7f)', url, re.I)):
        raise PolicyError('INVALID_URL')
    try:
        p = urlsplit(url)
        if p.scheme not in {'http', 'https'} or not p.hostname or p.username is not None or p.password is not None:
            raise ValueError()
        host = p.hostname.encode('idna').decode('ascii').lower()
        if '%' in host or host.endswith('.'):
            raise ValueError()
        port = p.port
        if port is not None and not 1 <= port <= 65535:
            raise ValueError()
    except (ValueError, UnicodeError):
        raise PolicyError('INVALID_URL') from None
    return p, host, port


def origin(url: str) -> str:
    p, host, port = _parts(url)
    if ':' in host:
        host = '[' + host + ']'
    if port is not None and port != (443 if p.scheme == 'https' else 80):
        host += ':' + str(port)
    return p.scheme + '://' + host


def _validate(profile: WebProfile, url: str, navigation: bool) -> str:
    destination = origin(url)
    permitted = profile.navigation_origins if navigation else profile.resource_origins
    if destination not in permitted:
        raise PolicyError('ORIGIN_DENIED')
    p, _, _ = _parts(url)
    return urlunsplit((p.scheme, destination.split('://', 1)[1], p.path or '/', p.query, p.fragment))


def validate_navigation(profile: WebProfile, url: str) -> str:
    return _validate(profile, url, True)


def validate_resource(profile: WebProfile, url: str) -> str:
    return _validate(profile, url, False)


def redirect_target(profile: WebProfile, current: str, location: str, navigation: bool) -> str:
    # Validate raw Location before urljoin can discard controls or normalize backslashes.
    if not isinstance(location, str) or '\\' in location or any(ord(c) < 33 for c in location):
        raise PolicyError('INVALID_REDIRECT')
    return _validate(profile, urljoin(current, location), navigation)


def build_web_profile(application_id: str, project_root, entry_url: str,
                      navigation_origins, resource_origins, upload_roots,
                      allowed_root: Path) -> WebProfile:
    """Validate reusable web-policy fields without reading an application file."""
    if not isinstance(application_id, str) or not re.fullmatch(r'[a-z0-9_-]{1,64}', application_id):
        raise PolicyError('INVALID_APPLICATION_ID')
    try:
        allowed_root = Path(allowed_root).resolve()
        project = Path(project_root).resolve(strict=True)
        if not project.is_dir() or project == allowed_root or not project.is_relative_to(allowed_root):
            raise ValueError()
        origins = []
        for values in (navigation_origins, resource_origins):
            if not isinstance(values, list) or not 1 <= len(values) <= 32:
                raise ValueError()
            normalized = []
            for value in values:
                canonical = origin(value)
                p, _, _ = _parts(value)
                if p.path not in {'', '/'} or p.query or p.fragment:
                    raise ValueError()
                normalized.append(canonical)
            origins.append(tuple(sorted(set(normalized))))
        if not isinstance(upload_roots, list) or len(upload_roots) > 16:
            raise ValueError()
        normalized_uploads = []
        for value in upload_roots:
            if (not isinstance(value, str) or not value or len(value) > 512 or '\\' in value
                    or ':' in value or any(ord(c) < 32 for c in value)):
                raise ValueError()
            relative = PurePosixPath(value)
            if (relative.is_absolute() or value == '.' or '..' in relative.parts
                    or any(part.startswith('.') for part in relative.parts)):
                raise ValueError()
            normalized_uploads.append(relative.as_posix())
        profile = WebProfile(application_id, entry_url, origins[0], origins[1], project,
                             tuple(sorted(set(normalized_uploads))))
        validate_navigation(profile, profile.entry_url)
        return profile
    except (ValueError, TypeError, OSError, KeyError):
        raise PolicyError('INVALID_WEB_PROFILE') from None


def load_web_profile(apps_dir: Path, application_id: str, allowed_root: Path) -> WebProfile:
    if not isinstance(application_id, str) or not re.fullmatch(r'[a-z0-9_-]{1,64}', application_id):
        raise PolicyError('INVALID_APPLICATION_ID')
    apps_dir, allowed_root = Path(apps_dir).resolve(), Path(allowed_root).resolve()
    path = apps_dir / (application_id + '.json')
    if not path.resolve().is_relative_to(apps_dir) or not path.is_file() or path.stat().st_size > 32768:
        raise PolicyError('PROFILE_UNAVAILABLE')
    try:
        row = json.loads(path.read_text(encoding='utf-8-sig'))
        required = {'version', 'id', 'kind', 'project_root', 'entry_url', 'navigation_origins', 'resource_origins'}
        if not isinstance(row, dict) or not required <= set(row) or set(row) - required - {'transfer'}:
            raise ValueError()
        if type(row['version']) is not int or row['version'] != 1 or row['id'] != application_id or row['kind'] != 'web':
            raise ValueError()
        transfer = row.get('transfer', {'upload_roots': []})
        if not isinstance(transfer, dict) or set(transfer) != {'upload_roots'}:
            raise ValueError()
        return build_web_profile(
            application_id, apps_dir / row['project_root'], row['entry_url'],
            row['navigation_origins'], row['resource_origins'], transfer['upload_roots'], allowed_root,
        )
    except (ValueError, TypeError, OSError, KeyError):
        raise PolicyError('INVALID_WEB_PROFILE') from None
