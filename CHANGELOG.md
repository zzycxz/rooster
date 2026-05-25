# Changelog

## [0.2.3] - 2026-05-25

### Added
- **Traffic Control & Queuing (`traffic.py`)**: Implemented Phase 1 of a production-grade Agent stability roadmap (inspired by OpenDevin). Added global and provider-level concurrency limits to the LLM client, effectively eliminating 429 rate-limit crashes during heavy parallel subtask execution.
- **Asynchronous Dreaming (`dream_worker.py`)**: Shifted memory compaction out of the synchronous execution path. The system now distills context in the background (Dreaming), dramatically improving the ReAct loop latency and overall execution smoothness.
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
