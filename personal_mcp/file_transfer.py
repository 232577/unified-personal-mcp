"""任务隔离、有界快照和只创建新文件的二进制传输。"""

from __future__ import annotations

import base64
import binascii
import ctypes
import hashlib
import json
import os
import re
import stat
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from typing import TYPE_CHECKING

from coding_tools_mcp.server import ToolFailure

if TYPE_CHECKING:
    from .coding import CodingRuntime


@dataclass
class _Transfer:
    owner: str
    runtime: CodingRuntime
    direction: str
    path: str
    staged: Path
    size: int
    sha256: str
    created: float
    target: Path | None = None
    parent_identity: tuple | None = None
    next_offset: int = 0
    committed: dict | None = None


def _fail(code, message, category="validation"):
    raise ToolFailure(code, message, category=category)


def _integer(value, name, minimum=0):
    if type(value) is not int or value < minimum:
        _fail("INVALID_ARGUMENT", f"{name} must be an integer >= {minimum}.")
    return value


def _identity(path):
    info = path.stat()
    return info.st_dev, info.st_ino


def _reject_links(path):
    # Windows 的 junction 与其他 reparse point 也不能作为上传目标路径。
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            _fail("SYMLINK_ESCAPE", "Uploading through links or reparse points is denied.", "security")


@contextmanager
def _pinned_parent(parent):
    """在 Windows 锁住父级的删除/移动，在 POSIX 使用目录描述符提交。"""
    if os.name != "nt":
        descriptor = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                             | getattr(os, "O_NOFOLLOW", 0))
        try:
            yield descriptor
        finally:
            os.close(descriptor)
        return
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    handles = []
    try:
        for part in reversed((parent, *parent.parents)):
            # share_read | share_write，故意不开放 share_delete。
            handle = create(str(part), 0x80, 3, None, 3, 0x02000000 | 0x00200000, None)
            if handle == ctypes.c_void_p(-1).value:
                raise ctypes.WinError(ctypes.get_last_error())
            handles.append(handle)
        _reject_links(parent)
        yield None
    finally:
        for handle in reversed(handles):
            close(handle)


class FileTransferManager:
    TTL_SECONDS = 15 * 60
    MAX_OWNER_TRANSFERS = 2
    MARKER_NAME = ".file-transfer-cache.json"
    CACHE_FORMAT = "unified-personal-mcp/file-transfer-cache/v1"
    MAX_CACHE_FILES = 512
    MAX_CACHE_DIRS = 1024
    MAX_MARKER_BYTES = 262144
    _cache_lock = threading.RLock()

    def __init__(self, data_root, *, clock=None, max_file_bytes=128 * 1024 * 1024,
                 max_total_bytes=256 * 1024 * 1024, chunk_bytes=192 * 1024):
        self.max_file_bytes = _integer(max_file_bytes, "max_file_bytes", 1)
        self.max_total_bytes = _integer(max_total_bytes, "max_total_bytes", 1)
        self.chunk_bytes = _integer(chunk_bytes, "chunk_bytes", 1)
        self.clock = clock or time.monotonic
        root = Path(data_root).resolve()
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock = threading.RLock()
        self._transfers = {}
        self._closed = False
        self._cache_parent = root
        self._nonce = os.urandom(16).hex()
        self.private_root = root / ("file-transfers-" + self._nonce)
        self._cache_created = False
        import psutil
        self._marker = {"format": self.CACHE_FORMAT, "pid": os.getpid(),
                        "process_created": psutil.Process(os.getpid()).create_time(),
                        "created_at": time.time(), "nonce": self._nonce, "files": {}}
        self._external_bytes = 0
        self._cleanup_required = False
        self._orphan_pending = False
        self._refresh_caches()
        if not self._cleanup_required:
            self._ensure_cache()

    def _ensure_cache(self):
        with self._cache_lock:
            if not self._cache_created:
                self.private_root.mkdir(mode=0o700)
                self._cache_created = True
                self._write_marker()

    def _write_marker(self):
        with self._cache_lock:
            data = json.dumps(self._marker, ensure_ascii=False, allow_nan=False).encode("utf-8")
            if len(data) > self.MAX_MARKER_BYTES:
                _fail("TRANSFER_CAPACITY_EXCEEDED", "Too many cached transfer records.", "runtime")
            temporary = self.private_root / (".marker-" + os.urandom(16).hex() + ".tmp")
            with temporary.open("xb") as stream:
                try:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                except Exception:
                    stream.close()
                    temporary.unlink(missing_ok=True)
                    raise
            try:
                os.replace(temporary, self.private_root / self.MARKER_NAME)
            finally:
                temporary.unlink(missing_ok=True)

    @staticmethod
    def _regular_info(path):
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400
                or info.st_nlink != 1):
            raise ValueError("Cache member is not a private regular file.")
        return info

    def _cache_snapshot(self, cache):
        info = cache.lstat()
        if (cache.parent != self._cache_parent or not stat.S_ISDIR(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & 0x400 or cache.is_symlink()):
            raise ValueError("Cache directory is not a direct ordinary directory.")
        marker = cache / self.MARKER_NAME
        marker_info = self._regular_info(marker)
        if marker_info.st_size > self.MAX_MARKER_BYTES:
            raise ValueError("Cache marker is oversized.")
        with marker.open("rb") as stream:
            if (os.fstat(stream.fileno()).st_dev, os.fstat(stream.fileno()).st_ino) != (
                    marker_info.st_dev, marker_info.st_ino):
                raise ValueError("Cache marker changed.")
            metadata = json.loads(stream.read(self.MAX_MARKER_BYTES + 1).decode("utf-8"))
        if (not isinstance(metadata, dict) or set(metadata) != {
                "format", "pid", "process_created", "created_at", "nonce", "files"}
                or metadata["format"] != self.CACHE_FORMAT or type(metadata["pid"]) is not int
                or metadata["pid"] <= 0 or not isinstance(metadata["nonce"], str)
                or not re.fullmatch(r"[0-9a-f]{32}", metadata["nonce"])
                or cache.name != "file-transfers-" + metadata["nonce"]
                or any(type(metadata[key]) not in {int, float} or not 0 < metadata[key] < 1e15
                       for key in ("process_created", "created_at"))
                or not isinstance(metadata["files"], dict) or len(metadata["files"]) > self.MAX_CACHE_FILES):
            raise ValueError("Cache marker is not recognized.")
        for name, record in metadata["files"].items():
            if (not isinstance(name, str) or not re.fullmatch(
                    "part-" + metadata["nonce"] + r"-[0-9a-f]{16}\.bin", name)
                    or not isinstance(record, dict) or set(record) != {"dev", "ino", "reserved"}
                    or any(type(record[key]) is not int or record[key] < 0 for key in record)):
                raise ValueError("Cache file registration is invalid.")
        entries = list(islice(cache.iterdir(), self.MAX_CACHE_FILES + 2))
        if len(entries) > self.MAX_CACHE_FILES + 1:
            raise ValueError("Cache contains unknown entries.")
        members, reserved = {}, 0
        newest = max(metadata["created_at"], marker_info.st_mtime)
        for entry in entries:
            if entry.name == self.MARKER_NAME:
                continue
            record = metadata["files"].get(entry.name)
            actual = self._regular_info(entry)
            if (record is None or (actual.st_dev, actual.st_ino) != (record["dev"], record["ino"])
                    or actual.st_size > record["reserved"]):
                raise ValueError("Cache contains an unregistered or replaced file.")
            members[entry.name] = (actual.st_dev, actual.st_ino, actual.st_size, actual.st_mtime_ns)
            reserved += record["reserved"]
            newest = max(newest, actual.st_mtime)
        return {"metadata": metadata, "identity": (info.st_dev, info.st_ino), "members": members,
                "marker_identity": (marker_info.st_dev, marker_info.st_ino), "reserved": reserved, "newest": newest}

    @staticmethod
    def _original_process_alive(metadata):
        import psutil
        try:
            return psutil.Process(metadata["pid"]).create_time() == metadata["process_created"]
        except psutil.NoSuchProcess:
            return False
        except (psutil.AccessDenied, OSError):
            # 无法核验时保留，不把查询失败当作进程已经退出。
            return True

    def _recover_cache(self, cache, snapshot):
        _reject_links(cache)
        with _pinned_parent(self._cache_parent):
            with _pinned_parent(cache):
                again = self._cache_snapshot(cache)
                if (again != snapshot or self._original_process_alive(again["metadata"])
                        or time.time() - again["newest"] < self.TTL_SECONDS):
                    raise ValueError("Cache ownership or lifetime changed.")
                for name, expected in again["members"].items():
                    path = cache / name
                    info = self._regular_info(path)
                    if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != expected:
                        raise ValueError("Cache file changed during recovery.")
                    path.unlink()
                marker = cache / self.MARKER_NAME
                info = self._regular_info(marker)
                if (info.st_dev, info.st_ino) != again["marker_identity"]:
                    raise ValueError("Cache marker changed during recovery.")
                marker.unlink()
            _reject_links(cache)
            if _identity(cache) != snapshot["identity"]:
                raise ValueError("Cache directory changed during recovery.")
            # 非递归移除；任何后来出现的未知内容都会令 rmdir 失败并保留。
            cache.rmdir()

    def _refresh_caches(self):
        with self._cache_lock:
            self._external_bytes, self._cleanup_required, self._orphan_pending = 0, False, False
            for count, cache in enumerate(self._cache_parent.iterdir(), 1):
                if count > self.MAX_CACHE_DIRS:
                    self._cleanup_required = True
                    break
                if not cache.name.startswith("file-transfers-") or cache == self.private_root:
                    continue
                try:
                    snapshot = self._cache_snapshot(cache)
                    alive = self._original_process_alive(snapshot["metadata"])
                    if not alive and time.time() - snapshot["newest"] >= self.TTL_SECONDS:
                        self._recover_cache(cache, snapshot)
                    else:
                        self._external_bytes += snapshot["reserved"]
                        self._orphan_pending |= not alive
                except (OSError, ValueError, RuntimeError, UnicodeError):
                    # 未知目录、链接和不完整 marker 均保留，只暂停新传输。
                    self._cleanup_required = True

    def handle(self, owner: str, runtime: CodingRuntime, args: dict) -> dict:
        try:
            with self._lock:
                if self._closed:
                    _fail("TRANSFER_CLOSED", "File transfer manager is closed.", "runtime")
                if not isinstance(owner, str) or not owner or not isinstance(args, dict):
                    _fail("INVALID_ARGUMENT", "Internal owner and argument object are required.")
                self._prune()
                action = args.get("action")
                if not isinstance(action, str):
                    _fail("INVALID_ARGUMENT", "action must be a string.")
                if action == "upload_begin":
                    return self._upload_begin(owner, runtime, args)
                if action == "download_begin":
                    return self._download_begin(owner, runtime, args)
                if action not in {"upload_chunk", "upload_commit", "download_chunk", "status", "release"}:
                    _fail("INVALID_ARGUMENT", "Unsupported transfer action.")
                key = args.get("transfer_id")
                item = self._transfers.get(key) if isinstance(key, str) else None
                if item is None or item.owner != owner or item.runtime is not runtime:
                    _fail("TRANSFER_NOT_FOUND", "Transfer not found for this task.", "not_found")
                if action == "status":
                    return self._status(key, item)
                if action == "release":
                    self._discard(key)
                    return {"ok": True, "transfer_id": key, "released": True}
                direction = "download" if action == "download_chunk" else "upload"
                if item.direction != direction:
                    _fail("TRANSFER_DIRECTION_MISMATCH", "Transfer action has the wrong direction.")
                if action == "upload_chunk":
                    return self._upload_chunk(key, item, args)
                if action == "upload_commit":
                    return self._upload_commit(key, item)
                return self._download_chunk(key, item, args)
        except ToolFailure as exc:
            return {"ok": False, "error": {"code": exc.code, "message": exc.message,
                                            "category": exc.category, "retryable": exc.retryable}}
        except (OSError, ValueError, RuntimeError):
            # 不将系统异常中的私有路径或其他敏感细节回传。
            return {"ok": False, "error": {"code": "TRANSFER_IO_ERROR", "message": "File transfer I/O failed.",
                                            "category": "runtime", "retryable": False}}

    def _capacity(self, owner, size):
        if size > self.max_file_bytes:
            _fail("TRANSFER_FILE_TOO_LARGE", "File exceeds the configured transfer size limit.")
        if sum(item.owner == owner for item in self._transfers.values()) >= self.MAX_OWNER_TRANSFERS:
            _fail("TRANSFER_OWNER_LIMIT", "This task already has two transfers.", "runtime")
        self._refresh_caches()
        if self._cleanup_required:
            _fail("TRANSFER_CLEANUP_REQUIRED", "私有传输缓存需要检查，未知内容已保留。", "runtime")
        if size + self._external_bytes + sum(item.size for item in self._transfers.values()) > self.max_total_bytes:
            if self._orphan_pending:
                _fail("TRANSFER_CLEANUP_REQUIRED", "旧传输缓存尚未到期，请稍后重试。", "runtime")
            _fail("TRANSFER_CAPACITY_EXCEEDED", "Transfer storage capacity is exhausted.", "runtime")

    def _target(self, runtime, raw):
        if not isinstance(raw, str) or not raw:
            _fail("INVALID_ARGUMENT", "path must be a nonempty string.")
        runtime.workspace.reject_write_symlink(raw)
        resolved = runtime.resolve_for_write(raw)
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = runtime.workspace.root / candidate
        _reject_links(candidate)
        if resolved.existed or resolved.path.exists():
            _fail("TRANSFER_TARGET_EXISTS", "Uploading never overwrites an existing file.")
        if not getattr(runtime, "full_control", False) and runtime.workspace.is_ignored_path(resolved.path):
            # 这是传输功能新增的较窄边界，不能绕过既有模式。
            _fail("TRANSFER_WRITE_PATH_DENIED", "该目录不允许通过传输写入，选择项目普通目录。", "permission")
        if not resolved.path.parent.is_dir():
            _fail("TRANSFER_PARENT_NOT_FOUND", "Upload parent directory must already exist.", "not_found")
        return resolved

    def _allocate(self, size):
        with self._cache_lock:
            self._refresh_caches()
            if self._cleanup_required:
                _fail("TRANSFER_CLEANUP_REQUIRED", "私有传输缓存需要检查，未知内容已保留。", "runtime")
            if (len(self._marker["files"]) >= self.MAX_CACHE_FILES or size + self._external_bytes
                    + sum(record["reserved"] for record in self._marker["files"].values()) > self.max_total_bytes):
                _fail("TRANSFER_CAPACITY_EXCEEDED", "Transfer storage capacity is exhausted.", "runtime")
            self._ensure_cache()
            path = self.private_root / ("part-" + self._nonce + "-" + os.urandom(8).hex() + ".bin")
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
            info = os.fstat(descriptor)
            os.close(descriptor)
            self._marker["files"][path.name] = {"dev": info.st_dev, "ino": info.st_ino, "reserved": size}
            try:
                self._write_marker()
            except Exception:
                self._marker["files"].pop(path.name, None)
                path.unlink(missing_ok=True)
                raise
            return os.urandom(24).hex(), path

    def _remove_staged(self, staged):
        with self._cache_lock:
            try:
                info = self._regular_info(staged)
            except FileNotFoundError:
                info = None
            except (OSError, ValueError):
                return False
            record = self._marker["files"].get(staged.name)
            if info is not None:
                if record is None or (info.st_dev, info.st_ino) != (record["dev"], record["ino"]):
                    return False
                staged.unlink()
            self._marker["files"].pop(staged.name, None)
            self._write_marker()
            return True

    def _upload_begin(self, owner, runtime, args):
        size = _integer(args.get("size"), "size")
        digest = args.get("sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            _fail("INVALID_ARGUMENT", "sha256 must contain 64 hexadecimal characters.")
        self._capacity(owner, size)
        resolved = self._target(runtime, args.get("path"))
        parent_identity = _identity(resolved.path.parent)
        key, staged = self._allocate(size)
        self._transfers[key] = _Transfer(owner, runtime, "upload", args["path"], staged, size,
                                         digest.lower(), self.clock(), resolved.path,
                                         parent_identity)
        return self._status(key, self._transfers[key])

    def _upload_chunk(self, key, item, args):
        if item.committed:
            _fail("TRANSFER_ALREADY_COMMITTED", "Transfer has already been committed.")
        offset = _integer(args.get("offset"), "offset")
        encoded = args.get("data_base64")
        if not isinstance(encoded, str) or not encoded:
            _fail("INVALID_ARGUMENT", "A nonempty base64 chunk is required.")
        if len(encoded) > 4 * ((self.chunk_bytes + 2) // 3):
            _fail("TRANSFER_CHUNK_TOO_LARGE", "Chunk exceeds the configured limit.")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            _fail("INVALID_ARGUMENT", "Chunk is not valid base64.")
        if not data:
            _fail("INVALID_ARGUMENT", "Empty chunks are not allowed.")
        if len(data) > self.chunk_bytes:
            _fail("TRANSFER_CHUNK_TOO_LARGE", "Chunk exceeds the configured limit.")
        end = offset + len(data)
        if end > item.size:
            _fail("TRANSFER_SIZE_MISMATCH", "Chunk exceeds the declared file size.")
        if offset < item.next_offset and end <= item.next_offset:
            with item.staged.open("rb") as stream:
                stream.seek(offset)
                if stream.read(len(data)) != data:
                    _fail("TRANSFER_CHUNK_CONFLICT", "Duplicate chunk content differs.")
        elif offset != item.next_offset:
            _fail("TRANSFER_OFFSET_MISMATCH", "Chunk must start at next_offset.")
        else:
            with item.staged.open("r+b") as stream:
                stream.seek(offset)
                stream.write(data)
            item.next_offset = end
        return self._status(key, item)

    def _upload_commit(self, key, item):
        if item.committed:
            return dict(item.committed)
        if item.next_offset != item.size:
            _fail("TRANSFER_INCOMPLETE", "All bytes must be uploaded before committing.")
        with item.staged.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != item.sha256:
            _fail("TRANSFER_HASH_MISMATCH", "Uploaded content does not match the declared SHA-256.")
        with item.runtime.patch_lock:
            resolved = self._target(item.runtime, item.path)
            if resolved.path != item.target or _identity(resolved.path.parent) != item.parent_identity:
                _fail("TRANSFER_TARGET_CHANGED", "Upload destination changed after transfer began.", "security")
            with _pinned_parent(resolved.path.parent) as directory:
                again = self._target(item.runtime, item.path)
                if again.path != item.target or _identity(again.path.parent) != item.parent_identity:
                    _fail("TRANSFER_TARGET_CHANGED", "Upload destination changed during commit.", "security")
                temporary = ".upload-" + os.urandom(16).hex() + ".tmp"
                temporary_path = again.path.parent / temporary
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
                extra = {"dir_fd": directory} if directory is not None else {}
                descriptor = os.open(temporary if directory is not None else temporary_path, flags, 0o600, **extra)
                try:
                    with os.fdopen(descriptor, "wb") as output, item.staged.open("rb") as source:
                        while data := source.read(self.chunk_bytes):
                            output.write(data)
                        output.flush()
                        os.fsync(output.fileno())
                        # Windows 保持文件句柄打开，阻止临时文件在链接前被替换。
                        try:
                            if directory is None:
                                os.link(temporary_path, again.path)
                            else:
                                os.link(temporary, again.path.name, src_dir_fd=directory, dst_dir_fd=directory,
                                        follow_symlinks=False)
                        except FileExistsError:
                            _fail("TRANSFER_TARGET_EXISTS", "Upload destination already exists.")
                finally:
                    os.unlink(temporary if directory is not None else temporary_path, **extra)
        item.committed = {"ok": True, "transfer_id": key, "path": resolved.display,
                          "size": item.size, "sha256": item.sha256, "state": "committed"}
        self._remove_staged(item.staged)
        return dict(item.committed)

    def _download_begin(self, owner, runtime, args):
        raw = args.get("path")
        if not isinstance(raw, str) or not raw:
            _fail("INVALID_ARGUMENT", "path must be a nonempty string.")
        resolved = runtime.resolve_existing(raw)
        if not stat.S_ISREG(resolved.path.stat().st_mode):
            _fail("TRANSFER_NOT_REGULAR_FILE", "Download source must be a regular file.")
        with resolved.path.open("rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode):
                _fail("TRANSFER_NOT_REGULAR_FILE", "Download source must be a regular file.")
            self._capacity(owner, info.st_size)
            key, staged = self._allocate(info.st_size)
            digest, total = hashlib.sha256(), 0
            try:
                with staged.open("wb") as output:
                    while data := source.read(min(self.chunk_bytes, info.st_size - total + 1)):
                        total += len(data)
                        if total > info.st_size:
                            _fail("TRANSFER_SOURCE_CHANGED", "Download source grew while snapshotting.")
                        digest.update(data)
                        output.write(data)
                after = os.fstat(source.fileno())
                if total != info.st_size or (info.st_mtime_ns, info.st_ctime_ns) != (after.st_mtime_ns, after.st_ctime_ns):
                    _fail("TRANSFER_SOURCE_CHANGED", "Download source changed while snapshotting.")
                checked = runtime.resolve_existing(raw)
                if checked.path != resolved.path or _identity(checked.path) != (info.st_dev, info.st_ino):
                    _fail("TRANSFER_SOURCE_CHANGED", "Download source path changed while snapshotting.")
            except Exception:
                self._remove_staged(staged)
                raise
        item = _Transfer(owner, runtime, "download", resolved.display, staged, total,
                         digest.hexdigest(), self.clock(), next_offset=0)
        self._transfers[key] = item
        return {**self._status(key, item), "name": resolved.path.name}

    def _download_chunk(self, key, item, args):
        offset = _integer(args.get("offset"), "offset")
        limit = _integer(args.get("limit", self.chunk_bytes), "limit", 1)
        if limit > self.chunk_bytes:
            _fail("TRANSFER_CHUNK_TOO_LARGE", "Chunk exceeds the configured limit.")
        if offset > item.size:
            _fail("TRANSFER_OFFSET_MISMATCH", "Offset exceeds the snapshot size.")
        with item.staged.open("rb") as source:
            source.seek(offset)
            data = source.read(limit)
        return {"ok": True, "transfer_id": key, "offset": offset, "next_offset": offset + len(data),
                "data_base64": base64.b64encode(data).decode("ascii"), "eof": offset + len(data) == item.size}

    def _status(self, key, item):
        return {"ok": True, "transfer_id": key, "direction": item.direction,
                "state": "committed" if item.committed else "active", "size": item.size,
                "sha256": item.sha256, "next_offset": item.next_offset, "chunk_bytes": self.chunk_bytes,
                "expires_in_seconds": max(0, int(item.created + self.TTL_SECONDS - self.clock()))}

    def _discard(self, key):
        item = self._transfers.pop(key)
        self._remove_staged(item.staged)

    def _prune(self):
        for key, item in list(self._transfers.items()):
            if self.clock() >= item.created + self.TTL_SECONDS:
                self._discard(key)

    def close_owner(self, owner):
        with self._lock:
            for key, item in list(self._transfers.items()):
                if item.owner == owner:
                    self._discard(key)

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for key in list(self._transfers):
                self._discard(key)
            with self._cache_lock:
                if self._cache_created and list(self.private_root.iterdir()) == [self.private_root / self.MARKER_NAME]:
                    (self.private_root / self.MARKER_NAME).unlink()
                    self.private_root.rmdir()
