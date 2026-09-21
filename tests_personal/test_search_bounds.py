import json
import sys

from personal_mcp.search import SearchManager
from personal_mcp.windows_jobs import OwnedJob
from tests_personal.test_search import settled


def test_search_timeout_ends_only_the_owned_process(tmp_path, monkeypatch):
    original = OwnedJob.spawn
    spawned = []
    def slow(job, command, **kwargs):
        process = original(job, [sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
        spawned.append(process)
        return process
    monkeypatch.setattr(OwnedJob, "spawn", slow)
    manager = SearchManager(max_seconds=0.1)
    try:
        row = manager.start("owner", tmp_path, "needle")
        result = settled(manager, "owner", row["search_id"])
        assert result["state"] == "partial" and result["reason"] == "TIME_LIMIT"
        assert len(spawned) == 1
        spawned[0].wait(timeout=3)
    finally:
        manager.close()


def test_single_large_match_cannot_exceed_response_budget(tmp_path):
    (tmp_path / "large.txt").write_text("needle" + "界" * 50000, encoding="utf-8")
    manager = SearchManager()
    try:
        row = manager.start("owner", tmp_path, "needle")
        result = settled(manager, "owner", row["search_id"])
        assert result["state"] == "partial"
        assert len(json.dumps(result, ensure_ascii=False).encode()) < 65536
    finally:
        manager.close()
