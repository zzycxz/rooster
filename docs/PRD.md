# Rooster 产品需求文档 (PRD)

> **版本**: v1.0 | **日期**: 2026-05-27 | **作者**: zzycxz

---

## 一、产品概述

### 1.1 产品定位

Rooster 是一个**自主多智能体桌面操作系统**（Autonomous Multi-Agent Desktop OS），用户通过自然语言下达指令，系统自主完成桌面操作、网页浏览、文件处理、数据查询等复杂任务。

### 1.2 目标用户

| 用户类型 | 典型场景 | 核心诉求 |
|----------|---------|---------|
| 开发者 | 代码生成、Git 操作、文件批量处理 | 快速、可控、可集成 |
| 效率用户 | 影视/软件资源下载、数据查询汇总 | 说一句话就能完成，零学习成本 |
| 企业用户 | 定时报告、飞书群消息、数据监控 | 可靠、安全、可审计 |

### 1.3 核心价值主张

- **一句话做事**：用户用自然语言描述需求，系统自主规划、执行、交付结果
- **多 Agent 并行**：复杂任务自动拆解为可并行的子任务，多 Agent 同时执行
- **越用越聪明**：自进化引擎从交互中学习用户偏好，持续优化行为
- **安全可靠**：纵深防御安全体系，从网关认证到运行时沙箱全覆盖

---

## 二、功能需求

### 2.1 智能路由系统 (Router)

**FR-ROUTER-001：意图五分流**
- 系统对每条用户输入进行意图分类，分流至五条路径之一：
  - `[TALK]`：纯对话/问答 → 直接回复，不启动执行流程
  - `[DIRECT]`：清晰的执行任务 → 直接执行（不经过 Strategist 规划）
  - `[REFRAME]`：模糊/复杂任务 → 意图重构后执行
  - `[SCHEDULE]`：定时任务 → 注册为定时计划
  - `[BLOCK]`：安全拦截 → 拒绝并提示

**FR-ROUTER-002：短路路由**
- 高频下载任务（影视/软件）命中静态规则时，零 LLM 延迟直通工具执行
- 支持实体清洗：自动剥除"帮我下载""请问"等修饰语

**FR-ROUTER-003：意图重构**
- 模糊意图经 Reframer 转化为结构化工具指令
- 支持歧义检测：识别多义实体（如同名片名），触发澄清确认

### 2.2 任务执行引擎 (Executor)

**FR-EXEC-001：ReAct 循环**
- 基于 Think-Act-Observe 循环执行任务
- 支持原生 Function Calling 和 XML 降级解析双模式
- 单次任务最大步数可配置（默认 20 步）

**FR-EXEC-002：工具调用系统**
- 注册 46+ 工具，按 Kit 分组，LLM 可见 29 个核心工具
- 支持并行工具调用（多工具同时执行）
- 工具失败自动重试与自愈（ReflectionEngine）

**FR-EXEC-003：上下文管理**
- 自动检测 context 占用量，超阈值时触发语义压缩
- 支持视觉注入（截图作为 LLM 输入）

**FR-EXEC-004：卡死检测**
- 检测重复工具调用（同一签名连续 N 次），自动中断
- 空响应自动重试（最多 2 次）
- 步数耗尽时强制输出紧急摘要

### 2.3 多步任务编排 (MissionRunner)

**FR-MISSION-001：流式规划**
- Strategist 流式输出子任务列表，实时产出不等待
- 基于 DAG 的依赖调度，支持真正并行执行

**FR-MISSION-002：子任务执行模式**

| 模式 | 说明 | 典型场景 |
|------|------|---------|
| NORMAL | 标准执行，共享上下文 | 顺序依赖任务 |
| ISOLATED | 克隆工具注册表，独立上下文 | 安全敏感操作 |
| PARALLEL | 多 Agent 并行 | 同时搜索多个网站 |
| SANDBOXED | 严格权限沙箱 | 不可信代码执行 |
| RACE | 竞争模式，取最快结果 | 多源数据竞争 |

**FR-MISSION-003：断点续跑**
- 长任务每步保存 checkpoint，崩溃后从断点恢复
- 必须默认开启（`CHECKPOINT_ENABLED=true`）

**FR-MISSION-004：动态重规划**
- 任务遇到死胡同时，Contrastive Replanner 重新规划
- 保留原始目标不变，仅调整执行路径

### 2.4 质量审计 (Auditor)

**FR-AUDIT-001：五种裁决**

| 裁决 | 含义 | 后续动作 |
|------|------|---------|
| AFFIRM | 审核通过 | 继续下一子任务 |
| REMAND | 质量不达标 | 注入完整上下文后重试 |
| REPLAN | 路径死胡同 | Strategist 重新规划 |
| CLOSURE | 受限无法达成 | 优雅收尾 |
| ESCALATE | 高危阻断 | 请求人工介入 |

**FR-AUDIT-002：阶段感知**
- EXECUTE 阶段：宽松审计，鼓励执行
- COMMIT 阶段：严格审计，确保交付质量

**FR-AUDIT-003：审计范围限制**
- 仅对高风险操作（文件删除、批量写入、发送消息）触发审计
- 低风险叶节点直接标记 SUCCESS，跳过审计

### 2.5 多通道接入 (Channels)

**FR-CHAN-001：支持通道**

| 通道 | 协议 | 状态 |
|------|------|------|
| CLI | Rich 终端 UI | 已实现 |
| 飞书/Lark | 飞书 API + Webhook | 已实现 |
| Dashboard | WebSocket + HTTP | 已实现 |
| Webhook | HTTP POST | 已实现 |

**FR-CHAN-002：飞书交互卡片**
- 歧义确认使用飞书交互卡片（带按钮），替代纯文本输入
- 支持卡片按钮回调接收

### 2.6 记忆系统 (Memory)

**FR-MEM-001：长期记忆**
- 基于嵌入向量的语义检索 + BM25 关键词检索混合
- SQLite FTS5 全文索引
- 支持 DECISION_LOG / TOOL_RESULT / ARTIFACT_CREATED / RESEARCH_FINDING / USER_PREFERENCE / CORRECTION 六种事实类型

**FR-MEM-002：自进化引擎**
- 每轮对话后自动触发，分析纠正信号、偏好信号、里程碑信号
- 纠正信号写入 SOUL.md，偏好信号写入 USER.md
- 核心身份字段受代码级保护，不可篡改

**FR-MEM-003：会话记忆**
- 支持多会话并行，会话历史持久化
- 会话结束自动生成摘要（Epilogue）

### 2.7 LLM 多供应商管理

**FR-LLM-001：多供应商支持**

| 供应商 | 类型 | 说明 |
|--------|------|------|
| 智谱 (Zhipu) | 云端 | CodingPlan 编程增强版 |
| 小米 MiMo | 云端 | 轻量快速 |
| 九天 MoMA | 云端 | 双模型自动路由 |
| OpenAI | 云端 | GPT-4o 等 |
| Anthropic | 云端 | Claude 原生 API |
| Kimi | 云端 | Moonshot AI |
| 通义千问 | 云端 | DashScope 兼容 |
| 本地 Ollama | 本地 | 私有化部署 |

**FR-LLM-002：自动 Failover**
- 按优先级轮转，每 Provider 独立冷却（Circuit Breaker）
- 指数退避 + 随机抖动
- 冷却状态持久化，Guardian 重启后恢复

**FR-LLM-003：角色模型分配**
- Router / Strategist / Executor / Auditor / Solo 可分别指定供应商
- 支持运行时动态切换

### 2.8 安全体系

**FR-SEC-001：网关安全**
- API Key 认证（`X-API-Key` / Bearer Token）
- Webhook HMAC-SHA256 签名验证
- IP 滑动窗口限流（100 req/min）
- 安全响应头（CSP / X-Frame-Options / X-Content-Type-Options）
- 请求体大小限制（1MB）

**FR-SEC-002：运行时安全**
- 路径遍历防护（PathGuard，realpath + prefix 校验）
- 提示注入检测（AdvancedGuard，三级正则匹配）
- Skill 供应链投毒检测
- 日志自动脱敏（Secrets Mask）
- 工具速率限制（per-tool rate limiter）
- Python 代码 AST 安全检查

**FR-SEC-003：隐私保护**
- 本地目录数据强制路由本地模型（L0 物理隔离）
- PII 实体扫描（手机号、身份证、银行卡）→ 本地处理（L1）
- 截图本地 OCR + 脱敏后再上云

### 2.9 Dashboard 监控面板

**FR-DASH-001：功能面板**

| 面板 | 功能 |
|------|------|
| 指令交互 | Agent 对话 + Pipeline 实时可视化 + 会话管理 |
| 步骤追踪 | 每步执行时间线（工具参数/返回值） |
| 运行日志 | 实时日志流（级别过滤 + 搜索 + 导出） |
| 错误列表 | 错误收集 + 堆栈追踪 + 修复建议 |
| 工具审计 | 工具调用历史 + 成功/失败/延迟统计 |
| 技能市场 | 技能管理 + ClawHub 在线安装 |
| 长期记忆 | 记忆浏览 + SOUL.md/USER.md 编辑 |
| 性监 | 活跃会话/延迟百分位/Guardian 状态 |
| 系统配置 | .env 配置展示（密钥脱敏） |
| 初始配置 | 供应商管理 + Ollama + HF 模型浏览器 |
| 健康诊断 | 服务连通性 + CPU/内存/磁盘/网络 |
| MCP Market | MCP Server 市场（安装/管理/状态监控） |

**FR-DASH-002：实时通信**
- 基于 WebSocket 的实时事件推送
- Agent 思考、工具调用、执行进度实时可视化
- 支持中英双语切换

### 2.10 守护进程 (Guardian)

**FR-GUARD-001：进程看护**
- 独立于主进程的外部看门狗
- 心跳监控（30s 轮询）+ 资源熔断（CPU > 95% / 内存 > 2GB 持续 120s）
- 崩溃自动重启，指数退避

**FR-GUARD-002：自愈能力**
- 缺包自装（白名单机制，23 个安全库）
- 端口冲突自动释放
- aria2c 等周边服务自动拉起

**FR-GUARD-003：定时调度**
- 支持 cron 表达式和 interval 触发
- 每 60s 轮询 `schedules.json`，到期任务精准触发

### 2.11 技能系统 (Skills)

**FR-SKILL-001：技能管理**
- YAML frontmatter + Markdown body 格式
- 自动发现、健康检查（Python 包 / 系统命令 / 环境变量）
- 技能摘要注入 System Prompt

**FR-SKILL-002：内置技能**
- 12 个内置技能：coding-agent, data-analysis, dev-tools, git-ops, github, pdf-tools, resource-downloader, self-improving, summarize, visual-control, weather-query, web-search

**FR-SKILL-003：MCP Server 集成**
- MCP Server 市场（12 个预配置 Server）
- UV/Node.js 双运行时隔离
- 进程管理（安装/启动/停止/重启/卸载）
- 健康检查（30s 间隔）+ 崩溃自动重启

### 2.12 视觉能力

**FR-VIS-001：桌面操控**
- UIA（UI Automation）引擎：系统接口获取全屏控件信息
- YOLO 视觉引擎：39MB 轻量模型，覆盖自定义控件和游戏图标
- 双引擎协同：UIA 保障效率，YOLO 兜底覆盖率

**FR-VIS-002：视觉策略**
- 四级视觉识别策略，安全降级
- 浏览器 DOM 修剪（Playwright + pruner）

---

## 三、非功能需求

### 3.1 性能

| 指标 | 当前值 | 目标值 |
|------|--------|--------|
| 简单任务首次成功率 | ~60% | ≥ 85% |
| 平均任务延迟（含规划） | 60-120s | < 30s |
| 长任务崩溃重试率 | ~40% | < 10% |
| 简单任务 LLM 调用次数 | 5 次 | ≤ 3 次 |

### 3.2 可靠性

- 单任务 ReAct 循环必须包含 stuck 检测、空响应重试、步数限制
- MissionRunner 必须开启 checkpoint（`CHECKPOINT_ENABLED=true`）
- Guardian 必须能在主进程崩溃后 30s 内自动重启
- LLM 供应商故障时自动 failover，单 Provider 冷却不影响整体服务

### 3.3 可扩展性

- 新增工具：在 `toolset/definitions/` 创建文件 → 注册表自动发现
- 新增技能：在 `skills/` 创建目录 → SkillLoader 自动加载
- 新增通道：实现 `BaseChannel` → 注册到 ChannelRegistry
- 新增 LLM 供应商：实现 `BaseModelClient` → 注册到 ModelFactory

### 3.4 可观测性

- 每次 LLM 调用附带 trace_id，关联同一任务的所有调用
- Dashboard 实时展示活跃任务状态
- Executor ReAct 循环 step 级别 checkpoint（每 5 步保存 session_history）
- Prometheus 指标暴露（`/metrics`）

### 3.5 安全合规

- 所有密钥在日志中自动脱敏
- 文件操作受 PathGuard 沙箱限制
- 代码执行支持 E2B 沙箱隔离
- 符合安全策略文档 (SECURITY.md)

---

## 四、交互设计原则

### 4.1 三规则交互哲学

**规则 1：聊天永不打断**
`[TALK]` 类任务直接回答，不询问、不给选项、不要求确认。

**规则 2：任务前解决歧义，任务中不打断**
`[DIRECT]` / `[REFRAME]` 任务：意图清晰 → 直接执行；意图模糊 → 执行前发一次确认，回复后执行到底。

**规则 3：高风险操作永远确认且展示后果**
删除/发送/重启类操作：操作前展示"将要做什么"，明确要求确认。

### 4.2 歧义确认方式

- Dashboard：交互对话框，带选项按钮
- 飞书：交互卡片，带按钮（替代纯文本输入）
- CLI：文字选项（A/B/C）

---

## 五、产品路线图

### Phase 0：配置层修复（已完成）
- ✅ Checkpoint 默认开启
- ✅ REMAND 重试次数限制
- ✅ Strategist 超时确认

### Phase 1：路由优化（当前）
- [DIRECT] 任务走 SoloRunner，跳过 Strategist
- MissionRunner 单子任务快速路径
- 目标：简单任务延迟降低 30%+

### Phase 2：Executor 增强
- 语义上下文压缩（对标 Claude Code）
- 内联规划（3 步以上任务 Executor 自己输出 `<plan>`）
- REMAND 重试携带完整 observation
- SoloRunner 取消强制 JSON 输出

### Phase 3：Strategist 加固
- `plan_stream()` 超时保护
- Auditor 仅审计高风险操作
- 流式 JSON 解析鲁棒性提升

### Phase 4：可观测性
- LLM 调用 trace_id 关联
- Dashboard 活跃任务监控
- Step 级轻量 checkpoint

### Phase 5：交互体验
- 歧义检测前移至 Router 层
- 飞书交互卡片按钮回调
- Dashboard 交互确认对话框 + 文件推送
