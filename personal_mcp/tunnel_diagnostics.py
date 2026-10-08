"""Bounded evidence from the official client's polling state transitions."""

import json
from datetime import datetime


def latest_poller_state(path, *, since):
    """Return only a known state and time; do not expose diagnostic payloads."""
    try:
        with path.open('rb') as source:
            source.seek(0, 2)
            source.seek(max(0, source.tell() - 65536))
            lines = source.read(65536).decode('utf-8', errors='replace').splitlines()
    except OSError:
        return None
    latest = None
    for line in lines:
        try:
            row = json.loads(line)
            message = row.get('msg')
            if message not in {'poll failed; backing off', 'poller recovered; polling operational'}:
                continue
            observed = datetime.fromisoformat(row['time']).timestamp()
            if observed < since:
                continue
            if latest is None or observed >= latest['observed_at']:
                latest = {'connected': message == 'poller recovered; polling operational',
                          'observed_at': observed}
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
            continue
    return latest
