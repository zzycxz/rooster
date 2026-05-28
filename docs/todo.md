# Rooster 稳定性改造 — 执行 Checklist

> **策略**：按优先级从上到下执行，每个 Phase 完成后验收再继续。

---

## Phase 0：立即见效（配置层，不改代码）⏱ 30分钟

> 这些改动只需修改 `.env`，随时可回滚，零风险。

- [ ] `.env` 添加 `CHECKPOINT_ENABLED=true`
  - 效果：长任务崩溃后从断点续跑，而不是从头重来
  - 基础设施已在 `mission_runner.py` 完整实现，只是默认关闭
- [ ] `.env` 确认 `STRATEGIST_LLM_TIMEOUT` 值合理（建议 90s）
  - 当前 `plan()` 有超时保护，但需确认时间足够
- [ ] `.env` 设置 `AUDIT_MAX_REMAND_RETRY=1`
  - 当前默认值不明，REMAND 重试由于"失忆"问题，多次重试意义不大

**Phase 0 验收**：重启服务，发起一个下载任务，中途 Ctrl+C 杀进程，重新启动后发相同任务，观察是否从断点续跑。

---

## Phase 1：路由手术（最高 ROI）⏱ 2-3小时

> 改动集中在 `router.py` 和 `mission_runner.py` 入口。

### 1.1 [DIRECT] 任务走 SoloRunner

- [ ] `router.py`：将 `[DIRECT]` 路由从 `mission_runner.run()` 改为 `solo_runner.run()`
  ```python
  # 改前
  if triage_state in ["[DIRECT]", "[REFRAME]"]:
      await self.mission_runner.run(...)
  
  # 改后
  if triage_state == "[DIRECT]":
      await self.solo_runner.run(...)   # 直接走循环，不经 Strategist
      return
  if triage_state == "[REFRAME]":
      await self.mission_runner.run(...)  # 保留多步规划
  ```

### 1.2 MissionRunner 单子任务快速路径

- [ ] `mission_runner.py`：Strategist 规划结束后，检查子任务数量
  ```python
  if len(subtask_list) <= 1:
      # 只有 1 个子任务，不需要 Strategist 的开销
      # 转为 SoloRunner 执行，跳过 Auditor
      await self._run_as_solo(subtask_list[0], msg, channel, dynamic_event_handler)
      return
  ```

**Phase 1 验收**：发起 10 个不同的简单任务（搜索、下载、查询），记录首次成功率和平均响应时间，目标比改造前提升 30% 以上。

---

## Phase 1.5：Context 语义压缩（对标 Claude Code）⏱ 3-4小时

> 这是原计划遗漏的关键一环。当前 `_prune_history` 只做截断（丢弃旧消息），Claude Code 是语义摘要（摘要替换旧历史）。这一步让长任务不再"失忆"。

### 1.5.1 实现 `_inline_compact_history()` 方法

- [ ] `executor.py`：新增 `_inline_compact_history()` 异步方法
  - 触发条件：`count_message_tokens(session_history) > context_limit * 0.5`
  - 用 `settings.FAST_MODEL_NAME`（qwen3.6-35b）生成语义摘要
  - 摘要内容：已完成步骤 / 关键数据 / 失败路径 / 当前状态
  - 摘要替换旧历史，保留最近 6 条原始消息（`keep_recent=6`）
  - 压缩失败时**优雅降级**为原有 `_prune_history` 截断

- [ ] `executor.py` L255：将 `_prune_history` 调用替换为 `_inline_compact_history`
  ```python
  # 改前
  session_history = self._prune_history(session_history, max_total_tokens=context_limit)
  # 改后
  session_history = await self._inline_compact_history(session_history, context_limit)
  ```

### 1.5.2 新建 `src/prompts/compaction.md`

- [ ] 创建 `src/prompts/compaction.md`，内容包含：
  - 必须保留：已完成步骤（每步一行）/ 关键数据（URL、路径、评分、选中项）/ 失败路径 / 当前状态
  - 不保留：思考过程 / 完整 JSON 输出 / 重复细节
  - 输出格式模板（`[已完成]` / `[关键数据]` / `[失败路径]` / `[当前状态]` 四段式）
- [ ] `executor.py` 的 `_inline_compact_history()` 从 `compaction.md` 读取 Prompt（通过 `SoulLoader` 或直接读文件），不硬编码

### 1.5.3 executor.md 改造为三段式

- [ ] `src/prompts/executor.md`：在现有内容前后各插入一段
  - **首段（Phase 1 内联规划）**：3 步以上任务必须先输出 `<plan>...</plan>`
  - **中段（Phase 2 执行循环）**：现有 ReAct Loop 内容保留不变
  - **尾段（Phase 3 完成前自检）**：输出最终答案前逐条核对需求，发现遗漏直接补救
- [ ] 确认 SoloRunner 模式加载的是改造后的 executor.md（而非旧版）

### 1.5.4 Sandwich 模式（可选优化）

- [ ] `prompt_builder.py` `compose_messages()`：当传入 `key_state_hint` 时，将关键状态追加到最后一条 user message 末尾
  - 目的：关键信息同时出现在 context 首部（SUMMARY）和尾部，对抗"Lost in the Middle"

**Phase 1.5 验收**：
- 发起 20+ 步长任务（批量下载或多文件处理）
- 观察 Step 20 时 LLM 的工具调用，确认它仍能准确引用 Step 3-5 发现的关键数据
- 确认不会出现"遗忘性重复搜索"（同一关键词搜索 2 次以上）

---

## Phase 2：Executor 增强 ⏱ 3-4小时

> 改动在 `executor.md`（prompt）和 `mission_runner.py`（REMAND 逻辑）。

### 2.1 executor.md 增加内联规划指令

- [ ] 在 `executor.md` 的"Execution Loop"章节前插入内联规划章节：
  ```markdown
  ## 内联规划（复杂任务）
  对于需要多步操作的任务，在第一次工具调用前先输出执行计划：
  <plan>
  步骤1: ...
  步骤2: ...
  </plan>
  计划是你自己的检查表，执行中可以根据实际情况调整，不需要等待确认。
  ```

### 2.2 SoloRunner 取消强制 JSON 输出

- [ ] 为 SoloRunner 模式和 MissionRunner 模式分别维护 executor 的 system prompt
  - SoloRunner：自然语言输出，不要求 FINAL_REPORT JSON
  - MissionRunner subtask：保留 FINAL_REPORT JSON（Auditor 依赖它）

### 2.3 REMAND 重试携带完整 observation

- [ ] `mission_runner.py` `_run_subtask_inner()`：REMAND 后重试时注入上次完整结果
  ```python
  # 改前
  context_parts.append(f"审计官修正指令：\n{previous_audit_cmd}")
  
  # 改后
  if previous_report is not None:
      context_parts.append(
          f"上次执行结果（完整）：\n{previous_report.observation[:3000]}\n\n"
          f"审计官修正指令：\n{previous_audit_cmd}"
      )
  ```

**Phase 2 验收**：手动触发一次 REMAND（让任务故意失败），观察重试时 Executor 的 prompt 是否包含上次工具输出。

---

## Phase 3：Strategist 加固 ⏱ 2小时

> 消除规划层的脆弱性。

### 3.1 plan_stream() 加超时保护

- [ ] `strategist.py`：在 `plan_stream()` 的 `chat_stream()` 外层加超时
  ```python
  async def plan_stream(self, user_request: str):
      try:
          async with asyncio.timeout(settings.STRATEGIST_LLM_TIMEOUT):
              async for delta in self.llm_client.chat_stream(...):
                  ...
      except asyncio.TimeoutError:
          logger.error(f"plan_stream() 超时，降级为单步 FAILSAFE")
          yield SubTask(id="FAILSAFE", instruction=user_request, ...)
  ```

### 3.2 Auditor 仅审计高风险操作

- [ ] `strategist.md`：在 Strategist prompt 中加入指令，高风险操作（文件删除、批量写入、发送消息）的子任务才标记 `requires_confirm=true`
- [ ] `mission_runner.py`：非高风险叶节点跳过 Auditor，直接标记为 SUCCESS

**Phase 3 验收**：发起一个多步任务，Strategist 规划时手动在网络层引入 30s 延迟，观察是否超时降级而非永久阻塞。

---

## Phase 4：可观测性 ⏱ 4-6小时

- [ ] 每次 LLM 调用附加 `trace_id`，关联同一个任务的所有 LLM 调用
- [ ] Dashboard 增加"当前活跃任务"监控面板（执行步骤数、耗时、使用模型）
- [ ] Executor ReAct 循环加 step 级别轻量 checkpoint（每 5 步保存一次 session_history）

---

## 验收总标准

- [ ] 10 次简单任务测试，首次成功率 ≥ 8/10
- [ ] 长任务（预计 >5min）崩溃后能从断点续跑
- [ ] REMAND 触发后，重试 Executor 能在 prompt 中看到上次工具输出
- [ ] 平均响应时间对比改造前记录一次基准值
- [ ] **语义压缩测试**：20步以上长任务，Step 20 时 LLM 仍准确引用 Step 2-3 关键数据，无遗忘性重复搜索
- [ ] **三段式 Prompt 测试**：SoloRunner 任务开始前输出 `<plan>`，结束前有自检，无需 Auditor 仍能发现遗漏

