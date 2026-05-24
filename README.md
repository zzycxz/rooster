# Rooster — Autonomous Multi-Agent Desktop OS

English | [中文](README.cn.md)

[![CI](https://github.com/zzycxz/rooster/actions/workflows/ci.yml/badge.svg)](https://github.com/zzycxz/rooster/actions/workflows/ci.yml)

> Version: 0.2.0 | Python >= 3.12 | License: MIT

---

## 1. Project Overview

Rooster is a **multi-role Agent framework** that autonomously handles complex tasks including desktop automation, web browsing, file processing, and data queries.

### Core Features

- **Multi-role collaboration**: Router (triage) → Strategist (planning) → Executor (execution) → Auditor (review)
- **Hybrid execution modes**: Solo (single-turn quick) / Mission (multi-step long-running) / Schedule (timed tasks)
- **Visual grounding**: YOLO-driven desktop UI element detection and manipulation
- **Hybrid browser**: httpx static scraping + Playwright dynamic rendering with automatic fallback
- **Long-term memory**: Embedding-based semantic memory retrieval + TTL retention + JSONL import/export
- **Streaming responses**: Real-time WebSocket push of Agent thoughts, tool calls, and execution progress
- **Multi-LLM failover**: Zhipu / MiMo / Jiutian / OpenAI / Anthropic / Kimi / Qwen / Cloud / Local — 10+ provider auto-switching
- **Gateway security**: API Key auth + HMAC signing + IP rate limiting + security headers + request size limits
- **Dashboard UI**: Real-time monitoring — 11 panels, bilingual (ZH/EN), mobile-responsive

---

## 2. Directory Structure

```
rooster/
├── .env                        # Non-sensitive config (model routing, behavior policies)
├── .env.local.example          # Secrets template
├── pyproject.toml              # Project config & dependencies
├── guardian.py                 # Process guardian (lifecycle, port cleanup, auto-restart)
├── start.bat / start.command   # Platform launch scripts
├── CONTRIBUTING.md             # Contribution guidelines
├── SECURITY.md                 # Security policy
│
├── resources/
│   └── models/                 # Vision model weights (committed to git, no extra download)
│       └── grounding/
│           └── icon_detect/
│               └── model.pt    #   YOLO UI element detection model (39 MB)
│
├── skills/                     # External skills (12 built-in)
│   ├── coding-agent/           #   Coding agent
│   ├── data-analysis/          #   Data analysis
│   ├── dev-tools/              #   Dev tools
│   ├── git-ops/                #   Git operations
│   ├── github/                 #   GitHub operations
│   ├── pdf-tools/              #   PDF tools
│   ├── resource-downloader/    #   Resource downloader
│   ├── self-improving/         #   Self-improvement
│   ├── summarize/              #   Summarization
│   ├── visual-control/         #   Visual control
│   ├── weather-query/          #   Weather query
│   └── web-search/             #   Web search
│
├── src/
│   ├── main.py                 # Entry: preflight + launch
│   ├── launcher.py             # Launcher: gateway + CLI + channels + memory warmup
│   │
│   ├── agents/                 # Core Agent roles
│   │   ├── protocol.py         #   Data protocol (MissionPlan / SubTask / Report / AuditVerdict)
│   │   ├── router.py           #   Entry router: Triage → SoloRunner / MissionRunner / Schedule
│   │   ├── reframer.py         #   Intent normalizer (vague → structured instructions)
│   │   ├── short_circuit.py    #   Short-circuit router (fast-path for common tasks)
│   │   ├── strategist.py       #   Strategist (DAG subtask decomposition + replan)
│   │   ├── executor.py         #   Executor (ReAct loop + tool dispatch)
│   │   ├── auditor.py          #   Auditor (AFFIRM / REMAND / REPLAN / ESCALATE)
│   │   ├── orchestrator.py     #   Tool orchestrator (visual strategy + self-healing)
│   │   ├── mission_tactician.py#   Tactician (DAG topo sort + parallel grouping)
│   │   ├── mission_blackboard.py#  Shared blackboard for concurrent subtasks
│   │   ├── reflection_engine.py#   Reflection engine (error pattern analysis)
│   │   ├── llm_client.py       #  LLM client (multi-provider rotation + cooldown + backoff)
│   │   ├── prompt_builder.py   #   5-layer System Prompt builder
│   │   ├── tool_dispatch.py    #   Tool call extraction & execution
│   │   └── runners/
│   │       ├── solo_runner.py  #     Single-turn quick mode
│   │       └── mission_runner.py#    Multi-step mission mode
│   │
│   ├── toolset/                # Tool registry (55 tools, 32 exposed to LLM)
│   │   ├── base.py             #   BaseTool base class (platform / kit / fc_hidden)
│   │   ├── registry.py         #   Global tool registry (auto-discovery + schema validation)
│   │   └── definitions/        #   Tool implementations (22 modules)
│   │       ├── browser.py          #   Browser (nav / fetch / act / batch_fetch)
│   │       ├── visual_control.py   #   Desktop visual control (grounding_scan / read_screen / act)
│   │       ├── file_system.py      #   File system (file_system_op — read/write/list/search/mkdir)
│   │       ├── office.py           #   Office (excel_op / docx_write / pdf_op)
│   │       ├── interpreter.py      #   Python execution (E2B sandbox / local)
│   │       ├── exa_search.py       #   Search (4-tier fallback chain)
│   │       ├── subagent.py         #   SubAgent orchestration
│   │       ├── task_manager.py     #   Task management
│   │       ├── task_scheduler.py   #   Scheduled tasks (Windows schtasks / macOS launchd)
│   │       ├── email.py            #   Email sending
│   │       ├── ocr.py              #   OCR text extraction
│   │       ├── memory.py           #   Long-term memory write
│   │       └── ...                 #   22 definition files total
│   │
│   ├── gateway/                # HTTP / WebSocket gateway
│   │   ├── server.py           #   FastAPI application factory
│   │   ├── auth.py             #   API Key auth + HMAC + rate limiting
│   │   ├── security.py         #   Security headers + request size limits
│   │   ├── run_manager.py      #   Run task management (with global cancel)
│   │   ├── connection_manager.py#  Node connection pool
│   │   ├── dashboard_ws.py     #   Dashboard WebSocket push
│   │   ├── event_handler.py    #   Agent events → WebSocket broadcast
│   │   ├── local_node.py       #   Local controlled desktop node
│   │   ├── metrics.py          #   Prometheus metrics
│   │   ├── stream.py           #   Streaming protocol
│   │   └── routes/
│   │       ├── websockets.py   #     WebSocket endpoints
│   │       ├── config.py       #     /api/config read/write
│   │       ├── memory.py       #     /api/memory CRUD
│   │       ├── models.py       #     /api/models (Ollama / HuggingFace)
│   │       ├── skills.py       #     /api/skills marketplace
│   │       └── system.py       #     /api system endpoints
│   │
│   ├── channels/               # Input channels
│   │   ├── cli.py              #   Console interaction (bilingual /lang switch)
│   │   ├── feishu.py           #   Feishu/Lark bot (lazy-loaded)
│   │   ├── webhook.py          #   HTTP Webhook channel
│   │   └── registry.py         #   Channel registry
│   │
│   ├── models/                 # LLM provider adapters
│   │   ├── factory.py          #   ModelFactory (provider → client factory)
│   │   ├── openai_adapter.py   #   OpenAI-compatible adapter
│   │   ├── anthropic_adapter.py#   Anthropic Claude adapter
│   │   └── vision_strategy.py  #   Vision strategy
│   │
│   ├── memory/                 # Memory system
│   │   ├── manager.py          #   LTM core (embedding + retrieval + decay)
│   │   ├── backends.py         #   JSON / Markdown storage backend
│   │   ├── soul_loader.py      #   SOUL.md / USER.md loading + 5-layer prompt build
│   │   ├── embeddings.py       #   Embedding vector generation
│   │   ├── semantic_search.py  #   Semantic search
│   │   ├── dedup.py            #   Memory deduplication
│   │   ├── indexer.py          #   Full-text indexer
│   │   ├── compactor.py        #   Memory compaction
│   │   └── watcher.py          #   File system watcher (hot reload)
│   │
│   ├── evolution/              # Self-evolution engine
│   │   ├── engine.py           #   Signal detection → LLM extraction → auto-write SOUL/USER
│   │   ├── soul_writer.py      #   SOUL.md append-only writer
│   │   └── user_writer.py      #   USER.md writer
│   │
│   ├── sessions/               # Session management
│   │   └── store.py            #   Global session store (atomic write)
│   │
│   ├── prompts/                # Prompt templates (Markdown)
│   │   ├── base.md             #   Base behavior protocol
│   │   ├── strategist.md       #   Strategist prompt
│   │   ├── executor.md         #   Executor prompt
│   │   ├── auditor.md          #   Auditor prompt
│   │   ├── replan.md           #   Replan prompt
│   │   ├── router_triage.md    #   Router triage prompt
│   │   └── intent_reframer.md  #   Intent reframer prompt
│   │
│   └── utils/                  # Utilities
│       ├── config/             #   Config system
│       │   ├── _base.py        #     Env var reading helpers
│       │   ├── _settings.py    #     Composite Settings
│       │   ├── loader.py       #     Config loader (deprecated — .env is the sole config source)
│       │   ├── providers.py    #     LLM provider config
│       │   ├── runtime.py      #     Runtime config
│       │   ├── hardware.py     #     Hardware/vision config
│       │   ├── channels.py     #     Channel config
│       │   └── memory.py       #     Memory config
│       ├── security/           #   Security modules
│       │   ├── path_guard.py   #     Path guard (symlink bypass prevention)
│       │   ├── state_guard.py  #     State guard
│       │   ├── advanced_guard.py#    Jailbreak detection
│       │   ├── input_guard.py  #     Input validation
│       │   ├── secrets_mask.py #     Log secret masking
│       │   └── tool_rate_limiter.py # Per-tool rate limiting
│       ├── vision/             #   Vision engine (YOLO)
│       ├── browser/            #   Browser tools (Playwright)
│       └── audit/              #   Audit tools
│
├── tests/                      # Test suite (132 tests)
└── .rooster/                   # Runtime data (gitignored)
    ├── SOUL.md                 #   Agent soul file
    ├── USER.md                 #   User profile file
    ├── project_memory.json     #   Long-term memory
    ├── schedules.json          #   Scheduled tasks
    ├── sessions/               #   Session data
    └── logs/                   #   Logs
```

---

## 3. Core Architecture

### Request Processing Flow

```
User Message (CLI / Feishu / WebSocket / Dashboard)
    │
    ▼
Router (Triage)
    │
    ├─ TALK ─────► SoloRunner (quick reply) ──► Response
    ├─ BLOCK ────► Safety intercept ──► Response
    ├─ SCHEDULE ─► Scheduled task registration → schedules.json
    │
    ├─ DIRECT ───┐
    │             │  (clear task, skip to planning)
    │             ▼
    └─ REFRAME ► Reframer (intent normalization) ───┐
                       Vague → Structured            │
                                                     ▼
                                            ShortCircuit (fast-path check)
                                              ├─ Match → Direct execution (skip planning)
                                              └─ No match ↓
                                             MissionRunner
                                                   │
                                             ┌─────┴─────┐
                                             ▼           ▼
                                        Strategist   Executor
                                        (DAG split)  (ReAct + tools)
                                             │           │
                                             └─────┬─────┘
                                                   ▼
                                               Auditor
                                              (quality review)
                                                   │
                                             ┌─────┴─────┐
                                             ▼           ▼
                                           AFFIRM    REMAND / REPLAN / ESCALATE
                                          (continue)  (redo / replan / escalate)
```

### 5-Layer System Prompt Architecture

```
Layer 1: SOUL.md         — Agent soul / personality (highest priority)
Layer 2: USER.md         — User profile / preferences
Layer 3: Skills digest   — Installed skills summary
Layer 4: LTM context     — Long-term memory semantic recall
Layer 5: Base prompt     — Role prompt (strategist.md / executor.md / etc.)
```

### LLM Provider System

Multi-provider automatic failover, degrading by priority:

| Provider | Env Variable | Notes |
|:---|:---|:---|
| Zhipu CodingPlan | `ZHIPU_KEY` | Enhanced coding GLM, current primary |
| Zhipu GLM Standard | `ZHIPU_GLM_KEY` | Standard API fallback |
| Xiaomi MiMo | `MIMO_KEY` | Lightweight, default for reframer |
| Jiutian MoMA | `JIUTIAN_KEY` | Dual-model (large/small) auto-routing |
| OpenAI | `OPENAI_KEY` | GPT-4o etc. |
| Anthropic Claude | `ANTHROPIC_KEY` | Native Messages API |
| Kimi (Moonshot) | `KIMI_KEY` | Moonshot AI |
| Qwen (Tongyi Qianwen) | `QWEN_KEY` | DashScope compatible |
| Cloud | `CLOUD_KEY` | Generic OpenAI-compatible |
| Local | `LOCAL_KEY` | llama.cpp / Ollama local inference |

### Tool System

55 tools registered, 32 exposed to LLM for Function Calling (23 are internal/legacy). Grouped by Kit:

| Kit | Core Tools | Capabilities |
|:---|:---|:---|
| Browser | `browser_nav`, `browser_act`, `web_fetch`, `batch_web_fetch` | Web browsing & scraping |
| Search | `exa_search`, `linkup_search` | Multi-engine search (4-tier fallback) |
| Vision | `desktop_grounding_scan`, `desktop_act`, `desktop_read_screen` | Desktop UI control |
| FileSystem | `file_system_op` | File read/write/list/search/mkdir/download |
| Office | `excel_op`, `office_docx_write`, `pdf_op` | Excel / Word / PDF |
| Interpreter | `python_interpreter` | Python execution (E2B sandbox / local) |
| Memory | `memory_add_fact` | Long-term memory write |
| Task | `task_manager`, `task_scheduler` | Task management + scheduled tasks |
| SubAgent | `subagent_spawn`, `subagent_result` | SubAgent orchestration |
| Comms | `email_send`, `feishu_push_file` | Email / Feishu push |
| Multimedia | `multimedia_download`, `movie_downloader` | Resource downloads |
| OCR | `ocr_extract` | Image text extraction (PaddleOCR) |
| Plan | `plan_mode` | Planning mode (pause → user review → continue) |
| System | `tool_info`, `skill_read`, `wait_until` | Meta-tools |

### Security System

| Layer | Mechanism | Details |
|:---|:---|:---|
| Gateway auth | API Key (`X-API-Key` / `Authorization: Bearer`) | Skipped when `GATEWAY_API_KEY` is empty (local dev) |
| Webhook signing | HMAC-SHA256 | Enabled when `WEBHOOK_HMAC_SECRET` is set |
| Rate limiting | IP sliding window (100 req/min) | Localhost automatically exempt |
| Security headers | CSP / X-Frame-Options / X-Content-Type-Options | Global middleware |
| Request size | 1 MB limit | Prevents oversized request bodies |
| Input validation | Config key whitelist + value length limits | /api/config/save endpoint |
| File sandbox | PathGuard (realpath + prefix) | Prevents symlink bypass |
| Jailbreak detection | AdvancedGuard | Detects prompt injection / skill poisoning |
| Log masking | secrets_mask | Auto-masks secrets in logs |
| Tool rate limiting | tool_rate_limiter | Per-tool quota rate limiting |

---

## 4. Quick Start

### Option A: Local Installation (Recommended)

```bash
# 1. Clone and install (Dashboard is pre-built, no Node.js needed)
git clone https://github.com/zzycxz/rooster.git
cd rooster
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 2. Configure API keys (at least one LLM key required)
cp .env.local.example .env.local
# Edit .env.local — fill in at least one API Key

# 3. Launch (guardian mode with auto-restart)
python guardian.py
```

Dashboard opens automatically at `http://localhost:8765/dashboard`.

> **First-time user notes:**
> - Ready to use after `git clone` — model weights and Dashboard frontend are included in the repo
> - If the browser doesn't open automatically, navigate to `http://localhost:8765/dashboard`
> - Dashboard supports bilingual ZH/EN (Language button at sidebar bottom)
> - Only one LLM API key is needed to run (Zhipu recommended — has free tier)
> - Ollama users: install Ollama and pull a model first

### macOS Notes

Core features (LLM, browser, file ops, Dashboard) work identically on macOS. Known differences:

| Feature | macOS Status | Notes |
|:---|:---|:---|
| Visual desktop control | Partial | Screenshots + pyautogui work; UIA window scanning unavailable |
| `window_visible` wait | Needs permission | Grant Accessibility access to Terminal/Python in System Settings |
| Playwright | Needs install | `playwright install chromium` |
| YOLO grounding | Manual install | `pip install -e ".[vision]"` |

### Option B: Dashboard First-Run Setup

On first launch, the Dashboard automatically detects whether `.env.local` is configured. If not:

1. Open `http://localhost:8765/dashboard`
2. Click the "Setup" tab on the left
3. Select an LLM provider → Enter API Key → Click "Save Config"
4. The system auto-restarts with the new config
5. Use "Test Connection" to verify provider connectivity

The Setup panel also includes:
- **Ollama Guardian**: Detect local Ollama status, pull models, assign roles
- **HuggingFace Model Browser**: Search/download GGUF models, import to Ollama or launch llama.cpp
- **Role Matrix**: Assign providers to Router / Strategist / Executor / Auditor / Solo individually

---

## 5. Dashboard

Dashboard is a single-page web app (Alpine.js + Tailwind) with 11 panels:

| Panel | Features |
|:---|:---|
| **Execution** | Agent chat + real-time Pipeline visualization (Router→Strategist→Executor→Auditor status) + session management + image paste |
| **Steps** | Detailed timeline of every Agent action (tool args / return values), with filter and search |
| **Logs** | Real-time log stream (level filter + search + export + stack trace expand) |
| **Errors** | Error collection (stack traces + fix suggestions) |
| **Tools** | Tool call history (args + results + duration) + per-tool success/fail/latency stats |
| **Skills** | Installed skill management (load/unload/test/fix-deps) + ClawHub online marketplace |
| **Memory** | Memory facts browser (search / delete / decay) + SOUL.md / USER.md editor |
| **Metrics** | Active sessions/subtasks/requests + LLM/Tool/HTTP latency percentiles + Guardian watchdog status |
| **Config** | .env config view (grouped by category, secrets masked) |
| **Setup** | 10 provider cards + Ollama manager + HF model browser + role matrix + failover config + danger zone |
| **Health** | Service connectivity check + CPU / Memory / Disk / Network / Top processes |

---

## 6. Integration Guide

### 6.1 WebSocket API (Recommended)

Gateway listens on `ws://127.0.0.1:8765/ws/gateway` by default.

**Send a task:**

```json
{
  "method": "chat.send",
  "params": {
    "sessionKey": "my_session_001",
    "message": "Search for Python asyncio usage"
  },
  "id": "req_001"
}
```

**Cancel execution:**

```json
{
  "method": "chat.cancel",
  "params": { "sessionKey": "my_session_001" },
  "id": "cancel_001"
}
```

### 6.2 HTTP API

**System endpoints:**

| Endpoint | Method | Description |
|:---|:---|:---|
| `/api/health` | GET | Health check (LLM + .env.local) |
| `/api/version` | GET | Version number |
| `/api/cancel` | POST | Global cancel all running tasks |
| `/api/metrics/summary` | GET | JSON metrics summary |
| `/metrics` | GET | Prometheus metrics |
| `/api/system/stats` | GET | System resources (CPU / memory / disk / network) |
| `/api/guardian/status` | GET | Guardian watchdog status |
| `/api/sessions` | GET | Session list |
| `/api/toolset` | GET | Registered tools (grouped by Kit) |
| `/api/security/status` | GET | Security configuration status |

**Config endpoints** (`/api/config`):

| Endpoint | Method | Description |
|:---|:---|:---|
| `/api/config/save` | POST | Save config to .env.local (auto-restart) |
| `/api/config/reload` | POST | Hot-reload .env files (no restart) |
| `/api/config/models` | GET | Configured provider list |
| `/api/config/masked` | GET | Masked full config |
| `/api/config/test` | GET | Test provider connectivity |

**Memory endpoints** (`/api/memory`):

| Endpoint | Method | Description |
|:---|:---|:---|
| `/api/memory/stats` | GET | Memory statistics |
| `/api/memory/facts` | GET | Memory facts list |
| `/api/memory/facts/{id}` | DELETE | Delete a memory |
| `/api/memory/decay` | POST | Trigger memory decay |
| `/api/memory/soul` | GET / PUT | SOUL.md read/write |
| `/api/memory/user` | GET / PUT | USER.md read/write |

**Skills endpoints** (`/api/skills`):

| Endpoint | Method | Description |
|:---|:---|:---|
| `/api/skills` | GET | Installed skills list |
| `/api/skills/market` | GET | ClawHub skill marketplace |
| `/api/skills/install` | POST | Install a skill |
| `/api/skills/uninstall` | POST | Uninstall a skill |
| `/api/skills/reload` | POST | Hot-reload skill cache |
| `/api/skills/toggle` | POST | Enable / disable a skill |
| `/api/skills/test` | POST | Test a skill |

**Model endpoints** (`/api/models`):

| Endpoint | Method | Description |
|:---|:---|:---|
| `/api/models/ollama/scan` | GET | Scan local Ollama models |
| `/api/models/ollama/pull` | POST | Pull Ollama model |
| `/api/models/ollama/apply` | POST | Assign model to role |
| `/api/models/ollama/delete` | POST | Delete Ollama model |
| `/api/models/hf/search` | GET | Search HuggingFace GGUF models |
| `/api/models/hf/download` | POST | Download HF model |
| `/api/models/hf/import/ollama` | POST | Import to Ollama |
| `/api/models/hf/import/llamacpp` | POST | Launch llama.cpp server |

### 6.3 CLI

```bash
python guardian.py
# Enters interactive CLI

# Available commands:
/new      - Start a new session
/list     - List sessions
/switch   - Switch session
/model    - Switch model
/proxy    - Proxy control (status / on / off)
/lang     - Switch language (zh/en)
/exit     - Exit
```

### 6.4 Node WebSocket

```
WS /ws/gateway   — Main gateway WebSocket (Dashboard push)
WS /ws/dashboard — Dashboard real-time updates
WS /v1/node/ws   — Controlled desktop node (with auth_required handshake)
```

---

## 7. Key Configuration

> See the `.env` file for the full list (80+ config items). Only core items listed here.

### Required: At Least One LLM Key

```ini
# Recommended (Zhipu — has free tier)
ZHIPU_KEY=your_key

# Or other providers (any one is sufficient)
OPENAI_KEY=your_key
ANTHROPIC_KEY=your_key
MIMO_KEY=your_key
JIUTIAN_KEY=your_key
KIMI_KEY=your_key
QWEN_KEY=your_key
CLOUD_KEY=your_key
```

### Gateway Security

```ini
GATEWAY_API_KEY=your-secret-key    # Leave empty to skip auth (local dev)
WEBHOOK_HMAC_SECRET=your-hmac      # Webhook signing key
```

### Role Model Assignment

```ini
STRATEGIST_MODEL_MODE=zhipu        # Strategist (default: zhipu)
EXECUTOR_MODEL_MODE=jiutian        # Executor (default: jiutian)
AUDITOR_MODEL_MODE=jiutian         # Auditor (default: jiutian)
ROUTER_MODEL_MODE=zhipu            # Router (default: zhipu)
SOLO_MODEL_MODE=jiutian            # Solo chat (default: jiutian)
```

### Failover

```ini
LLM_FAILOVER_ENABLED=true
LLM_FAILOVER_ORDER=jiutian,zhipu,mimo,local
LLM_FAILOVER_RETRY_MAX=2
```

### Network / Proxy

```ini
GATEWAY_PORT=8765
OLLAMA_URL=http://localhost:11434         # Ollama management API
HF_ENDPOINT=https://huggingface.co        # HuggingFace mirror (China: hf-mirror.com)
# HTTP_PROXY=http://127.0.0.1:7897        # Configure in .env.local
```

---

## 8. Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Lint
ruff check src/ tests/
```

### Adding a New Skill

Create a directory and `SKILL.md` under `skills/`:

```yaml
---
name: my-skill
description: "Skill description"
metadata:
  rooster:
    emoji: "🔧"
    platform: ["any"]
    category: "automation"
    requires:
      python_packages: ["pandas"]
      bins: ["git"]
---
```

### Adding a New Tool

Create a Python file under `src/toolset/definitions/`, inheriting `BaseTool`:

```python
from toolset.base import BaseTool
from pydantic import BaseModel

class MyToolArgs(BaseModel):
    query: str

class MyTool(BaseTool):
    name = "my_tool"
    description = "A custom tool"
    kit = "custom"
    args_schema = MyToolArgs

    async def run(self, **kwargs):
        return {"result": "done"}
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for detailed guidelines.

---

## 9. Debugging Reference

| Issue | Check First |
|:---|:---|
| Startup failure | Console output — preflight checks indicate missing API keys |
| Task not executing | `src/agents/router.py` → `handle_inbound()` |
| Subtask timeout | `src/agents/strategist.py` → timeout parameter |
| Tool call failure | `src/agents/tool_dispatch.py` → `_execute_tool_with_healing()` |
| LLM call failure | `src/agents/llm_client.py` → Provider switching logic |
| Vision grounding inaccurate | `src/utils/vision/grounding.py` (requires `pip install -e ".[vision]"`) |
| Dashboard disconnected | Check `GATEWAY_API_KEY` config and browser console |
| Feishu channel not starting | Normal — auto-skipped when `lark-oapi` is not installed |

### Common Issues

| Symptom | Cause | Solution |
|:---|:---|:---|
| "No LLM API keys" error | .env.local not configured | `cp .env.local.example .env.local` and fill in a key |
| Web scraping returns empty | Anti-scraping block | Check HTTP_PROXY or `playwright install chromium` |
| Vision tool error | Missing YOLO dependencies | `pip install -e ".[vision]"` |
| Dashboard shows disconnected | Auth mismatch | Ensure browser has auth header injected, or clear `GATEWAY_API_KEY` |
| Tool registration failed | BaseTool subclass missing name/description/run | Refer to `toolset/base.py` contract |
| Ollama connection failed | Ollama not running or wrong port | Check `OLLAMA_URL` config, default `http://localhost:11434` |

---

## License

MIT
