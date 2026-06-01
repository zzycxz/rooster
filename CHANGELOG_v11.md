# ROOSTER v11.0 变更记录

> **日期**: 2026-05-31
> **版本**: v0.4.0 → v11.0
> **核心主题**: CCP 能力约束驱动规划 + USER 步骤支持 + Checkpoint 增强

---

## 一、变更概述

v11.0 引入了 **CCP（Capability-Constrained Planning）规划协议**，让 Strategist 在生成计划时显式声明能力边界、前置阻塞项、交付物承诺。同时支持将需要用户物理操作的步骤（如购买服务器、提供账号）标记为 `owner: USER`，在执行时挂起等待用户确认。

**核心原则：5 不做 + 4 聚焦**

不做 CapabilityMap / MissionState FSM / loop_back / SQLite 持久化 / 新模块。
聚焦 Protocol 扩展 / Strategist Prompt / Checkpoint 增强 / USER 步骤路由。

---

## 二、逐文件变更明细

### 2.1 `src/agents/protocol.py`

**SubTask 新增 3 字段**（全部有默认值，向后兼容）：

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `owner` | `str` | `"AGENT"` | `AGENT`（AI 独立完成）或 `USER`（需人类操作） |
| `confidence` | `str` | `"HIGH"` | `HIGH` / `MEDIUM` / `LOW` |
| `risk_note` | `str` | `""` | 风险提示文本 |

**MissionPlan 新增 3 字段**：

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `blockers` | `List[Dict[str, str]]` | `[]` | 前置阻塞项（缺凭据/资源/授权） |
| `deliverables` | `List[str]` | `[]` | 预期交付物列表 |
| `feasibility_note` | `str` | `""` | 可行性边界说明 |

**新增 Pydantic 校验**：

- `SubTask.validate_subtask_logic()` — `owner == "USER"` 时 `instruction` 必须 ≥ 5 字符
- `MissionPlan.validate_mission_plan()` — 存在 `confidence == "LOW"` 时 `feasibility_note` 不能为空；子任务总数 ≤ 50；DAG 拓扑合法性（存在性 + 防环）

---

### 2.2 `src/prompts/strategist.md`

**Output Schema 升级为 `schema_version: "11.0"`**：

- 新增顶层 `blockers`、`deliverables`、`feasibility_note` 字段
- 新增子任务级 `owner`、`confidence`、`risk_note` 字段

**追加 CCP Planning Protocol（六步法）**：

```
Step 1: Blocker Detection    — 检测前置阻塞项
Step 2: Owner Labeling       — 标注 AGENT / USER
Step 3: Confidence Rating    — 评估 HIGH / MEDIUM / LOW
Step 4: Deliverable Declaration — 声明交付物
Step 5: Feasibility Note     — 可行性边界
Step 6: DAG Construction     — 依赖关系构建
```

---

### 2.3 `src/agents/strategist.py`

**1. tool_registry 注入**（`__init__` 新增参数）：

```python
def __init__(self, llm_client, memory_manager=None, tool_registry=None):
    self._tool_registry = tool_registry
```

- 用途 1：`_get_skills_digest()` 将原生工具 schema 注入系统 prompt（让 Strategist 知道有哪些工具可用）
- 用途 2：`plan()` 中校验 LLM 规划的 tool 名是否在注册表中，不存在则降级为 `generic_tool`

**2. SoulLoader 传 llm_client**（`plan()` 和 `plan_stream()` 两处）：

```python
soul_loader = SoulLoader(llm_client=self.llm_client, model=settings.STRATEGIST_MODEL_NAME)
```

修复问题：原来无参创建 SoulLoader 绕过了 SOUL.md/USER.md 超长自动精简机制。

**3. `_last_plan_meta` 元数据提取**（`plan_stream()` 末尾）：

```python
# 流结束后从 full_content 解析顶层元数据
self._last_plan_meta = {
    "blockers": plan_data.get("blockers", []),
    "deliverables": plan_data.get("deliverables", []),
    "feasibility_note": plan_data.get("feasibility_note", ""),
}
```

替代原方案"再调一次 plan()"的方案，避免额外 LLM 调用。

**4. v11 字段补全**：

- `plan()` 的 setdefault 补全：`blockers`, `deliverables`, `feasibility_note`, `owner`, `confidence`, `risk_note`
- `plan()` 的 fallback 路径也包含新字段
- `plan_stream()` 的流式解析和全量抢救都补全新字段
- `replan()` 的 SubTask 构造补全 `owner`, `confidence`, `risk_note`

---

### 2.4 `src/agents/runners/mission_runner.py`

**1. tool_registry 传入 Strategist**（L59）：

```python
self.strategist = Strategist(self.strat_llm, memory_manager=self.memory_manager, tool_registry=self.tool_registry)
```

**2. 元数据消费**（L309-316）：

规划完成后从 `self.strategist._last_plan_meta` 读取 blockers/deliverables/feasibility_note，写入 `current_mission_plan`。

**3. USER 步骤路由**（L369-418）：

- USER 步骤不占用 semaphore 槽位（L369-371）
- 复用 `_request_user_confirmation` 机制
- 用户确认 → SUCCESS + 保存 checkpoint
- 超时/拒绝 → CANCELLED + 加入 completed_task_ids 防死循环

**4. 上游取消传播**（L448-457）：

如果依赖链中的 USER 步骤被取消，下游 AGENT 步骤也自动取消，避免空等。

**5. 阻塞项/交付物前置展示**（L323-337）：

规划完成后、执行前汇总展示 blockers、feasibility_note、deliverables。仅首次规划时展示，checkpoint 恢复时跳过。

**6. 子任务指标收集**（L321, 401-404, 809-812）：

```python
_subtask_metrics: Dict[str, dict] = {}
# 记录 {"duration_s": 1.2, "retries": 0}
```

写入 checkpoint，崩溃恢复后可查看已完成步骤的耗时和重试次数。

**7. Checkpoint 增强**（L91-117, 243-256）：

`_save_checkpoint` 新增 `blockers`、`deliverables`、`feasibility_note`、`subtask_metrics` 四个字段。
`_load_checkpoint` 恢复时读取这些字段。

---

### 2.5 不改动的文件

| 文件 | 说明 |
|------|------|
| `src/memory/soul_loader.py` | 无需改动 |
| `src/agents/mission_tactician.py` | 无需改动 |
| `src/agents/mission_blackboard.py` | 无需改动 |
| `src/agents/executor.py` | 无需改动 |
| `src/agents/auditor.py` | 无需改动 |
| `src/agents/router.py` | 无需改动 |

---

## 三、代码审查结论

### ✅ 全部正确的实现

1. **Protocol 新字段** — 全部有默认值，向后兼容，校验逻辑完整
2. **tool_registry 注入** — 构造函数接收 + plan() 中校验 tool 名
3. **SoulLoader 传 llm_client** — 两处均已修复
4. **_last_plan_meta** — 预置默认值 + 流结束后解析 + getattr 防御
5. **USER 步骤路由** — 不占 semaphore、复用确认机制、超时→CANCELLED、取消传播
6. **Checkpoint 增强** — 存取完整，恢复后不重复展示阻塞项
7. **_subtask_metrics** — 计时从实际执行开始（不含依赖等待时间）
8. **所有 fallback 路径** — plan() fallback、plan_stream 全量抢救、replan() 均包含新字段

### ⚠️ 已知遗留（非 v11 引入）

| 项 | 位置 | 影响 |
|----|------|------|
| replan() 不传 blockers/deliverables/feasibility_note | strategist.py:574-584 | 重规划后元数据丢失，当前行为可接受（阻塞项可能已变化） |
| plan_stream 无内部超时 | strategist.py:302-308 | 依赖外部 mission_runner 的 wait_for 保护 |

---

## 四、明确不做（已否决的原方案内容）

| 不做 | 否决原因 |
|------|---------|
| CapabilityMap 模块 | strategist.md 已有手工映射 + tool_registry 注入已覆盖 |
| MissionState FSM | mission_tactician.py 已有同名 MissionState + 三层追踪过度工程 |
| loop_back 调度机制 | REMAND 已提供重试能力，DAG 回边导致计数失真 |
| SQLite 持久化 | JSON checkpoint 够用，等性能瓶颈再升级 |
| NodeMetrics dataclass | 普通 dict 足够，不需要类型化 |

---

## 五、测试场景

| 场景 | 输入 | 预期行为 |
|------|------|---------|
| 向后兼容 | "搜索今天天气" | owner=AGENT, confidence=HIGH, 无 blockers，行为与 v10 一致 |
| USER 步骤 | "在阿里云部署网站" | 生成 USER 步骤 + blockers，AGENT 先完成代码部分 |
| 不可保证结果 | "帮我获得1000用户" | confidence=LOW + feasibility_note，承诺与保证分离 |
| 崩溃恢复 | 执行中 kill 进程 | checkpoint 含 metrics，恢复后继续执行 |
| is_direct | 简单任务走 DIRECT | 新字段默认值不报错，无额外逻辑触发 |
| 上游取消 | USER 步骤超时 | 下游依赖步骤自动 CANCELLED |
