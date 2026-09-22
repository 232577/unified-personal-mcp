# BF connection generations implementation

Date: 2026-09-22. Branch: integration/portable-unified. Production service was not stopped or changed.

## Contract and implementation

- `call(name, arguments, timeout=180)` keeps its existing keyword-only timeout. `submit(name, arguments)` returns a concurrent Future; cancellation is rejected after dispatch so cancelling a waiter cannot erase an ongoing native operation.
- Each generation owns its ready Future, thread, event loop, client and dispatched outcomes. Reconnection is demand-driven on the next submission and allowed only after the old thread has conclusively exited and its outcomes have been archived. Concurrent callers share one bounded connection attempt (20 seconds).
- The same server, TaskStore, BrowserManager and HybridManager survive. Bootstrap needed no changes: the pinned FastMCP default lifespan owns no BF business resources.
- Pending operations are registered once, completed once and never replayed. Transport uncertainty resolves the original Future as `BF_CALL_OUTCOME_UNKNOWN`. Callers/journal retain that outcome; the executor does not accumulate an unbounded history.
- An old callback cannot overwrite new-generation health. Cached snapshot fields are status, last_success (Unix seconds), error_code, recovery_attempts and next_retry. next_retry is null because recovery is triggered by submissions.
- `close()` immediately forbids new submissions/recovery. It drains protocol outcomes and uncertain-generation cleanup fences, then requests shutdown; a still-live thread reports incomplete shutdown. A subsequent close can finish cleanup, but never reopens admission.
- Completed protocol outcomes do not prove that native side effects stopped. Connection exit or uncertain completion immediately sets the compatibility `failure` flag, preventing WorkflowResources from calling the resource idle while an original native worker remains alive. Existing store/browser/hybrid inventories remain responsible for actual owned resource lifetimes.

## Diagnostics

Submission errors: BF_EXECUTOR_CLOSED, BF_RECOVERING, BF_CONNECTION_UNAVAILABLE. Dispatched uncertain outcome: BF_CALL_OUTCOME_UNKNOWN. Health additionally reports BF_CONNECTION_LOST or BF_CALL_TIMEOUT. Drain uses BF_OPERATIONS_STILL_RUNNING; incomplete shutdown uses BF_SHUTDOWN_INCOMPLETE. No raw dependency exception is exposed through snapshot or outcome errors.

## Verification

Working directory for all commands: D:/run/unified-personal-mcp. Runtime: .venv-host/Scripts/python.exe, Python 3.13.14, FastMCP 4.0.3.

Initial red command:

```text
.venv-host/Scripts/python.exe -m pytest -q tests_personal/test_bf_executor.py tests_personal/test_bf_reconnect.py
4 failed, 2 passed in 2.13s
```

Failures demonstrated missing submit/generation support. Later focused red checks caught cached health remaining healthy during a slow real lifespan exit, and failure remaining null after a protocol Future completed while its native thread was still executing. Both were fixed and included below.

Final regression command (isolated project-local fixtures):

```text
.venv-host/Scripts/python.exe -m pytest -q tests_personal/test_bf_executor.py tests_personal/test_bf_reconnect.py tests_personal/test_bf_bootstrap.py tests_personal/test_app_ownership.py tests_personal/test_native_ownership.py tests_personal/test_workflow_resources.py tests_personal/test_hybrid_ownership.py --basetemp=.tmp/bf-reconnect-20260922-final
............................                                             [100%]
28 passed in 3.38s
Exit code: 0

.venv-host/Scripts/python.exe -m ruff check personal_mcp/bf/executor.py tests_personal/test_bf_executor.py tests_personal/test_bf_reconnect.py
All checks passed!
Exit code: 0
```

Coverage includes six simultaneous reconnecting callers, real lifespan enter/exit on both generations, exact TaskStore/BrowserManager/HybridManager identity, an isolated owned Python process surviving reconnect, timeout and cancellation without replay, slow exit refusing replacement, forced real FastMCP session cancellation producing unknown, native worker continuing after protocol cancellation, old callback isolation, and terminal close racing with slow reconnect startup. Native ownership and hybrid regression tests verify cleanup only affects owned fixtures.

The initial use of a project-local pytest basetemp failed because its parent .tmp did not exist (17 passed, 11 setup errors); creating that project-local directory fixed the environment and the final command above passed. Earlier red runs used pytest's normal isolated OS temporary directories.

Tool fallback: coding connector discovery/metadata was unavailable; execution used the authorized local interpreter, as confirmed by the parent task. No connector service was changed. Source edits used apply_patch.

## Limits

This component does not claim actual tunnel/client plugin acceptance, browser or WebView navigation preservation across a production fault, or a pythonw packaged-process smoke. It verifies manager identity and native owned PID preservation with pinned real FastMCP; whole-system/package acceptance belongs to the integration task. A live unresponsive old thread is intentionally never replaced, and a disconnected in-flight side effect is never assumed safe to repeat.

## Fresh-review correction: uncertain native cleanup barrier

Independent review found that a failed protocol Future was removed from pending before its asyncio.to_thread worker had stopped. The compatibility failure flag protected idle observation, but explicit WorkflowResources.close called drain directly and could end the TaskStore too early. A new real-session red test reproduced this: drain returned without raising while the native worker and generation were still alive.

Each generation now retains an owner-specific uncertainty fence. Any unknown outcome disables further admission to that generation and requests orderly stop, even if its client transport still appears connected. drain waits for protocol outcomes and then joins that owner's uncertain generation within the caller's remaining timeout; it raises BF_OPERATIONS_STILL_RUNNING while the old thread remains alive. An unrelated owner is not fenced. No native worker is force-killed or replayed.

Further pinned-runtime characterization exposed asyncio.run's five-minute default-executor shutdown grace period: the connection thread could exit even while a native worker continued. The regression compresses the actual CPython THREAD_JOIN_TIMEOUT to 0.02 seconds and reproduced this failure. The executor now joins its default native-worker executor without that internal grace timeout before allowing the connection generation to end. Public drain/close keep bounded waits and leave the generation alive and recoverable for a later cleanup retry.

The real cancelled-client test now also invokes actual WorkflowResources.close against a real TaskStore. It confirms the task remains active during the uncertain native operation, then can be ended only after the worker finishes and the original generation exits. A separate real MCP side effect followed by injected response loss confirms an apparently connected but uncertain generation retires without replay.

```text
.venv-host/Scripts/python.exe -m pytest -q tests_personal/test_bf_executor.py tests_personal/test_bf_reconnect.py tests_personal/test_bf_bootstrap.py tests_personal/test_app_ownership.py tests_personal/test_native_ownership.py tests_personal/test_workflow_resources.py tests_personal/test_hybrid_ownership.py tests_personal/test_workflows.py tests_personal/test_workflow_parallel_control.py tests_personal/test_workflow_resume.py --basetemp=.tmp/bf-drain-native-final
63 passed in 6.83s
Exit code: 0

.venv-host/Scripts/python.exe -m ruff check personal_mcp/bf/executor.py tests_personal/test_bf_reconnect.py
All checks passed!
Exit code: 0
```

The initial targeted red runs independently demonstrated premature drain, failure to retire a still-connected unknown generation, and CPython's timed-out native join. The final run above passed without warnings. All fixtures remained project-local and production services were untouched.

## Independent targeted re-review of 018e9f0

Date: 2026-09-22. Reviewer: independent tunnel/workflow implementation agent, who originally reproduced the premature-drain finding. Product source was read only during this re-review.

Result: the original P1 is resolved in the reviewed scope; no additional actionable defect was found in this targeted change.

- `_finished` records the uncertain owner under the executor lock before completing the caller's outcome Future. A waiter waking with BF_CALL_OUTCOME_UNKNOWN therefore cannot outrun creation of its cleanup fence.
- `drain` checks that generation's uncertainty inventory after waiting for protocol outcomes and joins the actual generation thread within its existing deadline. Unrelated owners are not fenced. New generations cannot supersede a live old thread, so replacement cannot erase a still-relevant fence.
- Unknown completion disables admission and retires even an apparently connected generation. Retained writes are not resubmitted.
- `_lifetime` explicitly awaits default-executor shutdown without the CPython Runner grace timeout, keeping the generation thread alive while its asyncio native workers continue. The formal test compresses the real CPython THREAD_JOIN_TIMEOUT to 0.02 seconds and confirms the thread/fence persist beyond that interval.
- The formal regression invokes actual WorkflowResources.close and TaskStore: cleanup is refused while the native operation remains unfinished, ownership stays active, and retry succeeds only after its original worker and generation exit.

Independent verification:

```text
.venv-host/Scripts/python.exe -m pytest -q tests_personal/test_bf_executor.py tests_personal/test_bf_reconnect.py tests_personal/test_workflow_resources.py --basetemp=.tmp/bf-review-018e9f0
20 passed in 2.44s
Exit code: 0
```

An additional isolated real FastMCP synchronous-tool probe (rather than the regression's explicit asyncio.to_thread tool) held native execution on an event, cancelled the client session, and observed BF_CALL_OUTCOME_UNKNOWN while the generation remained alive. `drain(owner, timeout=0.01)` raised until native work was released. The owned fixture exited cleanly afterward. No production service, tunnel or data was used.

This review verifies local connection/native-worker cleanup ordering. Existing limits for production switching, real tunnel/client acceptance, and manager-owned external resource inventories remain unchanged.
