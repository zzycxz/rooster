# Changelog

## [0.3.0] - 2026-05-26

### Added
- **MCP Market**: Full-featured MCP Server marketplace integrated into the Dashboard Skill Center. Users can browse, install, start, stop, restart, and uninstall MCP servers from a single UI panel with real-time status indicators (running/starting/installing/error/stopped).
- **MCP Server Process Manager (`mcp_runner.py`)**: Production-grade lifecycle manager for local MCP servers. Supports UV (Python) and npx (Node.js) dual-runtime isolation, automatic dependency installation, process supervision, health checking (30s interval), and crash auto-restart (max 3 consecutive attempts). Registry persisted to `.rooster/mcp/registry.json` for state recovery across restarts.
- **MCP Market API (`dashboard/src/routes/mcp.py`)**: 8 REST endpoints — `/api/mcp/market`, `/api/mcp/status`, `/api/mcp/install`, `/api/mcp/start`, `/api/mcp/stop`, `/api/mcp/restart`, `/api/mcp/uninstall`, `/api/mcp/health`. Start endpoint auto-registers MCP tools into `global_tool_registry`.
- **Built-in MCP Server Catalog**: 12 popular MCP servers pre-configured — filesystem, github, sqlite, brave-search, puppeteer, memory, slack, fetch, git, google-drive, postgres, sequential-thinking.
- **UV Integration**: `uv>=0.4.0` added as optional dependency (`pip install rooster[mcp]`). UV provides fast isolated venv creation for Python MCP servers. Falls back to `python -m venv + pip` if UV is unavailable.
- **Dashboard MCP Tab**: New "🔌 MCP Market" tab in Skill Center alongside Local and Skill Market tabs. Purple-themed card grid with runtime badge (Python/Node.js), live status indicator, URL display, and action buttons.
- **MCP Health Banner**: Warns users when neither UV nor npx is detected, with installation instructions.
- **Bilingual i18n**: Full English + Chinese translations for all MCP Market UI strings.

### Changed
- **Dashboard Skill Center**: Tab system extended from `local | market` to `local | market | mcp` with new MCP toolbar actions and registry badge.
- **`pyproject.toml`**: Added `mcp = ["uv>=0.4.0"]` optional dependency group; `all` target now includes `mcp`.

### Previous UX Upgrades (also in this release)
- **SubAgent Recursion Depth Guard**: Added `spawn_depth` field and `MAX_SUBAGENT_DEPTH=3` config to prevent infinite SubAgent recursion.
- **CJK-Aware Token Estimation**: Replaced hardcoded `chars/3.5` with `chunker.estimate_char_limit()` for accurate Chinese text token calculation.
- **Progressive Tool Feedback**: Replaced `asyncio.gather` with `asyncio.as_completed` for real-time tool response streaming to Dashboard.
- **Dashboard Pipeline State Flow**: Fixed `_updatePipeline` mapping + added `strategist_start`, `auditor_start`, `all_subtasks_done` lifecycle events so the pipeline correctly progresses through all 4 nodes.
- **Token/Usage Tracking**: Added `UsageInfo` model to `LLMResponseDelta` and `metrics.observe_tokens()` for prompt/completion token telemetry.
- **MCP Default ON**: Changed `MCP_DYNAMIC_ENABLED` default from `False` to `True`.
- **MCP SSE Transport**: Added `text/event-stream` SSE response parsing in `mcp_dynamic.py`.
- **Guardian Dynamic Sleep**: Replaced fixed 60s sleep with `_calculate_next_fire_delay` for drift-free scheduling.
- **Error Message Truncation**: Raw `str(e)` truncated to 100 chars with type classification for user-facing messages.
- **LTM Auto-Write**: Mission completion now auto-persists artifact paths, execution summaries, and tool traces to Long-Term Memory.

## [0.2.3] - 2026-05-25

### Added
- **Traffic Control & Queuing (`traffic.py`)**: Implemented Phase 1 of a production-grade Agent stability roadmap (inspired by OpenDevin). Added global and provider-level concurrency limits to the LLM client, effectively eliminating 429 rate-limit crashes during heavy parallel subtask execution.
- **Memory Compactor (`memory_compactor.py`)**: Shifted memory compaction out of the synchronous execution path. The system now distills context in the background asynchronously, dramatically improving the ReAct loop latency and overall execution smoothness.
- **Extended Guardian Triggers**: Upgraded `guardian.py` to support `cron` expressions and `interval`-based execution triggers for automated background tasks.

### Changed
- **Centralized Model Fallback**: Removed fragile, hardcoded `switch_provider` logic from `executor.py`. Fallbacks (handling timeouts, empty responses) are now strictly managed by the `LLMClient`, preventing the "brain-switching" protocol pollution that previously broke function-calling schemas.

### Fixed
- **Dashboard Guardian Status**: Fixed an incorrect path calculation (`os.path.dirname`) in `dashboard/src/routes/system.py` that caused the UI to erroneously report "Guardian not running". The Dashboard now accurately reflects the active daemon.
- **CI Formatting**: Ran `ruff format` on `src/utils/config/__init__.py` to resolve formatting discrepancies causing pipeline failures.

## [0.2.2] - 2026-05-25

### Added
- `Rooster.app` macOS launcher: single-line AppleScript, auto-activates venv, no extra terminal text
- Dashboard pre-built `src/ui/dist/` committed to repo (no Node.js needed after clone)
- Integrated full-featured visual download manager (AriaNg) directly into the Rooster Dashboard interface.
- Implemented dynamic, zero-hardcoding connections (`getAriaNgUrl`) to automatically bind local Aria2 RPC URL and tokens dynamically from configuration.
- Synchronized comprehensive English and Chinese translation keys for the Downloader tab and descriptions in both `dashboard.html` and `i18n.js`.

### Fixed
- macOS Python SSL certificate verification: auto-set `SSL_CERT_FILE` to certifi bundle in config init
- start.bat: auto-activate `.venv\Scripts\activate.bat` before running guardian
- Resolved Steps timeline stream merging issue where AI assistant stream text outputs were displayed word-by-word due to run ID mismatch (snake_case/camelCase keys) and early loops termination on undefined keys.
- Correctly restored and persisted `run_id` / `runId` in dashboard local storage cache to prevent log caching merge breakages.

### Changed
- Remove `start.command` (logic internalized into `Rooster.app`)
- `.gitignore`: exclude `src/ui/dist/` from global `dist/` ignore rule
- `.env.local.example`: add proxy and SSL_CERT_FILE template
- README: macOS launch instructions with Sequoia Gatekeeper note

## [0.2.1] - 2026-05-24

### Fixed
- pin starlette>=1.0.1 to avoid PYSEC-2026-161 CVE
- test: use tmp_path instead of hardcoded dummy_save.png
- ruff lint / format issues across source

### Changed
- README: switch to English, Chinese version as README.cn.md
- bilingual comments (Chinese / English) across all source files
- rewrite CONTRIBUTING.md
- clean up .env.local comments, add ZHIPU_GLM_KEY
- utils compat shim: lazy-load heavy dependencies

## [0.2.0] - 2026-05-22

- initial open-source release
