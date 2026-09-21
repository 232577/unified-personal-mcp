import time

import pytest

from personal_mcp.search import SearchManager


def settled(manager, owner, search_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = manager.read(owner, search_id)
        if result["state"] != "running":
            return result
        time.sleep(0.01)
    pytest.fail("search failed to finish")


def test_real_search_unicode_paging_and_foreign_access(tmp_path):
    (tmp_path / "sample.txt").write_text("第一行 needle\n第二行 needle\nother\n", encoding="utf-8")
    manager = SearchManager()
    try:
        row = manager.start("a", tmp_path, "needle")
        result = settled(manager, "a", row["search_id"])
        assert result["state"] == "completed"
        assert len(result["results"]) == 2
        assert result["results"][0]["text"] == "第一行 needle"
        one = manager.read("a", row["search_id"], cursor=0, limit=1)
        assert one["next_cursor"] == 1 and one["has_more"]
        assert manager.read("a", row["search_id"], cursor=0, limit=1) == one
        for action in (manager.read, manager.stop):
            with pytest.raises(PermissionError):
                action("b", row["search_id"])
        with pytest.raises(ValueError):
            manager.read("a", row["search_id"], cursor=-1)
    finally:
        manager.close()


def test_output_limit_is_reported_as_partial(tmp_path):
    (tmp_path / "large.txt").write_text("needle\n" * 100, encoding="utf-8")
    manager = SearchManager(max_records=5)
    try:
        row = manager.start("a", tmp_path, "needle")
        result = settled(manager, "a", row["search_id"])
        assert result["state"] == "partial" and len(result["results"]) == 5
        assert result["reason"] == "RESULT_LIMIT"
    finally:
        manager.close()


def test_missing_rg_and_invalid_regex_are_failures(tmp_path):
    (tmp_path / "a.txt").write_text("data", encoding="utf-8")
    manager = SearchManager(rg="does-not-exist-rg.exe")
    try:
        with pytest.raises(FileNotFoundError):
            manager.start("a", tmp_path, "needle")
        assert not manager.sessions
    finally:
        manager.close()
    manager = SearchManager()
    try:
        result = manager.start("a", tmp_path, "[", regex=True)
        assert settled(manager, "a", result["search_id"])["state"] == "failed"
    finally:
        manager.close()


def test_limits_and_end_release_only_owned_sessions(tmp_path):
    (tmp_path / "a.txt").write_text("needle", encoding="utf-8")
    manager = SearchManager()
    try:
        manager.start("a", tmp_path, "needle")
        manager.start("a", tmp_path, "needle")
        with pytest.raises(ValueError, match="LIMIT"):
            manager.start("a", tmp_path, "needle")
        b = manager.start("b", tmp_path, "needle")
        manager.end_owner("a")
        assert manager.read("b", b["search_id"])["search_id"] == b["search_id"]
        assert len(manager.sessions) == 1
    finally:
        manager.close()
