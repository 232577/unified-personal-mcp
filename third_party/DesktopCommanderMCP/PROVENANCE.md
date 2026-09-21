# Desktop Commander source reuse

Local open-source core: https://github.com/wonderwhy-er/DesktopCommanderMCP

Pinned release: 0.2.50; commit a781f5a4b8cfebac6638bc6fcbd38fca6326be53.
License: MIT, reproduced alongside this file. The proprietary hosted Remote
Desktop Commander service is not redistributed or used by this program.

`personal_mcp/overlays/windows_environment.py` adapts
`src/terminal-manager.ts#getRepairedPathExt` to operate on a caller-supplied,
already filtered environment. It never reads the parent environment again.

Progressive search will adapt the bounded session contract from
`src/search-manager.ts`, with workflow ownership, opaque identifiers,
nonnegative absolute cursors and explicit output limits. No cloud channel,
telemetry, global search singleton or unrestricted kill-by-PID is imported.
