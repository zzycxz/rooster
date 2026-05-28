# Rooster 执行稳定性目标文档

> **版本**: v1.0 | **日期**: 2026-05-27 | **状态**: 待审批

---

## 一、北极星指标

| 指标 | 当前估计 | 目标值 |
|------|---------|-------|
| 单任务首次成功率 | ~60% | ≥ 85% |
| 平均任务延迟（含规划） | 60~120s | < 30s |
| 长任务崩溃重试率（>5min） | ~40% | < 10% |
| 每次简单任务 LLM 调用次数 | 5次 | ≤ 3次 |

---

## 二、核心设计原则

### 原则 1：单一 Context 优先
> 能不切换 agent 就不切换。每次 agent 边界都是信息丢失点。

Claude Code / Codex 的稳定性根源：从头到尾只有一个 LLM 实例、一个连续的 context window。  
Rooster 的目标：单任务路径（SoloRunner）也做到相同效果。

### 原则 2：原地重试优先
> 工具失败先在循环内重试，不触发 ESCALATE / REMAND。

当工具失败，错误信息应保留在当前 session_history 中，下一步 LLM 直接看到并推理如何修复，而不是把任务抛给外部审计器再重新分配。

### 原则 3：最少 LLM 调用
> 简单任务不超过 3 次 LLM 调用（含 triage）。

串行 LLM 调用是延迟的直接来源。每增加一个 agent 角色，就增加一次等待时间和一次 context 断裂风险。

### 原则 4：规划内嵌，不外包
> 对于单步和顺序任务，让 Executor 自己在 chain-of-thought 里做规划，不依赖外部 Strategist。

Strategist 存在的价值在于**真正的并行分解**（如同时搜索 5 个网站）。对于线性任务，Executor 内联规划更快、更稳定。

---

## 三、架构目标

### 理想调用链（简单任务）

```
用户输入
→ Router._triage_via_llm()  [LLM #1]
→ 判定为 [DIRECT] 或 [TALK]
→ SoloRunner → AgentExecutor.run()
    while True:
        response = llm(history + tools)  [LLM #2..N]
        if done: break
        tool_results = execute(response.tool_calls)
        history += [response, tool_results]
→ 直接返回结果
```

**最少 2 次 LLM 调用，单一连续 context。**

### 多 agent 模式（真正并行任务）

```
用户输入："同时搜索 5 个网站，汇总结果"
→ Router 判定为 [PARALLEL_MISSION]
→ MissionRunner → Strategist 拆子任务
→ 5 个 AgentExecutor 并发执行（真正并行）
→ Blackboard 汇总结果
→ 输出
```

**多 agent 模式只用于真正需要并行的任务，不用于线性顺序任务。**

---

## 四、与 Claude Code 的关键差异（我们的优势）

> Rooster 不是要完全复制 Claude Code，而是在单任务稳定性上对齐它，同时保留多 agent 并行这个 Claude Code 不具备的能力。

| 能力 | Claude Code | Codex | Rooster 目标 |
|------|------------|-------|-------------|
| 单任务稳定执行 | ✅ | ✅ | ✅（改造后） |
| 真正并行多 agent | ❌ | ❌ | ✅（保留） |
| 任务续跑（Checkpoint） | 有限 | 有限 | ✅（已实现，开启即用） |
| 中文生态支持 | 有限 | 有限 | ✅ |
| 本地模型支持 | ❌ | ❌ | ✅ |

---

## 五、不做什么

- **不重写 executor.py**：ReAct 循环已经写得很好，是 Claude Code 风格，不需要推翻。
- **不删除 MissionRunner**：它的并行调度能力是核心价值，保留但提高进入门槛。
- **不删除 Auditor**：保留用于高风险操作（文件删除、付款、批量写入），但从默认路径移除。
