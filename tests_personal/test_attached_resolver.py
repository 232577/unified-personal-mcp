import pytest

from bf_automation.hybrid.attach import ExistingWebViewResolver
from tests_bf.hybrid.test_hybrid_lifecycle import manager_for, make_profile


def test_existing_debug_port_is_derived_only_from_owned_descendants(tmp_path, monkeypatch):
    project, executable, store, platform, manager = manager_for(tmp_path)
    resolver = ExistingWebViewResolver(platform)
    monkeypatch.setattr(resolver, "_command_line", lambda pid: ["msedgewebview2.exe", "--remote-debugging-port=53123"])
    monkeypatch.setattr(resolver, "_engine_version", lambda port: "Edg/153.0.0.0")
    proof = resolver.resolve(platform.shell, make_profile(project, executable, ownership="attached"),
                             {"pid": platform.shell.pid, "hwnd": 500})
    assert proof["endpoint"] == "http://127.0.0.1:53123"
    assert proof["webview_pid"] == platform.webview.pid
    assert platform.terminated == []


@pytest.mark.parametrize("args", [[], ["--remote-debugging-port=bad"], ["--remote-debugging-port=0"]])
def test_absent_or_invalid_debugging_is_reported_unavailable(tmp_path, monkeypatch, args):
    project, executable, store, platform, manager = manager_for(tmp_path)
    resolver = ExistingWebViewResolver(platform)
    monkeypatch.setattr(resolver, "_command_line", lambda pid: args)
    monkeypatch.setattr(resolver, "_engine_version", lambda port: pytest.fail("must not probe an unproven port"))
    assert resolver.resolve(platform.shell, make_profile(project, executable, ownership="attached"), {}) is None


def test_listener_must_belong_to_the_selected_webview_process(tmp_path, monkeypatch):
    project, executable, store, platform, manager = manager_for(tmp_path)
    resolver = ExistingWebViewResolver(platform)
    monkeypatch.setattr(resolver, "_command_line", lambda pid: ["--remote-debugging-port=53123"])
    monkeypatch.setattr(platform, "listener_pids", lambda port: {99999})
    monkeypatch.setattr(resolver, "_engine_version", lambda port: pytest.fail("must not probe an unrelated process"))
    assert resolver.resolve(platform.shell, make_profile(project, executable, ownership="attached"), {}) is None
