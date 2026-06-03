# Changelog

## [0.5.5] - 2026-06-03

> **涵盖**: V15 路由架构重写 — L1 硬路由 + Strategist 语义主权
> **核心范式**: Router 不再调 LLM；语义判断下沉到 Strategist.decide()（fast 模型）；能力索引 SkillIndex 辅助决策

---

### Added

- **Strategist.decide() 语义主权入口**：新增 `decide()` 方法，调用 fast LLM 判断任务深度，输出 `PlanDecision` 结构（`DIRECT_REPLY` / `SINGLE_STEP` / `DAG_PLAN` / `CLARIFY`）。不修改现有 `plan()` / `plan_stream()` / `replan()`。(`src/agents/strategist.py`)
- **strategist_triage.md 深度判断 Prompt**：新增分诊 prompt，含工具动词兜底规则、model_tier 建议、CLARIFY 触发条件。(`src/prompts/strategist_triage.md`)
- **SkillIndex 能力索引（TF-IDF 第一版）**：零外部依赖的关键词匹配索引，全局单例，支持从 `skills/` 目录自动加载 SKILL.md。可配置阈值 `SKILL_INDEX_THRESHOLD`。(`src/agents/skill_index.py`)
- **PlanMode / PlanDecision / MissionState 数据结构**：`PlanMode` 枚举（4 值）、`PlanDecision`（含 lazy 流式生成器 `reply_stream` + `clarify_question` + `plan`）、`MissionState`（7 态，只在内存不写 checkpoint）。(`src/agents/protocol.py`)
- **PASS_TO_PLANNER 路由目标**：新增 `RouteTarget.PASS_TO_PLANNER`，L1 门闸未命中硬规则时下沉给 Strategist。(`src/agents/routing_protocol.py`)
- **L1 硬规则门闸**：`Router._l1_gate()` 纯代码分诊（< 5ms），安全词表 → BLOCK、定时词表 → SCHEDULE、下载词表 → flag:reframe。(`src/agents/router.py`)
- **MissionRunner.run_with_decision()**：V15 新入口，调用 `Strategist.decide()` 获取 `PlanDecision` 后执行。DIRECT_REPLY 走流式缓冲，CLARIFY 发送澄清问题，SINGLE_STEP/DAG_PLAN 委托给 `run()`。(`src/agents/runners/mission_runner.py`)
- **V15 Metrics 指标**：新增 `observe_v15_l1_gate()`、`observe_v15_plan_decision()`、`observe_v15_skill_hint()`。(`src/gateway/metrics.py`)
- **V15 模型三档配置**：新增 `MODEL_TIER_FAST` / `MODEL_TIER_STANDARD` / `MODEL_TIER_REASONING`、`EXECUTOR_AUTO_UPGRADE` / `EXECUTOR_UPGRADE_THRESHOLD`。(`src/utils/config/providers.py`)

### Changed

- **Router 架构重写**：删除 `_triage_via_llm()`、`_triage_by_keyword()`、`_handle_inbound_legacy()` 及所有类级关键词列表（`_TALK_KW` / `_COMPLEX_KW` / `_DOWNLOAD_KW` / `_SCHEDULE_KW` / `_CAPABILITY_QUERY_RE`）。Router 不再持有 `_triage_llm` 和 `solo_runner`。(`src/agents/router.py`)
- **MissionRunner.run() 签名变更**：移除 `is_direct: bool` 参数，新增 `pre_planned_plan: Optional[MissionPlan]` 参数。当提供 `pre_planned_plan` 时跳过规划阶段直接执行。(`src/agents/runners/mission_runner.py`)
- **Strategist.decide() 使用 FAST_MODEL_NAME**：分诊调用 fast 模型（`FAST_MODEL_NAME`），超时 15s，降级为 SINGLE_STEP。(`src/agents/strategist.py`)
- **Executor for_solo() 模型变更**：`agent_id` 从 `rooster_solo` 改为 `rooster_direct_reply`，模型从 `SOLO_MODEL_NAME` 改为 `EXECUTOR_MODEL_NAME`。(`src/agents/executor.py`)
- **validation.py 模型变更**：校验管道 LLM 调用从 `ROUTER_MODEL_NAME` 改为 `FAST_MODEL_NAME`。(`src/toolset/validation.py`)
- **Reframer 触发时机变更**：从"Router LLM 分诊返回 REFRAME"改为"L1 门闸命中下载词表后触发"。代码不变，只改触发入口。(`src/agents/router.py`)
- **strategist.md REROUTE 表更新**：`suggested_route` 从 bracket-tag 格式改为 PlanMode 值（`[DIRECT]`→`single_step`、`[REFRAME]`→`dag_plan`、`[TALK]`→`direct_reply`）。(`src/prompts/strategist.md`)
- **intent_reframer.md 更新**：REDIRECT 输出的 `suggested_route` 从 `"[DIRECT]"` 改为 `"single_step"`。(`src/prompts/intent_reframer.md`)

### Removed

- **SoloRunner 完全删除**：`solo_runner.py` 删除，Router 不再持有 SoloRunner 实例。DIRECT_REPLY 由 Strategist.decide() + MissionRunner.run_with_decision() 流式处理。(`src/agents/runners/solo_runner.py`)
- **Router LLM 分诊删除**：`_triage_via_llm()`、`_triage_by_keyword()`、`_triage_llm` 实例、`router_triage.md` prompt 全部删除。(`src/agents/router.py`, `src/prompts/router_triage.md`)
- **Legacy 路由路径删除**：`_handle_inbound_legacy()` 及其依赖的 bracket-tag 状态机（`triage_state`、`_TRIAGE_TO_TARGET`）全部删除。(`src/agents/router.py`)
- **Deprecated RouteTarget 枚举删除**：`TALK`、`DIRECT_EXECUTOR`、`MISSION` 及其工厂方法 `talk()`、`direct()`、`mission()` 全部删除。(`src/agents/routing_protocol.py`)
- **ROUTER_MODEL_MODE / ROUTER_MODEL_NAME 配置删除**：从 `providers.py`、`.env`、`security.py` allowlist、dashboard 中移除。(`src/utils/config/providers.py`, `.env`, `src/gateway/security.py`)
- **SOLO_MODEL_MODE / SOLO_MODEL_NAME / SOLO_FAILOVER_ORDER 配置删除**：从 `providers.py`、`.env` 中移除。(`src/utils/config/providers.py`, `.env`)
- **Legacy Metrics 删除**：`observe_route_decision()` 方法删除（含 `route_triage_llm_total` 计数器）。(`src/gateway/metrics.py`)
- **废弃测试文件删除**：`test_router_triage.py`（测试已删除的 `_triage_by_keyword`）、`test_streaming_fix.py`（使用已删除的 `triage_state` bracket-tag）。(`tests/`)
- **Dashboard 清理**：`ROUTER_MODEL_MODE` 和 `SOLO_MODEL_MODE` 从 setup 表单、配置路由、模型路由中移除。(`dashboard/`)
- **README 更新**：架构图、目录树、流程图、配置示例全部更新为 V15 架构。(`README.md`, `README.cn.md`)

---

## [0.5.0] - 2026-06-02

> **涵盖**: v11 CCP 规划协议 | v12 执行引擎增强 (B1-B5) | v13 工程加固 (安全/资源/观测/测试) | v14.1 架构范式重塑 (P1 DI + P2 Schema 自愈)

---

### v11 CCP 能力约束驱动规划

> **核心主题**: CCP 规划协议 + USER 步骤支持 + Checkpoint 增强
> **设计原则**: 5 不做 (CapabilityMap / MissionState FSM / loop_back / SQLite 持久化 / 新模块) + 4 聚焦 (Protocol 扩展 / Strategist Prompt / Checkpoint 增强 / USER 步骤路由)

#### Added

- **CCP 六步法规划协议 (Capability-Constrained Planning)**：在 `Strategist` 中引入了全新的六步规划协议（阻塞项检测、执行者标记、置信度评级、交付物声明、可行性分析、DAG 拓扑构建）。
- **Protocol 蓝图字段升级**：`SubTask` 新增 `owner` (AGENT|USER)、`confidence` 与 `risk_note`；`MissionPlan` 新增 `blockers`、`deliverables` 与 `feasibility_note` 字段。全部有默认值，向后兼容。
- **Pydantic 自修复引擎 (Self-Healing)**：在 `Strategist` 中实现了基于 `ValidationError` 和 `JSONDecodeError` 的拦截与大模型重试管道。
- **核心逻辑与 DAG 防护网**：通过 `@model_validator` 为 `MissionPlan` 引入了严格的 DAG 拓扑校验（防向后引用与深度优先循环依赖检测），从底层杜绝发散。
- **USER 步骤路由**：在 `MissionRunner` 中新增了基于 `owner: USER` 的分离路由，复用 `requires_confirm` 进行人类协作，且不占用并发槽位。
- **前置预警展示**：规划完成后、执行前，自动展示前置阻塞项、可行性说明与预期交付物，提高决策掌控感。
- **子任务执行指标**：新增任务级别的指标收集（包含耗时 `duration_s` 与重试次数 `retries`）并持久化到 Checkpoint 中。
- **工具合法性校验**：`Strategist` 现已注入 `tool_registry`，可检测并降级不存在的幻觉工具至 `generic_tool`。
- **上游取消传播**：如果依赖链中的 USER 步骤被取消，下游 AGENT 步骤也自动取消，避免空等。

#### Changed

- **Checkpoint 增强**：序列化模型新增 `blockers`、`deliverables`、`feasibility_note`、`subtask_metrics` 字段，实现更细粒度的崩溃恢复。
- **双通道无损元数据提取**：优化了 `plan_stream()` 的流式拆包逻辑，通过正则在一次流式回复中同步提取全局元数据，省去一次额外的 LLM 调用。
- **tool_registry 注入 Strategist**：构造函数接收 `tool_registry` 参数，`_get_skills_digest()` 将原生工具 schema 注入系统 prompt，`plan()` 中校验 LLM 规划的 tool 名是否在注册表中。
- **SoulLoader 传 llm_client**：修复 `plan()` 和 `plan_stream()` 中实例化 `SoulLoader` 时未传入 `llm_client` 导致超长提示词无法自动精简的问题。

#### Fixed

- **USER 步骤超时死循环**：修复了人类超时未操作时调度器挂起的问题（将其标记为 `CANCELLED` 并安全取消下游依赖）。
- **指标计时偏差**：将子任务的计时起点移至依赖等待之后，消除因等待上游任务导致的耗时虚高。
- **Checkpoint 预警复读**：断点续跑时跳过重复打印规划阶段的交付物和阻塞预警。
- **Fallback 字段缺失**：修补了 `Strategist` 极端降级正则表达式提取路径，确保强行解析时具备安全的默认值。

#### ⚠️ v11 Known Gaps

- **replan() 元数据丢失**：`replan()` 不传递 `blockers`/`deliverables`/`feasibility_note`，重规划后元数据丢失（当前可接受，阻塞项可能已变化）。
- **plan_stream 无内部超时**：依赖外部 `mission_runner` 的 `wait_for` 保护。

---

### v12 执行引擎增强

#### Added
- **Progressive History Compression (渐进式历史压缩)**：在 `Executor` 执行循环中引入渐进式上下文修剪机制 (`_summarize_mid_history`)。每 10 步触发一次局部语义蒸馏，保留最近 5 步原始上下文，彻底解决长任务后期因触碰 60% 阈值导致的”硬截断降智”问题。
- **Task-Level Heartbeats (任务级心跳守护)**：在 `MissionRunner` 中新增任务级 `.heartbeat` 文件发射机制。`Guardian` 守护进程现可检测并干预长达 3 分钟毫无进度的子任务”隐性卡死”，而不仅仅是进程级崩溃。
- **Blackboard Fact Confidence (黑板事实置信度)**：扩展 Blackboard 的 `FactEntry` 结构，新增 `status` (`confirmed`, `tentative`, `superseded`) 状态标签，避免子任务试错阶段的错误数据污染全局并发记忆。

#### Changed
- **Executor Path Optimization (路径缓存优化)**：重构了 `SystemPrompt` 与 `FC Schema` 的渲染逻辑。通过计算 LTM 与最近使用工具的哈希值实现热缓存，消除了 ReAct 循环中多余的重复构建开销。
- **Observation Head-Tail Truncation (头尾保留截断)**：优化了依赖任务的结果提取逻辑，由原先的”硬截断 2000 字符”改为”保留头部 600 字结论与尾部 600 字堆栈，省略中间部分”，确保长输出中的关键错误信息不丢失。

#### Fixed
- **Fallback Chain 降级链收束 (B1)**：彻底修复了 6 条规划降级路径最终导向死胡同（”Tool not found”）的问题。引入并注册 `generic_tool` 作为标准兜底工具，使得规划失败的子任务能安全移交至 `Executor` 进行自主 ReAct 探索。
- **Interpreter Sandbox Leak (沙箱线程泄漏)**：修复了云端 E2B 沙箱执行死循环代码时，未向 SDK 传递超时限制导致的 Python 后台线程与计费沙箱永久挂起的问题。
- **Local Interpreter Vulnerabilities (解释器沙盒漏洞)**：
  - 修复 `tool_dispatch.py` 中因参数默认值不一致导致 AST 安全检查被绕过的漏洞。
  - 将本地子进程中的 `”python”` 指令替换为 `sys.executable`，解决虚拟环境隔离断裂问题。
  - 修复了安全拦截时引发大模型”幻觉死循环”的矛盾提示词。

---

### v13 工程底座加固

#### Security (v13.1)

- **Security Defaults Upgraded (安全默认值升级)**：将 `runtime.py` 中 `ADVANCED_SECURITY` 默认从 `false` 改为 `true`，`CONFIRMATION_BEHAVIOR` 默认从 `log` 改为 `block`。危险操作不再静默放行，需显式确认。
- **PI Scan Exemptions Removed (注入扫描豁免清除)**：清空 `advanced_guard.py` 的 `_PI_EXEMPT_TOOLS`，此前 `python_exec`、`terminal` 等破坏力最强的代码执行工具被豁免了 Prompt Injection 扫描。现在所有工具输出一律强制扫描。
- **Typed Signal Exceptions (类型化控制流信号)**：新建 `src/utils/exceptions.py`，用原生 Python 类型安全异常 `EscalateSignal`、`AbortSignal` 替代了过去散落在 `executor.py` 和 `mission_runner.py` 中的 `raise Exception(“__ESCALATE__: ...”)` + `.replace()` 魔法字符串解析模式。异常捕捉和堆栈回溯现在精确到类型。

#### Resource Governance (v13.2)

- **Launcher Graceful Shutdown (长生命周期资源回收)**：在 `src/launcher.py:cleanup()` 中挂载统一退出钩子，确保 Router、DistillationScheduler、ModelFactory 所持有的 LLMClient 连接池在进程退出时正确释放。之前这些单例组件的 HTTPX 连接池会随进程被强杀而泄漏。
- **Bare Except Elimination (静默吞错修复)**：将 `observation.py` 中的裸 `except:` 全部收敛为 `except (json.JSONDecodeError, ValueError):`，防止底层严重异常被无意吞没。

#### Observability (v13.3)

- **Structured Metrics (立体指标监控)**：在 `gateway/metrics.py` 新增 `observe_tool_execution()`（工具延迟直方图 + 状态计数器）、`observe_subtask_execution()`（子任务耗时）、`observe_failover()`（Provider 故障切换率）、`observe_llm_error()`（LLM 错误追踪）。打破此前仅监控 token 计数的盲区。
- **Mission Correlation ID (任务关联 ID)**：新建 `src/utils/logging_context.py`，通过 `ContextVar` 实现 `mission_id` 在日志中的自动注入。`MissionRunner` 调用 `set_mission_id()` 后，该任务产生的所有日志均带有 `[mission=xxx]` 标记。
- **Robust Config Parsing (环境变量防呆)**：增强 `_env_bool()` / `_env_int()` 的容错解析，类似写成 `TURE` 的错别字不再被静默当成 `False`，而是抛出明确的配置错误提示。

#### Test Quality (v13.4)

- **Hardening Test Suite (加固测试集)**：新增 `tests/test_v13_hardening.py`（6 个测试），覆盖 `_env_bool` 常见拼写、`_env_int` 非数字输入、LLMClient close 语义、PI 扫描终端输出、AbortSignal 传播、V13 指标注册。
- **pytest-cov Integration (覆盖率基础设施)**：将 `pytest-cov` 加入 dev 依赖，为后续覆盖率门禁打下基础。

#### ⚠️ v13 Known Gaps (已验证未闭合)

> 以下 4 项在代码实证核查（2026-06-02）中发现未完全落地，已纳入 v14.1 收尾批次。

- **Critical Injection Not Blocking (关键注入未阻断)**：`tool_dispatch.py` 检测到 critical 注入攻击时仍只添加 warning prefix，未 `raise AbortSignal` 硬阻断。测试通过 monkeypatch 模拟而非验证真实代码路径。
- **Subtask LLMClient Leak (子任务连接泄漏)**：`mission_runner.py` 每个子任务创建独立的 `subtask_llm_client`（含 HTTPX 异步连接池），但 `finally` 块中没有 `await llm_client.close()`。长任务中连接池累积泄漏。
- **Correlation ID Partial Coverage (关联 ID 覆盖不全)**：只有 `MissionRunner` 调用了 `set_mission_id()`。Router、Strategist、Executor、Auditor 四个核心模块的日志仍显示 `mission=-`。
- **Coverage Gate Unconfigured (覆盖率门禁未配置)**：`pytest-cov` 是 dev 依赖，但无 `--cov-fail-under` 配置，无 CI workflow 强制执行。

---

### v14.1 架构范式重塑

#### P1: Tool Dependency Injection (工具级依赖注入上下文)

- **RoosterContext Protocol (上下文协议)**：新建 `src/toolset/context.py`，定义 `RoosterContext` dataclass（含 `session_id`、`task_id`、`workspace_dir`、`memory_manager`、`llm_client`、`blackboard`、`config`、`security_policy` 等字段），作为工具执行的统一依赖注入容器。
- **BaseTool Dual Signature (基类双签名兼容)**：重构 `src/toolset/base.py`，新增 `execute(self, args, ctx)` 签名规范。`run(**kwargs)` 方法自动检测子类是否实现了 `execute`，若存在且有 `args_schema`，则自动将 kwargs 反序列化为 Pydantic model 并注入 `RoosterContext`。旧工具无需改动即可继续运行（零破坏迁移）。
- **Dispatcher Context Assembly (调度器上下文装配)**：重构 `src/agents/tool_dispatch.py`，在每次工具调用时从当前执行配置中构建 `RoosterContext` 并注入到工具的 `execute` 调用中。

#### P2: Schema Validation Self-Healing (Schema 强校验底盘拦截)

- **ToolCallValidator (工具调用校验器)**：新建 `src/toolset/validation.py`，基于 Pydantic 设计底层校验引擎。包含两条自愈路径：JSON 语法修复（`_heal_json_parse`）和 Schema 结构修复（`_heal_schema`），各最多 2 次重试。
- **Validation Pipeline Integration (校验管道集成)**：在 `tool_dispatch.py` 的执行链中，先经过 `ToolCallValidator.validate_and_heal()` 校验，通过后才交给工具执行。如果 Pydantic `model_validate` 抛出 `ValidationError`，校验器自动将错误信息打包成 Prompt 发给轻量路由模型（`ROUTER_MODEL_NAME`，`temperature=0.1`）要求订正参数。大模型在一两秒内修正后系统继续执行，上层 MissionRunner / Strategist 完全无感。
- **Healing Budget (自愈预算硬上限)**：`MAX_HEAL_RETRIES = 2`，超出后降级为返回 SchemaValidationFailed 错误，由 ReAct 循环自主处理。永不死循环。

#### ⚠️ v14.1 Known Gaps (已验证未闭合)

> 以下 1 项在代码实证核查（2026-06-02）中发现未落地，已纳入 v14.1 收尾批次。

- **Zero Tools Using ctx Parameter (无工具使用注入上下文)**：框架管道全部到位，但 25+ 个工具中没有任何一个实际从注入的 `RoosterContext` 中获取依赖。`memory.py` 内部自己重建 `MemoryManager()`；`subagent.py` 从 `self.context` dict 取值；`file_system.py` 仍用旧 `run(**kwargs)` 模式。管道已修好，水未接通。

## [0.4.0] - 2026-05-31

### Added
- **CCP 六步法规划协议 (Capability-Constrained Planning)**：在 `Strategist` 中引入了全新的六步规划协议（阻塞项检测、执行者标记、置信度评级、交付物声明、可行性分析、DAG 拓扑构建）。
- **Protocol 蓝图字段升级**：`SubTask` 新增 `owner` (AGENT|USER)、`confidence` 与 `risk_note`；`MissionPlan` 新增 `blockers`、`deliverables` 与 `feasibility_note` 字段。
- **Pydantic-AI 自修复引擎 (Self-Healing)**：在 `Strategist` 中实现了基于 `ValidationError` 和 `JSONDecodeError` 的拦截与大模型重试管道。
- **核心逻辑与 DAG 防护网**：通过 `@model_validator` 为 `MissionPlan` 引入了严格的 DAG 拓扑校验（防向后引用与深度优先循环依赖检测），从底层杜绝发散。
- **USER 步骤路由**：在 `MissionRunner` 中新增了基于 `owner: USER` 的分离路由，复用 `requires_confirm` 进行人类协作，且不占用并发槽位。
- **前置预警展示**：规划完成后、执行前，自动展示前置阻塞项、可行性说明与预期交付物，提高决策掌控感。
- **子任务执行指标**：新增任务级别的指标收集（包含耗时 `duration_s` 与重试次数 `retries`）并持久化到 Checkpoint 中。
- **工具合法性校验**：`Strategist` 现已注入 `tool_registry`，可检测并降级不存在的幻觉工具至 `generic_tool`。

### Changed
- **Checkpoint 增强**：序列化模型新增 `blockers`、`deliverables`、`feasibility_note`、`subtask_metrics` 字段，实现更细粒度的崩溃恢复。
- **双通道无损元数据提取**：优化了 `plan_stream()` 的流式拆包逻辑，通过正则在一次流式回复中同步提取全局元数据，省去一次额外的 LLM 调用。

### Fixed
- **SoulLoader 精简失效**：修复了 `Strategist` 实例化 `SoulLoader` 时未传入 `llm_client` 导致超长提示词无法自动精简的问题。
- **USER 步骤超时死循环**：修复了人类超时未操作时调度器挂起的问题（将其标记为 `CANCELLED` 并安全取消下游依赖）。
- **指标计时偏差**：将子任务的计时起点移至依赖等待之后，消除因等待上游任务导致的耗时虚高。
- **Checkpoint 预警复读**：断点续跑时跳过重复打印规划阶段的交付物和阻塞预警。
- **Fallback 字段缺失**：修补了 `Strategist` 极端降级正则表达式提取路径，确保强行解析时具备安全的默认值。
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
