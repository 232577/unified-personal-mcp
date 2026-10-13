"""二进制传输只操作隔离夹具，并验证容量、权限和无覆盖提交。"""

import base64
import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import replace

import pytest

from personal_mcp.coding import build_coding
from personal_mcp.config import load_config
from tests_personal.test_config import installation


@pytest.fixture
def transfer(tmp_path):
    from personal_mcp.file_transfer import FileTransferManager

    cfg = replace(load_config(installation(tmp_path)), permission_mode="trusted")
    subprocess.run(["git", "init", str(cfg.project("app"))], capture_output=True, check=True)
    runtime = build_coding(cfg, cfg.project("app"))
    now = [100.0]
    manager = FileTransferManager(cfg.data_root, clock=lambda: now[0],
                                  max_file_bytes=16, max_total_bytes=24, chunk_bytes=4)
    try:
        yield manager, runtime, now
    finally:
        manager.close()
        runtime.close()


def call(manager, runtime, action, **args):
    return manager.handle("owner-a", runtime, {"action": action, **args})


def begin(manager, runtime, data=b"abcdef", path="received.bin"):
    return call(manager, runtime, "upload_begin", path=path, size=len(data),
                sha256=hashlib.sha256(data).hexdigest())


def chunk(manager, runtime, transfer_id, offset, data):
    return call(manager, runtime, "upload_chunk", transfer_id=transfer_id, offset=offset,
                data_base64=base64.b64encode(data).decode("ascii"))


def payloads(manager):
    return [path for path in manager.private_root.iterdir() if path.name != ".file-transfer-cache.json"]


def test_upload_roundtrip_duplicate_and_new_file_only(transfer):
    manager, runtime, _ = transfer
    started = begin(manager, runtime)
    assert started["ok"] and started["next_offset"] == 0
    key = started["transfer_id"]
    assert chunk(manager, runtime, key, 0, b"abcd")["next_offset"] == 4
    assert chunk(manager, runtime, key, 0, b"abcd")["next_offset"] == 4
    assert chunk(manager, runtime, key, 4, b"ef")["next_offset"] == 6
    result = call(manager, runtime, "upload_commit", transfer_id=key)
    assert result["ok"] and result["path"] == "received.bin" and result["size"] == 6
    assert result["sha256"] == "bef57ec7f53a6d40beb640a780a639c83bc29ac8a9816f1fc6c5c6dcd93c4721"
    assert (runtime.workspace.root / "received.bin").read_bytes() == b"abcdef"
    assert call(manager, runtime, "upload_commit", transfer_id=key) == result
    assert begin(manager, runtime)["error"]["code"] == "TRANSFER_TARGET_EXISTS"


@pytest.mark.parametrize("offset,data,code", [
    (1, b"ab", "TRANSFER_OFFSET_MISMATCH"),
    (0, b"abcde", "TRANSFER_CHUNK_TOO_LARGE"),
    (0, b"", "INVALID_ARGUMENT"),
    (-1, b"a", "INVALID_ARGUMENT"),
])
def test_invalid_chunk_never_advances_or_creates_target(transfer, offset, data, code):
    manager, runtime, _ = transfer
    key = begin(manager, runtime)["transfer_id"]
    result = chunk(manager, runtime, key, offset, data)
    assert result["error"]["code"] == code
    assert call(manager, runtime, "status", transfer_id=key)["next_offset"] == 0
    assert not (runtime.workspace.root / "received.bin").exists()


def test_changed_duplicate_and_overlap_are_denied(transfer):
    manager, runtime, _ = transfer
    key = begin(manager, runtime)["transfer_id"]
    chunk(manager, runtime, key, 0, b"abc")
    assert chunk(manager, runtime, key, 0, b"abd")["error"]["code"] == "TRANSFER_CHUNK_CONFLICT"
    assert chunk(manager, runtime, key, 2, b"cd")["error"]["code"] == "TRANSFER_OFFSET_MISMATCH"
    assert call(manager, runtime, "status", transfer_id=key)["next_offset"] == 3


def test_incomplete_and_bad_hash_preserve_target_absence(transfer):
    manager, runtime, _ = transfer
    key = begin(manager, runtime)["transfer_id"]
    assert call(manager, runtime, "upload_commit", transfer_id=key)["error"]["code"] == "TRANSFER_INCOMPLETE"
    chunk(manager, runtime, key, 0, b"abcd")
    chunk(manager, runtime, key, 4, b"zz")
    assert call(manager, runtime, "upload_commit", transfer_id=key)["error"]["code"] == "TRANSFER_HASH_MISMATCH"
    assert not (runtime.workspace.root / "received.bin").exists()


def test_target_created_during_upload_is_retained(transfer):
    manager, runtime, _ = transfer
    key = begin(manager, runtime, b"data")["transfer_id"]
    chunk(manager, runtime, key, 0, b"data")
    target = runtime.workspace.root / "received.bin"
    target.write_bytes(b"old contents")
    assert call(manager, runtime, "upload_commit", transfer_id=key)["error"]["code"] == "TRANSFER_TARGET_EXISTS"
    manager.close_owner("owner-a")
    assert target.read_bytes() == b"old contents"


def test_download_uses_snapshot_and_bounded_chunks(transfer):
    manager, runtime, _ = transfer
    source = runtime.workspace.root / "source.bin"
    source.write_bytes(b"abcdef")
    started = call(manager, runtime, "download_begin", path="source.bin")
    assert started["ok"] and started["name"] == "source.bin" and started["size"] == 6
    source.write_bytes(b"replaced")
    key = started["transfer_id"]
    first = call(manager, runtime, "download_chunk", transfer_id=key, offset=0, limit=4)
    last = call(manager, runtime, "download_chunk", transfer_id=key, offset=4, limit=4)
    assert base64.b64decode(first["data_base64"]) == b"abcd" and not first["eof"]
    assert base64.b64decode(last["data_base64"]) == b"ef" and last["eof"]
    assert last["next_offset"] == 6
    assert call(manager, runtime, "download_chunk", transfer_id=key, offset=0, limit=5)["error"]["code"] == "TRANSFER_CHUNK_TOO_LARGE"
    assert call(manager, runtime, "download_chunk", transfer_id=key, offset=7, limit=1)["error"]["code"] == "TRANSFER_OFFSET_MISMATCH"


def test_owner_and_runtime_isolation(transfer):
    manager, runtime, _ = transfer
    key = begin(manager, runtime)["transfer_id"]
    result = manager.handle("owner-b", runtime, {"action": "release", "transfer_id": key})
    assert result["error"]["code"] == "TRANSFER_NOT_FOUND"
    assert call(manager, object(), "status", transfer_id=key)["error"]["code"] == "TRANSFER_NOT_FOUND"
    assert call(manager, runtime, "status", transfer_id=key)["ok"]


def test_limits_expiry_and_cleanup(transfer):
    manager, runtime, now = transfer
    first = begin(manager, runtime, b"a" * 16)["transfer_id"]
    assert begin(manager, runtime, b"b" * 9, "second.bin")["error"]["code"] == "TRANSFER_CAPACITY_EXCEEDED"
    second = begin(manager, runtime, b"b" * 8, "second.bin")["transfer_id"]
    assert begin(manager, runtime, b"", "third.bin")["error"]["code"] == "TRANSFER_OWNER_LIMIT"
    assert begin(manager, runtime, b"c" * 17)["error"]["code"] == "TRANSFER_FILE_TOO_LARGE"
    now[0] = 1000.0
    assert call(manager, runtime, "status", transfer_id=first)["error"]["code"] == "TRANSFER_NOT_FOUND"
    assert call(manager, runtime, "status", transfer_id=second)["error"]["code"] == "TRANSFER_NOT_FOUND"
    assert begin(manager, runtime)["ok"]
    manager.close_owner("owner-a")
    assert not payloads(manager)
    manager.close()
    assert begin(manager, runtime)["error"]["code"] == "TRANSFER_CLOSED"


@pytest.mark.parametrize("path", ["../escape.bin", "node_modules/file.bin", ".hidden/file.bin"])
def test_denied_write_path_never_creates_files(transfer, path):
    manager, runtime, _ = transfer
    (runtime.workspace.root / "node_modules").mkdir()
    (runtime.workspace.root / ".hidden").mkdir()
    result = begin(manager, runtime, path=path)
    assert not result["ok"]
    assert result["error"]["code"] in {"PATH_OUTSIDE_WORKSPACE", "TRANSFER_WRITE_PATH_DENIED"}
    assert not payloads(manager)


def test_missing_parent_rejected_and_zero_byte_commit_allowed(transfer):
    manager, runtime, _ = transfer
    assert begin(manager, runtime, path="missing/file.bin")["error"]["code"] == "TRANSFER_PARENT_NOT_FOUND"
    key = begin(manager, runtime, b"")["transfer_id"]
    assert call(manager, runtime, "upload_commit", transfer_id=key)["size"] == 0
    assert (runtime.workspace.root / "received.bin").read_bytes() == b""


def test_release_is_owner_scoped_and_does_not_delete_committed_file(transfer):
    manager, runtime, _ = transfer
    key = begin(manager, runtime, b"data")["transfer_id"]
    chunk(manager, runtime, key, 0, b"data")
    call(manager, runtime, "upload_commit", transfer_id=key)
    assert call(manager, runtime, "release", transfer_id=key)["released"]
    assert call(manager, runtime, "status", transfer_id=key)["error"]["code"] == "TRANSFER_NOT_FOUND"
    assert (runtime.workspace.root / "received.bin").read_bytes() == b"data"


@pytest.mark.parametrize("args", [
    {"action": "unknown"},
    {"action": []},
    {"action": "upload_begin", "path": "x", "size": True, "sha256": "0" * 64},
    {"action": "upload_begin", "path": "x", "size": -1, "sha256": "0" * 64},
    {"action": "upload_begin", "path": "x", "size": 1, "sha256": "bad"},
])
def test_invalid_arguments_are_bounded_failures(transfer, args):
    manager, runtime, _ = transfer
    assert manager.handle("owner-a", runtime, args)["error"]["code"] == "INVALID_ARGUMENT"


def test_bad_base64_and_wrong_direction_preserve_transfer(transfer):
    manager, runtime, _ = transfer
    key = begin(manager, runtime)["transfer_id"]
    assert call(manager, runtime, "upload_chunk", transfer_id=key, offset=0, data_base64="@@")["error"]["code"] == "INVALID_ARGUMENT"
    assert call(manager, runtime, "download_chunk", transfer_id=key, offset=0, limit=1)["error"]["code"] == "TRANSFER_DIRECTION_MISMATCH"
    assert call(manager, runtime, "status", transfer_id=key)["next_offset"] == 0


@pytest.mark.parametrize("mode", ["safe", "trusted", "full_control"])
def test_ignored_write_policy_is_explicit_for_each_mode(transfer, mode):
    manager, original, _ = transfer
    runtime = type(original)(original.workspace.root, permission_mode=mode,
                             full_control=mode == "full_control")
    try:
        folder = runtime.workspace.root / "node_modules"
        folder.mkdir()
        result = begin(manager, runtime, b"", "node_modules/new.bin")
        if mode == "full_control":
            assert result["ok"], result
            assert call(manager, runtime, "upload_commit", transfer_id=result["transfer_id"])["ok"]
            assert (folder / "new.bin").read_bytes() == b""
        else:
            assert result["error"]["code"] == "TRANSFER_WRITE_PATH_DENIED"
            assert not (folder / "new.bin").exists()
    finally:
        runtime.close()


def junction(link, target):
    if os.name == "nt":
        subprocess.run(["cmd", "/d", "/c", "mklink", "/J", str(link), str(target)],
                       capture_output=True, check=True)
    else:
        link.symlink_to(target, target_is_directory=True)


def test_directory_link_is_denied_even_inside_workspace(transfer):
    manager, runtime, _ = transfer
    real = runtime.workspace.root / "real"
    real.mkdir()
    link = runtime.workspace.root / "linked"
    junction(link, real)
    try:
        result = begin(manager, runtime, b"data", "linked/new.bin")
        assert result["error"]["code"] == "SYMLINK_ESCAPE"
        assert not (real / "new.bin").exists()
    finally:
        link.rmdir() if os.name == "nt" else link.unlink()


def test_parent_replacement_between_begin_and_commit_is_denied(transfer):
    manager, runtime, _ = transfer
    folder = runtime.workspace.root / "destination"
    folder.mkdir()
    key = begin(manager, runtime, b"data", "destination/new.bin")["transfer_id"]
    chunk(manager, runtime, key, 0, b"data")
    folder.rename(runtime.workspace.root / "old-destination")
    folder.mkdir()
    result = call(manager, runtime, "upload_commit", transfer_id=key)
    assert result["error"]["code"] == "TRANSFER_TARGET_CHANGED"
    assert not (folder / "new.bin").exists()


def test_commit_failure_only_removes_its_temporary_file(transfer, monkeypatch):
    manager, runtime, _ = transfer
    key = begin(manager, runtime, b"data")["transfer_id"]
    chunk(manager, runtime, key, 0, b"data")
    unrelated = runtime.workspace.root / ".upload-retain.tmp"
    unrelated.write_bytes(b"retained")

    def failure(*args, **kwargs):
        raise OSError("fixture link failure")

    monkeypatch.setattr(os, "link", failure)
    result = call(manager, runtime, "upload_commit", transfer_id=key)
    assert result["error"]["code"] == "TRANSFER_IO_ERROR"
    assert not (runtime.workspace.root / "received.bin").exists()
    assert list(runtime.workspace.root.glob(".upload-*.tmp")) == [unrelated]
    assert unrelated.read_bytes() == b"retained"


def test_oversized_download_and_non_regular_path_never_allocate(transfer):
    manager, runtime, _ = transfer
    (runtime.workspace.root / "large.bin").write_bytes(b"x" * 17)
    assert call(manager, runtime, "download_begin", path="large.bin")["error"]["code"] == "TRANSFER_FILE_TOO_LARGE"
    assert call(manager, runtime, "download_begin", path=".")["error"]["code"] == "TRANSFER_NOT_REGULAR_FILE"
    assert not payloads(manager)


def test_chunk_cannot_exceed_declared_size(transfer):
    manager, runtime, _ = transfer
    key = begin(manager, runtime, b"ab")["transfer_id"]
    assert chunk(manager, runtime, key, 0, b"abc")["error"]["code"] == "TRANSFER_SIZE_MISMATCH"
    assert call(manager, runtime, "status", transfer_id=key)["next_offset"] == 0


def test_close_owner_preserves_another_owners_snapshot(transfer):
    manager, runtime, _ = transfer
    (runtime.workspace.root / "data.bin").write_bytes(b"data")
    key = manager.handle("owner-b", runtime, {"action": "download_begin", "path": "data.bin"})["transfer_id"]
    begin(manager, runtime)
    manager.close_owner("owner-a")
    result = manager.handle("owner-b", runtime, {"action": "download_chunk", "transfer_id": key,
                                               "offset": 0, "limit": 4})
    assert base64.b64decode(result["data_base64"]) == b"data"


def test_commit_retains_competing_file_created_at_exclusive_link(transfer, monkeypatch):
    manager, runtime, _ = transfer
    key = begin(manager, runtime, b"data")["transfer_id"]
    chunk(manager, runtime, key, 0, b"data")
    link = os.link

    def competing_link(source, destination, **kwargs):
        destination.write_bytes(b"competing file")
        return link(source, destination, **kwargs)

    if os.name != "nt":
        pytest.skip("Windows exclusive-link path fixture")
    monkeypatch.setattr(os, "link", competing_link)
    result = call(manager, runtime, "upload_commit", transfer_id=key)
    assert result["error"]["code"] == "TRANSFER_TARGET_EXISTS"
    assert (runtime.workspace.root / "received.bin").read_bytes() == b"competing file"
    assert not list(runtime.workspace.root.glob(".upload-*.tmp"))


def abandoned_cache(tmp_path, *, live=False):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    root = tmp_path / "private"
    root.mkdir()
    script = """
import base64, hashlib, json, sys
from pathlib import Path
from types import SimpleNamespace
from coding_tools_mcp.server import Workspace
from personal_mcp.file_transfer import FileTransferManager
w = Workspace(Path(sys.argv[1]))
r = SimpleNamespace(workspace=w, full_control=True, resolve_for_write=w.resolve_for_write)
m = FileTransferManager(Path(sys.argv[2]), max_file_bytes=16, max_total_bytes=24, chunk_bytes=4)
started = m.handle('fixture-owner', r, {'action':'upload_begin', 'path':'new.bin', 'size':16,
    'sha256':hashlib.sha256(b'payload' * 2 + b'xx').hexdigest()})
assert started['ok'], started
sent = m.handle('fixture-owner', r, {'action':'upload_chunk', 'transfer_id':started['transfer_id'],
    'offset':0, 'data_base64':base64.b64encode(b'data').decode()})
assert sent['ok'], sent
print(json.dumps({'root':str(m.private_root)}), flush=True)
if sys.argv[3] == 'live':
    sys.stdin.readline()
"""
    process = subprocess.Popen([sys.executable, "-c", script, str(workspace), str(root), "live" if live else "exit"],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    line = process.stdout.readline()
    if not line:
        raise AssertionError(process.communicate(timeout=10))
    cache = root / json.loads(line)["root"].split(os.sep)[-1]
    if not live:
        process.communicate(timeout=10)
        assert process.returncode == 0
    return root, cache, process


def test_known_dead_expired_cache_is_reclaimed_without_touching_other_data(tmp_path, monkeypatch):
    from personal_mcp.file_transfer import FileTransferManager
    import personal_mcp.file_transfer as module

    root, old, _ = abandoned_cache(tmp_path)
    marker = old / ".file-transfer-cache.json"
    assert marker.exists()
    data = json.loads(marker.read_text(encoding="utf-8"))
    assert data["pid"] > 0 and data["process_created"] > 0 and data["nonce"]
    unrelated = root / "retained.bin"
    unrelated.write_bytes(b"retained")
    later = time.time() + 901
    monkeypatch.setattr(module.time, "time", lambda: later)
    manager = FileTransferManager(root)
    try:
        assert not old.exists()
        assert unrelated.read_bytes() == b"retained"
    finally:
        manager.close()


def test_alive_cache_is_retained_even_after_ttl(tmp_path, monkeypatch):
    from personal_mcp.file_transfer import FileTransferManager
    import personal_mcp.file_transfer as module

    root, old, process = abandoned_cache(tmp_path, live=True)
    later = time.time() + 901
    monkeypatch.setattr(module.time, "time", lambda: later)
    manager = None
    try:
        manager = FileTransferManager(root)
        assert old.exists()
        assert sum(path.stat().st_size for path in old.iterdir()) > 4
    finally:
        if manager:
            manager.close()
        process.communicate("exit\n", timeout=10)
        assert process.returncode == 0


@pytest.mark.parametrize("mutation", ["unknown", "missing_marker", "bad_marker", "identity_changed"])
def test_unprovable_cache_is_retained_and_blocks_new_transfer(tmp_path, monkeypatch, mutation):
    from personal_mcp.file_transfer import FileTransferManager
    import personal_mcp.file_transfer as module
    from coding_tools_mcp.server import Workspace
    from types import SimpleNamespace

    root, old, _ = abandoned_cache(tmp_path)
    marker = old / ".file-transfer-cache.json"
    if mutation == "unknown":
        (old / "retain.bin").write_bytes(b"unknown contents")
    elif mutation == "missing_marker":
        marker.unlink()
    elif mutation == "bad_marker":
        marker.write_text("invalid", encoding="utf-8")
    else:
        payload = next(path for path in old.iterdir() if path.name != marker.name)
        payload.rename(old / "unknown.bin")
        payload.write_bytes(b"new external contents")
    before = {path.name: path.read_bytes() for path in old.iterdir()}
    later = time.time() + 901
    monkeypatch.setattr(module.time, "time", lambda: later)
    manager = FileTransferManager(root)
    workspace = Workspace(tmp_path / "workspace")
    runtime = SimpleNamespace(workspace=workspace, full_control=True, resolve_for_write=workspace.resolve_for_write)
    try:
        result = begin(manager, runtime, b"", path="new.bin")
        assert result["error"]["code"] == "TRANSFER_CLEANUP_REQUIRED"
        assert {path.name: path.read_bytes() for path in old.iterdir()} == before
    finally:
        manager.close()


def test_recent_dead_cache_counts_reserved_bytes_until_ttl(tmp_path):
    from personal_mcp.file_transfer import FileTransferManager
    from coding_tools_mcp.server import Workspace
    from types import SimpleNamespace

    root, old, _ = abandoned_cache(tmp_path)
    manager = FileTransferManager(root, max_file_bytes=16, max_total_bytes=24, chunk_bytes=4)
    workspace = Workspace(tmp_path / "workspace")
    runtime = SimpleNamespace(workspace=workspace, full_control=True, resolve_for_write=workspace.resolve_for_write)
    try:
        result = begin(manager, runtime, b"x" * 9, path="new.bin")
        assert result["error"]["code"] == "TRANSFER_CLEANUP_REQUIRED"
        assert old.exists()
    finally:
        manager.close()


def test_cache_junction_is_never_followed_or_removed(tmp_path, monkeypatch):
    from personal_mcp.file_transfer import FileTransferManager
    import personal_mcp.file_transfer as module
    from coding_tools_mcp.server import Workspace
    from types import SimpleNamespace

    root, old, _ = abandoned_cache(tmp_path)
    outside = tmp_path / "retained-target"
    old.rename(outside)
    junction(old, outside)
    before = {path.name: path.read_bytes() for path in outside.iterdir()}
    later = time.time() + 901
    monkeypatch.setattr(module.time, "time", lambda: later)
    manager = FileTransferManager(root)
    workspace = Workspace(tmp_path / "workspace")
    runtime = SimpleNamespace(workspace=workspace, full_control=True, resolve_for_write=workspace.resolve_for_write)
    try:
        assert begin(manager, runtime, b"")["error"]["code"] == "TRANSFER_CLEANUP_REQUIRED"
        assert old.exists()
        assert {path.name: path.read_bytes() for path in outside.iterdir()} == before
    finally:
        manager.close()
        old.rmdir() if os.name == "nt" else old.unlink()


def test_recent_orphan_is_recovered_after_ttl_without_restart(tmp_path, monkeypatch):
    from personal_mcp.file_transfer import FileTransferManager
    import personal_mcp.file_transfer as module
    from coding_tools_mcp.server import Workspace
    from types import SimpleNamespace

    root, old, _ = abandoned_cache(tmp_path)
    manager = FileTransferManager(root, max_file_bytes=16, max_total_bytes=24, chunk_bytes=4)
    workspace = Workspace(tmp_path / "workspace")
    runtime = SimpleNamespace(workspace=workspace, full_control=True, resolve_for_write=workspace.resolve_for_write)
    try:
        assert begin(manager, runtime, b"x" * 9)["error"]["code"] == "TRANSFER_CLEANUP_REQUIRED"
        later = time.time() + 901
        monkeypatch.setattr(module.time, "time", lambda: later)
        assert begin(manager, runtime, b"x" * 9)["ok"]
        assert not old.exists()
    finally:
        manager.close()


def test_unknown_cache_blocks_restart_without_creating_more_cache_directories(tmp_path):
    from personal_mcp.file_transfer import FileTransferManager
    from coding_tools_mcp.server import Workspace
    from types import SimpleNamespace

    root = tmp_path / "private"
    old = root / "file-transfers-old-unmarked"
    old.mkdir(parents=True)
    (old / "retain.bin").write_bytes(b"unknown contents")
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    workspace = Workspace(workspace_path)
    runtime = SimpleNamespace(workspace=workspace, full_control=True, resolve_for_write=workspace.resolve_for_write)
    for _ in range(3):
        manager = FileTransferManager(root)
        try:
            assert begin(manager, runtime, b"")["error"]["code"] == "TRANSFER_CLEANUP_REQUIRED"
        finally:
            manager.close()
    assert list(root.iterdir()) == [old]
    assert (old / "retain.bin").read_bytes() == b"unknown contents"


def test_close_preserves_cache_payload_replaced_by_external_file(transfer):
    manager, runtime, _ = transfer
    begin(manager, runtime, b"data")
    staged = payloads(manager)[0]
    retained = manager.private_root / "unknown-original.bin"
    staged.rename(retained)
    staged.write_bytes(b"external replacement")
    manager.close_owner("owner-a")
    manager.close()
    assert staged.read_bytes() == b"external replacement"
    assert retained.read_bytes() == b""
