"""BF-specific MCP tools layered on top of pinned Windows-MCP."""

from __future__ import annotations

import json
import math
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Literal

import psutil
from fastmcp.tools.base import ToolResult
from fastmcp.utilities.types import Image
from mcp.types import TextContent, ToolAnnotations

from .application_registry import ApplicationRegistry, project_profiles
from .runtime import application_environment
from .window_control import WindowController


def _window_result(payload, capture=None) -> ToolResult:
    content = [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))]
    if capture and capture.get("path"):
        content.append(Image(path=capture["path"]).to_image_content())
    return ToolResult(content=content, structured_content=payload)


def _application_windows(windows):
    usable = []
    for window in windows:
        name = window.get("class_name", "")
        if name in {"GDI+ Hook Window Class", "PyInstallerOnefileHiddenWindow", "MSCTFIME UI", "IME"}:
            continue
        if name.startswith(".NET-BroadcastEventWindow"):
            continue
        rect = window.get("rect")
        if rect and not window.get("minimized") and (rect[2] - rect[0] < 32 or rect[3] - rect[1] < 32):
            continue
        usable.append(window)
    visible = [item for item in usable if item.get("visible") or item.get("minimized")]
    return visible or usable


def _existing_application_window(profile, controller, token):
    windows = []
    for process in psutil.process_iter(["pid", "exe"]):
        try:
            executable = process.info.get("exe")
            if not executable or Path(executable).resolve() != profile.executable:
                continue
            windows.extend(controller.inventory(
                token, include_hidden=True, title_filter=profile.title_contains,
                process_id=int(process.info["pid"]),
            )["windows"])
        except (psutil.Error, OSError):
            continue
    windows = _application_windows(windows)
    if len(windows) > 1:
        raise RuntimeError("multiple matching application windows; select a window explicitly")
    return windows[0] if windows else None


def _collect_descendant_pids(root_pid: int) -> set[int]:
    pids = {int(root_pid)}
    try:
        process = psutil.Process(int(root_pid))
        pids.update(int(child.pid) for child in process.children(recursive=True))
    except psutil.Error:
        pass
    return pids


def register_bf_tools(
    mcp,
    *,
    store,
    lease,
    allowed_root: Path,
    apps_dir: Path,
    controller_factory=None,
):
    controller_instance = None
    controller_lock = threading.Lock()

    def controller():
        nonlocal controller_instance
        with controller_lock:
            if controller_instance is None:
                controller_instance = (
                    controller_factory() if controller_factory is not None
                    else WindowController(store, desktop_lease=lease)
                )
        return controller_instance

    @mcp.tool(
        name="task_context",
        description="Create, inspect, or end a BF task context. Begin must provide a concrete project_path inside the configured workspace.",
        annotations=ToolAnnotations(
            title="Manage BF task context",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    def task_context(
        action: Literal["begin", "status", "end"] = "begin",
        bf_task_id: str | None = None,
        project_path: str | None = None,
    ) -> dict:
        if action == "begin":
            if not project_path:
                raise ValueError("project_path is required for begin")
            return store.begin(project_path)
        if not bf_task_id:
            raise ValueError("bf_task_id is required for status/end")
        if action == "status":
            return store.status(bf_task_id)
        return store.end(bf_task_id)

    @mcp.tool(
        name="task_diagnostics",
        description="Read task ownership and global foreground-desktop lease state without exposing the raw task secret.",
        annotations=ToolAnnotations(
            title="BF task diagnostics",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    def task_diagnostics(bf_task_id: str) -> dict:
        result = store.status(bf_task_id)
        result.update(lease.status(bf_task_id))
        result["raw_task_id_exposed"] = False
        return result

    @mcp.tool(
        name="prepare_browser_session",
        description="Prepare a task-private Edge or Chrome user-data directory and return launch arguments. Does not launch the browser.",
        annotations=ToolAnnotations(
            title="Prepare isolated browser session",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    def prepare_browser_session(
        bf_task_id: str,
        browser: Literal["edge", "chrome"] = "edge",
    ) -> dict:
        task_dir, record = store.require(bf_task_id)
        profile = task_dir / "browser" / browser
        profile.mkdir(parents=True, exist_ok=True)
        return {
            "browser": browser,
            "task_key": record["task_key"],
            "profile_path": str(profile),
            "launch_arguments": [
                f"--user-data-dir={profile}",
                "--no-first-run",
                "--no-default-browser-check",
            ],
            "note": "Use this profile only for this BF task; do not reuse another task's profile.",
        }

    @mcp.tool(
        name="WindowInventory",
        description="Enumerate top-level windows and return task-scoped opaque window_id values. This background operation does not acquire the global desktop lease.",
        annotations=ToolAnnotations(
            title="Read window inventory",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    def window_inventory(
        bf_task_id: str,
        include_hidden: bool = False,
        title_filter: str | None = None,
        process_id: int | None = None,
    ) -> dict:
        return controller().inventory(
            bf_task_id,
            include_hidden=include_hidden,
            title_filter=title_filter,
            process_id=process_id,
        )

    @mcp.tool(
        name="WindowScreenshot",
        description="Capture one bound window surface in the background. Minimized windows may be staged off-screen and restored.",
        annotations=ToolAnnotations(
            title="Capture one window",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    def window_screenshot(
        bf_task_id: str,
        window_id: str,
        include_frame: bool = False,
        allow_staging: bool = True,
    ) -> ToolResult:
        del include_frame  # client-area/native surface policy is backend-defined
        result = controller().screenshot(
            bf_task_id, window_id, allow_staging=allow_staging
        )
        return _window_result(result, result["window_capture"])

    @mcp.tool(
        name="WindowSnapshot",
        description="Read a bounded UI Automation tree for one task-owned window, optionally with a window-only screenshot.",
        annotations=ToolAnnotations(
            title="Read one window state",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    def window_snapshot(
        bf_task_id: str,
        window_id: str,
        include_image: bool = True,
        max_elements: int = 120,
        allow_staging: bool = True,
    ) -> ToolResult:
        result = controller().snapshot(
            bf_task_id,
            window_id,
            include_image=include_image,
            max_elements=max_elements,
            allow_staging=allow_staging,
        )
        return _window_result(result, result["window_snapshot"].get("capture"))

    @mcp.tool(
        name="WindowControl",
        description="Perform semantic background control on one task-owned window. Foreground fallback is refused unless explicitly enabled.",
        annotations=ToolAnnotations(
            title="Control one window",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=True,
        ),
    )
    def window_control(
        bf_task_id: str,
        window_id: str,
        action: Literal[
            "invoke",
            "set_value",
            "toggle",
            "select",
            "scroll_into_view",
            "click",
            "type",
            "shortcut",
        ],
        element_id: str | None = None,
        loc: list[int] | None = None,
        text: str | None = None,
        clear: bool = False,
        button: Literal["left", "right", "middle"] = "left",
        clicks: Literal[1, 2] = 1,
        shortcut: str | None = None,
        allow_foreground_fallback: bool = False,
    ) -> dict:
        return controller().control(
            bf_task_id,
            window_id,
            action=action,
            element_id=element_id,
            loc=loc,
            text=text,
            clear=clear,
            button=button,
            clicks=clicks,
            shortcut=shortcut,
            allow_foreground_fallback=allow_foreground_fallback,
        )

    @mcp.tool(
        name="LaunchApplication",
        description="Launch a registered application profile with shell=False, record its PID in the BF task, and bind its matching window. Data/source files are never launched by association.",
        annotations=ToolAnnotations(
            title="Launch registered application",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    def launch_application(
        bf_task_id: str,
        application_id: str,
        wait_seconds: float = 10.0,
    ) -> dict:
        task_dir, _ = store.require(bf_task_id)
        if not math.isfinite(float(wait_seconds)):
            raise ValueError("wait_seconds must be finite")
        profile = ApplicationRegistry(project_profiles(store, bf_task_id, apps_dir, application_id)).get(application_id)
        if profile.reuse_existing:
            existing = _existing_application_window(profile, controller(), bf_task_id)
            if existing:
                return {
                    "application_id": profile.app_id, "status": "existing_window",
                    "pid": existing["pid"], "owned_pids": [], "reused_existing": True,
                    "window": existing, "window_detected": True,
                    "note": "Existing application attached by exact executable; its process is not owned by this task.",
                }
        app_dir = task_dir / "applications" / profile.app_id
        instance_dir = app_dir / "instance"
        instance_dir.mkdir(parents=True, exist_ok=True)
        args = [value.replace("{instance_dir}", str(instance_dir)) for value in profile.args]
        stdout_path = app_dir / "stdout.log"
        stderr_path = app_dir / "stderr.log"
        # Never open a document by association and never discard startup errors.
        with stdout_path.open("ab") as stdout, stderr_path.open("ab") as stderr:
            process = store.spawn_owned(bf_task_id,
                [str(profile.executable), *args],
                cwd=str(profile.cwd) if profile.cwd is not None else None,
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                close_fds=True,
                env=application_environment(dict(os.environ)),
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        windows = []
        try:
            observed_pids = {int(process.pid)}
            store.add_owned_pid(bf_task_id, process.pid)
            deadline = time.monotonic() + max(0.0, min(float(wait_seconds), 60.0))
            while time.monotonic() <= deadline:
                observed_pids.update(_collect_descendant_pids(process.pid))
                for pid in sorted(observed_pids):
                    store.add_owned_pid(bf_task_id, pid)
                    windows = _application_windows(controller().inventory(
                        bf_task_id,
                        include_hidden=True,
                        title_filter=profile.title_contains,
                        process_id=pid,
                    )["windows"])
                    if windows:
                        break
                if windows:
                    break
                if process.poll() is not None and not any(
                    psutil.pid_exists(pid) for pid in observed_pids if pid != process.pid
                ):
                    break
                time.sleep(0.1)
            exit_code = process.poll()
        finally:
            if not windows:
                store.stop_owned(process)
        return {
            "application_id": profile.app_id,
            "status": "window_ready" if windows else (
                "exited_without_window" if exit_code is not None else "window_timeout"
            ),
            "reused_existing": False,
            "pid": process.pid,
            "exit_code": exit_code,
            "instance_directory": str(instance_dir),
            "logs": {"stdout": str(stdout_path), "stderr": str(stderr_path)},
            "owned_pids": sorted(observed_pids),
            "window": windows[0] if windows else None,
            "window_detected": bool(windows),
        }
