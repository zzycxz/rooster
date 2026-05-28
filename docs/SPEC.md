# Rooster 技术规格说明书 (SPEC)

> **版本**: v1.0 | **日期**: 2026-05-27 | **作者**: zzycxz
> **关联**: [PRD.md](PRD.md) 产品需求文档

---

## 一、系统架构

### 1.1 整体架构图

```
┌─────────────────────────────────────────────────────────────┐
│                      Client Layer                           │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │
│  │   CLI    │  │ Dashboard│  │   飞书    │  │ Webhook  │   │
│  │  Rich UI │  │  Alpine  │  │  Lark    │  │  HTTP    │   │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘   │
│       │              │              │              │         │
├───────┼──────────────┼──────────────┼──────────────┼─────────┤
│       ▼              ▼              ▼              ▼         │
│  ┌─────────────────────────────────────────────────────┐    │
│  │              Gateway (FastAPI + Uvicorn)             │    │
│  │  ┌─────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐  │    │
│  │  │  Auth   │ │ Security │ │   Rate   │ │  WSS   │  │    │
│  │  │ API Key │ │ Headers  │ │ Limiter  │ │Manager │  │    │
│  │  └────┬────┘ └──────────┘ └──────────┘ └───┬────┘  │    │
│  └───────┼────────────────────────────────────┼────────┘    │
│          ▼                                    ▼             │
│  ┌──────────────┐                  ┌──────────────────┐     │
│  │ MessageRouter │                  │ Dashboard SubApp │     │
│  │  process_run()│                  │  routes + ws     │     │
│  └──────┬───────┘                  └──────────────────┘     │
│         │                                                   │
├─────────┼───────────────────────────────────────────────────┤
│         ▼                                                   │
│  ┌─────────────────────────────────────────────────────┐   │
│  │                 Agent Layer                          │   │
│  │                                                     │   │
│  │  ┌────────┐   ┌──────────┐   ┌──────────┐          │   │
│  │  │ Router │──▶│Reframer  │──▶│Strategist│          │   │
│  │  │triage  │   │(optional)│   │(optional)│          │   │
│  │  └───┬────┘   └──────────┘   └────┬─────┘          │   │
│  │      │                            │                 │   │
│  │      ▼                            ▼                 │   │
│  │  ┌──────────┐             ┌──────────────┐         │   │
│  │  │SoloRunner│             │MissionRunner │         │   │
│  │  │(简单任务) │             │ (复杂任务)    │         │   │
│  │  └────┬─────┘             └──────┬───────┘         │   │
│  │       │                          │                  │   │
│  │       ▼                          ▼                  │   │
│  │  ┌─────────────────────────────────────────┐       │   │
│  │  │           AgentExecutor (ReAct)          │       │   │
│  │  │  ┌────────┐ ┌──────────┐ ┌──────────┐   │       │   │
│  │  │  │ Prompt │ │Tool      │ │Reflection│   │       │   │
│  │  │  │ Builder│ │Dispatch  │ │ Engine   │   │       │   │
│  │  │  └────────┘ └──────────┘ └──────────┘   │       │   │
│  │  └─────────────────────────────────────────┘       │   │
│  │                      │                              │   │
│  │                      ▼                              │   │
│  │               ┌──────────┐                         │   │
│  │               │ Auditor  │ (optional, leaf nodes)  │   │
│  │               └──────────┘                         │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│                    Infrastructure                            │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │
│  │  LLM     │  │ Toolset  │  │  Memory  │  │ Guardian │   │
│  │ Providers│  │ Registry │  │  Manager │  │ Watchdog │   │
│  │ 10+ fail │  │ 46+ tools│  │  LTM+FTS │  │  Daemon  │   │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘   │
└─────────────────────────────────────────────────────────────┘
```

### 1.2 请求处理时序

```
User Input
    │
    ▼
Router.handle_inbound()
    │
    ├─ _triage_via_llm()                    [LLM #1: Router model]
    │   → "[TALK]" / "[DIRECT]" / "[REFRAME]" / "[SCHEDULE]" / "[BLOCK]"
    │
    ├─ [TALK] ──▶ SoloRunner.run()
    │               │
    │               └─ AgentExecutor.run()   [LLM #2..N: Executor model]
    │                    │
    │                    └─ while True:
    │                         llm_response = chat_stream(history)
    │                         tool_calls = parse(llm_response)
    │                         results = dispatch(tool_calls)
    │                         history.append(llm_response, results)
    │                         if done: break
    │
    ├─ [DIRECT] ──▶ SoloRunner.run()        (目标: 跳过 Strategist)
    │               └─ (同上)
    │
    ├─ [REFRAME] ──▶ Reframer.reframe()     [LLM #2: Reframer model]
    │                    │
    │                    ▼
    │                MissionRunner.run()
    │                    │
    │                    ├─ Strategist.plan_stream()  [LLM #3: Strategist model]
    │                    │   └─ yields SubTask(s) as stream
    │                    │
    │                    ├─ DAG Scheduler
    │                    │   └─ topological sort + parallel grouping
    │                    │
    │                    ├─ AgentExecutor.run(ST1)    [LLM #4..N]
    │                    ├─ AgentExecutor.run(ST2)    [LLM #4..N] (parallel)
    │                    │   ...
    │                    │
    │                    └─ Auditor.review(leaf_nodes) [LLM #M]
    │                         ├─ AFFIRM → continue
    │                         ├─ REMAND → retry with full context
    │                         └─ ESCALATE → replan
    │
    ├─ [SCHEDULE] ──▶ _handle_schedule()
    │                   └─ write .rooster/schedules.json
    │
    └─ [BLOCK] ──▶ security block message
```

---

## 二、核心数据模型

### 2.1 Agent 协议模型 (`src/agents/protocol.py`)

```python
class SubTask(BaseModel):
    id: str                          # 子任务唯一标识
    instruction: str                 # 自然语言指令
    domain: str                      # 领域标签 (e.g., "search", "file", "web")
    tool: Optional[str]              # 指定工具名
    depends_on: List[str] = []       # 依赖的前置子任务 ID
    on_failure: str = "ESCALATE"     # 失败策略: CONTINUE / ESCALATE / RETRY
    sub_agent_mode: str = "NORMAL"   # 执行模式
    race_group: Optional[str]        # 竞争组 ID
    requires_confirm: bool = False   # 是否需要用户确认
    timeout: int = 300               # 超时秒数

class MissionPlan(BaseModel):
    goal: str                        # 原始用户目标
    subtasks: List[SubTask]          # 子任务列表
    replan_count: int = 0            # 重规划次数
    max_replans: int = 2             # 最大重规划次数
    status: str = "PENDING"          # PENDING / RUNNING / DONE / FAILED

class Report(BaseModel):
    subtask_id: str                  # 对应子任务 ID
    status: str                      # SUCCESS / FAILED / PARTIAL
    observation: str                 # 执行结果文本（给下游/用户看）
    evidence: List[str] = []         # 证据列表（URL/文件路径/数据片段）
    artifacts: List[str] = []        # 产出文件路径
    snapshot: Optional[str]          # 截图路径（桌面操作时）
    error: Optional[str]             # 错误信息
    token_usage: Optional[dict]      # Token 消耗统计

class AuditVerdict(BaseModel):
    verdict: str                     # AFFIRM / REMAND / REPLAN / CLOSURE / ESCALATE
    routing: str                     # 目标路由
    findings: str                    # 审计发现文本
    score: float = 0.0               # 质量评分 (0-1)
```

### 2.2 会话模型 (`src/sessions/models.py`)

```python
class Message(BaseModel):
    role: str                        # "system" / "user" / "assistant" / "tool"
    content: str                     # 消息内容
    tool_calls: Optional[list]       # 工具调用列表
    tool_call_id: Optional[str]      # 工具响应对应的调用 ID
    name: Optional[str]              # 工具名称
    timestamp: float                 # 时间戳

class Session(BaseModel):
    session_id: str                  # 会话唯一 ID
    history: List[Message]           # 消息历史
    metadata: dict = {}              # 元数据 (model_override, channel, etc.)
    created_at: float                # 创建时间
    updated_at: float                # 最后更新时间
```

### 2.3 记忆模型 (`src/memory/models.py`)

```python
class MemoryFact(BaseModel):
    id: str                          # 事实唯一 ID
    type: str                        # DECISION_LOG / TOOL_RESULT / ARTIFACT_CREATED
                                     # RESEARCH_FINDING / USER_PREFERENCE / CORRECTION
    content: str                     # 事实内容
    source_session: str              # 来源会话 ID
    confidence: float                # 置信度 (0-1)
    created_at: float                # 创建时间
    last_accessed_at: float          # 最后访问时间
    access_count: int = 0            # 访问次数
    ttl_days: int = 90               # 生存天数
    embedding: Optional[List[float]] # 嵌入向量
```

### 2.4 LLM 适配器接口 (`src/models/base.py`)

```python
class LLMResponseDelta(BaseModel):
    content: Optional[str]           # 文本内容增量
    tool_calls: Optional[list]       # 工具调用增量
    reasoning_content: Optional[str] # 推理内容 (<think> tags)
    usage: Optional[UsageInfo]       # Token 用量
    finish_reason: Optional[str]     # stop / tool_calls / length

class BaseModelClient(ABC):
    @abstractmethod
    async def chat_stream(
        self,
        messages: list[dict],
        model: str,
        tools: Optional[list] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs
    ) -> AsyncGenerator[LLMResponseDelta, None]: ...

    @abstractmethod
    async def chat_non_stream(
        self,
        messages: list[dict],
        model: str,
        **kwargs
    ) -> LLMResponseDelta: ...

    async def close(self): ...
```

---

## 三、模块接口规格

### 3.1 Router (`src/agents/router.py`)

**职责**: 请求入口分拣器，决定走哪条执行路径。

**核心方法**:
```
Router.handle_inbound(message, channel, event_handler) -> None
  ├── _triage_via_llm(message) -> str     # 返回 "[TALK]"|"DIRECT"|...
  ├── _handle_talk(message, channel)      # TALK 路径
  ├── _handle_direct(message, channel)    # DIRECT 路径
  ├── _handle_reframe(message, channel)   # REFRAME 路径
  └── _handle_schedule(message, channel)  # SCHEDULE 路径
```

**分诊 Prompt**: `src/prompts/router_triage.md` v5.1

**单例模式**: `Router.get_instance()` 全局唯一实例。

### 3.2 AgentExecutor (`src/agents/executor.py`)

**职责**: ReAct 循环核心引擎，所有任务的最终执行者。

**核心循环**:
```
AgentExecutor.run(session_history, tools, event_handler) -> Report
  1. Build system prompt (PromptBuilder.compose())
  2. while step < max_steps:
     a. Check context limit → trigger compaction if needed
     b. Call LLM stream → collect delta
     c. Parse tool calls (native FC or XML fallback)
     d. Check stuck detection (repeated signatures)
     e. Dispatch tools via tool_dispatch.py
     f. Append [response, tool_results] to history
     g. If FINAL_REPORT detected → break
  3. If max_steps reached → emergency summary
  4. Write session history back to SessionStore
```

**关键配置**:
| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `AGENT_MAX_STEPS` | 20 | 最大 ReAct 步数 |
| `AGENT_STUCK_THRESHOLD` | 4 | 重复工具调用检测阈值 |
| `AGENT_EMPTY_RETRY_MAX` | 2 | 空响应最大重试 |
| `CONTEXT_LIMIT` | 60000 | Token 上下文限制 |

**Prompt 构建**: 五层架构
```
Layer 1: SOUL.md         — Agent 灵魂（soul_loader 加载）
Layer 2: USER.md         — 用户画像
Layer 3: Skills digest   — 已安装技能摘要
Layer 4: LTM context     — 长期记忆语义召回
Layer 5: Base prompt     — executor.md + runtime supplement
```

### 3.3 Strategist (`src/agents/strategist.py`)

**职责**: 复杂任务的 DAG 拆分规划。

**接口**:
```
Strategist.plan(user_request) -> MissionPlan       # 非流式，一次性返回
Strategist.plan_stream(user_request) -> AsyncGenerator[SubTask]  # 流式，实时产出
Strategist.replan(failed_report, original_goal) -> MissionPlan   # 动态重规划
```

**流式 JSON 解析机制**:
- 使用手写深度计数器实时解析 LLM 输出的 JSON
- `{` 计数器递增，`}` 递减，depth=0 时提取完整 JSON 对象
- `json.loads()` 失败 → 全量 fallback regex → 再失败 → FAILSAFE 降级

**Prompt**: `src/prompts/strategist.md` v10.0，输出格式:
```json
{
  "goal": "用户原始目标",
  "subtasks": [
    {
      "id": "ST1",
      "instruction": "自然语言指令",
      "domain": "search",
      "tool": "exa_search",
      "depends_on": [],
      "on_failure": "CONTINUE",
      "sub_agent_mode": "NORMAL"
    }
  ]
}
```

### 3.4 Auditor (`src/agents/auditor.py`)

**职责**: 叶节点执行结果的质量审计。

**接口**:
```
Auditor.review(report, subtask, mission_plan, phase) -> AuditVerdict
```

**审计维度**:
1. **意图对齐**: 结果是否满足子任务 instruction
2. **来源可信度**: 信息来源分级 (官方 > 权威媒体 > 用户生成 > 未知)
3. **数据收敛**: 数值型数据的一致性检查
4. **完整性**: 证据链是否完整

**降级策略**: LLM 超时或异常 → `PASS_WITH_WARNING`，不阻塞流程。

### 3.5 MissionRunner (`src/agents/runners/mission_runner.py`)

**职责**: 多步任务的编排调度器。

**执行流程**:
```
MissionRunner.run(message, channel, ...)
  1. Strategist.plan_stream() → SubTask list
  2. Build DAG (topological sort)
  3. Execute phases:
     For each parallel_group in DAG:
       ├─ Spawn AgentExecutor per subtask (asyncio.gather)
       ├─ Collect Reports to Blackboard
       └─ For leaf nodes → Auditor.review()
            ├─ AFFIRM → continue
            ├─ REMAND → retry with full observation
            └─ ESCALATE → replan
  4. Final synthesis → deliver result
```

**Checkpoint 机制**:
```
checkpoint_path = .rooster/checkpoints/{session_id}.json
save: after each subtask completion
load: on startup, resume from last checkpoint
```

**子任务上下文构建**:
```python
context_parts = []
if dep_results:
    context_parts.append("前置任务结果：\n" + "\n".join(dep_results))
if previous_report:
    context_parts.append(f"上次执行结果：\n{previous_report.observation[:3000]}")
if previous_audit_cmd:
    context_parts.append(f"审计官修正指令：\n{previous_audit_cmd}")
```

### 3.6 Reframer (`src/agents/reframer.py`)

**职责**: 模糊意图的语义重构。

**双层处理**:
```
Reframer.reframe(message) -> ReframeResult
  Layer 1: StaticRuleEngine (regex, 0ms)
    ├─ 影视触发词 → movie_downloader 指令
    ├─ 软件触发词 → software search 指令
    └─ 命中则直接返回，不调用 LLM
  Layer 2: LLM 重构 (fallback)
    ├─ 结构化意图 → 返回
    ├─ CLARIFICATION_NEEDED → 触发歧义确认
    └─ REDIRECT → 重新分流到 Router
```

### 3.7 ToolDispatch (`src/agents/tool_dispatch.py`)

**职责**: 工具调用的解析与执行编排。

**解析策略**:
```
ToolDispatch.parse_tool_calls(llm_response) -> List[ToolCall]
  1. Try native Function Calling format (OpenAI FC schema)
  2. If failed → XML fallback with balanced-brace JSON extraction
  3. If still failed → return empty (LLM will retry)
```

**执行流程**:
```
ToolDispatch.dispatch(tool_call)
  ├── Pre-dispatch: path resolution (semantic path mapping)
  ├── Permission check (sandbox policy)
  ├── Input guard (path traversal / injection scan)
  ├── Rate limit check (per-tool limiter)
  ├── Code safety gate (AST check for python_interpreter)
  ├── Blackboard resource lock (parallel safety)
  ├── Tool execution
  │   └── On failure → ReflectionEngine self-heal
  ├── Vision strategy (screenshot handling)
  └── Post-dispatch: output truncation
```

### 3.8 ToolRegistry (`src/toolset/registry.py`)

**职责**: 全局工具注册与发现。

**注册流程**:
```
ToolRegistry.__init__()
  └── pkgutil.iter_modules("toolset/definitions/")
      └── For each module:
          └── Find BaseTool subclasses → register()
              └── Platform filter (skip if platform mismatch)
```

**Schema 输出**:
```
get_all_tool_schemas() -> List[dict]          # 完整 schema
get_all_fc_schemas() -> List[dict]            # OpenAI FC 格式
get_fc_schemas_for_prompt(context) -> List[dict]  # 按 Kit 路由的精简 schema
clone() -> ToolRegistry                       # ISOLATED 子 Agent 用
```

### 3.9 ToolRouter (`src/toolset/router.py`)

**职责**: Kit-based FC schema 选择器，减少每步的 token 消耗。

**路由逻辑**:
```
ToolRouter.route(message, context) -> List[dict]
  1. Always include "system" kit (tool_info, skill_read, wait_until)
  2. Keyword match → select relevant kits
  3. If matched tools < min_threshold → fallback to full set
  4. Return FC schemas for selected kits
```

**配置**:
| 变量 | 默认值 | 说明 |
|------|--------|------|
| `TOOL_ROUTER_ENABLED` | true | 启用 Kit 路由 |
| `TOOL_ROUTER_MAX_TOOLS` | 15 | 单步最大工具数 |
| `TOOL_ROUTER_RULES_JSON` | (内置) | Kit→keyword 映射规则 |

---

## 四、LLM 供应商管理

### 4.1 供应商 Failover 流程

```
LLMClient.chat_stream()
  │
  ├─ Select primary provider (from role config)
  │
  ├─ Try primary
  │   ├─ Success → return
  │   ├─ Rate limit (429) → cooldown provider, try next
  │   ├─ Timeout → cooldown provider, try next
  │   └─ Empty response → retry (max 2)
  │
  ├─ Try failover providers (in priority order)
  │   └─ Skip cooled-down providers
  │
  └─ All failed → raise LLMError
```

### 4.2 Circuit Breaker

```
cooldown_state = {
    "zhipu": {"until": 1716800000, "reason": "rate_limit"},
    "mimo": {"until": 1716800100, "reason": "timeout"},
}
```

- **冷却时长**: 指数退避，base=60s, max=600s
- **持久化**: 写入 `.rooster/cooldown_state.json`，Guardian 重启后恢复
- **恢复**: 冷却到期后自动恢复，下次调用重试该 Provider

### 4.3 Provider 适配器

| 适配器 | 文件 | 协议 | 特殊处理 |
|--------|------|------|---------|
| OpenAI 兼容 | `openai_adapter.py` | OpenAI Chat Completions | 大部分云端 Provider 共用 |
| Anthropic | `anthropic_adapter.py` | Claude Messages API | 原生 tool_use, <think> blocks |
| 本地 Ollama | via `openai_adapter.py` | OpenAI 兼容 | `LOCAL_KEY` 为空时跳过 |

---

## 五、安全规格

### 5.1 安全层级矩阵

| 层级 | 组件 | 机制 | 延迟 | 触发条件 |
|------|------|------|------|---------|
| L0 网关 | `auth.py` | API Key 验证 | <1ms | 每个请求 |
| L0 网关 | `security.py` | 安全头 + 大小限制 | <1ms | 每个请求 |
| L1 输入 | `input_guard.py` | 路径遍历/URL/命令注入扫描 | 1-5ms | 工具调用前 |
| L1 输入 | `advanced_guard.py` | 越狱检测/提示注入扫描 | 5-20ms | 用户输入时 |
| L2 运行时 | `path_guard.py` | 文件系统路径前缀校验 | <1ms | 文件工具调用 |
| L2 运行时 | `code_safety.py` | Python AST 安全检查 | 1-3ms | 代码执行前 |
| L2 运行时 | `tool_rate_limiter.py` | per-tool 滑动窗口限流 | <1ms | 每次工具调用 |
| L3 隐私 | `privacy_router.py` | 目录+PII 路由本地模型 | 5-20ms | LLM 调用前 |
| L3 隐私 | `secrets_mask.py` | 日志凭证脱敏 | <1ms | 日志写入前 |

### 5.2 输入防护详情

**PathGuard**:
```python
def check_path(path: str) -> bool:
    resolved = os.path.realpath(path)
    for allowed in ALLOWED_PATH_PREFIXES:
        if resolved.startswith(allowed):
            return True
    return False  # 拒绝访问
```

**AdvancedGuard** 越狱检测模式:
- Level 1: 精确匹配 (DAN, "ignore previous instructions")
- Level 2: 正则匹配 (角色扮演越狱模式)
- Level 3: 语义分析 (prompt injection 特征)

**Skill 投毒检测**:
- 扫描 SKILL.md 中的 `eval()`, `exec()`, base64 混淆
- 检测隐藏的网络请求 (`fetch`, `curl` in non-network skills)
- 检测 description 中夹带的系统指令

### 5.3 隐私隔离

```
PrivacyRouter.route(message, tool, context) -> str
  │
  ├─ Check LOCAL_DIRS: if path in local dirs → "local"
  ├─ Check PII: presidio scan on content → "local" if PII found
  ├─ Check type: memory/compaction → "local"
  ├─ Check vision: screenshot → OCR + desensitize first
  └─ Default → "cloud"
```

---

## 六、记忆系统规格

### 6.1 存储架构

```
.rooster/
├── project_memory.json     # 主记忆存储 (JSON backend)
├── memory_index.db         # SQLite FTS5 + vector index
├── SOUL.md                 # Agent 行为原则 (最高优先级)
├── USER.md                 # 用户偏好画像
└── sessions/               # 会话历史
```

### 6.2 索引与检索

```
MemoryManager.search(query, top_k=5) -> List[MemoryFact]
  ├── Embedding search (cosine similarity)
  │   └── query → embedding provider → compare with stored vectors
  ├── BM25 search (keyword matching)
  │   └── query → SQLite FTS5 MATCH
  └── Reciprocal Rank Fusion (merge results)
      └── return top_k facts
```

**嵌入提供者**:
- 优先: OpenAI-compatible embedding API
- 降级: n-gram BM25 (零依赖)

### 6.3 文本分块

```
Chunker.chunk(text, chunk_size=400, overlap=80) -> List[str]
  - 按 token 数切分（非字符数）
  - 80 token 重叠保证上下文连续
  - CJK-aware token estimation
```

### 6.4 自进化引擎

```
EvolutionEngine.process(session_events)
  │
  ├─ Triggers.detect(events) -> List[Signal]
  │   ├─ CORRECTION: "不对", "你理解错" → SOUL.md
  │   ├─ PREFERENCE: "以后", "我希望" → USER.md
  │   └─ MILESTONE: "已上线", "成功了" → USER.md
  │
  ├─ LLM extraction (local model, never cloud)
  │   └─ Analyze last 5 turns, 200 chars each
  │
  └─ Writers
      ├─ SoulWriter.append(SOUL.md)  # append-only
      └─ UserWriter.update(USER.md)  # merge
```

**保护机制**: SOUL.md 中 Identity / Hard Limits / Memory Protocol 字段受代码级保护。

---

## 七、Dashboard 技术规格

### 7.1 技术栈

| 组件 | 技术 | 说明 |
|------|------|------|
| 前端 | Alpine.js + Tailwind CSS | 单页应用，零构建依赖 |
| 后端 | FastAPI 子应用 | 独立路由，挂载到主网关 |
| 实时通信 | WebSocket | 双向事件推送 |
| 国际化 | 自研 i18n | 中英双语，`/lang` 切换 |
| 构建产物 | 单 HTML 文件 | `dashboard.html` 内联 JS/CSS |

### 7.2 WebSocket 事件协议

```
Client → Server:
  {"method": "chat.send", "params": {"sessionKey": "xxx", "message": "..."}, "id": "req_1"}
  {"method": "chat.cancel", "params": {"sessionKey": "xxx"}, "id": "cancel_1"}

Server → Client:
  {"type": "assistant_delta", "data": {"content": "...", "runId": "xxx"}}
  {"type": "tool_call", "data": {"name": "exa_search", "args": {...}, "runId": "xxx"}}
  {"type": "tool_response", "data": {"name": "exa_search", "result": "...", "runId": "xxx"}}
  {"type": "pipeline_update", "data": {"stage": "executor", "status": "running"}}
  {"type": "audit_verdict", "data": {"verdict": "AFFIRM", "score": 0.95}}
  {"type": "log", "data": {"level": "info", "message": "..."}}
  {"type": "file_ready", "data": {"filename": "...", "download_url": "..."}}
```

### 7.3 API 端点汇总

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/health` | GET | 健康检查 |
| `/api/version` | GET | 版本号 |
| `/api/cancel` | POST | 全局取消 |
| `/api/metrics/summary` | GET | JSON 指标 |
| `/metrics` | GET | Prometheus 指标 |
| `/api/system/stats` | GET | 系统资源 |
| `/api/guardian/status` | GET | Guardian 状态 |
| `/api/sessions` | GET | 会话列表 |
| `/api/toolset` | GET | 工具列表 |
| `/api/config/*` | * | 配置 CRUD |
| `/api/memory/*` | * | 记忆 CRUD |
| `/api/skills/*` | * | 技能管理 |
| `/api/models/*` | * | 模型管理 |
| `/api/mcp/*` | * | MCP Server 管理 |

---

## 八、Guardian 守护规格

### 8.1 监控线程

| 线程 | 函数 | 间隔 | 触发条件 | 动作 |
|------|------|------|---------|------|
| 心跳 | `_heartbeat_loop` | 30s | 3 次无响应 | 强杀重启 |
| 资源 | `_resource_loop` | 15s | CPU>95% 或 Mem>2GB 持续 120s | 强杀重启 |
| 调度 | `_cron_loop` | 60s | schedules.json 中任务到期 | POST 触发 |

### 8.2 自愈策略

```
Child process crash detected
  │
  ├─ Check error log for ModuleNotFoundError
  │   └─ Match against 23-item allowlist → pip install
  │
  ├─ Check for port conflict (OSError: [WinError 10048])
  │   └─ Regex extract port → kill occupying process
  │
  ├─ Check restart count (last 300s)
  │   └─ If >= 5 → alert webhook, STOP
  │
  └─ Exponential backoff + jitter
      └─ delay = min(2^retry_count * 10 + random(0..10), 300)
```

### 8.3 告警

- **Webhook 告警**: 支持飞书/钉钉/Slack 兼容格式
- **触发条件**: 连续失败达到上限、资源溢出、端口冲突无法释放
- **Payload**: `{event, message, timestamp, process_info}`

---

## 九、工具系统规格

### 9.1 工具注册表

**自动发现**: `pkgutil.iter_modules("src/toolset/definitions/")` 扫描所有 `BaseTool` 子类。

**平台过滤**: 注册时检查 `tool.platforms`，当前平台不匹配则跳过。

**Kit 分组**:

| Kit | 工具数 | FC 暴露 | 说明 |
|-----|--------|---------|------|
| browser | 5 | 4 | 网页浏览 |
| search | 4 | 2 | 搜索引擎 |
| vision | 3 | 3 | 桌面视觉 |
| filesystem | 1 | 1 | 文件操作 |
| office | 3 | 3 | 文档处理 |
| interpreter | 1 | 1 | Python 执行 |
| memory | 1 | 1 | 记忆写入 |
| task | 3 | 2 | 任务管理 |
| subagent | 2 | 2 | 子 Agent |
| comms | 2 | 2 | 通信 |
| multimedia | 3 | 2 | 多媒体 |
| ocr | 1 | 1 | 文字识别 |
| plan | 1 | 1 | 规划模式 |
| system | 3 | 3 | 元工具 |

### 9.2 工具生命周期

```
LLM output → parse tool calls → ToolDispatch.dispatch()
  │
  ├── 1. Path resolution (orchestrator.pre_dispatch)
  │      "Desktop/report.xlsx" → "C:/Users/xxx/Desktop/report.xlsx"
  │
  ├── 2. Permission check (permission_policy)
  │      sandboxed? → check restricted tools
  │
  ├── 3. Input guard (input_guard)
  │      scan args for path traversal, injection
  │
  ├── 4. Rate limit (tool_rate_limiter)
  │      per-tool sliding window
  │
  ├── 5. Code safety (code_safety)
  │      python_interpreter args → AST check
  │
  ├── 6. Blackboard lock (mission_blackboard)
  │      concurrent file access → acquire lock
  │
  ├── 7. Execute (tool.run(**args))
  │      └── On failure → reflection_engine.try_heal()
  │             ModuleNotFoundError → pip install (allowlist)
  │             FileNotFoundError → path sniffing
  │             PermissionError → redirect to temp
  │
  ├── 8. Vision strategy (vision_strategy)
  │      screenshot? → apply privacy filter
  │
  └── 9. Post-dispatch (orchestrator.post_dispatch)
         truncate output to fit context
```

### 9.3 ReflectionEngine 自愈

```
Budget: max 3 attempts per error type

ModuleNotFoundError:
  └─ Extract module name → match against allowlist → pip install

FileNotFoundError:
  └─ Fuzzy search for filename in common dirs → rewrite path

PermissionError:
  └─ Redirect output to temp directory
```

**包白名单** (23 个): pandas, numpy, beautifulsoup4, requests, openpyxl, python-docx, pypdf, Pillow, matplotlib, csv, json, re, pathlib, ...

---

## 十、Prompt 体系规格

### 10.1 Prompt 文件清单

| 文件 | 版本 | 字数 | 用途 |
|------|------|------|------|
| `base.md` | v6.0 | ~2000 | 核心身份、原则、工具选择、输出标准 |
| `executor.md` | v7.1 | ~3500 | ReAct 循环、证据规则、CONFIRM_REQUIRED、FINAL_REPORT |
| `strategist.md` | v10.0 | ~4000 | JSON 输出 schema、EXECUTE/COMMIT 阶段、域模型、子 Agent 模式 |
| `auditor.md` | v4.0 | ~2500 | 阶段审计标准、来源可信度、收敛协议 |
| `router_triage.md` | v5.1 | ~1500 | 五分流决策树 |
| `intent_reframer.md` | v5.0 | ~2000 | 下载意图检测、歧义检查、实体提取 |
| `replan.md` | - | ~800 | 对比性失败指令、原始目标不变 |

### 10.2 Prompt 构建流程

```
PromptBuilder.compose(role, tools, context) -> str
  │
  ├─ Load SOUL.md (via soul_loader)
  ├─ Load USER.md
  ├─ Generate skills digest (via skill_loader)
  ├─ Retrieve LTM context (via memory_manager.search)
  ├─ Load role-specific prompt (executor.md / strategist.md / ...)
  ├─ Compose runtime supplement (workspace paths, tool list, config)
  └─ Concatenate in priority order: SOUL > USER > Skills > LTM > Base > Runtime
```

---

## 十一、配置规格

### 11.1 配置源

| 配置文件 | 用途 | 安全级别 |
|----------|------|---------|
| `.env` | 运行时配置（80+ 项） | 公开，提交到 git |
| `.env.local` | API 密钥 | 绝密，`.gitignore` 排除 |
| `.env.local.example` | 密钥模板 | 公开 |

### 11.2 配置模块映射

```
src/utils/config/
├── _settings.py    → Settings (组合类)
├── providers.py    → ProvidersConfig (LLM Provider)
├── runtime.py      → RuntimeConfig (超时/阈值/限制)
├── hardware.py     → HardwareConfig (硬件/视觉)
├── channels.py     → ChannelsConfig (飞书/Webhook)
└── memory.py       → MemoryConfig (记忆系统)
```

### 11.3 关键配置项

```bash
# === LLM Provider ===
ZHIPU_KEY=                  # 智谱 API Key
MIMO_KEY=                   # 小米 MiMo Key
JIUTIAN_KEY=                # 九天 MoMA Key
OPENAI_KEY=                 # OpenAI Key
ANTHROPIC_KEY=              # Anthropic Key
KIMI_KEY=                   # Kimi Key
QWEN_KEY=                   # 通义千问 Key

# === 角色模型分配 ===
ROUTER_MODEL_MODE=zhipu     # 分诊模型
STRATEGIST_MODEL_MODE=zhipu # 规划模型
EXECUTOR_MODEL_MODE=jiutian # 执行模型
AUDITOR_MODEL_MODE=jiutian  # 审计模型
SOLO_MODEL_MODE=jiutian     # SoloRunner 模型

# === Failover ===
LLM_FAILOVER_ENABLED=true
LLM_FAILOVER_ORDER=jiutian,zhipu,mimo,local

# === Agent 行为 ===
AGENT_MAX_STEPS=20           # 最大 ReAct 步数
AGENT_STUCK_THRESHOLD=4      # 重复检测阈值
CHECKPOINT_ENABLED=true      # 断点续跑（必须开启）
STRATEGIST_LLM_TIMEOUT=90    # Strategist 超时秒数
AUDIT_MAX_REMAND_RETRY=1     # REMAND 最大重试

# === 并发 ===
MAX_PARALLEL_SUBTASKS=3      # 最大并行子任务数

# === 网关 ===
GATEWAY_PORT=8765
GATEWAY_API_KEY=             # 留空跳过认证

# === 安全 ===
ADVANCED_SECURITY_ENABLED=true

# === 记忆 ===
MEMORY_BACKEND=json
MEMORY_EMBEDDING_PROVIDER=local
```

---

## 十二、部署规格

### 12.1 系统要求

| 项目 | 最低要求 | 推荐配置 |
|------|---------|---------|
| Python | >= 3.12 | 3.12 |
| 操作系统 | Windows 10+ / macOS 12+ | Windows 11 |
| 内存 | 4GB | 8GB+ |
| 磁盘 | 2GB | 5GB+ |
| 网络 | 需要访问至少一个 LLM API | 稳定网络 |

### 12.2 启动流程

```
python guardian.py
  │
  ├─ Pre-flight checks
  │   ├─ .env.local exists? → warn if missing
  │   ├─ At least one LLM key configured? → exit if none
  │   └─ Port available? → kill conflicting process
  │
  ├─ Launch child process
  │   └─ python -m src.main
  │       ├─ Load settings
  │       ├─ Initialize tool registry
  │       ├─ Warm up memory index
  │       ├─ Start Gateway (uvicorn)
  │       ├─ Start channels (CLI, Feishu, Webhook)
  │       └─ Open Dashboard (browser)
  │
  └─ Monitor loop
      ├─ Heartbeat (30s)
      ├─ Resource check (15s)
      └─ Cron scheduler (60s)
```

### 12.3 目录结构

```
rooster/
├── src/                    # 核心源码
│   ├── agents/             # Agent 层 (15 files)
│   ├── gateway/            # 网关层 (12 files)
│   ├── models/             # LLM 适配器 (6 files)
│   ├── toolset/            # 工具系统 (24 files)
│   ├── memory/             # 记忆系统 (12 files)
│   ├── channels/           # 通道 (5 files)
│   ├── sessions/           # 会话 (3 files)
│   ├── evolution/          # 进化引擎 (5 files)
│   ├── utils/              # 工具库 (30+ files)
│   └── prompts/            # Prompt 模板 (7 files)
├── dashboard/              # Dashboard 子应用
├── skills/                 # 外挂技能 (12 dirs)
├── tests/                  # 测试套件 (16 files)
├── scripts/                # 工具脚本
├── models/                 # ML 模型权重
├── resources/              # 静态资源
├── .rooster/               # 运行时数据 (gitignored)
├── guardian.py             # 守护进程入口
├── pyproject.toml          # 项目配置
└── .env / .env.local       # 配置文件
```

---

## 十三、已知技术债务

| 编号 | 问题 | 位置 | 严重度 | 解决方案 |
|------|------|------|--------|---------|
| TD-001 | `plan_stream()` 无超时 | strategist.py:175 | 🔴 Critical | 加 `asyncio.timeout()` |
| TD-002 | REMAND 重试失忆 | mission_runner.py:451 | 🔴 Critical | 注入 `previous_report.observation` |
| TD-003 | [DIRECT] 走 MissionRunner | router.py:192 | 🟠 High | 改路由到 SoloRunner |
| TD-004 | Strategist JSON 解析脆弱 | strategist.py:213-268 | 🟠 High | 加强 JSON 修复或换 parser |
| TD-005 | baseline history 截断过激 | mission_runner.py:276 | 🟡 Medium | 放宽截断阈值或语义压缩 |
| TD-006 | executor.md 强制 JSON | executor.md | 🟡 Medium | SoloRunner 模式取消 FINAL_REPORT |
| TD-007 | 飞书卡片回调未注册 | feishu.py | 🟡 Medium | 注册 `card_action_trigger` |
| TD-008 | Dashboard 无文件推送 | dashboard ws | 🟡 Medium | 添加 `file_ready` 事件 |

---

## 十四、测试策略

### 14.1 测试金字塔

```
        ┌──────────┐
        │   E2E    │  test_agent_full.py (端到端 Agent 执行)
        ├──────────┤
        │Integration│  test_executor.py, test_tool_dispatch.py
        │          │  test_downloader_integration.py
        ├──────────┤
        │  Unit    │  test_auth.py, test_security.py
        │          │  test_llm_providers.py, test_config_routes.py
        └──────────┘
```

### 14.2 测试覆盖范围

| 模块 | 测试文件 | 覆盖点 |
|------|---------|--------|
| Router | test_router_triage.py | 分诊准确性 |
| Executor | test_executor.py, test_executor_fc_protocol.py | ReAct 循环、FC 协议 |
| Strategist | test_replan_contrastive.py | 重规划逻辑 |
| ToolDispatch | test_tool_dispatch.py | 工具解析与执行 |
| LLM | test_llm_providers.py, test_anthropic_adapter.py | 供应商适配 |
| Security | test_security.py, test_privacy_router.py | 安全防护 |
| Sessions | test_sessions_store.py, test_session_title_fix.py | 会话管理 |
| Gateway | test_auth.py, test_config_routes.py | 网关接口 |
| Search | test_search_providers.py | 搜索降级链 |
| Vision | test_vision_cross_platform.py | 跨平台视觉 |
| Guardian | test_guardian_cron.py | 定时调度 |

### 14.3 运行测试

```bash
# 全量测试
pytest tests/ -v

# 单文件
pytest tests/test_executor.py -v

# 带覆盖率
pytest tests/ -v --cov=src --cov-report=term-missing
```
