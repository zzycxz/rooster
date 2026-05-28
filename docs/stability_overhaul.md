# Rooster 稳定性改造总纲

> **版本**: v2.0 | **日期**: 2026-05-27 | **状态**: 待执行
> **本文替代**: `plan.md`、`todo.md`、`stability_analysis.md`、`interaction_design.md`、`goal.md`
> **保留**: `stability_analysis.md` 作为根因参考归档

---

## 一、现状诊断

### 1.1 一次普通任务的真实代价

用户说"帮我下载误杀"，系统实际发生了什么：

```
用户输入
  ↓
[LLM #1] Router._triage_via_llm()        → 判定 [DIRECT] 或 [REFRAME]
  ↓
[LLM #2] Reframer.reframe()               → 输出结构化意图（细节开始丢失）
  ↓
[LLM #3] Strategist.plan_stream()         → 流式输出子任务 JSON（无超时保护！）
  ↓
[LLM #4] AgentExecutor.run() for ST1      → 真正干活（ReAct 循环）
  ↓
[LLM #5] Auditor.review() for 叶节点      → 审计结果
  ↓（REMAND 时）
[LLM #6] AgentExecutor 重试               → 失忆重跑（不知道上次做了什么）
```

**5-6 次串行 LLM 调用，每次边界都是信息丢失点。**

Claude Code 做同样的事：1 个连续 context，工具失败就地重试，1-2 次 LLM 调用。

### 1.2 核心矛盾

`executor.py`（1054 行）的 ReAct 循环已经写得很好——包含 stuck 检测、空响应重试、FC schema 路由、上下文管理。**问题是它被套在 3-4 层编排下面，只有 20% 的任务能顺畅进入它。**

### 1.3 七个根因（按严重程度排序）

| # | 根因 | 风险 | 代码位置 | 一句话 |
|---|------|------|---------|--------|
| 1 | `plan_stream()` 无超时 | 致命 | `strategist.py:208` | LLM 流中断 → 系统永久挂死 |
| 2 | REMAND 重试失忆 | 严重 | `mission_runner.py:451-457` | 重试时只有审计官文字，无上次工具输出 |
| 3 | `[DIRECT]` 走 MissionRunner | 高 | `router.py:192-240` | 清晰任务仍过 Strategist + Auditor，多 3 次 LLM 调用 |
| 4 | Strategist JSON 流式解析脆弱 | 高 | `strategist.py:213-268` | LLM 格式偏移 → FAILSAFE → 质量断崖 |
| 5 | Checkpoint 默认关闭 | 中 | `mission_runner.py:98` | 长任务崩溃必须从零重来 |
| 6 | dep_results 截断过激 | 中 | `mission_runner.py:301-307` | 子任务间只传 2000 字符，复杂结果被截断 |
| 7 | SoloRunner 强制 JSON 输出 | 中 | `executor.md:149-167` | SoloRunner 无 Auditor，JSON 格式错误反增失败率 |

### 1.4 迭代式复杂任务的真实执行模式

上面的诊断以"帮我下载误杀"为例，但更典型的场景是**迭代式复杂任务**——比如"搜索 AI 行业信息，做一个 10 页 PPT"。

这类任务不是"先搜完再做"的线性流水线。真实执行模式是**边做边搜、搜完即用、遇到问题再搜**：

```
"搜索 AI 行业信息，做一个 10 页 PPT"

Step 1-2:   搜索 "AI 行业报告 2026" → 拿到概览
Step 3:     整合，规划 PPT 大纲（10 页分别讲什么）
Step 4-5:   搜索第 1 页需要的具体数据 → 生成第 1 页
Step 6-7:   搜索第 2 页需要的具体数据 → 生成第 2 页
Step 8:     搜索时发现数据矛盾 → 搜索验证
Step 9:     整合验证结果，调整第 2 页
Step 10-14: 逐页搜索 + 生成第 3-5 页
Step 15:    自检发现第 5 页数据不足 → 再搜索补充
Step 16:    修正第 5 页
Step 17-20: 继续逐页生成第 6-10 页
Step 21:    最终自检 → 输出 PPT
```

**核心特征**：
- **迭代式**：搜一点、做一点、发现问题再搜，不是一次性规划完所有步骤
- **上下文依赖强**：Step 15 修正第 5 页时，必须记得 Step 3 规划的大纲和 Step 10 写的内容
- **不可预拆**：Strategist 不可能在事前就知道"第 5 页数据不足需要补充搜索"——这是执行过程中涌现的需求

**当前系统为什么做不好这类任务**：

1. Router 把它判为 `[REFRAME]` → MissionRunner → Strategist 预拆成"搜索"和"做 PPT"两个子任务
2. 但第一步"搜索"不可能一次性搜完所有信息——Executor 在做第 3 页时才发现需要新的搜索，这时已经进入了"PPT 生成"子任务，没有搜索工具或搜索结果被截断
3. 即使不拆子任务，当前 `_prune_history` 做截断——Step 15 时 Step 3 的大纲和 Step 1-2 的原始搜索数据已经被丢弃，Executor 不记得自己规划了什么

**正确路径**：这类任务必须由一个 Executor 在**单一连续 context** 中跑完——每一步的搜索结果直接留在 session_history 里，后续步骤自然能看到全部历史数据。这依赖三件事：

1. **三段式 executor.md**（2.1）— Executor 会自己规划大纲，并在做每页前搜索
2. **语义压缩**（2.5）— Step 20 时仍能记住 Step 3 规划的大纲（摘要保留了关键数据）
3. **Router 路由正确**（2.2 + 2.7）— 把这类任务判为 `[DIRECT]`，走 SoloRunner

对于**真正需要并行**的任务（"同时搜 5 个网站对比价格"），MissionRunner 仍然正确。

### 1.5 Executor 本身的评价

`executor.py` 的 ReAct 循环**不需要重写**。它已包含：

- Stuck 检测（同一工具调用重复 N 次自动打断，`executor.py:431-473`）
- 空响应重试（max 2 次带退避，`executor.py:366-380`）
- 上下文自动剪枝（超 60% 阈值触发，`executor.py:243-255`）
- FC per-step schema 路由（`executor.py:320-330`）
- 视觉支持（隐私路由 + base64 注入）
- 工具失败单个隔离（不因一个失败取消其他并发工具）

**问题不在执行器，而在编排层。**

---

## 二、目标

### 2.1 北极星指标

| 指标 | 当前估计 | 目标 | 衡量方式 |
|------|---------|------|---------|
| 单任务首次成功率 | ~60% | ≥ 85% | 10 次不同简单任务，记录首次成功数 |
| 简单任务平均延迟 | 60-120s | < 30s | 从发送到收到完整回复的端到端时间 |
| 长任务崩溃需重来比例 | ~40% | < 10% | 发起长任务，中途杀进程，测试续跑 |
| 简单任务 LLM 调用次数 | 5 次 | ≤ 3 次 | 通过日志统计单任务 LLM 调用 |

### 2.2 设计原则

**原则 1：单一 Context 优先**
能不切换 agent 就不切换。每次 agent 边界都是信息丢失点。

**原则 2：原地重试优先**
工具失败先在循环内重试。错误信息保留在 session_history 中，下一步 LLM 直接看到并推理修复方案。

**原则 3：最少 LLM 调用**
简单任务不超过 3 次 LLM 调用（含 triage）。每增加一个角色，增加一次等待和一次 context 断裂风险。

**原则 4：规划内嵌，不外包**
对单步和顺序任务，让 Executor 在 chain-of-thought 里做规划。Strategist 只用于真正的并行分解。

**原则 5：任务前解决歧义，任务中不打断**
意图模糊 → 执行前问一次。执行开始后不再中断，除非高风险操作。

### 2.3 改造后调用链

```
简单任务（[DIRECT]）— 改造后:
  用户输入 → Router [LLM #1] → SoloRunner → Executor ReAct [LLM #2..N]
  最少 2 次 LLM 调用，单一连续 context

复杂并行任务（[REFRAME]）— 保留:
  用户输入 → Router [LLM #1] → Reframer [LLM #2]
           → MissionRunner → Strategist [LLM #3] → 并行 Executor × N [LLM #4..N]
  多 agent 真正并行，这是 Claude Code 没有的能力
```

### 2.4 差异化定位

Rooster 不是 Claude Code 的复制品。目标是在**单任务稳定性**上对齐 Claude Code，同时保留它**没有**的：

| 能力 | Claude Code | Rooster 目标 |
|------|------------|-------------|
| 单任务稳定执行 | ✅ | ✅（改造后） |
| 真正并行多 agent | ❌ | ✅（保留） |
| 任务续跑（Checkpoint） | 有限 | ✅（已实现，开启即用） |
| 中文生态 + 本地模型 | 有限 | ✅ |

---

## 三、执行计划（按三层组织）

> **核心思路**：止血 → 提速 → 体验。每层独立可交付，不影响其他层。
> **依赖关系**：层内部分改动有先后顺序，已标注。

### ┖ 第一层：止血 — 1-2 天

> 解决"系统挂死"和"长任务归零"。三项改动互相独立，可以并行。

---

#### 1.1 开启 Checkpoint ⏱ 1 分钟

**根因**：`mission_runner.py:98` 的 `_save_checkpoint()` 被 `CHECKPOINT_ENABLED` 默认 False 守卫。长任务崩溃后必须从零重来。

**现状**：Checkpoint 基础设施已完整实现——每子任务粒度保存、24 小时过期、失败时保留成功时清除。

**改动**：`.env` 新增一行

```bash
CHECKPOINT_ENABLED=true
```

**回滚**：删除该行或设为 `false`。

**验收**：发起下载任务 → 中途 Ctrl+C → 重启后发相同任务 → 日志出现 `Resuming from checkpoint`。

---

#### 1.2 `plan_stream()` 加超时保护 ⏱ 30 分钟

**根因**：`strategist.py:208` 的 `self.llm_client.chat_stream()` 外层无 `asyncio.timeout()`。对比 `plan()` 方法（`strategist.py:82`）和 `replan()` 方法（`strategist.py:383`）都有 `asyncio.wait_for` 超时保护，唯独 `plan_stream()` 没有。LLM 提供商流式输出中途网络中断 → MissionRunner 永久阻塞。

**改动文件**：`src/agents/strategist.py`

```python
# plan_stream() 方法（约 L175）
async def plan_stream(self, user_request: str):
    # ... 构建 messages ...
    yielded_ids = set()

    try:
        async with asyncio.timeout(settings.STRATEGIST_LLM_TIMEOUT):
            async for delta in self.llm_client.chat_stream(...):
                # ... 原有 JSON 解析和 yield 逻辑 ...
                pass

    except asyncio.TimeoutError:
        logger.error(f"[Strategist] plan_stream() 超时，降级 FAILSAFE")
        if not yielded_ids:
            yield SubTask(id="FAILSAFE", instruction=user_request, ...)
        return
    except Exception as e:
        logger.error(f"[Strategist] plan_stream() 异常: {e}")
        if not yielded_ids:
            yield SubTask(id="FAILSAFE", instruction=user_request, ...)
        return
```

**风险**：`STRATEGIST_LLM_TIMEOUT` 设过短会截断正常回复。当前 `.env` 未定义此值，需新增并设为 90s。

**验收**：临时将超时硬编码为 1s → 发起复杂请求 → 日志打印超时降级而非挂死。

---

#### 1.3 REMAND 重试携带完整 observation ⏱ 1 小时

**根因**：`mission_runner.py:451-457` 构造重试 context 时，只注入了 `previous_audit_cmd`（审计官文字），没有 `previous_report.observation`（上次工具输出）。等于告诉一个失忆的人"你做错了，重做"。

**改动文件**：`src/agents/runners/mission_runner.py`

```python
# _run_subtask_inner() 约第 451 行

# 改前：
context_parts.append(f"审计官修正指令：\n{previous_audit_cmd}")

# 改后：
if previous_report is not None:
    prev_obs = previous_report.observation or ""
    if len(prev_obs) > 3000:
        prev_obs = prev_obs[:3000] + "\n...[截断]"
    context_parts.append(
        f"【上次执行结果（已驳回）】\n{prev_obs}\n\n"
        f"【审计官修正指令】\n{previous_audit_cmd}"
    )
else:
    context_parts.append(f"审计官修正指令：\n{previous_audit_cmd}")
```

**验收**：构造会触发 REMAND 的任务 → 查看第二次 Executor 日志 → 确认 prompt 包含上次 observation。

---

#### 1.4 配置调优 ⏱ 5 分钟

在 `.env` 中补充以下配置（配合止血改动）：

```bash
STRATEGIST_LLM_TIMEOUT=90       # 为 plan_stream() 提供超时值（当前缺失）
AUDIT_MAX_REMAND_RETRY=1         # 重试次数从 2 降到 1（失忆问题修复前多次重试无意义）
```

---

### ┖ 第二层：提速 — 3-5 天

> 让 80% 的日常任务绕过 Strategist + Auditor，从 5 次 LLM 调用降到 2-3 次。
> **注意**：改动 2.2（executor 三段式）必须在改动 2.1（路由手术）之前完成。

---

#### 2.1 executor.md 三段式重构 ⏱ 2 小时 🔒 前置：必须先于 2.2 完成

**根因**：当前 `executor.md` 把 Executor 定位为"纯干活"角色（line 8: "You do **not** plan, speculate, or expand scope"）。一旦 SoloRunner 接管复杂任务，Executor 必须同时承担规划、执行和自检职责。

**改动文件**：`src/prompts/executor.md`

在现有内容前后各插入一段，保留现有 ReAct 循环指令不变：

**首段（迭代式规划）** — 插入在 `## Execution Loop` 之前：

```markdown
## 迭代式执行（复杂任务）

对于多步任务（搜索 → 整合 → 生成文件、多页文档等），按以下模式执行：

1. **初步探索**：先搜索获取概览，了解可用信息的范围和质量
2. **动态规划**：基于初步结果，规划整体结构和分步计划
3. **逐项执行**：每做一部分前搜索该部分所需的具体信息，搜完即用
4. **遇到问题再搜**：执行中发现数据矛盾、信息不足时，立即搜索补充
5. **全程自检**：每完成一部分后检查质量，发现问题直接补救

不要试图一次性搜完所有信息再做——边做边搜、搜完即用的效果更好。
计划可以根据实际情况随时调整，不需要等待确认。
对于 1-2 步的简单任务，直接执行，不需要规划。
```

**尾段（完成前自检）** — 插入在 `## Mandatory Override` 之后：

```markdown
## 完成前自检

在输出最终答案或标记任务完成前，逐条核对：
- 用户要求的每件事都做了吗？
- 结果与目标一致吗？
- 如果失败了，有没有尝试过不同的方法？

如发现遗漏，请直接调用工具补救，不要只报告问题。
```

**同时修改 Identity 段** — 将 "You do **not** plan" 改为：

```markdown
- 你**规划**自己的行动路线，**执行**工具调用，**验证**结果，**报告**证据。
```

**风险**：prompt 变长，轻微增加 token 开销。简单任务可能不必要地输出 `<plan>`。
**回滚**：恢复旧版 `executor.md`。

---

#### 2.2 `[DIRECT]` → SoloRunner 路由手术 ⏱ 2 小时 🔒 依赖：2.1 完成后

**根因**：`router.py:192-240` 中，`[DIRECT]` 和 `[REFRAME]` 都走 `self.mission_runner.run()`。`[DIRECT]` 语义是"意图清晰不需要分解"，但仍过完整流水线。

**改动文件**：`src/agents/router.py`

```python
# handle_inbound() 约第 192 行

# 改前（L192-240）：
if triage_state in ["[DIRECT]", "[REFRAME]"]:
    ...
    await self.mission_runner.run(msg, channel, reframed_text, ...)

# 改后：
if triage_state == "[DIRECT]":
    # 意图清晰，直接执行，跳过 Strategist + Auditor
    logger.info("[Router] DIRECT → SoloRunner")
    await self.solo_runner.run(msg, channel, dynamic_event_handler)
    self._fire_and_forget(evolution_engine.on_turn_complete(...))
    return

if triage_state == "[REFRAME]":
    # 保留完整 MissionRunner 路径（含 Reframer + Strategist）
    ...
    await self.mission_runner.run(msg, channel, reframed_text, ...)
    self._fire_and_forget(evolution_engine.on_turn_complete(...))
```

**边界情况确认**：

| 场景 | 当前行为 | 改造后 | 正确性 |
|------|---------|-------|--------|
| `[DIRECT]` 简单搜索 | MissionRunner → 5 次 LLM | SoloRunner → 2 次 | ✅ |
| `[DIRECT]` 下载任务 | MissionRunner → Strategist | SoloRunner → 内联规划 | ✅ 三段式覆盖 |
| `[REFRAME]` 复杂任务 | MissionRunner | MissionRunner 不变 | ✅ |
| `[TALK]` 纯聊天 | SoloRunner | SoloRunner 不变 | ✅ |

**风险**：多步 DIRECT 任务依赖 executor 三段式的内联规划能力。如果三段式改得不好，多步任务质量可能下降。**这就是为什么 2.1 必须先做。**
**回滚**：把 `[DIRECT]` 改回走 `mission_runner.run()`，一行代码。

---

#### 2.3 MissionRunner 单子任务快速路径 ⏱ 1 小时

**根因**：即使 `[REFRAME]` 任务正确走 MissionRunner，Strategist 有时只产出 1 个子任务。这时走完整 Auditor 审计是浪费。

**改动文件**：`src/agents/runners/mission_runner.py`

在 Strategist 规划完成、子任务列表生成后（`_run_plan_phase()` 末尾）：

```python
subtask_list = await self._collect_subtasks(strategist_stream)

# 新增：单子任务快速路径
if len(subtask_list) == 1:
    logger.info(
        f"[MissionRunner] 只有 1 个子任务，转为 SoloRunner 快速路径: "
        f"{subtask_list[0].instruction[:50]}"
    )
    solo_msg = copy.copy(msg)
    solo_msg.text = subtask_list[0].instruction
    await self.solo_runner.run(solo_msg, channel, dynamic_event_handler)
    return

# 原有多子任务路径不变
await self._run_parallel_subtasks(subtask_list, ...)
```

---

#### 2.4 SoloRunner 取消强制 JSON 输出 ⏱ 30 分钟

**根因**：`executor.md:149-167` 要求每次任务结束必须输出 FINAL_REPORT JSON。SoloRunner 模式没有 Auditor 需要解析这个 JSON，强制输出增加格式化负担和解析失败风险。

**改动文件**：`src/agents/prompt_builder.py`

```python
# compose_messages() 或 build_system_prompt() 中
if agent_id and agent_id.startswith("solo"):
    # SoloRunner：不附加 JSON 要求
    final_instruction = "任务完成后，直接给出用户的最终答案或任务结果。"
else:
    # MissionRunner：保留 JSON 供 Auditor 解析
    final_instruction = "你必须使用严格的 JSON 格式输出 FINAL_REPORT..."
```

---

#### 2.5 Context 语义压缩 ⏱ 3-4 小时

**根因**：`executor.py:942-987` 的 `_prune_history` 只做截断——直接丢弃旧消息。当 context 超过阈值，保留第一条 + 最后 10 条，中间全部丢掉。长任务 Step 20 时 LLM 完全不知道 Step 2-3 做了什么，导致"遗忘性重复搜索"。

**改动文件**：`src/agents/executor.py`

新增 `_inline_compact_history()` 异步方法，替换 `_prune_history` 调用：

```python
async def _inline_compact_history(
    self,
    session_history: List[Dict],
    context_limit: int,
    keep_recent: int = 6,
) -> List[Dict]:
    """语义压缩：用 FAST_MODEL 生成执行摘要，替换旧历史。"""
    from utils.token_counter import count_message_tokens

    estimated = count_message_tokens(session_history)
    if estimated < context_limit * 0.5 or len(session_history) < 10:
        return self._prune_history(session_history, context_limit)

    old_history = session_history[:-keep_recent]
    recent_history = session_history[-keep_recent:]

    compaction_prompt = [{
        "role": "user",
        "content": (
            "请将以下执行历史压缩为简洁摘要。必须保留：\n"
            "1. 已完成步骤（每步一行）\n"
            "2. 关键数据：URL、文件路径、评分、选中项\n"
            "3. 失败路径（避免重复）\n"
            "4. 当前任务状态\n"
            "不要保留思考过程和完整 JSON。\n\n"
            "执行历史：\n" +
            "\n".join(
                f"{m['role']}: {str(m.get('content',''))[:300]}"
                for m in old_history
            )
        )
    }]

    try:
        summary = ""
        async for delta in self.llm_client.chat_stream(
            model=settings.FAST_MODEL_NAME or "qwen/qwen3.6-35b",
            messages=compaction_prompt,
            max_tokens=1500,
        ):
            if delta.content:
                summary += delta.content
        executor_logger.info(
            f"[Compact] {len(old_history)} 条 → {len(summary)} 字"
        )
    except Exception as e:
        executor_logger.warning(f"[Compact] 压缩失败，降级截断: {e}")
        return self._prune_history(session_history, context_limit)

    return [
        {"role": "user", "content": f"[EXECUTION SUMMARY]\n{summary}"},
        {"role": "assistant", "content": "已了解执行进度，继续任务。"}
    ] + recent_history
```

在 ReAct 循环（`executor.py:255`）替换调用：

```python
# 改前：
session_history = self._prune_history(session_history, max_total_tokens=context_limit)

# 改后：
session_history = await self._inline_compact_history(session_history, context_limit)
```

**风险**：压缩本身消耗 1 次 LLM 调用（FAST_MODEL，成本低）。压缩失败时优雅降级为原有截断。
**验收**：20+ 步长任务 → Step 20 时 LLM 仍准确引用 Step 2-3 关键数据 → 不出现重复搜索。

---

#### 2.6 dep_results 截断放宽 ⏱ 30 分钟

**根因**：`mission_runner.py:301-307` 中，上游子任务的 observation 传递给下游时被截断到 2000 字符。对于搜索 → 生成类任务（如"搜索信息做 PPT"），第一个子任务搜到的详细数据到第二个子任务时只剩碎片。

**改动文件**：`src/agents/runners/mission_runner.py`

```python
# _run_subtask_inner() 约第 301-307 行

# 改前：
dep_results.append(f"[{dep_id}] {dep_report.observation[:2000]}")

# 改后：放宽到 8000 字符（约 4000 tokens，对于 131072 context 可承受）
max_obs = 8000
obs_text = dep_report.observation or ""
if len(obs_text) > max_obs:
    obs_text = obs_text[:max_obs] + f"\n...[截断，原文 {len(dep_report.observation)} 字]"
dep_results.append(f"[{dep_id}] {obs_text}")
```

**风险**：多子任务并发时 context 可能增长。但 `AGENT_CONTEXT_LIMIT=131072`，8000 字符的 dep_result 占比不到 10%。
**回滚**：改回 2000。

---

#### 2.7 Router triage 调优 ⏱ 1-2 小时

**根因**：当前 `router_triage.md` 的决策树倾向于把多步任务都判为 `[REFRAME]`。但"搜索信息做 PPT"这类**多步但线性**的任务，走 SoloRunner（单一 Executor 跑全程）比走 MissionRunner（拆子任务 + 信息截断）效果更好。Router 需要区分"线性复杂"和"真正并行"。

**改动文件**：`src/prompts/router_triage.md`

在决策树中补充区分规则：

```markdown
### [DIRECT] vs [REFRAME] 的判断标准

[DIRECT]（迭代式执行 — 单一 Executor 跑全程）：
- 单步操作：搜索、下载、查询、文件操作
- 迭代式多步：搜索 → 整合 → 逐项搜索 → 生成文件（边做边搜，搜完即用）
- 多步骤但不需要预拆：做 PPT、写报告、整理资料（过程中需要什么搜什么）
- 关键特征：上下文连贯性比并行更重要，中间步骤不可预知

[REFRAME]（真正并行 — MissionRunner 多 Executor 并发）：
- 明确要求"同时"做多件事
- 步骤间无强依赖，可以并发执行
- 涉及多个独立数据源需要分别处理（如同时搜 5 个网站对比）
- 需要竞速（RACE 模式）：多个方案并行，取最优
```

**同步调整**：`router.py:287-295` 的 fallback 解析逻辑，确保 `[DIRECT]` 优先级不低于 `[REFRAME]`。

---

#### 2.8 Sandwich 模式（可选优化）⏱ 1 小时

**目的**：对抗"Lost in the Middle"注意力衰减。将关键状态信息同时放在 context 首部（SUMMARY）和尾部（compose_messages 末尾追加）。

**改动文件**：`src/agents/prompt_builder.py`

当传入 `key_state_hint` 时，将关键状态追加到最后一条 user message 末尾。

---

### ┖ 第三层：体验 — 1-2 周

> 在系统稳定跑起来之后再做。让交互更自然、可观测性更强。

---

#### 3.1 Auditor 降级为高风险专属 ⏱ 1 小时

**根因**：当前每个叶节点都调 `auditor.review()`（`mission_runner.py:565-605`）。搜索、查询等低风险读操作无需审计，徒增 1 次 LLM 调用。

**改动文件**：`src/agents/runners/mission_runner.py`

```python
HIGH_RISK_DOMAINS = {"FILE_DELETE", "EMAIL", "PAYMENT", "SYSTEM_MODIFY"}

needs_llm_audit = st.domain in HIGH_RISK_DOMAINS or getattr(st, 'requires_confirm', False)

if needs_llm_audit:
    verdict = await self.auditor.review(report, st, is_leaf=True)
else:
    # 低风险：Executor 返回 SUCCESS 即视为通过
    is_affirm = (report.status == "SUCCESS")
```

**前置条件**：第二层的 executor 三段式自检能力已完成，否则低风险任务可能漏过错误。

---

#### 3.2 歧义检测前移 ⏱ 3-4 小时

**根因**：当前 CONFIRM_REQUIRED 在 ReAct 循环执行几步后才触发（`executor.py:401-422`），用户不知道已经做了什么。

**目标**：在 Router 层（任务开始前）完成歧义解决，不在执行中打断。

**改动文件**：`src/agents/router.py`，新增 `_pre_flight_ambiguity_check()` 方法。

---

#### 3.3 飞书卡片确认 ⏱ 3-4 小时

**根因**：`feishu.py` 已实现 `send_card()` 但未实现卡片按钮回调注册。用户确认是纯文本输入。

**改动**：
1. `feishu.py`：注册 `p2_card_action_trigger` 回调
2. 将歧义确认和高风险确认改为飞书交互卡片（带按钮）
3. 卡片回调注入 `_wait_for_clarification` 等待队列

---

#### 3.4 Trace ID 贯穿调用链 ⏱ 4-6 小时

**根因**：多用户 + 多并行子任务，日志交织无法追踪单任务完整链路。

**改动**：
1. `AgentRunConfig` 增加 `trace_id` 字段
2. Router 收到消息时生成 uuid4 前 8 位
3. 透传 MissionRunner → SubTask → Executor
4. 日志每行带 `[Trace: {trace_id}]`

---

#### 3.5 Dashboard 活跃任务监控 ⏱ 4-6 小时

**改动**：
1. 后端维护 inflight 任务状态字典
2. Executor 每步更新状态
3. WebSocket 推送到前端 Dashboard 新增"活跃任务"卡片

---

#### 3.6 Executor step 级 Checkpoint ⏱ 2-3 小时

**根因**：当前 Checkpoint 粒度为子任务（可能几十步 LLM 交互）。最后一步崩溃 = 整个子任务重来。

**改动**：ReAct 循环每 5 步保存 `session_history` 快照。恢复时优先读取快照。

---

## 四、优先级总表

| 层 | 改动 | 工作量 | 收益 | 依赖 |
|----|------|--------|------|------|
| 止血 | Checkpoint 开启 | 1 分钟 | 长任务续跑 | 无 |
| 止血 | `plan_stream()` 超时 | 30 分钟 | 消除永久挂死 | 需新增 `STRATEGIST_LLM_TIMEOUT` |
| 止血 | REMAND 带 observation | 1 小时 | 重试有效率 | 无 |
| 止血 | `.env` 配置调优 | 5 分钟 | 配合止血 | 无 |
| 提速 | executor.md 三段式 | 2 小时 | SoloRunner 有规划+自检能力 | 无 |
| 提速 | DIRECT → SoloRunner | 2 小时 | 简单任务快 50% | **先完成三段式** |
| 提速 | 单子任务快速路径 | 1 小时 | 减少不必要的 Auditor | 无 |
| 提速 | SoloRunner 取消 JSON | 30 分钟 | 减少格式化负担 | 无 |
| 提速 | 语义压缩 | 3-4 小时 | 长任务不再失忆 | 无 |
| 提速 | dep_results 截断放宽 | 30 分钟 | 复杂任务信息不再断层 | 无 |
| 提速 | Router triage 调优 | 1-2 小时 | 线性复杂任务正确路由 | 无 |
| 提速 | Sandwich 模式 | 1 小时 | 对抗注意力衰减 | 语义压缩 |
| 体验 | Auditor 高风险专属 | 1 小时 | 减少 1 次 LLM 调用 | 三段式自检 |
| 体验 | 歧义检测前移 | 3-4 小时 | 交互体验提升 | 无 |
| 体验 | 飞书卡片确认 | 3-4 小时 | 飞书用户体验 | 歧义检测前移 |
| 体验 | Trace ID | 4-6 小时 | 可诊断性 | 无 |
| 体验 | Dashboard 监控 | 4-6 小时 | 运维可见性 | Trace ID |
| 体验 | Step 级 Checkpoint | 2-3 小时 | 精粒度续跑 | 无 |

---

## 五、验收标准

### 第一层验收（止血）

- [ ] 发起长任务 → 中途杀进程 → 重启后续跑成功
- [ ] 临时将 `plan_stream()` 超时设为 1s → 发起请求 → 降级而非挂死
- [ ] 触发 REMAND → 重试 Executor 的 prompt 包含上次 observation
- [ ] `.env` 中 `STRATEGIST_LLM_TIMEOUT=90` 和 `AUDIT_MAX_REMAND_RETRY=1` 已设置

### 第二层验收（提速）

- [ ] 10 次简单任务（搜索、下载、查询），首次成功率 ≥ 8/10
- [ ] 简单任务平均 LLM 调用次数 ≤ 3
- [ ] 简单任务平均响应时间 < 30s
- [ ] 日志中 `[DIRECT]` 任务走 SoloRunner 路径
- [ ] 20+ 步长任务，Step 20 时 LLM 仍准确引用 Step 2-3 数据，无重复搜索
- [ ] SoloRunner 任务有 `<plan>` 和自检行为
- [ ] **迭代式复杂任务测试**："搜索 X 信息并做一个 PPT"走 SoloRunner 路径，Executor 边做边搜（不是一次性搜完），PPT 每页内容准确引用搜索到的数据，遇到信息不足时能自主补充搜索
- [ ] **路由准确性测试**：10 个任务中，线性多步任务 ≥ 8 个被正确判为 `[DIRECT]`，真正并行任务被正确判为 `[REFRAME]`
- [ ] **MissionRunner 兜底测试**：被误判为 `[REFRAME]` 的线性任务，dep_results 传递的信息 ≥ 8000 字符，下游子任务信息完整

### 第三层验收（体验）

- [ ] 搜索类低风险任务跳过 Auditor 审计
- [ ] 歧义确认在执行前触发，执行中不中断
- [ ] 飞书用户可点击卡片按钮完成确认
- [ ] `grep "Trace: 8a9b2c3d" rooster.log` 能提取完整任务链路
- [ ] Dashboard 显示实时活跃任务进度

---

## 六、不做什么

- **不重写 `executor.py`**：ReAct 循环写得很好，不需要推翻。改 prompt 和路由即可。
- **不删除 MissionRunner**：它的并行调度能力是核心价值，保留但提高进入门槛。
- **不删除 Auditor**：保留用于高风险操作（文件删除、付款、批量写入），但从默认路径移除。
- **不删除 Strategist**：保留用于真正的并行分解（如同时搜 5 个网站），线性任务走 Executor 内联规划。

---

## 七、附录：当前架构速查

### 路由决策树（当前实际状态）

```
Router._triage_via_llm() [router.py:251]
  │
  ├─ [TALK]     → SoloRunner.run()               ✅ 正确 [router.py:177]
  ├─ [DIRECT]   → MissionRunner.run()             ❌ 应走 SoloRunner [router.py:240]
  ├─ [REFRAME]  → Reframer → MissionRunner        ✅ 正确 [router.py:198-240]
  ├─ [SCHEDULE] → _handle_schedule()              ✅ 正确 [router.py:187]
  └─ [BLOCK]    → 拦截 + 提示                     ✅ 正确 [router.py:163]
```

### 关键文件行号索引

| 文件 | 行号 | 内容 |
|------|------|------|
| `router.py` | 177-183 | [TALK] → SoloRunner |
| `router.py` | 192-240 | [DIRECT]/[REFRAME] → MissionRunner（待改） |
| `router.py` | 251-295 | `_triage_via_llm()` 分诊逻辑 |
| `mission_runner.py` | 98-119 | `_save_checkpoint()` — 被 CHECKPOINT_ENABLED 守卫 |
| `mission_runner.py` | 121-138 | `_load_checkpoint()` — 24 小时过期 |
| `mission_runner.py` | 282-695 | `_run_subtask_inner()` — 含 REMAND 循环 |
| `mission_runner.py` | 301-307 | dep_results 截断（2000→8000，待改） |
| `mission_runner.py` | 451-457 | REMAND context 构造（待改） |
| `mission_runner.py` | 565-605 | 叶节点 Auditor 审计（待改） |
| `mission_runner.py` | 698-781 | DAG 调度循环（含 replan） |
| `executor.py` | 151-711 | ReAct 主循环 `run()` |
| `executor.py` | 243-255 | 上下文管理（prune 调用点，待改） |
| `executor.py` | 366-380 | 空响应重试 |
| `executor.py` | 401-422 | CONFIRM_REQUIRED 检测 |
| `executor.py` | 431-473 | Stuck 检测 |
| `executor.py` | 942-987 | `_prune_history()` 截断逻辑（待替换） |
| `strategist.py` | 48-173 | `plan()` — 有超时保护 |
| `strategist.py` | 175-319 | `plan_stream()` — **无超时保护**（待改） |
| `strategist.py` | 213-268 | 流式 JSON 解析（脆弱点） |
| `strategist.py` | 321-454 | `replan()` — 有超时保护 |
| `auditor.py` | 23-215 | `review()` — 超时降级为 PASS_WITH_WARNING |
| `executor.md` | 1-198 | Executor 系统提示词（待三段式改造） |
| `solo_runner.py` | 45-76 | `run()` — SoloRunner 入口 |

### 当前 `.env` 关键配置

```bash
# 角色路由
STRATEGIST_MODEL_MODE=zhipu    STRATEGIST_MODEL_NAME=glm-5.1
EXECUTOR_MODEL_MODE=jiutian    EXECUTOR_MODEL_NAME=openai/gpt-oss-120b
AUDITOR_MODEL_MODE=jiutian
ROUTER_MODEL_MODE=zhipu        ROUTER_MODEL_NAME=GLM-4.7
SOLO_MODEL_MODE=jiutian        SOLO_MODEL_NAME=openai/gpt-oss-120b

# Agent 运行时
AGENT_CONTEXT_LIMIT=131072
AGENT_MAX_STEPS=50

# 审计
AUDIT_STRICTNESS=Medium
AUDIT_MAX_REMAND_RETRY=2       # 建议改为 1
CHECKPOINT_ENABLED=            # 未设置（默认 False），必须开启

# 故障转移
LLM_FAILOVER_ORDER=jiutian,zhipu,mimo,local
```

### 已有但默认关闭的机制

| 机制 | 配置项 | 当前状态 | 代码位置 |
|------|--------|---------|---------|
| Checkpoint | `CHECKPOINT_ENABLED` | 未设置（False） | `mission_runner.py:98` |
| Reframer | `ENABLE_REFRAMER` | False | `.env:76` |
| Webhook 通道 | `WEBHOOK_ENABLED` | False | `.env:157` |
| MCP 动态 | `MCP_DYNAMIC_ENABLED` | False | `.env:162` |
