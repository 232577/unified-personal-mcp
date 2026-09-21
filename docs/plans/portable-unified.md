# Portable Unified Personal MCP implementation plan

Approved scope: one configurable Windows program, one public MCP catalog, coding-tools-mcp as host, BF desktop/browser/WebView2, selected DesktopCommanderMCP reuse, OpenAI Secure MCP Tunnel as the only remote transport. Public source repository: 232577/unified-personal-mcp.

The existing services remain operational. New installation configuration is per user and per machine; no machine name, drive, customer profile, credential or runtime database is embedded in source. Publication is authorized after verification and release-content inspection; replacing existing production services still requires a maintenance window.

## Task 1 — Fixed upstream and portable installation configuration

Preserve coding-tools-mcp 0.3.0 commit 5d6e131afebd89f98438b1c1dca8d157c0713c8a, Apache-2.0 LICENSE/NOTICE and original tests. Create an independent host environment, run unittest/compliance baseline, migrate scoped console/context overlays and MIT PATHEXT repair. Implement validated configuration, per-user state and secret-file references, setup/doctor commands and two independent installation fixtures. Never copy live auth/state. Files: personal_mcp/config.py, overlays/, tests_personal/test_config.py, third_party/.

## Task 2 — Authentication, workflows and project leases

Bearer authentication on loopback maps to one configured owner. No caller-supplied identity. Project selection is a strict child of configured workspace root, canonicalized, and cannot overlap private state. Each workflow owns its coding Runtime/CommandManager and BF task token. Begin request identifiers are payload-bound idempotency labels; retries never disclose a workflow capability. RESERVED expires without business processes; activation by owner+token races atomically with expiry. Tombstones persist; failed cleanup retains leases. Tests include same-owner collisions, conflicting payloads, path escapes, cross-workflow command/output IDs and end races. Files: workflows.py, protection.py, coding.py, tests_personal/.

## Task 3 — BF library extraction and native adapter

Import allowlisted U4A sources into bf_automation, retaining provenance. Remove import-time environment/log changes and Windows-MCP singleton resets. Explicit factory with one async execution thread, UIA thread initialization, ownership and desktop leases. No legacy HTTP proxy. Owned launch records exist before readiness wait; cleanup exceptions cannot hide resource leaks. Run migrated BF regression and real background native fixture checks. Files: bf_automation/, personal_mcp/bf.py.

## Task 4 — Unified host and catalog

Reuse upstream HTTP/protocol/auth machinery. Instance-level facade exposes 18 coding + 29 BF + UnifiedTask + SearchSession = 49 tools. Workflow context is required except server_info. Preserve structured errors/images, deny undeclared/internal arguments, preserve truthful annotations. No fallback to a broad workspace runtime. Tests through authenticated HTTP include complete catalog, cross-task rejection and zero unauthenticated side effects. Files: host.py, catalog.py.

## Task 5 — Desktop Commander progressive search

Port selected MIT 0.2.50 semantics with source attribution. Workflow-owned rg process, random IDs, nonnegative absolute cursor, separate result/byte limits, explicit running/completed/partial/failed/cancelled. Bounds: 2 per workflow, 4 global; 8 MiB and 10,000 results; 64 KiB response. No global process management, cloud pairing, telemetry, Office stack or implicit tail cursor. Tests cover Unicode chunks, huge lines, capacity, timeout and end/stop races.

## Task 6 — Chromium and WebView2 integration

Run migrated U3 ownership/unknown non-replay regression and four-workflow browser acceptance. Run U4A two-instance three-round fixture, form/upload/download/hash, native Save As, browser/window screenshots, occlusion, minimized report and attached non-termination. Fixtures and browser dependencies stay separate from the host. Only controlled project data; existing applications are not altered. Files: tests_personal/acceptance_*, fixtures/.

## Task 7 — Lifecycle, recovery and configuration program

Unify process/search/browser/hybrid release, retain unknown records and failed-cleanup leases. Verify crash/restart, PID reuse, launch timeout, disk errors and unrelated process survival. Provide a Windows configuration window with workspace, per-device label, tunnel ID and key-file selection; start/stop/status/doctor; hide secrets and background consoles. One installation program works from another directory or user home. No claim of physical second-machine testing without evidence.

## Task 8 — OpenAI tunnel and end-to-end client verification

Pinned tunnel-client verified from official release. Private backend bearer is separate from OpenAI runtime key; child environment references and private files only, never command-line values. Check readiness, device installation identity and one backend per configured tunnel. Use a separate candidate tunnel for real client acceptance, never compete with production using one tunnel ID. Local HTTP success cannot stand in for the current client's connection. Build→GUI→browser/native→assert→screenshots→end, with another workflow continuing.

## Task 9 — Portable package and public publication

Build reproducible Windows program/package, docs and sanitized example config; source/third-party license manifest; inspect all tracked content/history introduced by this project for secrets, machine/customer data and generated state. Fresh-directory install/doctor/smoke, appropriate tests and one independent final review. Publish to the approved public repository; verify remote commit and visibility. Credentials required by a new machine are supplied locally by its owner. Do not claim unified_candidate_verified while required acceptance is incomplete.

Every meaningful change follows RED → implementation → GREEN → relevant full regression. Record decisions, failures and checkpoints in this plan's private ledger. The detailed original BF acceptance requirements remain applicable, including attached non-termination, unknown non-replay and current-client evidence.
