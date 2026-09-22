# Reliable Remote Development Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development for independent components and main-agent integration. Every component receives fresh review before release. Steps use checkbox syntax for tracking.

**Goal:** Complete the approved connection recovery, workflow continuity, parallel control, and portable installation design.

**Architecture:** Retain the authenticated HTTP host and existing resource ownership. Add independent component supervisors, durable operation outcomes, explicit credential rotation, and short lifecycle admission locks. Installation validates packages before changing the next-login target.

**Tech Stack:** Windows, Python 3.13, fixed FastMCP dependencies, SQLite, DPAPI, Job Objects, Tkinter, Scheduled Tasks.

**Spec:** `docs/superpowers/specs/2026-09-22-reliable-remote-development-design.md` (approved by user: 完整方案).

## Global Constraints

- Preserve running projects, original BF/coding services, configuration and tunnel credentials.
- Fault injection uses isolated data, ports and owned processes only.
- Full local control remains bounded by the current Windows account.
- A timeout or reconnect never replays a dispatched side effect.
- Source, docs and package code use UTF-8. Use apply_patch for source changes.
- Source development is on integration/portable-unified; production executes its separate release directory.
- The user selected the full autonomous implementation option. Proceed through component tests, integration and review without another routine scope confirmation.
- Deliver the combined approved scope as 0.1.6; 0.1.4/0.1.5 are development milestones, not separate public packages. The next-login target changes only after package verification. Actual plugin acceptance waits for the authorized service switch.

## Review Focus

1. A future completing at the wait deadline must not regress from completed to unknown (Task 3).
2. Resume response loss followed by retry must return the same credential without another rotation (Task 4).
3. Alive but unready tunnel must not be duplicated or repeatedly killed (Task 1).
4. Closure during slow resource creation must neither leak the newly created resource nor block other projects (Task 4).
5. GUI opened while an external service is already running must show actual current/pending versions (Task 6).

## Task 1: Independent tunnel supervision

**Files:** `personal_mcp/tunnel.py`, `personal_mcp/service.py`, new `personal_mcp/tunnel_supervisor.py`, `tests_personal/test_tunnel_supervisor.py`, existing tunnel/service tests.

**Interfaces:** `TunnelSupervisor(config, binary, backend_key)` provides `start()`, `retry()`, `snapshot() -> dict`, `close()`. Snapshot contains status, last_success, error_code, recovery_attempts, next_retry. LocalService exposes a cached `health_snapshot()` and binds `runtime.health_provider` to it. Preserve service.status existing keys and add version/health.

- [x] Write red tests using fake owned runners, controlled time and events. Exercise dead child retry, alive/unready, missing key, close during startup and offline login.
```python
assert supervisor.snapshot()['status'] in {'recovering', 'degraded'}
assert runner.start_count == 1  # alive but unready
supervisor.close()
assert not supervisor.worker.is_alive()
```
- [x] Run `.venv-host/Scripts/python.exe -m pytest -q tests_personal/test_tunnel_supervisor.py` and record the initial failure.
- [x] Separate process creation from readiness. Only dead owned runners are disposed and retried with bounded exponential delay. Run supervision independently of registry expiration. Keep local service on transient initial connection failure; explicit close disables all future restarts.
- [x] Pass tunnel/service tests and verify no command starts with a visible console. Commit only owned component files.

## Task 2: BF connection generations

**Files:** `personal_mcp/bf/executor.py`, bootstrap only if real lifespan requires it, `tests_personal/test_bf_executor.py`, new `tests_personal/test_bf_reconnect.py`.

**Interfaces:** Keep `call(name, arguments, timeout=180)`. Add `submit(name, arguments) -> concurrent.futures.Future` and `snapshot() -> dict` using the same health fields. Retain lock/pending/drain compatibility with WorkflowResources. `close()` is terminal.

- [x] Write red tests: terminate a real fixed FastMCP client generation, reconnect with same server/store, simultaneous callers share one new generation, no resubmission, old callbacks cannot poison new health.
```python
future = executor.submit('Wait', {'bf_task_id': token, 'duration': 0})
assert not future.result(timeout=5)['isError']
assert server._bf_store is original_store
```
- [x] Run focused executor tests and capture failures before implementation.
- [x] Generation-bound coroutine/client references; never revive a live old thread. Register dispatched futures exactly once and archive them on exit. Verify real lifespan cleanup before assuming managers survive; keep business resources outside connection lifetime.
- [x] Pass real two-generation tests and existing executor/bootstrap/ownership tests; commit only owned files.

## Task 3: Durable operation results and health integration

**Files:** `personal_mcp/operations.py`, `personal_mcp/host.py`, `personal_mcp/catalog.py`, `tests_personal/test_operations.py`, new `tests_personal/test_operation_status.py`, new `tests_personal/test_health.py`.

**Interfaces:** `OperationJournal.run(owner, request_id, name, arguments, execute)` retains sync behavior; execute may return a Future. Add `status(owner, request_id) -> dict`, `result_policy() -> dict`. Host wraps OperationStatus in tool_result, uses stable resource key, routes BF writes through submit. Journal remains private; no raw credentials in logs/results metadata.

- [x] Red tests for synchronous result replay, late future, deadline race, request conflict, cross-owner lookup, restart unknown, bounded oversized result and result expiry.
```python
future.set_result({'ok': True})
assert journal.status(owner, request_id)['state'] == 'completed'
assert executions == 1
```
- [x] Migrate table additively; persist name/timestamps; register completion before waiting. Transactional updates cannot regress terminal results. Keep bounded result previews and private references; retain dedup tombstones after result eviction so old IDs never replay.
- [x] Add readonly OperationStatus schema. Add deterministic catalog_revision over names/schemas/annotations. server_info combines cached tunnel/BF states and owner-scoped resource snapshots without waiting on workflow operation gates.
- [x] Run journal/host/schema/health tests and commit the completed integration.

## Task 4: Credential resume and parallel lifecycle admission

**Files:** `personal_mcp/workflows.py`, new `personal_mcp/credentials.py`, new `tests_personal/test_workflow_resume.py`, new `tests_personal/test_workflow_parallel_control.py`; host/catalog integration stays with main agent.

**Interfaces:** `resume(owner, workflow_ref, request_id, expected_generation) -> dict`. `_row` resolves current credential to stable token_hash. `use(owner, token, write=False, control=False)` retains ownership/inflight; control bypasses write serialization only. `snapshot(owner) -> dict` returns cached counts/queue information. `list/status` include credential_generation.

- [x] Red tests: live resources survive rotation; old credential rejected; same request retry returns same new token; digest/generation conflict; cross-owner denial; expired/replaced cache rejected; slow project A factory leaves B usable; close during STARTING; control admission while a write waits.
```python
resumed = registry.resume(owner, ref, 'resume-1', 0)
assert resumed == registry.resume(owner, ref, 'resume-1', 0)
assert registry.resources[key] is original_resource
```
- [x] Add credential and resume-response tables. DPAPI protects short-lived cached credential response; never store raw workflow token. Resolve all old/new credentials through revocation state, including migrated legacy rows.
- [x] Check idempotent resume response before generation CAS. Lifecycle admission checks current state and credentials under short locks. Reserve STARTING under lock, initialize outside registry lock, commit under lock or clean up on close. Retain resources after bounded cleanup waits.
- [x] Run workflow/idle/recovery tests; commit only owned files. Main agent wires resume schema and routes.

## Task 5: Command control and discovery

**Files:** `personal_mcp/coding.py`, `personal_mcp/host.py`, `personal_mcp/catalog.py`, new `tests_personal/test_command_control.py`.

**Interfaces:** Workflow status may include owner-scoped command inventory. Control allowlist includes OperationStatus, polling write_stdin with empty chars, kill_command, read_output, and workflow status. No arbitrary tool bypass via readOnlyHint.

- [x] Red tests: a long write does not block status/cancel, nonempty stdin remains serialized, concurrent reads do not consume duplicate or missing byte ranges, wrong command owner rejected, cancellation idempotent.
```python
assert control_done.wait(1)
assert command_id in {item['command_id'] for item in snapshot['commands']}
```
- [x] Use per-command short locks for output snapshot/cursor movement, never across output waiting. Keep current command manager process ownership and bounded output buffers. Add status discovery without exposing another workflow's commands.
- [x] Run real isolated command tests and existing console/full-control/parallel tests. Commit after host integration passes.

## Task 6: Self-contained autostart and validated upgrade

**Files:** new `personal_mcp/autostart.py`, `personal_mcp/cli.py`, `personal_mcp/gui.py`, `scripts/build_portable.py`, autostart scripts, new `tests_personal/test_autostart.py` and GUI status tests.

**Interfaces:** Autostart manager provides status, enable(bundle, config), disable. Validate manifest, expected package version and isolated startup without a tunnel before replacing scheduled task. Save previous task XML/target for rollback. Status reports running_version and pending_version independently.

- [x] Write red tests for missing/corrupt file, manifest path escape, preserved task on failed smoke, packaged paths without repo scripts, GUI external-service status and hidden subprocess launches.
```python
with pytest.raises(ValueError):
    manager.enable(corrupt_bundle, config_path)
assert current_task() == previous_task
```
- [x] Implement current-user interactive-login task management using hidden subprocess or COM; no admin elevation. Bundle every required script/module. Expose CLI and GUI actions with understandable status. Background checks must not freeze GUI.
- [x] Run unit tests with isolated/mocked task names. Do not register or change the production task from component tests. Commit owned files.

## Task 7: Whole-system review and release

**Files:** `README.md`, recovery guide, package checks, version metadata, verification report.

- [x] Update recovery guidance and version to 0.1.6. Explain wait timeout vs process lifetime, explicit resume, parallel project directories, shared foreground limit, and refresh requirements.
- [x] Run full tests_personal/tests_bf and Ruff; run an independent fresh review of concurrency, credentials, supervisors and package installation. Fix consequential findings and rerun affected tests.
- [x] Pin compatible MCP Inspector outside production package, run authenticated tools/list and representative calls against isolated HTTP service. Save result evidence, not credentials. Do not assume client extension support.
- [x] Build clean 0.1.6 bundle with existing build_portable.py; verify manifests, ZIP, private-data exclusion and pythonw hidden-console behavior; run isolated packaged HTTP smoke and new-feature checks.
- [ ] Publish authorized source updates to existing public repository and verify Windows CI. Retain old validated bundle for rollback.
- [x] Register verified 0.1.6 as next-login target without stopping current service. Report current versus pending version accurately. Defer actual tunnel/plugin-new-schema verification until authorized runtime switch; record this remaining acceptance item explicitly.

## Preflight and progress

| Check | Result |
| --- | --- |
| Tool cache vs new resume/OperationStatus | New schema requires actual client refresh/verification after switching; local HTTP checks alone are insufficient |
| Credential stable key vs existing BF/jobs | Preserve token_hash as resource identity; only credential lookup rotates |
| Recovery vs replay | BF submit returns original future; journal completion observes it without second dispatch |
| Shutdown vs concurrent creation | STARTING reservation and inflight records survive bounded waits; resource cleanup remains owned |
| Packaging vs production | Build/test separate release directory; next-login registration does not stop service |

Progress is recorded in `verification/reliable-development-progress.md` with component tests, commits, review findings and remaining acceptance.
