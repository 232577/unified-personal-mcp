import os
import sys
from pathlib import Path


def browser_config(tmp_path):
    browsers = Path(os.environ.get('UPM_TEST_BROWSERS_PATH', Path(__file__).resolve().parents[1] / 'resources/browsers'))
    browsers.mkdir(parents=True, exist_ok=True)
    return {'enabled': True, 'version': 1, 'mode': 'headless', 'python': sys.executable,
            'browsers_path': str(browsers), 'max_sessions': 4, 'max_pages_per_session': 4,
            'actions_enabled': True, 'transfers_enabled': True}
