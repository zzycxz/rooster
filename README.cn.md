# Rooster — 自主多智能体桌面操作系统

[English](README.md) | 中文

[![CI](https://github.com/zzycxz/rooster/actions/workflows/ci.yml/badge.svg)](https://github.com/zzycxz/rooster/actions/workflows/ci.yml)

> 版本: 0.3.0 | Python >= 3.12 | 许可: MIT

---

## 一、项目概览

Rooster 是一个**多角色 Agent 框架**，能自主完成桌面操作、网页浏览、文件处理、数据查询等复杂任务。

### 核心特点

- **多角色协作**: Router（分拣）→ Strategist（规划）→ Executor（执行）→ Auditor（审计）
- **混合执行模式**: Solo（单轮快速）/ Mission（多步长任务）/ Schedule（定时任务）
- **视觉定位**: YOLO 模型驱动的桌面 UI 元素识别与操控
- **混合动力浏览器**: httpx 静态抓取 + Playwright 动态渲染自动降级
- **长期记忆**: 基于嵌入模型的语义记忆检索 + TTL 保留策略 + JSONL 导入导出
- **流式响应**: WebSocket 实时推送 Agent 思考、工具调用、执行进度
- **多 LLM Failover**: 智谱/小米/九天/OpenAI/Anthropic/Kimi/Qwen/云端/本地 10+ 供应商自动切换
- **Gateway 安全**: API Key 认证 + HMAC 签名 + IP 速率限制 + 安全头 + 请求大小限制
- **Dashboard UI**: 实时监控面板 — 11 个功能面板，中英双语，移动端适配

---

## 二、核心技术亮点

### 1. Router 智能五分流与零延迟重加工：每条指令走最优路径

**意图分发**：Router 对所有输入消息进行精准意图分类，自动分流至最快处理路径：

- `[TALK]`（约 70%）→ 直通 SoloRunner，单轮极速响应
- `[DIRECT]` → 短路路由直达 MissionRunner，跳过规划阶段
- `[REFRAME]` → 进入语义重加工链，解析模糊或复杂意图
- `[SCHEDULE]` → 解析为定时计划，Guardian 后台准时触发
- `[BLOCK]` → 安全拦截（含下载关键词时智能降级为 REFRAME）

**零延迟静态规则引擎（0ms）**：内置 17 个影视触发词 + 12 个软件触发词 + 通用下载触发词。`clean_target()` 自动剥除"帮我下载""请问""1080p"等修饰语，精准提取核心实体名。全程零 LLM 调用，零延迟。

**动态短路路由**：命中目标关键字（如 `resource-downloader`）后，完全绕过 Strategist 规划层，正则解析参数直达工具执行。内置域名信任清洗——15 个可信域（github.com、microsoft.com 等）优先呈现，13 个已知流氓站（onlinedown.net、pc6.com 等）自动屏蔽。

**LLM 语义兜底**：仅在静态规则未命中时，才启用大模型重写模糊意图。若 LLM 判断该消息不应走 REFRAME，返回 `REDIRECT` 让 Router 重新分流。用 0ms 解决约 80% 高频问题，不靠 LLM 硬扛一切。

### 2. 全维度安全沙箱与隐私隔离：从接入到执行的纵深防御

秉持"深度防御"理念，Rooster 从网络边界、运行时环境到大模型调用层，构建了全链路安全体系。遵循"宁可漏检也绝不阻塞用户"原则，所有拦截均支持平滑降级。

**数据隐私物理隔离漏斗**

| 层级 | 机制 | 延迟 |
|------|------|------|
| L0 物理截断 | 基于 `LOCAL_DIRS` 路径校验，命中的文件和请求强制路由本地模型（如 Ollama）处理，从源头切断数据上云 | 0ms |
| L1 实体清洗 | 集成深度定制的 Microsoft Presidio 引擎，毫秒级双语 PII 扫描，精准识别手机号（0.8）、身份证（0.85）、银行卡（0.6）等敏感资产 | 5–20ms |
| 视觉级隐私熔断 | 屏幕截图"不离机"处理——本地 PaddleOCR 提取画面文本 → Presidio 脱敏扫描 → 向云端放行的仅有安全文本描述，原始截图永远锁定在本地 | 逐帧 |

**AdvancedGuard 大模型深度防御引擎**
- **越狱免疫**：内置三级正则匹配矩阵，实时拦截 DAN 模式、"忽略之前指令"、无限制角色扮演等提示词越狱尝试。
- **输出注入拦截**：Agent 使用浏览器或读取外部文件时，实时扫描工具返回内容，防止网页恶意隐藏指令对 Agent 实施二次劫持。
- **Skill 供应链投毒检测**：挂载第三方技能包（`SKILL.md`）时自动静态扫描，拦截 eval/base64 混淆执行、隐藏恶意网络请求、描述中夹带的系统指令。

**运行时沙箱与状态强管控**
- **PathGuard 目录沙箱**：基于 `os.path.realpath` 严格前缀校验，杜绝通过符号链接和 `../` 实施目录遍历攻击。
- **StateGuard 原子锁（RSA-Synchronizer）**：为多智能体并发设计的跨进程原子事务锁，消除 Race Condition 与脏数据注入。
- **工具防滥用限流**：自动掐断大模型因幻觉导致的无限死循环调用，防止算力和 API 额度被意外耗尽。

**边界网关与合规审计**
- **全栈流量管控**：API Key 强鉴权 + Webhook HMAC-SHA256 防篡改签名 + IP 滑动窗口限流（100 req/min）。
- **日志隐身衣（Secrets Mask）**：日志落盘前自动对 API Key、Token 等凭证做不可逆掩码，杜绝外流风险。
- **动态配置免疫（Input Guard）**：热更新接口的键白名单验证 + 超长值熔断，防御缓冲区溢出型攻击。

### 3. UIA 矩阵扫描 + YOLO 视觉接地：所见即所得的桌面操控

采用"系统底层 API + 机器视觉"双擎互补架构，无需目标软件开放任何接口，屏幕上看得到就能操作。

- **UIA（UI Automation）引擎**：通过系统接口获取全屏所有控件的标准化信息（类型、名称、位置、状态），覆盖面广，实现"快、准、稳"的确定性交互。
- **YOLO 视觉引擎**：内置仅 39MB 的超轻量目标检测模型，随仓库分发，零额外下载。有效弥补 UIA 无法解析的自定义控件、游戏图标、非标准 UI 等视觉盲区。
- **协同作战**：`desktop_grounding_scan` 负责全域元素感知，`desktop_act` 负责精准模拟点击/输入。UIA 保障效率，YOLO 兜底覆盖率。

### 4. Guardian 守望者：自愈、自调度、自修复的守护进程

独立于主控进程的外部看门狗，即使主进程彻底崩溃，守护与重启能力不受影响，实现真正的无人值守运行。

**三线程并行监控**

| 线程 | 机制 | 触发条件 |
|------|------|----------|
| 心跳守护 | 每 30s 轮询 `/api/health` | 连续 3 次无响应 → 强杀重启 |
| 资源熔断 | 每 15s 采样 CPU/内存 | CPU > 95% 或内存 > 2GB 持续 120s → 强杀防卡死 |
| 时间齿轮 | 每 60s 轮询 `schedules.json` | 到期任务精准 POST 触发执行 |

**全自动故障修复**
- **缺包自装**：捕获 `ModuleNotFoundError` 后，从 23 个白名单安全库中匹配并自动 `pip install`，白名单机制防止恶意代码注入。
- **端口自释**：精准匹配端口占用异常，跨平台正则提取端口号并立即终止占用进程。
- **服务唤醒**：`aria2c` 等周边服务 RPC 不响应时，自动拉起守护进程。

**企业级韧性控制**：熔断器（连续 2 次同类错误停止重试）、重启风暴免疫（300s 内 5 次重启触发告警并停止）、指数退避 + 随机抖动、单实例 PID 互斥、兼容飞书/钉钉/Slack 的 Webhook 告警。

### 5. 双重记忆自进化 + Auditor 质量门禁：越用越懂你，结果永远可控

**零延迟自进化引擎**

每轮对话结束后瞬间触发后台扫描，不阻塞当前用户进程。通过本地模型（永不走云端）分析最近 5 轮上下文（每轮截断 200 字符），捕捉三大核心信号并写入记忆库：

| 信号 | 触发词示例 | 写入目标 |
|------|-----------|----------|
| `CORRECTION`（纠正）| "不对""你理解错" | `SOUL.md` → 核心行为原则 |
| `PREFERENCE`（偏好）| "以后""我希望" | `USER.md` → 用户偏好画像 |
| `MILESTONE`（里程碑）| "已上线""成功了" | `USER.md` → 当前重点项目 |

核心身份设定字段（Identity / Hard Limits / Memory Protocol）受代码级硬保护，自进化引擎无法篡改。

**Auditor 独立质量门禁**

Executor 完成后，交由独立 Auditor 进行最终裁决，五种处置：

| 裁决 | 含义 | 用户体感 |
|------|------|----------|
| `AFFIRM` | 审核通过 | 拿到合格结果 |
| `REMAND` | 质量不达标 | 打回重做，全程无感 |
| `REPLAN` | 路径死胡同 | Strategist 重新推演任务结构 |
| `CLOSURE` | 受限无法达成 | 优雅收尾，拒绝烂尾 |
| `ESCALATE` | 高危/权限阻断 | 主动升级请求人工介入 |

**极强鲁棒性**：`_robust_json_parse()` 自动修复大模型返回的各类残缺格式——Markdown 代码块误包裹、尾部逗号、中文引号（`\u201c`/`\u201d`）等。Auditor 自身超时或异常时安全降级为 `PASS_WITH_WARNING`，不因审计系统故障而卡死用户流程。


### 6. 记忆防垃圾体系 v2 — 三道防线，长期记忆永不塞满

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Rooster 记忆防垃圾体系 v2                      │
│                                                                 │
│  用户发消息 → Router → MissionRunner → Executor(ReAct)           │
│                          │                                      │
│                          ▼                                      │
│  ┌──────────────────────────────────────────────────────────┐           │
│  │  第一道防线：源头拦截（mission_runner.py）         │           │
│  │                                                  │           │
│  │  子任务完成时：                                   │           │
│  │    ✖ 子任务执行成功 ──────── 已删除               │           │
│  │    ✖ 产出文件 ───────────── 移到结案时统一写     │           │
│  │                                                  │           │
│  │  任务结案时（batch 一次性提交）：                  │           │
│  │    ✖ 任务完成 ───────────── 已删除               │           │
│  │    ✖ 产出文件（重复）─────── 已删除，只走 batch    │           │
│  │    ✖ 工具调用轨迹 ─────────── 已删除               │           │
│  │    ✔ 产出文件路径 ────────── batch 中写 1 次      │           │
│  │    ✔ 执行结果摘要 ────────── 保留+过滤            │           │
│  │       条件：len > 50 且不含模板词                 │           │
│  │                                                  │           │
│  │  改造前：5-8 条/任务，每个触发 1 次 rebuild       │           │
│  │  改造后：0-2 条/任务，全部只触发 1 次 rebuild     │           │
│  └────────────────┬──────────────────────────────────────────┘           │
│                 │                                               │
│                 │ batch_update_facts(_batch)                    │
│                 │ 收集所有事实 → 一次 add_fact → 一次 rebuild    │
│                 ▼                                               │
│  ┌───────────────────────────────────────────────┐              │
│  │              LTM 存储层                        │              │
│  │         project_memory.json                   │              │
│  │         当前 9 条有效事实                      │              │
│  │    （从 50 条清理而来，垃圾归零）               │              │
│  └───────────────────┬────────────────────────────┘              │
│                 │                                               │
│       ┌─────────┴─────────┐                                    │
│       │                   │                                     │
│       ▼                   ▼                                     │
│  ┌─────────────┐  ┌──────────────────────────┐                        │
│  │  读取路径    │  │  蒸馏路径          │                        │
│  │             │  │                   │                        │
│  │ 每步 ReAct  │  │ 三种触发方式：     │                        │
│  │ 调用一次    │  │                   │                        │
│  │             │  │ · 定时：每 10 分钟 │                        │
│  │ 语义召回    │  │   蒸馏调度器自动   │                        │
│  │ query=当前  │  │   扫描安静 session │                        │
│  │ 任务        │  │                   │                        │
│  │             │  │ · 被动：token 超   │                        │
│  │ 先输出      │  │   60% 时           │                        │
│  │ ┌────────┐  │  │   memory_compactor │                        │
│  │ │关键实体│  │  │                   │                        │
│  │ │≤10个   │  │  │ · 手动：/distill   │                        │
│  │ │500字   │  │  │   或 API 调用      │                        │
│  │ └────────┘  │  │                   │                        │
│  │ 再输出      │  │ ───────────────── │                        │
│  │ ┌────────┐  │  │                   │                        │
│  │ │关键事实│  │  │ 第二道防线：       │                        │
│  │ │语义    │  │  │ 蒸馏负面规则       │                        │
│  │ │召回    │  │  │ (manager.py)       │                        │
│  │ │top 15  │  │  │                   │                        │
│  │ │2000字  │  │  │ LLM 被明确告知     │                        │
│  │ └────────┘  │  │ 不要提取：         │                        │
│  │             │  │ · 模板成功句       │                        │
│  │ 全部按      │  │ · 工具调用轨迹     │                        │
│  │ 任务相关性  │  │ · 截断输出         │                        │
│  │ 召回，不    │  │ · 无上下文内容     │                        │
│  │ 再是固定    │  └─────────────┬────────────┘                       │
│  │ top 15     │            │                                  │
│  └───────────────┘             ▼                                  │
│  ┌─────────────────────────────────────────────────┐             │
│  │  第三道防线：衰减淘汰                          │             │
│  │  periodic_housekeeping 每 6 小时自动运行       │             │
│  │                                              │             │
│  │  事实数 > 30 → 去重                           │             │
│  │  事实数 > 50 → 质量审计 + 低质量删除           │             │
│  │  事实数 > 60 → 硬上限驱逐最低权重               │             │
│  │  每 7 天半衰期 → 未被召回的事实权重自然降低      │             │
│  │                                              │             │
│  │  残留的低价值事实会随时间自动被淘汰               │             │
│  └─────────────────────────────────────────────────┘             │
│                                                              │
│  ┌─────────────────────────────────────────────────┐             │
│  │  附加优化：对话摘要层                          │             │
│  │  _prune_history 改为 async                    │             │
│  │                                              │             │
│  │  token 超限时不再直接丢弃中间对话，             │             │
│  │  而是用本地 LLM 压缩成 300 字摘要注入           │             │
│  │  保留首尾 + 摘要，信息零丢失                    │             │
│  └─────────────────────────────────────────────────┘             │
│                                                              │
└────────────────────────────────────────────────────────────────┘
```

**讲解 — 整体架构：三道防线串联 + 一个附加优化层。每道防线独立运行，叠加后垃圾事实趋近于零。**

**第一道：源头拦截。**这是最关键的一道，解决了"垃圾从哪来"的问题。改造前 MissionRunner 在两个时机（子任务完成 + 任务结案）总共无条件写入 5 种状态通知，而且同一个产出文件在两个时机各写一次导致重复。改造后：子任务完成时什么都不写，等任务结案时统一用 `batch_update_facts` 一次性提交。只保留产出文件路径（写 1 次）和经过模板词过滤的执行摘要。效果从每个任务 5-8 条降到 0-2 条，且索引只重建 1 次。

**第二道：蒸馏过滤。**即使第一道漏了些模板化内容进了对话历史，蒸馏时也不会被提取。三个蒸馏入口（定时 10 分钟、token 超限被动触发、手动 `/distill`）全部受到同一份负面规则的约束。这是改动最小（改了一个字符串）但覆盖面最广的防线。

**第三道：衰减淘汰。**长期兜底。未被召回的事实权重按 7 天半衰期自动降低，超过 30/50/60 条时触发去重、审计、硬驱逐。残留垃圾随时间自然消失。

**附加层：对话摘要。**当 ReAct 循环中 token 超过 context limit 时，`_prune_history` 不再直接丢弃中间对话，而是用本地 LLM 压缩成 300 字摘要注入。配合 LTM 语义召回（每步按当前任务相关性检索而非固定 top 15），确保 LLM 在任何时刻都能看到完整上下文。


---

## 三、目录结构

```
rooster/
├── .env                        # 非敏感配置（模型路由、行为策略）
├── .env.local.example          # 密钥模板
├── pyproject.toml              # 项目配置与依赖
├── guardian.py                 # 守护进程（进程管理、端口清理、自动重启）
├── start.bat                    # Windows 启动脚本 (macOS: 双击 Rooster.app)
├── CONTRIBUTING.md             # 贡献指南
├── SECURITY.md                 # 安全策略
│
├── resources/
│   └── models/                 # 视觉模型权重（已提交 git，无需额外下载）
│       └── grounding/
│           └── icon_detect/
│               └── model.pt    #   YOLO UI 元素检测模型 (39 MB)
│
├── skills/                     # 外挂技能（12 个内置技能）
│   ├── coding-agent/           #   编码智能体
│   ├── data-analysis/          #   数据分析
│   ├── dev-tools/              #   开发工具
│   ├── git-ops/                #   Git 操作
│   ├── github/                 #   GitHub 操作
│   ├── pdf-tools/              #   PDF 工具
│   ├── resource-downloader/    #   资源下载
│   ├── self-improving/         #   自我改进
│   ├── summarize/              #   摘要生成
│   ├── visual-control/         #   视觉控制
│   ├── weather-query/          #   天气查询
│   └── web-search/             #   网页搜索
│
├── src/
│   ├── main.py                 # 入口：预检 + 启动
│   ├── launcher.py             # 启动器：网关 + CLI + 通道 + 记忆预热
│   │
│   ├── agents/                 # 核心 Agent 角色
│   │   ├── protocol.py         #   数据协议（MissionPlan / SubTask / Report / AuditVerdict）
│   │   ├── router.py           #   入口路由：Triage → SoloRunner / MissionRunner / Schedule
│   │   ├── reframer.py         #   意图重构器（模糊需求 → 标准化指令）
│   │   ├── short_circuit.py    #   短路路由（高频简单任务直通执行）
│   │   ├── strategist.py       #   战略官（DAG 子任务拆分 + 重规划）
│   │   ├── executor.py         #   执行官（ReAct 循环 + 工具调用）
│   │   ├── auditor.py          #   审计官（AFFIRM / REMAND / REPLAN / ESCALATE）
│   │   ├── orchestrator.py     #   工具编排器（视觉策略 + 自愈）
│   │   ├── mission_tactician.py#   战术官（DAG 拓扑排序 + 并行分组）
│   │   ├── mission_blackboard.py#  并发任务共享黑板
│   │   ├── reflection_engine.py#   反思引擎（错误模式分析 + 修复建议）
│   │   ├── llm_client.py       #  LLM 客户端（多 Provider 轮转 + 冷却 + 退避）
│   │   ├── prompt_builder.py   #   五层 System Prompt 组装器
│   │   ├── tool_dispatch.py    #   工具调用提取与执行
│   │   └── runners/
│   │       ├── solo_runner.py  #     单轮快速模式
│   │       └── mission_runner.py#    多步任务模式
│   │
│   ├── toolset/                # 工具注册与定义（55 个工具，32 个暴露给 LLM）
│   │   ├── base.py             #   BaseTool 基类（含 platform / kit / fc_hidden）
│   │   ├── registry.py         #   全局工具注册表（自动发现 + schema 验证）
│   │   └── definitions/        #   工具实现（22 个模块）
│   │       ├── browser.py          #   浏览器（nav / fetch / act / batch_fetch）
│   │       ├── visual_control.py   #   桌面视觉操控（grounding_scan / read_screen / act）
│   │       ├── file_system.py      #   文件系统（file_system_op 全能操作）
│   │       ├── office.py           #   Office（excel_op / docx_write / pdf_op）
│   │       ├── interpreter.py      #   Python 代码执行（E2B 沙箱 / 本地）
│   │       ├── exa_search.py       #   搜索（4 级降级链）
│   │       ├── subagent.py         #   子 Agent 编排
│   │       ├── task_manager.py     #   任务管理
│   │       ├── task_scheduler.py   #   定时任务（Windows schtasks / macOS launchd）
│   │       ├── email.py            #   邮件发送
│   │       ├── ocr.py              #   OCR 文字识别
│   │       ├── memory.py           #   长期记忆写入
│   │       └── ...                 #   共 22 个定义文件
│   │
│   ├── gateway/                # HTTP / WebSocket 网关
│   │   ├── server.py           #   FastAPI 应用工厂
│   │   ├── auth.py             #   API Key 认证 + HMAC + 速率限制
│   │   ├── security.py         #   安全头 + 请求大小限制
│   │   ├── run_manager.py      #   运行任务管理（支持全局取消）
│   │   ├── connection_manager.py#  节点连接池
│   │   ├── dashboard_ws.py     #   Dashboard WebSocket 推送
│   │   ├── event_handler.py    #   Agent 事件 → WebSocket 广播
│   │   ├── local_node.py       #   本地受控桌面节点
│   │   ├── metrics.py          #   Prometheus 指标
│   │   ├── stream.py           #   流式响应协议
│   │   └── routes/
│   │       ├── websockets.py   #     WebSocket 端点
│   │       ├── config.py       #     /api/config 配置读写
│   │       ├── memory.py       #     /api/memory 记忆 CRUD
│   │       ├── models.py       #     /api/models 模型管理（Ollama / HuggingFace）
│   │       ├── skills.py       #     /api/skills 技能市场
│   │       └── system.py       #     /api 系统接口
│   │
│   ├── channels/               # 输入通道
│   │   ├── cli.py              #   控制台交互（中英双语 /lang 切换）
│   │   ├── feishu.py           #   飞书机器人（懒加载）
│   │   ├── webhook.py          #   HTTP Webhook 通道
│   │   └── registry.py         #   通道注册
│   │
│   ├── models/                 # LLM 提供商适配器
│   │   ├── factory.py          #   ModelFactory（provider → client 工厂）
│   │   ├── openai_adapter.py   #   OpenAI 兼容适配器
│   │   ├── anthropic_adapter.py#   Anthropic Claude 适配器
│   │   └── vision_strategy.py  #   视觉策略
│   │
│   ├── memory/                 # 记忆系统
│   │   ├── manager.py          #   LTM 核心（嵌入 + 检索 + 衰减）
│   │   ├── backends.py         #   JSON / Markdown 存储后端
│   │   ├── soul_loader.py      #   SOUL.md / USER.md 加载 + 五层 Prompt 构建
│   │   ├── embeddings.py       #   嵌入向量生成
│   │   ├── semantic_search.py  #   语义搜索
│   │   ├── dedup.py            #   记忆去重
│   │   ├── indexer.py          #   全文索引
│   │   ├── compactor.py        #   记忆压缩
│   │   └── watcher.py          #   文件系统监听（热更新）
│   │
│   ├── evolution/              # 进化引擎（自我学习）
│   │   ├── engine.py           #   信号检测 → LLM 提取 → 自动写入 SOUL/USER
│   │   ├── soul_writer.py      #   SOUL.md append-only 写入
│   │   └── user_writer.py      #   USER.md 写入
│   │
│   ├── sessions/               # 会话管理
│   │   └── store.py            #   全局会话存储（atomic write）
│   │
│   ├── prompts/                # Prompt 模板（Markdown）
│   │   ├── base.md             #   基础行为协议
│   │   ├── strategist.md       #   战略官 Prompt
│   │   ├── executor.md         #   执行官 Prompt
│   │   ├── auditor.md          #   审计官 Prompt
│   │   ├── replan.md           #   重规划 Prompt
│   │   ├── router_triage.md    #   路由分诊 Prompt
│   │   └── intent_reframer.md  #   意图重构 Prompt
│   │
│   └── utils/                  # 工具库
│       ├── config/             #   配置体系
│       │   ├── _base.py        #     环境变量读取工具
│       │   ├── _settings.py    #     组合 Settings
│       │   ├── loader.py       #     配置加载器（已废弃，.env 为唯一配置源）
│       │   ├── providers.py    #     LLM 供应商配置
│       │   ├── runtime.py      #     运行时配置
│       │   ├── hardware.py     #     硬件/视觉配置
│       │   ├── channels.py     #     通道配置
│       │   └── memory.py       #     记忆配置
│       ├── security/           #   安全模块
│       │   ├── path_guard.py   #     路径守卫（防符号链接绕过）
│       │   ├── state_guard.py  #     状态守卫
│       │   ├── advanced_guard.py#    越狱检测
│       │   ├── input_guard.py  #     输入验证
│       │   ├── secrets_mask.py #     日志脱敏
│       │   └── tool_rate_limiter.py # 工具限速
│       ├── vision/             #   视觉引擎（YOLO）
│       ├── browser/            #   浏览器工具（Playwright）
│       └── audit/              #   审计工具
│
├── tests/                      # 测试套件（132 个测试）
└── .rooster/                   # 运行时数据（gitignore）
    ├── SOUL.md                 #   Agent 灵魂文件
    ├── USER.md                 #   用户画像文件
    ├── project_memory.json     #   长期记忆
    ├── schedules.json          #   定时任务
    ├── sessions/               #   会话数据
    └── logs/                   #   日志
```

---

## 四、核心架构

### 请求处理流程

```
用户消息 (CLI / 飞书 / WebSocket / Dashboard)
    │
    ▼
Router (分拣器) ─── 关键词 / 意图分类
    │
    ├─ TALK (70%+) ──► SoloRunner (单轮快速回复) ──► 响应
    ├─ BLOCK ────────► 安全拦截 ──► 响应
    ├─ SCHEDULE ─────► 定时任务注册 → schedules.json
    │
    ├─ DIRECT ───────► ShortCircuit ──► MissionRunner（跳过语义清洗）
    │
    └─ REFRAME ──────► Reframer (语义清洗引擎)  ◄── 仅针对敏感/模糊意图
                           │
                           ├─ 静态规则引擎（本地，0ms，无需 LLM 调用）
                           │   影视/软件/下载意图 → 中性工具指令
                           │   "帮我下载XX电影" → "resource-downloader(title=XX, type=movie)"
                           │   完全绕过大模型内容审核
                           │
                           └─ LLM 重构（兜底，静态规则未命中时）
                                                     │
                                                     ▼
                                             MissionRunner
                                                   │
                                                   ▼
                                             Strategist (规划阶段)
                                             ├─ DAG 分解：任务 → 有序子任务
                                             ├─ 依赖分析：并行分组
                                             └─ 域路由：按子任务分配本地/云端
                                                   │
                                             ┌─────┴─────┐
                                             ▼           ▼
                                        Executor      Executor
                                        (ReAct 循环    (并行
                                         + 29 工具)    子任务)
                                             │           │
                                             └─────┬─────┘
                                                   ▼
                                            ┌──────────────┐
                                            │   Privacy    │
                                            │   Router     │
                                            │ ┌──────────┐ │
                                            │ │L0: 文件夹 │ │  LOCAL_DIRS → 本地模型
                                            │ │L1: PII   │ │  Presidio 扫描 → 本地模型
                                            │ │L3: 策略  │ │  记忆/压缩 → 本地
                                            │ └──────────┘ │  截图 → OCR + 脱敏
                                            └──────────────┘
                                                   │
                                                   ▼
                                               Auditor
                                              (质量审计)
                                                   │
                                             ┌─────┴─────┐
                                             ▼           ▼
                                           AFFIRM    REMAND / REPLAN / ESCALATE
                                          (继续)     (重做 / 重规划 / 上报)
```

### 五层 System Prompt 架构

```
Layer 1: SOUL.md         — Agent 灵魂/个性（最高优先级）
Layer 2: USER.md         — 用户画像/偏好
Layer 3: Skills digest   — 已安装技能摘要
Layer 4: LTM context     — 长期记忆语义召回片段
Layer 5: Base prompt     — 角色 Prompt（strategist.md / executor.md 等）
```

### LLM Provider 体系

多供应商自动 Failover，按优先级逐级降级：

| Provider | 环境变量 | 说明 |
|:---|:---|:---|
| 智谱 CodingPlan | `ZHIPU_KEY` | 编程增强版 GLM，当前主力 |
| 智谱 GLM 标准版 | `ZHIPU_GLM_KEY` | 标准 API 备选通道 |
| 小米 MiMo | `MIMO_KEY` | 轻量快速，重构器默认 |
| 九天 MoMA | `JIUTIAN_KEY` | 双模型（大/小）自动路由 |
| OpenAI | `OPENAI_KEY` | GPT-4o 等 |
| Anthropic Claude | `ANTHROPIC_KEY` | Claude 原生 Messages API |
| Kimi 月之暗面 | `KIMI_KEY` | Moonshot AI |
| 通义千问 | `QWEN_KEY` | DashScope 兼容接口 |
| 云端 | `CLOUD_KEY` | 通用 OpenAI 兼容 |
| 本地 | `LOCAL_KEY` | llama.cpp / Ollama 本地推理 |

### 工具系统

55 个工具注册在案，32 暴露给 LLM 进行 Function Calling（23 个为内部/旧版保留）。按 Kit 分组：

| Kit | 核心工具 | 能力 |
|:---|:---|:---|
| Browser | `browser_nav`, `browser_act`, `web_fetch`, `batch_web_fetch` | 网页浏览与抓取 |
| Search | `exa_search`, `linkup_search` | 多引擎搜索（4 级降级链） |
| Vision | `desktop_grounding_scan`, `desktop_act`, `desktop_read_screen` | 桌面 UI 操控 |
| FileSystem | `file_system_op` | 文件读写 / 列目录 / 搜索 / 下载 |
| Office | `excel_op`, `office_docx_write`, `pdf_op` | Excel / Word / PDF |
| Interpreter | `python_interpreter` | Python 代码执行（E2B 沙箱 / 本地） |
| Memory | `memory_add_fact` | 长期记忆写入 |
| Task | `task_manager`, `task_scheduler` | 任务管理 + 定时调度 |
| SubAgent | `subagent_spawn`, `subagent_result` | 子 Agent 编排 |
| Comms | `email_send`, `feishu_push_file` | 邮件 / 飞书推送 |
| Multimedia | `multimedia_download`, `movie_downloader` | 资源下载 |
| OCR | `ocr_extract` | 图片文字识别（PaddleOCR） |
| Plan | `plan_mode` | 规划模式（暂停 → 用户审批 → 继续） |
| System | `tool_info`, `skill_read`, `wait_until` | 元工具 |

### 安全体系

| 层级 | 机制 | 说明 |
|:---|:---|:---|
| 网关认证 | API Key (`X-API-Key` / `Authorization: Bearer`) | `GATEWAY_API_KEY` 为空时跳过（本地开发） |
| Webhook 签名 | HMAC-SHA256 | `WEBHOOK_HMAC_SECRET` 配置后启用 |
| 速率限制 | IP 滑动窗口 (100 req/min) | localhost 自动豁免 |
| 安全头 | CSP / X-Frame-Options / X-Content-Type-Options | 全局中间件 |
| 请求大小 | 1MB 限制 | 防超大请求体 |
| 输入验证 | 配置键白名单 + 值长度限制 | /api/config/save 端点 |
| 文件沙箱 | PathGuard (realpath + prefix) | 防符号链接绕过 |
| 越狱检测 | AdvancedGuard | 检测提示注入 / Skill 投毒 |
| 日志脱敏 | secrets_mask | 自动遮盖日志中的密钥 |
| 工具限速 | tool_rate_limiter | 按工具名配额限速 |

---

## 五、快速开始

### 方式一：本地安装（推荐）

```bash
# 1. 克隆并安装（Dashboard 前端已预构建，无需 Node.js）
git clone https://github.com/zzycxz/rooster.git
cd rooster
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 2. 配置密钥（至少填一个 LLM Key）
cp .env.local.example .env.local
# 编辑 .env.local，填入至少一个 API Key

# 3. 启动（守护进程模式，自动重启）
python guardian.py
```

启动后浏览器自动打开 Dashboard（默认 `http://localhost:8765/dashboard`）。

> **首次使用说明：**
> - `git clone` 后即可使用，模型权重和 Dashboard 前端已包含在仓库中
> - 如未自动打开浏览器，手动访问 `http://localhost:8765/dashboard`
> - Dashboard 支持中英双语切换（侧边栏底部 Language 按钮）
> - 仅需一个 LLM API Key 即可运行（推荐智谱，有免费额度）
> - 本地 Ollama 用户需先安装 Ollama 并拉取模型

### macOS 注意事项

macOS 上核心功能（LLM、浏览器、文件操作、Dashboard）完全兼容。以下功能有差异：

| 功能 | macOS 状态 | 说明 |
|:---|:---|:---|
| 视觉桌面操控 | 部分降级 | 截图 + pyautogui 可用，UIA 窗口扫描不可用 |
| `window_visible` 等待 | 需授权 | 需在 系统设置 → 隐私 → 辅助功能 中授权终端/Python |
| Playwright | 需安装浏览器 | `playwright install chromium` |
| YOLO 视觉定位 | 需手动安装 | `pip install -e ".[vision]"` |

### 方式二：Dashboard 首次配置

首次启动后，Dashboard 会自动检测 `.env.local` 是否存在。若未配置：

1. 浏览器打开 `http://localhost:8765/dashboard`
2. 点击左侧「初始配置」标签
3. 选择 LLM 供应商 → 填入 API Key → 点击「保存配置」
4. 系统自动重启，配置生效
5. 支持「测试连接」按钮验证各供应商连通性

Dashboard 的「初始配置」面板还支持：
- **Ollama Guardian**：检测本地 Ollama 状态、拉取模型、分配角色
- **HuggingFace 模型浏览器**：搜索 / 下载 GGUF 模型、导入 Ollama 或启动 llama.cpp
- **角色矩阵**：为 Router / Strategist / Executor / Auditor / Solo 分别指定供应商

---

## 六、Dashboard 面板

Dashboard 是一个单页 Web 应用（Alpine.js + Tailwind），提供 11 个功能面板：

| 面板 | 功能 |
|:---|:---|
| **指令交互** | Agent 对话界面 + Pipeline 实时可视化（Router→Strategist→Executor→Auditor 状态）+ 会话管理 + 图片粘贴 |
| **步骤追踪** | Agent 每一步执行的详细时间线（含工具参数 / 返回值），支持筛选和搜索 |
| **运行日志** | 实时日志流（支持级别过滤 + 搜索 + 导出 + 堆栈展开） |
| **错误列表** | 错误收集（含堆栈追踪 + 修复建议） |
| **工具审计** | 工具调用历史（参数 + 结果 + 耗时）+ 按工具统计的成功/失败/延迟 |
| **技能市场** | 已安装技能管理（加载/卸载/测试/修复依赖）+ ClawHub 在线搜索安装 |
| **长期记忆** | 记忆事实浏览（搜索 / 删除 / 手动衰减）+ SOUL.md / USER.md 查看编辑 |
| **性能监控** | 活跃会话 / 子任务 / 请求数 + LLM / Tool / HTTP 延迟百分位 + Guardian 看门狗状态 |
| **系统配置** | .env 配置展示（按分类分组，密钥脱敏） |
| **初始配置** | 10 供应商卡片 + Ollama 管理 + HF 模型浏览器 + 角色矩阵 + Failover 配置 + 危险操作区 |
| **健康诊断** | 服务连通性检测 + CPU / 内存 / 磁盘 / 网络 / Top 进程 |

---

## 七、集成指南

### 6.1 WebSocket API（推荐）

Rooster 网关默认监听 `ws://127.0.0.1:8765/ws/gateway`。

**发送任务：**

```json
{
  "method": "chat.send",
  "params": {
    "sessionKey": "my_session_001",
    "message": "帮我搜索 Python asyncio 的用法"
  },
  "id": "req_001"
}
```

**取消执行：**

```json
{
  "method": "chat.cancel",
  "params": { "sessionKey": "my_session_001" },
  "id": "cancel_001"
}
```

### 6.2 HTTP API

**系统接口：**

| 端点 | 方法 | 说明 |
|:---|:---|:---|
| `/api/health` | GET | 健康检查（LLM + .env.local） |
| `/api/version` | GET | 版本号 |
| `/api/cancel` | POST | 全局取消所有运行中任务 |
| `/api/metrics/summary` | GET | JSON 格式指标摘要 |
| `/metrics` | GET | Prometheus 格式指标 |
| `/api/system/stats` | GET | 系统资源（CPU / 内存 / 磁盘 / 网络） |
| `/api/guardian/status` | GET | Guardian 守护进程状态 |
| `/api/sessions` | GET | 会话列表 |
| `/api/toolset` | GET | 已注册工具列表（按 Kit 分组） |
| `/api/security/status` | GET | 安全配置状态 |

**配置接口**（`/api/config`）：

| 端点 | 方法 | 说明 |
|:---|:---|:---|
| `/api/config/save` | POST | 保存配置到 .env.local（自动重启） |
| `/api/config/reload` | POST | 热重载 .env 文件（不重启） |
| `/api/config/models` | GET | 已配置供应商列表 |
| `/api/config/masked` | GET | 脱敏后的完整配置 |
| `/api/config/test` | GET | 测试各供应商连通性 |

**记忆接口**（`/api/memory`）：

| 端点 | 方法 | 说明 |
|:---|:---|:---|
| `/api/memory/stats` | GET | 记忆统计 |
| `/api/memory/facts` | GET | 记忆事实列表 |
| `/api/memory/facts/{id}` | DELETE | 删除记忆 |
| `/api/memory/decay` | POST | 手动触发记忆衰减 |
| `/api/memory/soul` | GET / PUT | SOUL.md 读写 |
| `/api/memory/user` | GET / PUT | USER.md 读写 |

**技能接口**（`/api/skills`）：

| 端点 | 方法 | 说明 |
|:---|:---|:---|
| `/api/skills` | GET | 已安装技能列表 |
| `/api/skills/market` | GET | ClawHub 技能市场 |
| `/api/skills/install` | POST | 安装技能 |
| `/api/skills/uninstall` | POST | 卸载技能 |
| `/api/skills/reload` | POST | 热重载技能缓存 |
| `/api/skills/toggle` | POST | 启用 / 禁用技能 |
| `/api/skills/test` | POST | 测试技能 |

**模型接口**（`/api/models`）：

| 端点 | 方法 | 说明 |
|:---|:---|:---|
| `/api/models/ollama/scan` | GET | 扫描本地 Ollama 模型 |
| `/api/models/ollama/pull` | POST | 拉取 Ollama 模型 |
| `/api/models/ollama/apply` | POST | 将模型分配给角色 |
| `/api/models/ollama/delete` | POST | 删除 Ollama 模型 |
| `/api/models/hf/search` | GET | 搜索 HuggingFace GGUF 模型 |
| `/api/models/hf/download` | POST | 下载 HF 模型 |
| `/api/models/hf/import/ollama` | POST | 导入到 Ollama |
| `/api/models/hf/import/llamacpp` | POST | 启动 llama.cpp 服务 |

### 6.3 CLI 命令行

```bash
python guardian.py
# 进入交互式 CLI

# 可用命令:
/new      - 开启新会话
/list     - 查看会话列表
/switch   - 切换会话
/model    - 切换模型
/proxy    - 代理控制（status / on / off）
/lang     - 切换语言 (zh/en)
/exit     - 退出
```

### 6.4 节点 WebSocket

```
WS /ws/gateway   — 主网关 WebSocket（Dashboard 推送）
WS /ws/dashboard — Dashboard 实时更新
WS /v1/node/ws   — 受控桌面节点（含 auth_required 握手协议）
```

---

## 八、关键配置项

> 完整配置请参考 `.env` 文件（80+ 配置项），以下仅列出核心项。

### 必填：至少一个 LLM Key

```ini
# 推荐（智谱，免费额度）
ZHIPU_KEY=your_key

# 或其他供应商（任选其一即可）
OPENAI_KEY=your_key
ANTHROPIC_KEY=your_key
MIMO_KEY=your_key
JIUTIAN_KEY=your_key
KIMI_KEY=your_key
QWEN_KEY=your_key
CLOUD_KEY=your_key
```

### 网关安全

```ini
GATEWAY_API_KEY=your-secret-key    # 留空则跳过认证（本地开发）
WEBHOOK_HMAC_SECRET=your-hmac      # Webhook 签名密钥
```

### 角色模型分配

```ini
STRATEGIST_MODEL_MODE=zhipu        # 战略官（默认 zhipu）
EXECUTOR_MODEL_MODE=jiutian        # 执行官（默认 jiutian）
AUDITOR_MODEL_MODE=jiutian         # 审计官（默认 jiutian）
ROUTER_MODEL_MODE=zhipu            # 路由官（默认 zhipu）
SOLO_MODEL_MODE=jiutian            # 直接对话（默认 jiutian）
```

### Failover

```ini
LLM_FAILOVER_ENABLED=true
LLM_FAILOVER_ORDER=jiutian,zhipu,mimo,local
LLM_FAILOVER_RETRY_MAX=2
```

### 网络 / 代理

```ini
GATEWAY_PORT=8765
OLLAMA_URL=http://localhost:11434         # Ollama 管理 API
HF_ENDPOINT=https://huggingface.co        # HuggingFace 镜像（国内改 hf-mirror.com）
# HTTP_PROXY=http://127.0.0.1:7897        # 在 .env.local 中配置
```

---

## 九、开发

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 运行测试
pytest tests/ -v

# 代码检查
ruff check src/ tests/
```

### 添加新技能

在 `skills/` 下创建目录和 `SKILL.md`：

```yaml
---
name: my-skill
description: "技能描述"
metadata:
  rooster:
    emoji: "🔧"
    platform: ["any"]
    category: "automation"
    requires:
      python_packages: ["pandas"]
      bins: ["git"]
---
```

### 添加新工具

在 `src/toolset/definitions/` 下创建 Python 文件，继承 `BaseTool`：

```python
from toolset.base import BaseTool
from pydantic import BaseModel

class MyToolArgs(BaseModel):
    query: str

class MyTool(BaseTool):
    name = "my_tool"
    description = "A custom tool"
    kit = "custom"
    args_schema = MyToolArgs

    async def run(self, **kwargs):
        return {"result": "done"}
```

详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

---

## 十、调试参考

| 问题场景 | 首先检查 |
|:---|:---|
| 启动失败 | 控制台输出 — 预检会提示缺少哪些 API Key |
| 任务不执行 | `src/agents/router.py` → `handle_inbound()` |
| 子任务超时 | `src/agents/strategist.py` → timeout 参数 |
| 工具调用失败 | `src/agents/tool_dispatch.py` → `_execute_tool_with_healing()` |
| LLM 调用失败 | `src/agents/llm_client.py` → Provider 切换逻辑 |
| 视觉定位不准 | `src/utils/vision/grounding.py`（需 `pip install -e ".[vision]"`） |
| Dashboard 断开 | 检查 `GATEWAY_API_KEY` 配置及浏览器控制台 |
| 飞书通道不启动 | 正常 — `lark-oapi` 未安装时自动跳过 |

### 常见问题

| 症状 | 原因 | 解法 |
|:---|:---|:---|
| "No LLM API keys" | .env.local 未配置 | `cp .env.local.example .env.local` 并填入 Key |
| 网页抓取返回空 | 反爬拦截 | 检查 HTTP_PROXY 或 `playwright install chromium` |
| 视觉工具报错 | 缺少 YOLO 依赖 | `pip install -e ".[vision]"` |
| Dashboard 显示断开 | 认证不匹配 | 确认浏览器已注入认证 Header 或清除 `GATEWAY_API_KEY` |
| 工具注册失败 | BaseTool 子类缺少 name/description/run | 参照 `toolset/base.py` 契约 |
| Ollama 连接失败 | Ollama 未启动或端口不对 | 检查 `OLLAMA_URL` 配置，默认 `http://localhost:11434` |

---

## License

MIT
