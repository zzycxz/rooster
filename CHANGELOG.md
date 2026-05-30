# Changelog

## [0.3.7] - 2026-05-30

### Added
- **网络代理管控闭环**：支持通过 Dashboard 前端界面一键启停全局网络代理及动态修改 Proxy URL，并通过 `dotenv` 实时固化到 `.env.local`。结合新加入的 `ModelFactory.clear_instances()` 强杀机制，大模型客户端代理热重载现已无需重启后台服务。
- **细粒度网络异常审计**：为底层的超时异常增强了精确日志捕获，现可精准区分打印 `ConnectTimeout`（连接阻断）与 `ReadTimeout`（推理卡顿），有效指引排查网络瓶颈。
- **回复信息格式化**：优化智能体回复内容的排版与结构化展示，支持 Markdown 格式输出，提升消息可读性
- **智能体用户确认**：关键操作前增加用户确认机制，智能体执行敏感动作前需获取用户授权，防止误操作

### Fixed
- **执行沙箱硬编码超时清理**：清理了代码解释器工具 (`Interpreter`) 中遗留的本地与云端执行超时上限，现由全局统一配置 `INTERPRETER_TIMEOUT_SECONDS` 接管控制。
- **大模型长推理超时截断**：引入流式分段超时策略，区分网络握手与读取时间。将子任务存活时长 (`SUBTASK_MIN_TIMEOUT`) 等核心阈值统一上收至 `RuntimeConfig`，解决长推理任务被提前阻断的问题。
- **系统修复**：修复若干已知问题，提升系统稳定性与运行可靠性

## [0.3.6] - 2026-05-29

### Fixed
- **飞书消息碎片化**：非流式通道（飞书）收到 30+ 碎片气泡，改为缓冲合并后单条发送（`router.py`）
- **Dashboard 保存报错**：`FEISHU_APP_ID/APP_SECRET/USER_OPEN_ID` 缺失白名单导致 400（`security.py`）
- **ARIA2 配置不生效**：`ARIA2_TOKEN` 与 `ARIA2_RPC_SECRET` 命名不一致，统一为 `ARIA2_TOKEN`（`channels.py` + `multimedia.py`）
- **Strategist FAILSAFE**：`STRATEGIST_MODEL_MODE` 与 `STRATEGIST_MODEL_NAME` 不匹配（zhipu provider + mimo model），导致规划降级
- **SOUL 身份漂移**：MiMo 模型用默认身份自我介绍，加 Rooster 身份锚定（`SOUL.md`）
- **pywinauto / lark_oapi 警告**：过滤 SyntaxWarning 和 pkg_resources UserWarning（`main.py`）
- **Strategist base_prompt 路径**：使用 `__file__` 构建绝对路径，避免 CWD 依赖导致 src/src 双层路径
- **Dashboard 脱敏覆盖密钥**：保存配置时脱敏值写回 `.env.local`，覆盖真实 API Key（已知问题，待修复）

### Added
- **CLAUDE.md**：系统说明书，架构速查、核心原则、已知问题
- **docs/ 完整文档集**：PRD、SPEC、goal、plan、todo、stability_analysis、interaction_design 等
- **skills/email-139**：139 邮箱 SMTP 发送技能
- **tests/test_llm_providers.py**：LLM 提供商连通性测试
- **tests/test_strategist.py**：Strategist JSON 输出能力测试
- **tests/test_health.py**：LLM 健康检查（延迟 + 状态）
- **Strategist 诊断日志**：FAILSAFE 时打印 LLM 原始返回前 500 字符，定位 JSON 解析失败原因
- **tab-security.html**：Dashboard 安全管理页面

### Changed
- `.gitignore` 清理乱码行、添加 tests/ 和 debug 文件规则
- 删除根目录 `test_agent_full.py`、`test_search.py`（移至 tests/）
- README 版本号更新至 0.3.6

## [0.3.5] - 2026-05-27

### Fixed
- **Executor Stability**: Enhanced executor handling of output truncation, blackboard state, ambiguity detection, and intent audit logic to improve production reliability.
- **LLM Adapter Payload Validation**: Added strict payload validation in the LLM adapter layer to catch malformed requests before they reach upstream APIs.
- **Search Fallback & Audit Tuning**: Improved search tool fallback behavior and tuned auditor scoring thresholds for more accurate result evaluation.
- **Guardian Cron**: Fixed `NameError` in guardian cron trigger handling; corrected `pypdf` package name in dependencies.
- **Production Stability**: Comprehensive production hardening across core modules — strengthened error handling, input validation, and edge-case coverage.

### Added
- **Dashboard Partial Split**: Refactored dashboard into partial templates with backend multi-module restructuring for better maintainability.
- **Test Coverage**: Added guardian cron and executor function-calling protocol tests; fixed CI branch configuration.

### Changed
- **Core Module Architecture**: Backend modules reorganized for clearer separation of concerns.

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
