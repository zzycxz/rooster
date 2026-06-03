# Rooster 系统说明书 (CLAUDE.md)

> 这个文件是给所有参与 Rooster 开发的 AI 助手（和人类）看的系统总览。
> 读这个文件后，你应该能快速理解架构、找到关键代码位置、避免常见陷阱。

---

## 架构速查

### 请求入口

```
用户消息 → src/agents/router.py → Router.handle_inbound()
```

### V15 路由决策树

```
Router._handle_inbound_inner()
  │
  ├─ 安全检查 (AdvancedGuard)
  │
  ├─ L1 硬规则门闸 (< 5ms，纯代码)
  │   ├─ 安全词表 ──────────→ BLOCK
  │   ├─ /slash 命令 ────────→ SCHEDULE
  │   ├─ 定时词表 ──────────→ SCHEDULE
  │   ├─ 下载词表 ──────────→ flag:reframe → Reframer
  │   └─ 其他 ──────────────→ PASS_TO_PLANNER
  │
  ├─ SkillIndex (L2, ~20ms, TF-IDF) → SkillHint
  │
  └─ MissionRunner.run_with_decision()
      └─ Strategist.decide() (fast LLM)
          ├─ DIRECT_REPLY → 流式回复（lazy 异步生成器）
          ├─ SINGLE_STEP  → MissionRunner.run(pre_planned_plan=单任务)
          ├─ DAG_PLAN     → MissionRunner.run(pre_planned_plan=多步DAG)
          └─ CLARIFY      → 发送澄清问题
```

### 关键文件

| 文件 | 职责 |
|------|------|
| `src/agents/router.py` | L1 硬规则门闸 + SkillIndex + 事件处理器 |
| `src/agents/runners/mission_runner.py` | 任务编排（DIRECT_REPLY / SINGLE_STEP / DAG_PLAN） |
| `src/agents/executor.py` | ReAct 循环核心（不要轻易改） |
| `src/agents/strategist.py` | decide() 语义判断 + plan_stream() 任务分解 |
| `src/agents/auditor.py` | 结果审计（只用于叶节点 + 高风险操作） |
| `src/agents/reframer.py` | 意图重构（L1 下载词表命中后触发） |
| `src/agents/skill_index.py` | TF-IDF 能力索引（全局单例） |
| `src/agents/protocol.py` | PlanMode / PlanDecision / MissionState 数据结构 |
| `src/prompts/executor.md` | Executor 的 system prompt |
| `src/prompts/strategist.md` | Strategist 的 system prompt |
| `src/prompts/strategist_triage.md` | Strategist.decide() 深度判断 prompt |

---

## 核心原则（修改代码前必读）

### 1. Executor 是核心，不要轻易改
`executor.py` 的 ReAct 循环是整个系统最重要的代码。它已经包含 stuck 检测、空响应重试、FC schema 路由等完整机制。如果任务执行有问题，**先检查 prompt 和路由逻辑，而不是改 executor 本身**。

### 2. MissionRunner 是唯一执行入口
V15 不再有 SoloRunner。所有任务（DIRECT_REPLY / SINGLE_STEP / DAG_PLAN）都经过 MissionRunner。DIRECT_REPLY 由 Strategist.decide() 返回 lazy 流式生成器，MissionRunner.run_with_decision() 消费。

### 3. Strategist.decide() 是语义判断入口
decide() 用 fast LLM 判断任务深度，输出 PlanDecision。它不修改现有的 plan() / plan_stream() / replan()。分诊超时 15s，降级为 SINGLE_STEP。

### 4. Strategist JSON 解析是脆弱点
`strategist.py` 的 `plan_stream()` 用手写 regex 实时解析 LLM 流式 JSON。改 Strategist 的 prompt 时，必须确保输出格式极其严格。任何格式偏移都会导致 FAILSAFE 降级。

### 5. CHECKPOINT 必须开启
`.env` 中 `CHECKPOINT_ENABLED=true` 必须设置，否则长任务崩溃后从零重来。Checkpoint 基础设施已完整实现。

---

## 配置关键字段（.env）

```bash
# 模型配置
STRATEGIST_MODEL_NAME=  # 规划模型（强推理能力）
EXECUTOR_MODEL_NAME=    # 执行模型（主力）
AUDITOR_MODEL_NAME=     # 审计模型
FAST_MODEL_NAME=        # 轻量模型（Strategist.decide() 分诊 + 校验管道）

# V15 模型档位（已接线）
MODEL_TIER_FAST=        # decide()/DIRECT_REPLY/校验自愈优先使用；空则回退 FAST_MODEL_NAME
MODEL_TIER_STANDARD=    # 执行 standard 档；空则回退 EXECUTOR_MODEL_NAME
MODEL_TIER_REASONING=   # 执行 reasoning 档；空则回退 EXECUTOR_MODEL_NAME

# 稳定性配置
CHECKPOINT_ENABLED=true           # 长任务断点续跑（必须开启！）
STRATEGIST_LLM_TIMEOUT=90         # Strategist 超时秒数
SKILL_INDEX_THRESHOLD=0.3         # SkillIndex TF-IDF 匹配阈值
AGENT_MAX_STEPS=20                # 单任务 ReAct 最大步数
SUBTASK_MIN_TIMEOUT=1800          # 子任务最小超时秒数
MAX_PARALLEL_SUBTASKS=3           # MissionRunner 最大并发子任务数
```

---

## 开发约定

### 新增工具
1. 在 `src/toolset/definitions/` 下创建新文件
2. 在 `src/toolset/registry.py` 注册
3. 如果工具专属于某类任务，考虑设置 `fc_hidden=True` 并加入对应的 kit

### 修改 Prompt
1. Executor prompt：`src/prompts/executor.md`（影响所有任务执行）
2. Strategist prompt：`src/prompts/strategist.md`（影响任务分解，改动需严格测试 JSON 格式）
3. Strategist triage prompt：`src/prompts/strategist_triage.md`（影响 decide() 深度判断）
4. 修改 prompt 后，必须做端到端测试

### 测试
```bash
# 启动服务（开发模式）
python -m uvicorn dashboard.src.mount:app --reload
```

---

## 架构演进记录

| 版本 | 主要变更 |
|------|---------|
| v15.0 | L1 硬路由 + Strategist 语义主权，删除 SoloRunner/Router LLM 分诊，SkillIndex TF-IDF |
| v11.0 | CCP 六步法规划协议，USER 步骤路由，交付物/阻塞项声明，工具名校验，Checkpoint 增强 |
| v10.0 | DAG 拓扑推导 phase，叶节点才调 Auditor |
| v9.2 | 子任务 timeout 最小保底机制 |
| v8.0 | 流式 Strategist，实时产出子任务 |

---

## 参考文档

- `docs/v15_roadmap.md` — V15 迁移方案
- `docs/goal.md` — 稳定性目标和设计原则
- `docs/stability_analysis.md` — 根因深度分析报告
