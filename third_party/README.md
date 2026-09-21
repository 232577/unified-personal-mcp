# Component provenance

| Component | Fixed source | Use |
| --- | --- | --- |
| coding-tools-mcp | [xyTom/coding-tools-mcp](https://github.com/xyTom/coding-tools-mcp), 5d6e131afebd89f98438b1c1dca8d157c0713c8a (0.3.0), Apache-2.0 | Preserved host and coding implementation; original history, LICENSE and NOTICE retained |
| DesktopCommanderMCP | [wonderwhy-er/DesktopCommanderMCP](https://github.com/wonderwhy-er/DesktopCommanderMCP), a781f5a4b8cfebac6638bc6fcbd38fca6326be53 (0.2.50), MIT | PATHEXT normalization and progressive search adaptation; see the [detailed provenance](DesktopCommanderMCP/PROVENANCE.md) |
| BF automation | Owner-supplied U4A candidate sources | Extracted into bf_automation; rewritten ownership, lifecycle, profiles and portable host integration |
| Windows-MCP | [CursorTouch/Windows-MCP](https://github.com/CursorTouch/Windows-MCP), PyPI 0.8.5, MIT | Library factory with an explicit allowlist; no singleton server or analytics client |
| Playwright / Chromium | Playwright 1.63.0, Chromium build 1243 | Separate worker processes and private browser contexts |
| ripgrep | [15.1.0](https://github.com/BurntSushi/ripgrep/releases/tag/15.1.0), MIT or Unlicense | Bounded project search with owned process cleanup |
| OpenAI tunnel-client | 0.0.14+0f870e50a973fa820d4c409000059e181e8d242b, Apache-2.0 | Only remote transport; distributed resources retain release LICENSE, NOTICE and dependency license inventory |

No hosted Desktop Commander code, cloud pairing service or remote subscription is incorporated. No arbitrary process-kill feature was ported.

Python package versions are pinned in requirements-windows.lock; the [97-package metadata inventory](python-dependencies.json) records the installed license declarations. Licenses of transitive dependencies must be considered separately from the top-level MIT/Apache licenses. Windows-MCP currently depends on fuzzywuzzy and Levenshtein (GPL); public source publishing does not include their binaries or the private assembled installation. The personal build downloads these packages from their own distribution source and retains installed license files.

The archived workflows in docs/upstream/workflows are upstream references, not active publishing or deployment jobs.

