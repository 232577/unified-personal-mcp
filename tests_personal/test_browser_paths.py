import sys
from pathlib import Path

import pytest

from bf_automation.browser.process import WorkerProcess


def test_overlong_browser_state_fails_before_opening_process_or_log(tmp_path):
    directory = tmp_path / ("x" * 180)
    directory.mkdir()
    worker = None
    try:
        with pytest.raises(ValueError, match="BROWSER_STATE_PATH_TOO_LONG"):
            worker = WorkerProcess(Path(sys.executable), Path(__file__), directory, tmp_path)
        assert not (directory / "worker.stderr.log").exists()
    finally:
        if worker:
            worker.close()
