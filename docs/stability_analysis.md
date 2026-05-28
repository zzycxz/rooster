# Rooster 稳定性深度分析报告

> **基于完整代码阅读**：`router.py`、`mission_runner.py`、`executor.py`（1054行）、`strategist.py`、`executor.md`  
> **日期**: 2026-05-27 | **分析者**: Antigravity

---

## 一、架构现状：调用链全景图

### 当前实际调用链（以"帮我下载误杀"为例）

```
用户输入
  │
  ├─ [LLM Call #1] Router._triage_via_llm()
  │    模型: ROUTER_MODEL_NAME
  │    → 判定为 [REFRAME]
  │
  ├─ [LLM Call #2] Reframer.reframe()
  │    模型: REFRAMER_MODEL_NAME
  │    → 输出结构化意图 JSON（原始意图细节此时已损耗）
  │
  ├─ [LLM Call #3] Strategist.plan_stream()
  │    模型: STRATEGIST_MODEL_NAME | max_tokens=32768 | 无超时！
  │    → 流式输出子任务 JSON（实时 regex 解析，脆弱）
  │    → 产出 1-2 个 SubTask
  │
  ├─ [LLM Call #4] AgentExecutor.run() for ST1
  │    模型: EXECUTOR_MODEL_NAME
  │    → 真正的 ReAct 循环（这里才是实际工作）
  │    → history: 冻结的 _baseline_session_history[:20]
  │
  └─ [LLM Call #5] Auditor.review() for 叶节点 ST1
       模型: AUDITOR_MODEL_NAME
       → AFFIRM → 结束 ✅
       → REMAND → [LLM Call #6] 重试 AgentExecutor
                   history: 新实例 + previous_audit_cmd 文字（无上次工具输出）
                   → 实际上是失忆重跑 ❌
```

**结论：最少 5 次串行 LLM 调用，最多 6-8 次（含重试），每次边界都是信息丢失点。**

---

## 二、根因清单

### 根因 1：`plan_stream()` 无超时保护 🔴 最高风险

**位置**: `src/agents/strategist.py` L175-319

```python
async def plan_stream(self, user_request: str):
    # ↓ chat_stream 外层没有 asyncio.timeout()！
    async for delta in self.llm_client.chat_stream(
        messages=messages, model=settings.STRATEGIST_MODEL_NAME,
        temperature=0.1, max_tokens=32768    # 最大 32768 tokens
    ):
```

`plan()` 方法有 `asyncio.wait_for(timeout=settings.STRATEGIST_LLM_TIMEOUT)` 包裹，但 MissionRunner 实际调用的是 `plan_stream()`，**这个方法没有任何超时保护**。

LLM 提供商在流式输出中途网络中断 → Rooster 永久阻塞，任务挂死。

---

### 根因 2：REMAND 重试 Executor 完全失忆 🔴

**位置**: `src/agents/runners/mission_runner.py` L451-457

```python
context_parts = []
if dep_results:
    context_parts.append("前置任务结果：\n" + "\n".join(dep_results))
if previous_audit_cmd:
    context_parts.append(f"审计官修正指令：\n{previous_audit_cmd}")  # ← 只有文字命令
combined_context = "\n\n".join(context_parts) if context_parts else ""
```

Auditor 说"重做"，重试的 Executor 拿到的只是一段修正指令文字，**完全不包含上次执行的工具调用历史和工具返回值**。

等于告诉一个失忆的人"你刚才做错了，重做" — 他不知道自己刚才做了什么。

**对比 Claude Code**：工具失败的 error message 直接留在 history 里，LLM 下一步直接看到并推理如何修复。

---

### 根因 3：`[DIRECT]` 任务走错了路由 🟠

**位置**: `src/agents/router.py` L192-241

```python
# [TALK] 正确走 SoloRunner
if triage_state == "[TALK]":
    await self.solo_runner.run(msg, channel, dynamic_event_handler)
    return

# [DIRECT] 和 [REFRAME] 都走 MissionRunner ← 问题在这里
await self.mission_runner.run(msg, channel, reframed_text, dynamic_event_handler)
```

`[DIRECT]` 代表"任务清晰，不需要重构意图"，但它仍然调用 Strategist 拆子任务、Auditor 审计。大多数清晰任务（搜索、下载、单步查询）根本不需要子任务拆分。

Strategist 通常对简单任务只产出 1 个子任务（或 FAILSAFE），但已经多消耗了一次 LLM 调用和解析过程。

---

### 根因 4：Strategist JSON 流式解析脆弱 🟠

**位置**: `src/agents/strategist.py` L213-268

```python
# 手写深度计数器实时解析 LLM 流式 JSON
depth = 0
start_idx = -1
for i, char in enumerate(subtasks_part):
    if char == "{":
        if depth == 0:
            start_idx = i
        depth += 1
    elif char == "}":
        depth -= 1
        if depth == 0 and start_idx != -1:
            obj_str = subtasks_part[start_idx : i + 1].strip()
            task_data = json.loads(obj_str)  # 任何格式偏差都会 raise
```

LLM 输出 JSON 时轻微格式偏移 → 解析失败 → 触发全量 fallback regex → 再失败 → FAILSAFE 降级。

**FAILSAFE 是危险的**：Executor 拿着"降级任务处理"这个模糊指令去执行原始用户请求，效果往往很差。

---

### 根因 5：Checkpoint 完整但默认关闭 🟡

**位置**: `src/agents/runners/mission_runner.py` L101

```python
def _save_checkpoint(self, ...):
    if not getattr(settings, "CHECKPOINT_ENABLED", False):   # 默认 False！
        return
```

长任务崩溃必须从零重来。改一行 `.env` 即可解决，但这个配置项没有在文档中说明，导致它一直关着。

---

### 根因 6：并行子任务 baseline history 截断过激 🟡

**位置**: `src/agents/runners/mission_runner.py` L276

```python
_baseline_session_history = [{...} for m in session.history[-20:]]
```

子任务间通过 `dep_results` 传递信息，但：
```python
# L307: 上游结果被截断至 2000 字符
dep_results.append(f"[{dep_id}] {dep_report.observation[:2000]}")
```

一个长搜索结果（如下载列表）被截断到 2000 字符，下游子任务看到的是不完整的信息，可能选择错误的候选项。

---

### 根因 7：executor.md 强制 JSON 输出 🟡

Executor prompt 要求每次任务结束必须输出 FINAL_REPORT JSON。对于 SoloRunner 模式（直接回复用户）这是额外负担，且 JSON 格式错误会导致 Auditor 解析失败 → REMAND → 更多重试。

---

## 三、与 Claude Code 的架构对比

| 维度 | Claude Code | Rooster 现状 | Rooster 改造目标 |
|------|------------|-------------|----------------|
| Context 连续性 | 单一 context 贯穿全程 | 5 个角色各有独立 context | 简单任务单 context |
| 重试机制 | 工具 error 在 history 里，原地推理 | REMAND → 新 Executor 失忆重跑 | REMAND 携带完整 observation |
| 规划方式 | chain-of-thought 内联 | 独立 Strategist LLM + JSON | Executor 内联规划（简单任务） |
| 错误恢复 | 下一步直接看 error，换工具 | ESCALATE → Strategist.replan() | 原地重试优先 |
| 最少 LLM 调用数 | 2次（triage可选+执行） | 5次 | ≤ 3次（简单任务） |
| 长任务续跑 | 依赖外部持久化 | 已实现但默认关闭 | 默认开启 |
| 多 agent 并行 | ❌ 不支持 | ✅ 支持（核心优势） | ✅ 保留 |

---

## 四、Executor 本身的评价

> **结论：`executor.py` 的 ReAct 循环写得很好，不需要重写。**

1054 行的 executor 已经包含：
- ✅ Stuck 检测（同一工具调用重复 N 次自动打断）
- ✅ 空响应重试（Empty response → retry 2 次）
- ✅ Context 自动剪枝（超过 60% 阈值触发压缩）
- ✅ 函数调用（FC）per-step schema 路由（只给模型相关工具）
- ✅ Blackboard 多 agent 协调
- ✅ Vision 支持（隐私路由 + base64 注入）
- ✅ 工具失败单个隔离（不因一个工具失败取消其他并发工具）

**这已经是 Claude Code 级别的实现。问题是它被套在 3-4 层编排下面，只有 20% 的任务才能直接进入它。**

---

## 五、改造策略总结

**核心思路**：不是推翻现有架构，而是为简单任务开一条绕过 Strategist 和 Auditor 的快速通道。

```
改造前: 所有任务 → Router → Reframer → Strategist → Executor → Auditor
改造后:
  简单任务 ([DIRECT])  → Router → SoloRunner → Executor（单循环）
  复杂任务 ([REFRAME]) → Router → Reframer → Strategist → Executor → Auditor（保留）
```

**详细执行计划见 `docs/todo.md`。**
