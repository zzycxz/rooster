# Rooster 稳定性重构计划
## 对标 Claude Code / Codex，让任务执行像呼吸一样稳定

> **版本**: v1.0 | **日期**: 2026-05-27  
> **背景**: 当前任务执行不稳定、速度慢，希望对标 Claude Code / Codex 的稳定执行体验  
> **关联文档**: `stability_analysis.md`（根因详情） | `todo.md`（执行 Checklist） | `goal.md`（目标指标）

---

## 第一部分：我们在哪里（现状）

### 一次普通任务的实际代价

用户说"帮我下载误杀"，系统背后发生了什么：

```
用户输入
  ↓
[LLM 调用 #1]  Router 分诊         — 判断任务类型
  ↓
[LLM 调用 #2]  Reframer 意图重构   — 输出结构化 JSON（原始意图细节开始丢失）
  ↓
[LLM 调用 #3]  Strategist 规划     — 流式输出子任务（无超时保护！）
  ↓
[LLM 调用 #4]  Executor 执行       — 真正干活（但 history 是冻结副本）
  ↓
[LLM 调用 #5]  Auditor 审计        — 评判结果
  ↓（若失败）
[LLM 调用 #6]  Executor 重试       — 失忆重跑（不知道上次做了什么）
```

**5-6 次串行 LLM 调用，每次之间都有 context 断层，每次断层都是一个潜在的失败点。**

### Claude Code 做同样的事

```
用户输入
  ↓
[LLM 调用 #1]  开始执行循环（工具失败直接在同一 context 里重试）
  ↓
[LLM 调用 #2..N] 继续循环，直到完成
```

**1 个连续 context，工具失败就地重试，没有 context 断层。**

### 核心矛盾

> **你的 `executor.py` 已经是 Claude Code 风格的 ReAct 循环（1054 行，写得很完整）。**  
> **问题是它被套在 3-4 层编排下面，只有 20% 的任务才能顺畅地进入它。**

---

## 第二部分：为什么会这样（根因）

> 详细代码行定位见 `stability_analysis.md`，这里只列结论。

| # | 根因 | 风险等级 | 一句话描述 |
|---|------|---------|-----------|
| 1 | `plan_stream()` 无超时 | 🔴 致命 | LLM 流中途断开 → Rooster 永久挂死 |
| 2 | REMAND 重试失忆 | 🔴 严重 | 重试 Executor 不知道上次做了什么，必然重蹈覆辙 |
| 3 | `[DIRECT]` 走错路由 | 🟠 高 | 清晰任务仍走 Strategist + Auditor，多 3 次 LLM 调用 |
| 4 | Strategist JSON 解析脆弱 | 🟠 高 | LLM 输出格式轻微偏移 → FAILSAFE 降级 → 执行质量断崖 |
| 5 | Checkpoint 默认关闭 | 🟡 中 | 长任务崩溃必须从零重来，明明有机制却没开 |
| 6 | dep_results 截断过激 | 🟡 中 | 子任务间只传 2000 字符，复杂结果被截断 |
| 7 | Executor 强制 JSON 输出 | 🟡 中 | SoloRunner 模式不需要 JSON，多了一层格式化负担 |

---

## 第三部分：我们要去哪里（目标）

### 北极星指标

| 指标 | 当前估计 | 目标 |
|------|---------|------|
| 单任务首次成功率 | ~60% | ≥ 85% |
| 简单任务平均延迟 | 60-120s | < 30s |
| 长任务崩溃后需重头来 | ~40% | < 10% |
| 每次简单任务 LLM 调用次数 | 5次 | ≤ 3次 |

### 改造后的调用链

```
改造后——简单任务（[DIRECT]）:
用户输入 → Router 分诊 → SoloRunner → Executor ReAct 循环
           [LLM #1]                    [LLM #2..N]
           ← 最少 2 次 LLM 调用，单一连续 context →

改造后——复杂并行任务（[REFRAME]）:
用户输入 → Router → Reframer → MissionRunner → 并行 Executor × N
           [LLM#1]  [LLM#2]    Strategist[#3]   [LLM#4..N]
           ← 多 agent 真正并行，这是 Claude Code 没有的能力 →
```

### 我们的差异化定位

> Rooster 不是 Claude Code 的复制品。  
> 我们要在**单任务稳定性**上对齐 Claude Code，  
> 同时保留 Claude Code **没有**的：多 agent 真正并行能力、本地模型支持、中文生态。

---

## 第四部分：怎么做（执行计划）

---

### Phase 0 — 立即见效（改配置，不改代码）⏱ 30 分钟

> **策略**：零代码风险，随时可回滚。先把已经实现但未启用的保护机制全部打开。

---

#### 0.1 开启 Checkpoint（最重要）

**根因**：`mission_runner.py` 里有完整的 checkpoint 保存和恢复逻辑，但 `CHECKPOINT_ENABLED` 默认是 `false`。长任务（>5 分钟）一旦崩溃，所有进度归零。

**影响范围**：`src/agents/runners/mission_runner.py` 的 `_save_checkpoint()` 和 `_load_checkpoint()` 方法，数据写到 `.rooster/checkpoints/<session_id>.json`。

**配置操作**：
```bash
# .env
CHECKPOINT_ENABLED=true
CHECKPOINT_DIR=.rooster/checkpoints   # 默认值，可以不改
```

**开启后行为**：
- 每个子任务完成时自动保存进度（subtask 粒度）
- 重启后发送相同任务 → 系统检测到 checkpoint → 跳过已完成子任务，从失败点继续
- checkpoint 文件存储：subtask 状态（SUCCESS/FAIL）+ 每个子任务的 report.observation

**风险**：
- checkpoint 文件占磁盘空间（每个任务约 10-50KB）
- 如果任务逻辑变更，旧 checkpoint 可能导致跳过本应重新执行的步骤
- **回滚**：`CHECKPOINT_ENABLED=false` 即禁用，不影响任何功能

**验收**：
```bash
# 1. 发起下载任务
# 2. 任务执行到第 2 个子任务时 Ctrl+C
# 3. 检查文件是否存在
ls .rooster/checkpoints/
# 4. 重启后发送相同任务，观察日志
# 期望：[MissionRunner] Resuming from checkpoint, skipping completed subtasks
```

---

#### 0.2 补充 Jiutian 独立速率限制

**根因**：`providers.py` 只有 `ZHIPU_MIN_INTERVAL=6.0s` 的专属速率限制，jiutian（当前最常用 provider）只享有全局 `LLM_MIN_INTERVAL=1.5s`。3 个并行子任务可以在 1.5s 内全打到 jiutian，触发 429。

**影响范围**：`src/utils/config/providers.py` + `src/agents/llm_client.py` 的 `_get_provider_min_interval()` 函数。

**配置操作**（暂时用全局配置降低并发，代码修复在 Phase 2）：
```bash
# .env
LLM_MIN_INTERVAL=2.5                   # 全局间隔从 1.5s 提升到 2.5s
MAX_PARALLEL_SUBTASKS=2                # 并行子任务从 3 降到 2
```

**风险**：
- 全局速率提升 → 单步执行略慢（+1s/step）
- 并行数下降 → 多子任务任务整体耗时略增
- **回滚**：恢复原值即可

---

#### 0.3 REMAND 重试次数限制

**根因**：REMAND（审计失败重试）的根因是 Executor 重试时不知道上次做了什么（Phase 2 修复），在修复之前多次重试几乎没有意义，只是在浪费时间和 token。

**配置操作**：
```bash
# .env
AUDIT_MAX_REMAND_RETRY=1               # 从默认值（未知）降到最多 1 次重试
```

**影响**：REMAND 最多重试 1 次，重试失败直接报告 FAIL，不再无休止循环。

**验收**：Phase 0 全部配置完成后，重启服务并发起一个会触发 REMAND 的任务（故意构造一个让 Auditor 不满意的场景），观察日志确认重试次数 ≤ 1。

---

### Phase 1 — 路由手术（最高 ROI）⏱ 2-3 小时

> **核心目标**：让 80% 的日常任务（`[DIRECT]` 类）彻底绕过 Strategist 和 Auditor，减少 3 次串行 LLM 调用，从根本上提升速度和稳定性。

**当前问题（基于 router.py 代码阅读）**：
```python
# router.py 当前逻辑（简化）
if triage_state in ["[DIRECT]", "[REFRAME]"]:
    await self.mission_runner.run(msg, channel, reframed_text, ...)
    # ↑ DIRECT 和 REFRAME 走同一条路，都经过 Strategist
```

`[DIRECT]` 的语义是"意图清晰，不需要分解"，但当前代码却把它送进 `MissionRunner`（必须经过 Strategist → Executor → Auditor）。

---

#### 1.1 `[DIRECT]` → SoloRunner

**改动文件**：`src/agents/router.py`  
**改动位置**：`handle_inbound()` 方法，triage 结果判断处（约第 192 行）

**影响分析**：
- `[DIRECT]` 任务：Router → SoloRunner（节省 Strategist + Auditor 各 1 次 LLM 调用）
- `[REFRAME]` 任务：Router → MissionRunner（保持原有多步规划能力，不受影响）
- `[TALK]` 任务：不变
- `SoloRunner` 已存在，这个改动只是修改路由判断，不需要新建任何模块

**代码改动**：

```python
# router.py handle_inbound() 中的路由判断

# ===== 改前 =====
if triage_state in ["[DIRECT]", "[REFRAME]"]:
    await self.mission_runner.run(msg, channel, reframed_text, dynamic_event_handler)
    self._fire_and_forget(evolution_engine.on_turn_complete(...))

# ===== 改后 =====
if triage_state == "[DIRECT]":
    # DIRECT = 意图清晰，单 Executor 直接执行，不需要 Strategist 分解
    # 节省 2-3 次 LLM 调用（Reframer 重构 + Strategist 规划 + Auditor 审计）
    logger.info(f"[Router] DIRECT → SoloRunner (skip Strategist+Auditor)")
    await self.solo_runner.run(msg, channel, dynamic_event_handler)
    self._fire_and_forget(evolution_engine.on_turn_complete(...))
    return

if triage_state == "[REFRAME]":
    # REFRAME = 需要意图重构或多步分解，保留完整 MissionRunner 路径
    await self.mission_runner.run(msg, channel, reframed_text, dynamic_event_handler)
    self._fire_and_forget(evolution_engine.on_turn_complete(...))
```

**需要确认的边界情况**：

| 场景 | 当前行为 | 改造后行为 | 是否正确 |
|------|---------|-----------|---------|
| `[DIRECT]` 简单搜索 | MissionRunner → Strategist → Executor | SoloRunner → Executor | ✅ 更快 |
| `[DIRECT]` 下载任务（多步） | MissionRunner → Strategist → 多 Executor | SoloRunner → 单 Executor（内联规划）| ✅ Phase 2 的 executor.md 三段式覆盖 |
| `[REFRAME]` 真正复杂任务 | MissionRunner（正确） | MissionRunner（不变）| ✅ 正确 |
| `[TALK]` 纯聊天 | SoloRunner（正确） | SoloRunner（不变）| ✅ 正确 |

**风险**：
- 原本依赖 Strategist 分解的 `[DIRECT]` 多步任务，现在全靠 Executor 的内联规划（Phase 2 的 executor.md 三段式）。如果 executor.md 改造未完成，某些多步 DIRECT 任务质量可能下降。
- **建议执行顺序**：Phase 1.1 → Phase 2.3（executor.md 三段式）→ 再全量测试
- **回滚方案**：把 `[DIRECT]` 改回走 `mission_runner.run()` 即可，一行代码

**验收标准**：
```python
# 在日志中搜索
# 改造前：[MissionRunner] Starting mission for [DIRECT] task
# 改造后：[Router] DIRECT → SoloRunner
grep "DIRECT → SoloRunner" rooster.log

# 量化目标：10 次 DIRECT 任务，平均 LLM 调用次数 ≤ 3
```

---

#### 1.2 MissionRunner 单子任务快速路径

**背景**：即使 `[REFRAME]` 任务走 MissionRunner，Strategist 有时只会产出 1 个子任务（任务本来就不复杂）。这时走完整的 Auditor 审计毫无意义，白白增加 1 次 LLM 调用。

**改动文件**：`src/agents/runners/mission_runner.py`  
**改动位置**：Strategist 规划完成、子任务列表生成后（约在 `_run_plan_phase()` 方法末尾）

**代码改动**：
```python
# mission_runner.py _run_plan_phase() 结束后

subtask_list = await self._collect_subtasks(strategist_stream)

# ===== 新增：单子任务快速路径 =====
if len(subtask_list) == 1:
    logger.info(
        f"[MissionRunner] 只有 1 个子任务，转为 SoloRunner 快速路径，"
        f"跳过 Auditor（任务: {subtask_list[0].instruction[:50]}）"
    )
    # 把子任务 instruction 作为 SoloRunner 的输入
    solo_msg = copy.copy(msg)
    solo_msg.text = subtask_list[0].instruction
    await self.solo_runner.run(solo_msg, channel, dynamic_event_handler)
    return
# ===== 快速路径结束 =====

# 原有多子任务执行路径（不变）
await self._run_parallel_subtasks(subtask_list, msg, channel, ...)
```

**影响分析**：
- 只影响 Strategist 产出单子任务的情况（约占 MissionRunner 任务的 30-40%）
- 对真正的多子任务并行场景无任何影响

**风险**：
- 如果 `subtask_list[0].instruction` 和原始 `msg.text` 语义有偏差（Strategist 重写了），SoloRunner 收到的可能是 Strategist 重写后的指令，而不是用户原始指令
- **缓解**：在日志中记录原始指令和子任务指令，便于对比排查

**验收标准**：
- 发起一个中等复杂度任务（如"帮我查一下最近的 AI 新闻并总结"），观察 Strategist 是否只产出 1 个子任务，若是，则应走 SoloRunner 快速路径
- 日志中应看到：`[MissionRunner] 只有 1 个子任务，转为 SoloRunner 快速路径`

**Phase 1 整体验收**：
- 连续发起 10 个不同简单任务（3 搜索、3 下载、2 查询、2 聊天）
- 记录每次任务的 LLM 调用次数（通过日志 `trace_id` 统计）
- 目标：简单任务平均 LLM 调用次数从 5 次降到 ≤ 3 次，响应时间缩短 ≥ 40%


### Phase 1.5 — Context 语义压缩（对标 Claude Code）⏱ 3-4 小时

**背景**：这是原计划遗漏的关键一环，也是单模型统一 context 能否稳定的核心前提。

**当前代码实际状态（基于完整阅读 executor.py L240-255）**：

| 机制 | 状态 | 作用 |
|------|------|------|
| `count_message_tokens` | ✅ 已实现 | 60% 阈值触发检测 |
| `schedule_memory_compaction` | ✅ 已实现 | 后台写入 LTM（跨 session 长期记忆） |
| `_prune_history` | ✅ 已实现 | **但只是截断，直接丢旧消息** |
| **任务内联语义摘要** | ❌ 缺失 | 这是 Claude Code 的核心机制 |

**截断 vs 语义摘要的本质区别**：

```
截断（当前）：
  Step 2: 搜索到链接2（评分最高）→ 选了链接2
  ...（超过阈值，截断）
  Step 16: LLM 忘记链接2，重新搜索 ← 重复工作！

语义摘要（目标）：
  [SUMMARY] 已选《误杀》链接2(BluRay.1080p，15GB)
            已启动下载，进度23%，路径 /tmp/...
  Step 16: LLM 看摘要，继续监控进度 ✅
```

**Claude Code 的实际机制**：当上下文接近 90% 满载，用 Claude 自身生成语义摘要，内容包含：已完成步骤 / 关键数据（URL、路径、选中项）/ 失败路径 / 当前状态，然后**替换旧历史**，只保留最近 3-4 轮原始对话。

#### 1.5.1 实现 `_inline_compact_history()`

**改动位置**：`src/agents/executor.py`，新增方法并替换 `_prune_history` 调用

```python
async def _inline_compact_history(
    self,
    session_history: List[Dict],
    context_limit: int,
    keep_recent: int = 6,
) -> List[Dict]:
    """
    Claude Code 同款语义压缩。
    触发条件：session_history token 超过 context_limit * 0.5。
    用 FAST_MODEL 生成执行摘要，替换旧历史，保留最近 keep_recent 条。
    """
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
            "不要保留思考过程和完整 JSON。\n\n执行历史：\n" +
            "\n".join(
                f"{m['role']}: {str(m.get('content',''))[:300]}"
                for m in old_history
            )
        )
    }]
    try:
        summary = ""
        async for delta in self.llm_client.chat_stream(
            model=settings.FAST_MODEL_NAME,
            messages=compaction_prompt,
            max_tokens=1500,
        ):
            if delta.content:
                summary += delta.content
        executor_logger.info(
            f"[Compact] 语义压缩: {len(old_history)} 条 → {len(summary)} 字"
        )
    except Exception as e:
        executor_logger.warning(f"[Compact] 语义压缩失败，降级截断: {e}")
        return self._prune_history(session_history, context_limit)

    return [
        {"role": "user", "content": f"[EXECUTION SUMMARY]\n{summary}"},
        {"role": "assistant", "content": "已了解执行进度，继续任务。"}
    ] + recent_history
```

在 ReAct 循环 L255 处替换调用：

```python
# 改前：
session_history = self._prune_history(session_history, max_total_tokens=context_limit)

# 改后：
session_history = await self._inline_compact_history(session_history, context_limit)
```

#### 1.5.2 新建 `src/prompts/compaction.md`

将压缩 Prompt 抽离为独立文件，便于迭代，内容包含：必须保留的信息类别（已完成步骤 / 关键数据 / 失败路径 / 当前状态）、不保留的内容（思考过程 / 完整 JSON / 重复细节）、输出格式模板。

#### 1.5.3 executor.md 改为三段式（Plan → Execute → Verify）

SoloRunner 模式下，executor.md 是唯一的系统 Prompt，需覆盖 Strategist 和 Auditor 的职责：

```markdown
## Phase 1 — 内联规划（首次工具调用前）
对于 3 步以上任务，先输出 <plan>...</plan>

## Phase 2 — 执行循环（ReAct Loop）
[现有内容保留不变]

## Phase 3 — 完成前自检
输出最终答案前逐条核对：
□ 用户要求的每件事都做了吗？
□ 结果与目标一致吗？
如发现遗漏，直接补救，不只报告问题。
```

#### 1.5.4 Sandwich 模式（工程技巧）

针对"Lost in the Middle"注意力衰减，将关键状态信息**同时放在 context 首部（SUMMARY）和尾部（compose_messages 末尾追加）**，确保 LLM 始终能注意到它。

**验收标准**：
- 发起一个 20+ 步的长任务，Step 20 时 LLM 仍能准确引用 Step 2-3 发现的关键数据
- 对比压缩前后：压缩后应减少"遗忘性重复搜索"现象

---

### Phase 2 — Executor 增强 ⏱ 3-4 小时

**核心目标**：让 Executor 本身具备闭环能力（规划+执行+纠错），这是 SoloRunner 模式能独立工作的关键。同时修复 Auditor 驳回后"失忆"的致命 Bug。

---

#### 2.1 executor.md 内联规划（三段式重构）

**根因**：当前 Executor 被设计为"纯干活"的盲目的机器，缺乏全局视角。一旦脱离 Strategist 走 SoloRunner 模式，处理多步任务容易像无头苍蝇。
**影响范围**：`src/prompts/executor.md` 系统提示词文件。

**改动操作**：在 `executor.md` 增加首段（规划）和尾段（验证）：
```markdown
## Phase 1 — 内联规划（首次工具调用前）
对于 3 步以上的任务，你必须在第一次调用任何工具之前，先输出你的执行计划。
<plan>
步骤1: 搜索相关信息...
步骤2: 筛选结果并总结...
步骤3: 写入文件...
</plan>

## Phase 2 — 执行循环（ReAct Loop）
[保留原有内容]

## Phase 3 — 完成前自检
在输出最终答案或标记任务完成前，逐条核对：
□ 用户要求的每件事都做了吗？
□ 如果失败了，有没有尝试过不同的方法？
如发现遗漏，请直接调用工具补救，不要只报告问题。
```

**风险**：
- prompt 变长，可能轻微增加每次 token 开销。
- 模型可能在不需要规划的简单任务也输出 `<plan>`。
- **回滚**：恢复旧版 prompt 即可。

---

#### 2.2 REMAND 重试携带完整上次 observation

**根因**：当前代码中，当 Auditor 判定结果不合格并发起 `REMAND`（重试）时，传递给新 Executor 的 prompt 只有"审计官的修正指令"，而没有**上次尝试的完整工具输出**。这导致 Executor 重试时完全"失忆"，通常会重蹈覆辙（比如再次搜索同样的关键词，犯同样的错误）。
**影响范围**：`src/agents/runners/mission_runner.py` 的 `_run_subtask_inner()` 方法。

**代码改动**：在构造重试 context 时，注入 previous_report 的内容。

```python
# _run_subtask_inner() 第 450 行附近

# ===== 改前 =====
# 只有干瘪的重试指令
context_parts.append(f"审计官修正指令：\n{previous_audit_cmd}")

# ===== 改后 =====
if previous_report is not None:
    prev_obs = previous_report.observation or ""
    # 限制长度防止超长
    if len(prev_obs) > 3000:
        prev_obs = prev_obs[:3000] + "\n...[截断，仅保留前3000字]"
    
    # 注入：[你上次做错了什么] + [审计官要求你怎么改]
    context_parts.append(
        f"【你上一次尝试的执行结果（已被驳回）】\n{prev_obs}\n\n"
        f"【审计官的修正指令】\n{previous_audit_cmd}"
    )
```

**验收**：
- 手动构造一个一定会失败的任务（比如要求下载一个不存在的文件并强制 Auditor 检查）。
- 查看第二次执行的 Executor 日志，确认其收到的 prompt 包含了第一次尝试的完整或截断的 observation。

---

#### 2.3 SoloRunner 取消强制 JSON 输出

**根因**：当前无论在哪种模式下，Executor 最后一步都会被强制要求输出 `FINAL_REPORT` 格式的 JSON，因为 MissionRunner 的 Auditor 需要解析 JSON。但 SoloRunner 模式没有 Auditor，强制 JSON 输出不仅浪费 token，还可能因为格式错误导致解析失败。
**影响范围**：`src/agents/prompt_builder.py` 动态 prompt 构建。

**改动操作**：根据传入的 `agent_id` 区分是否强制 JSON。

```python
# prompt_builder.py

if agent_id.startswith("solo"):
    # 不附加 JSON 要求，鼓励直接自然语言回答
    final_instruction = "请直接给出用户的最终答案或任务结果。"
else:
    # 保持原样，要求输出 JSON 供 Auditor 解析
    final_instruction = "你必须使用严格的 JSON 格式输出最终报告..."
```

---

### Phase 3 — Strategist 加固 ⏱ 2 小时

**核心目标**：消除规划层（Strategist）和审计层（Auditor）的脆弱性，确保在网络不稳定或模型抽风时，系统能优雅降级而不是永久挂死。

---

#### 3.1 `plan_stream()` 加超时保护

**根因**：`strategist.py` 中的 `plan_stream()` 使用了异步生成器 `async for delta in self.llm_client.chat_stream(...)`。如果底层 HTTP 连接在接收流式响应时因为网络波动（例如运营商丢包）而半开（half-open）且没有抛出异常，整个 `plan_stream` 将会**永久阻塞**。当前代码外层没有 `asyncio.timeout()` 保护。
**影响范围**：`src/agents/strategist.py`。

**代码改动**：给整个流式调用包裹 `asyncio.timeout`。

```python
# strategist.py plan_stream() 方法

async def plan_stream(self, user_request: str):
    logger.info(f"🧠 [Strategist] 流式规划启动: {user_request}")
    # ... 构建 messages ...
    yielded_ids = set()
    
    try:
        # ===== 新增：整个流式规划加超时保护 =====
        async with asyncio.timeout(settings.STRATEGIST_LLM_TIMEOUT):
            async for delta in self.llm_client.chat_stream(...):
                # ... 原有 JSON 解析和 yield 逻辑 ...
                pass
                
    except asyncio.TimeoutError:
        logger.error(f"❌ [Strategist] plan_stream() 超时 ({settings.STRATEGIST_LLM_TIMEOUT}s)，触发降级机制")
        # 降级：如果没有 yield 过任何子任务，返回一个单步 FAILSAFE 任务
        if not yielded_ids:
            yield SubTask(id="FAILSAFE", instruction=user_request, domain="SYSTEM", tool="system_fallback")
        return
    except Exception as e:
        # 捕获其他网络或解析异常
        logger.error(f"❌ [Strategist] plan_stream() 发生错误: {e}")
        if not yielded_ids:
            yield SubTask(id="FAILSAFE", instruction=user_request, domain="SYSTEM", tool="system_fallback")
        return
```

**风险**：
- 如果 `STRATEGIST_LLM_TIMEOUT` 设置过短（例如 10s），会导致正常的长回复被截断。必须确保环境变量配置合理（建议 60s - 90s）。
- **回滚**：移除 `asyncio.timeout` 块。

**验收**：
- 在测试环境中，使用工具（如 tc）模拟网络延迟，或者临时将代码里的 timeout 硬编码为 1s。
- 发起复杂请求，观察日志是否打印 `[Strategist] plan_stream() 超时` 并生成 `FAILSAFE` 子任务。

---

#### 3.2 Auditor 降级为高风险专属

**根因**：当前每个 MissionRunner 产生的子任务完成时，都会调用 `Auditor.review()`。审计过程需要一次完整的 LLM 调用。对于查询、搜索等低风险且无副作用的读操作，Executor 自己通常就能验证结果，再套一层 Auditor 性价比极低，徒增延迟和失败率。
**影响范围**：`src/agents/runners/mission_runner.py` 叶节点审计判断处。

**代码改动**：只对系统预定义的高风险操作进行独立 Auditor 审计。

```python
# mission_runner.py 

# ===== 改前 =====
# 所有叶节点一律审计
verdict = await self.auditor.review(report, st, is_leaf=True)

# ===== 改后 =====
HIGH_RISK_DOMAINS = {"FILE_DELETE", "EMAIL", "PAYMENT", "SYSTEM_MODIFY"}
needs_llm_audit = st.domain in HIGH_RISK_DOMAINS or st.requires_confirm

if needs_llm_audit:
    logger.info(f"[MissionRunner] 高风险任务 {st.id}，进入 Auditor 审计")
    verdict = await self.auditor.review(report, st, is_leaf=True)
    is_affirm = verdict is not None and verdict.verdict == AuditVerdictType.AFFIRM
else:
    logger.debug(f"[MissionRunner] 低风险任务 {st.id}，跳过 Auditor 审计")
    # 低风险操作：只要 Executor 返回 SUCCESS 即视为通过
    is_affirm = (report.status == "SUCCESS")
```

**风险**：
- 如果 Executor"自信地做错了"且返回 SUCCESS，低风险任务可能产生无效结果返回给用户。
- 但由于我们已经在 Phase 2.1 为 Executor 增加了三段式的"完成前自检"，这部分风险可控。

**验收**：
- 运行一个搜索任务，观察日志，确认输出 `低风险任务，跳过 Auditor 审计`。
- 运行一个删除文件的任务，观察日志，确认输出 `高风险任务，进入 Auditor 审计`。

---

### Phase 4 — 可观测性 ⏱ 4-6 小时

**核心目标**：让生产环境中的并发多智能体系统具备可诊断性。当一个复杂任务失败时，可以通过日志和 Dashboard 快速定位是哪一步、哪个模型、由于什么原因导致了失败。

---

#### 4.1 Trace ID 贯穿调用链

**根因**：当前系统同时处理多个用户请求，且 MissionRunner 会并发生成多个子任务 Executor。所有 Agent 都在打印日志，`rooster.log` 是一个交织在一起的面条。如果某个子任务在第 15 步出错，无法从日志中抽取出该任务的完整上下文。
**影响范围**：整个框架，主要在 `src/agents/` 下的所有 agent（Router, Strategist, Executor, Auditor）以及 `llm_client.py`。

**代码改动建议**：
1. `AgentRunConfig` 增加 `trace_id` 属性。
2. 在 `handle_inbound` 收到用户消息时，生成全局唯一的 `trace_id`（例如 uuid4 的前 8 位）。
3. 这个 `trace_id` 需要顺着 `MissionRunner` -> `SubTask` -> `Executor` 一路透传下去。
4. 修改日志格式器（Formatter）或者在每次记录日志时带上 `[Trace: {trace_id}]`。
5. 在 `llm_client.py` 中，请求大模型前和收到回复后打印带有 trace_id 的 token 消耗日志。

**验收标准**：
- 能够通过命令 `grep "Trace: 8a9b2c3d" rooster.log` 提取出某次任务从用户输入到最终完成的所有相关日志。

---

#### 4.2 Dashboard 活跃任务监控面板

**根因**：目前只能通过尾部日志（tail -f）查看系统是否在运行，Dashboard 上缺乏正在执行任务的实时状态，对运营人员极不友好。
**影响范围**：前端 HTML (`dashboard.html`) 和后端状态报告。

**代码改动建议**：
1. **后端状态机**：维护一个全局字典或 redis key，存储当前所有 inflight 任务的状态（任务ID、开始时间、当前步骤描述、状态）。
2. **状态更新**：在 `executor.py` 执行完每个步骤后，更新此状态机。
3. **前端渲染**：通过 WebSocket 将状态变化推送到前端 Dashboard，新增一个专门的 `[当前活跃任务]` 卡片进行展示。

**验收标准**：
- 在网页 Dashboard 能看到实时跳动的进度条和当前正在执行的具体子步骤描述。

---

#### 4.3 Executor step 级别轻量 Checkpoint

**根因**：当前 Checkpoint 机制的粒度太粗，只在整个 `SubTask`（可能包含几十次 LLM 交互）结束时保存。如果任务在最后一步崩溃，重启时整个 SubTask 都要重头跑。
**影响范围**：`src/agents/executor.py` 的 `_save_checkpoint`。

**代码改动建议**：
1. 在 Executor 的 ReAct 循环中，每成功执行 N 步（例如 N=5），或者执行耗时极高的工具（如大文件下载）后。
2. 触发一次当前 `session_history` 的快照落盘。
3. 恢复时，优先读取该 session_history 快照，直接还原到中断时的对话状态，继续 ReAct。

**验收标准**：
- 构造一个需要 10 步循环的任务，在第 7 步主动断开服务。
- 重启后，任务应该直接从第 8 步继续，而不是回退到第 1 步重新开始。

---

## 第五部分：优先级总表

| 优先级 | 改动 | 工作量 | 预期收益 | Phase |
|--------|------|--------|---------|-------|
| 🔴 P0 | `CHECKPOINT_ENABLED=true` | 1分钟 | 长任务续跑 | 0 |
| 🔴 P0 | `plan_stream()` 加超时 | 30分钟 | 消除永久阻塞风险 | 3（可提前） |
| 🔴 P1 | `[DIRECT]` → SoloRunner | 2小时 | 速度提升 50%，稳定性大幅提升 | 1 |
| 🟠 P1 | 单子任务快速路径 | 1小时 | 进一步减少 Strategist 开销 | 1 |
| 🟠 P1 | **`_inline_compact_history()` 语义压缩** | 3小时 | 长任务不再失忆，Claude Code 同款机制 | 1.5 |
| 🟠 P1 | **executor.md 三段式重构** | 2小时 | SoloRunner 覆盖规划+执行+验证三角色 | 1.5 |
| 🟠 P1 | **新建 `compaction.md` 压缩 Prompt** | 1小时 | 压缩质量可独立迭代，不硬编码 | 1.5 |
| 🟠 P1 | REMAND 携带 observation | 1小时 | 重试成功率大幅提升 | 2 |
| 🟡 P2 | executor.md 内联规划指令 | 1小时 | SoloRunner 多步任务自主规划 | 2 |
| 🟡 P2 | Auditor 降级为高风险专属 | 1小时 | 减少 1 次 LLM 调用 | 3 |
| 🟡 P2 | Sandwich 模式（尾部状态重复） | 1小时 | 对抗 Lost-in-the-Middle | 1.5 |
| 🟢 P3 | Trace ID + 日志链路 | 4小时 | 可诊断性提升 | 4 |
| 🟢 P3 | Dashboard 活跃任务监控 | 4小时 | 运维可见性提升 | 4 |

---

## 第六部分：总验收标准

完成所有 Phase 后，执行以下测试：

- [ ] **稳定性测试**：10 次简单任务（搜索、下载、查询），首次成功率 ≥ 8/10
- [ ] **速度测试**：记录简单任务（"帮我搜索XX"）的平均响应时间，对比基准值
- [ ] **续跑测试**：长任务（>5分钟），中途杀进程，重启后能从断点继续
- [ ] **重试测试**：触发 REMAND，重试 Executor 的 prompt 包含上次工具输出
- [ ] **超时测试**：引入网络延迟，Strategist 超时降级而非挂死
- [ ] **语义压缩测试**：20步以上任务，Step 20 时 LLM 仍准确引用 Step 2-3 关键数据（不重复搜索）
- [ ] **三段式 Prompt 测试**：SoloRunner 任务开始输出 `<plan>`，结束前执行自检，无 Auditor 仍能发现遗漏

---

## 第七部分：原计划遗漏的两个关键问题

### 7.1 大模型切换：现状与盲区

#### 现有机制（已实现，无需改动）

每个 agent 角色可以独立配置模型，改 `.env` 即生效：

```bash
STRATEGIST_MODEL_MODE=zhipu        # 规划用智谱
EXECUTOR_MODEL_MODE=jiutian        # 执行用九天
AUDITOR_MODEL_MODE=jiutian         # 审计用九天
ROUTER_MODEL_MODE=zhipu            # 分诊用智谱（轻量）
SOLO_MODEL_MODE=jiutian            # SoloRunner 用九天
SOLO_MODEL_NAME=openai/gpt-oss-120b
```

失效转移链（Failover）也已实现：某 provider 返回 429 → 自动冷却 30s → 切换到下一个继续：

```bash
LLM_FAILOVER_ORDER=mimo,zhipu,jiutian,local   # 故障转移顺序
LLM_FAILOVER_ENABLED=true
```

#### ⚠️ 改造 Phase 1 后需要注意的盲区

当 `[DIRECT]` 路由改为走 SoloRunner 后，执行模型从 `EXECUTOR_MODEL_NAME` 变为 `SOLO_MODEL_NAME`。

**目前默认值**：
- `EXECUTOR_MODEL_NAME` = jiutian 路由 → `gpt-oss-120b`（重量级）
- `SOLO_MODEL_NAME` = `openai/gpt-oss-120b`（相同，但显式指定）

两者一致，**但必须在 Phase 1 改动完成后显式验证**：SoloRunner 能否稳定处理原本 MissionRunner 接手的任务类型（下载、搜索等）。

#### 如何切换到"最新模型"

若想换用更新的模型（如 Claude Sonnet 4.5、GPT-4o latest），只需：

1. 在 `.env` 中更新对应的 `*_MODEL_NAME` 字段
2. 如果是全新 provider，在 `providers.py` 注册 URL + KEY + 默认模型名
3. 在 `ModelFactory` 注册新 provider 的客户端类（`src/models/factory.py`）

**无需改动任何业务逻辑代码**，LLMClient 的 pipeline 机制会自动携带。

---

### 7.2 高频请求限流风险：现状与盲区

#### 现有防护（已实现）

| 机制 | 配置项 | 默认值 | 效果 |
|------|--------|--------|------|
| 全局最小间隔 | `LLM_MIN_INTERVAL` | 1.5s | 所有 LLM 调用至少间隔 1.5s |
| Zhipu 专属间隔 | `ZHIPU_MIN_INTERVAL` | 6.0s | Zhipu 每 6s 最多 1 次请求 |
| Per-Provider 并发槽 | `LLM_PROVIDER_MAX_CONCURRENT` | `zhipu:1,jiutian:2` | 限制每 provider 同时请求数 |
| 429 自适应冷却 | 代码内置 | 30s → 指数增长 → 上限 300s | 限流后自动等待，连续失败翻倍冷却 |
| 冷却状态持久化 | `.rooster/state/provider_cooldowns.json` | 自动 | 重启后恢复冷却，不会绕过 |

#### ⚠️ 真实风险 1：并行子任务瞬间爆发多个请求

```python
# mission_runner.py
self._semaphore = asyncio.Semaphore(settings.MAX_PARALLEL_SUBTASKS)  # 默认 3
```

3 个子任务同时执行 → 3 个 LLMClient 实例同时对同一 provider 发请求。  
`ZHIPU_MIN_INTERVAL=6.0s` 对 Zhipu 有效，但 jiutian 只有全局 1.5s 间隔，**3 个并发子任务可能在 3s 内打出 3 个 jiutian 请求**。

**建议修复**：
```bash
# .env 调整并发数与速率限制匹配
MAX_PARALLEL_SUBTASKS=2            # 从 3 降到 2，减少瞬间爆发
JIUTIAN_MIN_INTERVAL=3.0           # 为 jiutian 单独设速率（目前没有，用全局 1.5s）
```

并在 `providers.py` 补充：
```python
JIUTIAN_MIN_INTERVAL: float = _env_float("JIUTIAN_MIN_INTERVAL", 3.0)
```

在 `llm_client.py` 的 `_get_provider_min_interval()` 中补充 jiutian 分支：
```python
def _get_provider_min_interval(provider: str) -> float:
    if provider == "zhipu":
        return settings.ZHIPU_MIN_INTERVAL
    if provider == "jiutian":
        return settings.JIUTIAN_MIN_INTERVAL  # ← 新增
    return settings.LLM_MIN_INTERVAL
```

#### ⚠️ 真实风险 2：一个任务里多个角色共享同一 provider 时无感知

例如 Strategist 和 Executor 都用 `jiutian`，但它们是两个独立的 `LLMClient` 实例，各自持有独立的速率锁。**两者不共享 per-provider 的并发槽位感知**，可能导致：

- Strategist 发出请求（还未完成）
- Executor 同时发出请求
- 两者都通过了各自的速率检查，但实际同时打到了 jiutian

**缓解方式**：`llm_traffic_controller.slot()` 是共享的全局并发槽（`LLM_GLOBAL_MAX_CONCURRENT=6`），这是最后一道防线。但这是全局限制，不是 per-provider 限制。

**建议**：将 `MAX_PARALLEL_SUBTASKS` 和 `jiutian:2` 的并发槽配合使用，确保任意时刻 jiutian 最多 2 个并发请求。

#### ✅ 结论：目前防护基本足够，但需要补充 jiutian 的独立速率配置

todo.md Phase 0 补充项：
```bash
JIUTIAN_MIN_INTERVAL=3.0          # 为 jiutian 补独立速率限制
MAX_PARALLEL_SUBTASKS=2           # 并行子任务从 3 降到 2（与 jiutian:2 并发槽匹配）
```

---

## 附录：文件索引

| 文件 | 内容 |
|------|------|
| `CLAUDE.md`（根目录） | 系统总览、架构速查、配置说明、开发约定 |
| `docs/plan.md`（本文件） | 完整改造计划（目标 + 根因 + 执行步骤） |
| `docs/goal.md` | 北极星指标、设计原则 |
| `docs/todo.md` | 执行 Checklist（可逐项打勾） |
| `docs/stability_analysis.md` | 根因详细分析，含代码行定位 |
