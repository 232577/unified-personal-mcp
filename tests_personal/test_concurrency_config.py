import json
from collections import defaultdict
from types import SimpleNamespace

import pytest

from personal_mcp.config import load_config
from personal_mcp.search import SearchManager
from tests_personal.test_config import installation
from tests_personal.test_search import settled


def test_existing_settings_get_concurrent_project_defaults(tmp_path):
    config = load_config(installation(tmp_path))
    assert (config.workflow_idle_seconds, config.browser_sessions,
            config.webview2_instances, config.search_sessions) == (1800, 8, 4, 16)


@pytest.mark.parametrize(("field", "value"), [
    ("workflow_idle_seconds", 299), ("workflow_idle_seconds", 86401),
    ("workflow_idle_seconds", True), ("browser_sessions", 0),
    ("browser_sessions", 33), ("browser_sessions", "8"),
    ("webview2_instances", 0), ("webview2_instances", 17),
    ("webview2_instances", False), ("search_sessions", 0),
    ("search_sessions", 65), ("search_sessions", 1.5),
])
def test_concurrency_settings_require_bounded_integers(tmp_path, field, value):
    with pytest.raises(ValueError, match=field):
        load_config(installation(tmp_path, **{field: value}))


def test_hybrid_capacity_cannot_exceed_total_browser_capacity(tmp_path):
    with pytest.raises(ValueError, match="webview2_instances"):
        load_config(installation(tmp_path, browser_sessions=3, webview2_instances=4))


def test_gui_preserves_custom_concurrency_settings_when_saving(tmp_path):
    from personal_mcp.gui import SetupWindow

    class Value:
        def set(self, value):
            self.value = value

        def get(self):
            return self.value

    expected = {"workflow_idle_seconds": 2400, "browser_sessions": 12,
                "webview2_instances": 6, "search_sessions": 24}
    window = SetupWindow.__new__(SetupWindow)
    window.path = installation(tmp_path, **expected)
    window.values = defaultdict(Value)
    window.show = pytest.fail
    window.load()
    raw, key_import = window.raw()
    assert key_import is None
    assert {name: raw[name] for name in expected} == expected
    window.path.write_text(json.dumps(raw), encoding="utf-8")
    loaded = load_config(window.path)
    assert {name: getattr(loaded, name) for name in expected} == expected


def test_search_custom_global_capacity_keeps_per_owner_isolation(tmp_path):
    (tmp_path / "sample.txt").write_text("needle", encoding="utf-8")
    manager = SearchManager(max_sessions=6)
    try:
        rows = [manager.start(str(i), tmp_path, "needle") for i in range(6)]
        for index, row in enumerate(rows):
            settled(manager, str(index), row["search_id"])
        with pytest.raises(ValueError, match="SEARCH_SESSION_LIMIT"):
            manager.start("new", tmp_path, "needle")
        assert manager.occupancy("0") == {
            "used": 6, "running": 0, "limit": 6,
            "owner_used": 1, "owner_limit": 2,
        }
        manager.release("0", rows[0]["search_id"])
        manager.start("1", tmp_path, "needle")
        manager.release("2", rows[2]["search_id"])
        with pytest.raises(ValueError, match="SEARCH_SESSION_LIMIT"):
            manager.start("1", tmp_path, "needle")
    finally:
        manager.close()


@pytest.mark.parametrize("capacity", [0, 65, True, "16"])
def test_search_capacity_is_validated(capacity):
    with pytest.raises(ValueError, match="SEARCH_SESSION_LIMIT"):
        SearchManager(max_sessions=capacity)


@pytest.mark.parametrize(("exit_code", "collector_alive", "expected"), [
    (None, False, ["running_search"]),
    (0, True, ["running_search"]),
    (0, False, []),
])
def test_search_idle_blockers_consider_process_and_collector(exit_code, collector_alive, expected):
    manager = SearchManager()
    manager.sessions["retained"] = SimpleNamespace(
        owner="a", process=SimpleNamespace(poll=lambda: exit_code),
        thread=SimpleNamespace(is_alive=lambda: collector_alive),
    )
    assert manager.idle_blockers("a") == expected
    assert manager.idle_blockers("b") == []


def test_search_idle_probe_errors_are_not_mistaken_for_idle():
    manager = SearchManager()

    def failed_probe():
        raise OSError("process unavailable")

    manager.sessions["unavailable"] = SimpleNamespace(owner="a", process=SimpleNamespace(poll=failed_probe))
    with pytest.raises(OSError, match="process unavailable"):
        manager.idle_blockers("a")


@pytest.mark.parametrize("capacity", [0, 17, True, "4"])
def test_hybrid_capacity_is_validated(tmp_path, capacity):
    from bf_automation.hybrid.manager import HybridManager

    with pytest.raises(ValueError, match="UNVERIFIED_HYBRID_LIMIT"):
        HybridManager(SimpleNamespace(), platform=object(), attach_resolver=object(), max_managed=capacity)


def test_browser_and_hybrid_capacity_propagate_through_factory(tmp_path):
    from personal_mcp.bf.bootstrap import build_mcp
    from tests_bf.support import browser_config

    config = browser_config(tmp_path)
    config["max_sessions"] = 8
    path = tmp_path / "browser.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    server = build_mcp(state_root=tmp_path / "state", allowed_root=tmp_path,
                       browser_config_path=path, max_hybrid_instances=4)
    try:
        assert server._bf_browser_manager.max_sessions == 8
        assert server._bf_browser_manager.max_pages_per_session == 4
        hybrid = server._bf_hybrid_manager
        for index in range(4):
            hybrid._reserve_managed(str(index))
        with pytest.raises(ValueError, match="HYBRID_INSTANCE_LIMIT_REACHED"):
            hybrid._reserve_managed("extra")
        assert hybrid.max_managed == 4
    finally:
        server._bf_hybrid_manager._pending.clear()
        server._bf_browser_manager.shutdown()
