# Rooster 系统说明书 (CLAUDE.md)

> 这个文件是给所有参与 Rooster 开发的 AI 助手（和人类）看的系统总览。
> 读这个文件后，你应该能快速理解架构、找到关键代码位置、避免常见陷阱。

---

## 架构速查

### 请求入口

```
用户消息 → src/agents/router.py → Router.handle_inbound()
```

### 路由决策树

```
Router._triage_via_llm()
  │
  ├─ [TALK]     → SoloRunner → AgentExecutor.run()    # 对话/直答
  ├─ [DIRECT]   → （目标）SoloRunner → AgentExecutor.run()  # 清晰任务，直接执行
  ├─ [REFRAME]  → Reframer → MissionRunner → Strategist + Executor + Auditor
  ├─ [SCHEDULE] → _handle_schedule()  # 定时任务写入 .rooster/schedules.json
  └─ [BLOCK]    → 拦截 + 提示
```

### 关键文件

| 文件 | 职责 |
|------|------|
| `src/agents/router.py` | 请求分拣，决定走哪条路 |
| `src/agents/runners/solo_runner.py` | 单 agent 直接执行（推荐路径） |
| `src/agents/runners/mission_runner.py` | 多步并行任务编排 |
| `src/agents/executor.py` | ReAct 循环核心（1054 行，不要轻易改） |
| `src/agents/strategist.py` | 任务分解规划（只用于真正并行的复杂任务） |
| `src/agents/auditor.py` | 结果审计（只用于叶节点 + 高风险操作） |
| `src/agents/reframer.py` | 意图重构（只用于 [REFRAME] 任务） |
| `src/prompts/executor.md` | Executor 的 system prompt |
| `src/prompts/strategist.md` | Strategist 的 system prompt |

---

## 核心原则（修改代码前必读）

### 1. Executor 是核心，不要轻易改
`executor.py` 的 ReAct 循环是整个系统最重要的代码。它已经包含 stuck 检测、空响应重试、FC schema 路由等完整机制。如果任务执行有问题，**先检查 prompt 和路由逻辑，而不是改 executor 本身**。

### 2. MissionRunner 只用于真正并行的任务
如果一个任务可以顺序完成（大多数任务都可以），应该走 SoloRunner。MissionRunner 的价值在于**同时跑多个 AgentExecutor**，对于线性任务，它只会增加延迟和失败点。

### 3. Strategist JSON 解析是脆弱点
`strategist.py` 的 `plan_stream()` 用手写 regex 实时解析 LLM 流式 JSON。改 Strategist 的 prompt 时，必须确保输出格式极其严格。任何格式偏移都会导致 FAILSAFE 降级。

### 4. CHECKPOINT 必须开启
`.env` 中 `CHECKPOINT_ENABLED=true` 必须设置，否则长任务崩溃后从零重来。Checkpoint 基础设施已完整实现。

---

## 已知问题 & 解决方案

| 问题 | 位置 | 状态 | 解决方案 |
|------|------|------|---------|
| `plan_stream()` 无超时 | `strategist.py:175` | ⚠️ 待修复 | 加 `asyncio.timeout()` |
| REMAND 重试失忆 | `mission_runner.py:451` | ⚠️ 待修复 | 注入 `previous_report.observation` |
| `[DIRECT]` 走 MissionRunner | `router.py:192` | ⚠️ 待修复 | 改路由到 SoloRunner |
| Checkpoint 默认关闭 | `.env` | ✅ 配置修复 | `CHECKPOINT_ENABLED=true` |

**详细分析见 `docs/stability_analysis.md`**

---

## 配置关键字段（.env）

```bash
# 模型配置
ROUTER_MODEL_NAME=      # Triage 分诊模型（轻量快速）
STRATEGIST_MODEL_NAME=  # 规划模型（强推理能力）
EXECUTOR_MODEL_NAME=    # 执行模型（主力）
AUDITOR_MODEL_NAME=     # 审计模型

# 稳定性配置
CHECKPOINT_ENABLED=true           # 长任务断点续跑（必须开启！）
STRATEGIST_LLM_TIMEOUT=90         # Strategist 超时秒数
AUDIT_MAX_REMAND_RETRY=1          # Auditor REMAND 最大重试次数
AGENT_MAX_STEPS=20                # 单任务 ReAct 最大步数
AGENT_STUCK_THRESHOLD=4           # 重复工具调用检测阈值
SUBTASK_MIN_TIMEOUT=300           # 子任务最小超时秒数

# 并发配置
MAX_PARALLEL_SUBTASKS=3           # MissionRunner 最大并发子任务数
```

---

## 开发约定

### 新增工具
1. 在 `src/toolset/definitions/` 下创建新文件
2. 在 `src/toolset/registry.py` 注册
3. 如果工具专属于某类任务，考虑设置 `fc_hidden=True` 并加入对应的 kit
4. 参考 `docs/history_TOOL_CONSOLIDATION_ROUND10.md` 了解工具合并历史

### 修改 Prompt
1. Executor prompt：`src/prompts/executor.md`（影响所有任务执行）
2. Strategist prompt：`src/prompts/strategist.md`（影响任务分解，改动需严格测试 JSON 格式）
3. 修改 prompt 后，必须用 `test_agent_full.py` 做端到端测试

### 测试
```bash
# 单工具测试
python test_search.py

# 完整 agent 端到端测试
python test_agent_full.py

# 启动服务（开发模式）
python -m uvicorn dashboard.src.mount:app --reload
```

---

## 架构演进记录

| 版本 | 主要变更 |
|------|---------|
| v10.0 | DAG 拓扑推导 phase，叶节点才调 Auditor |
| v9.2 | 子任务 timeout 最小保底机制 |
| v8.0 | 流式 Strategist，实时产出子任务 |
| Round 10 | 工具合并为宏工具，LLM 可见工具从 40 降至 29 |

---

## 参考文档

- `docs/goal.md` — 稳定性目标和设计原则
- `docs/todo.md` — 改造执行 Checklist（从 Phase 0 开始）
- `docs/stability_analysis.md` — 根因深度分析报告
- `docs/history_TOOL_CONSOLIDATION_ROUND10.md` — 工具合并历史
