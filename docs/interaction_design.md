# Rooster 产品设计补充：歧义处理 & 交互体验

> **日期**: 2026-05-27  
> **关联文档**: `plan.md`（技术实现计划）  
> **本文覆盖**: 三大对标产品的真实交互模式 + Rooster 的歧义处理设计 + 前端/飞书能力盘点

---

## 一、对标产品的真实交互模式（基于实际使用知识）

### 1.1 Claude Code

Claude Code 是 Anthropic 出品的**命令行代码助手**，运行在终端。

**歧义处理方式**：
- **不开始就问**：Claude Code 在收到模糊任务时，会在**开始执行前**用自然语言提问，等用户回答后才开始行动
- **执行中不打断**：一旦开始执行，除非遇到高风险操作（如 `rm -rf`），否则不会中途要求确认
- **权限模式**：默认运行 bash 命令前询问，用户可选 `--dangerously-skip-permissions` 进全自动模式
- **纯聊天**：如果你只是问问题（"什么是 React hooks？"），它直接回答，不启动任何执行流程

**关键设计原则**：  
> 问题在执行**之前**解决，执行过程不打断，完成后直接呈现结果。

---

### 1.2 Codex CLI（OpenAI）

OpenAI 的 Codex CLI 是更轻量的代码执行助手。

**三种审批模式**（用户自选）：
```
suggest    → 只提建议，用户手动操作（最保守）
auto-edit  → 自动修改文件，但 shell 命令需用户批准
full-auto  → 全自动，任何操作不询问（最激进）
```

**歧义处理方式**：
- 倾向于**直接行动 + 展示过程**，而不是先问
- 对于真正模糊的任务，它会先做一小步，然后停下来问"我理解你想要 X，对吗？"
- **呈现方式**：内联展示 diff，用户看到会改什么，再批准

---

### 1.3 Manus（曼努斯）

Manus 是国内的**全自动计算机使用 Agent**，可以操作浏览器、写代码、处理文件。

**交互特点**：
- **完全自主**：用户说完需求就退出，Manus 自己跑完整个任务（包括几十步）
- **实时可见**：Web UI 实时显示 Agent 在做什么（浏览器截图、代码块、进度条）
- **结果推送**：任务完成后，结果文件可以直接从 UI 下载
- **无中途确认**：Manus 不会在执行中途打断用户；如果任务有歧义，它会**自己做出假设并执行**，然后在报告里说明假设了什么
- **限制**：正因如此，Manus 容易走偏——它的稳定性不如 Claude Code，因为中途无法纠错

---

### 1.4 三者的核心差异总结

| 维度 | Claude Code | Codex CLI | Manus |
|------|------------|-----------|-------|
| 歧义处理时机 | **执行前问** | 先做一步再问 | **自己假设，不问** |
| 执行中打断 | 高风险操作才打断 | 按审批模式 | 不打断 |
| 输出方式 | 终端文字 | 终端 diff | Web UI + 文件下载 |
| 适合场景 | 开发者代码任务 | 开发者快速编辑 | 非开发者复杂任务 |
| 稳定性 | 高 | 高 | 中（自主假设风险） |

---

## 二、Rooster 的歧义处理设计决策

Rooster 面对两种完全不同的场景，**必须区分处理**：

### 场景 A：纯聊天（[TALK]）

**标准**：用户想要的是信息、观点、解释，不需要系统执行任何操作。

```
"大模型是什么？"
"帮我解释一下 transformer 架构"
"今天天气怎么样（聊天性质）"
```

**正确处理**：**直接回答，绝不要求确认，绝不给选项。**

就像 ChatGPT 一样——你问什么它就答什么，不会说"您是想要(A)详细解释 (B)简短解释？"。这种中断感极差，是设计反模式。

---

### 场景 B：有歧义的任务（[DIRECT] / [REFRAME]）

**标准**：用户想让系统做某件事，但意图不够清晰。

```
"帮我下载一部电影"         ← 哪部？画质？
"帮我整理一下文件"         ← 哪些文件？怎么整理？
"帮我写一封邮件给老板"     ← 写什么内容？什么语气？
```

**推荐设计（对标 Claude Code 模式）**：

```
❌ 当前错误做法：
开始执行 → 中途遇到歧义 → 暂停 → 发出 CONFIRM_REQUIRED → 等待用户 → 继续
（问题：用户不知道任务已经开始了多少步，状态模糊）

✅ 正确做法：
收到任务 → [歧义检测] → 执行前用简洁语言问一次 → 用户回答 → 执行到底，不再打断
```

**歧义确认的最佳形式**：

不要让用户输入自由文本，而是**给出 2-3 个最可能的选项**：

```
🤔 我理解你想下载一部电影，请确认：
A. 《误杀》（2019年，陈思诚导演）
B. 《误杀2》（2021年）
C. 其他，请补充片名

回复 A/B/C 或直接告诉我
```

这就是飞书交互卡（Interactive Card）的用武之地。

---

### 场景 C：高风险任务（无论是否歧义）

```
"删除所有临时文件"         ← 不可逆操作
"发邮件给所有客户"         ← 批量操作
"重启服务器"               ← 生产环境操作
```

**必须确认，且必须展示将要做什么**：

```
⚠️ 即将执行高风险操作，请确认：

删除以下 47 个文件：
  /tmp/cache_20250526/*.log
  /tmp/downloads/test_*

此操作不可撤销。输入 "确认删除" 继续，或输入任意其他内容取消。
```

---

## 三、当前 Rooster 歧义处理的现状与问题

### 现有机制

Rooster 已经实现了一套确认机制，但时机和体验有问题：

1. **Reframer 的澄清门**（`router.py`）：Reframer 输出 `__CLARIFICATION_NEEDED__: 问题` 时，Router 拦截并直接问用户
2. **CONFIRM_REQUIRED**（`executor.md`）：Executor 在执行中途输出 CONFIRM_REQUIRED JSON，暂停并等待用户回复
3. **`_request_user_confirmation`**（`mission_runner.py`）：高风险子任务暂停等待用户输入

### 问题

**问题 1：CONFIRM_REQUIRED 在执行中途触发**
Executor 在 ReAct 循环已经执行了几步之后才发出 CONFIRM_REQUIRED，这时用户不知道已经做了什么，体验很差。  
**应该改为：在进入 ReAct 循环之前就完成歧义解决。**

**问题 2：`_wait_for_clarification` 是文本轮询等待**
```python
# mission_runner.py - 当前实现
async def _wait_for_clarification(self, session_id: str, timeout: int = 120) -> Optional[str]:
    start_time = time.time()
    while time.time() - start_time < timeout:
        reply = self.clarification_replies.get(session_id)
        if reply:
            del self.clarification_replies[session_id]
            return reply
        await asyncio.sleep(2.0)
    return None
```
轮询 2s 间隔，等待 120s，用户回复的是纯文本。没有选项按钮，没有结构化输入。

**问题 3：飞书的确认是纯文本**
当前确认消息是一段文字，用户需要手动输入"确认"或"1"/"A"。这在飞书上体验不好。

---

## 四、前端与飞书的能力盘点

### 4.1 飞书当前已实现的能力

| 能力 | 实现状态 | 备注 |
|------|---------|------|
| 发送文本消息 | ✅ `send_message()` | 最基础能力 |
| 发送文件 | ✅ `send_file()` | 支持 ≤10MB 文件 |
| 发送图片 | ✅ `send_image()` | 内联显示 |
| 发送富文本 | ✅ `send_post()` | 标题 + 结构化段落 |
| **发送交互卡片** | ✅ `send_card()` | **已实现但未使用！** |
| 接收卡片按钮回调 | ❌ 未实现 | 需要注册 card action webhook |

**关键发现**：`send_card()` 已经实现了！飞书的交互卡片（带按钮）已经可以发出去，**但没有实现接收用户点击按钮后的回调**。这是最需要补充的一块。

### 4.2 飞书交互卡片的能力（API 层面）

飞书消息卡片（Interactive Card）支持：

```json
// 示例：歧义确认卡片结构
{
  "config": {"wide_screen_mode": true},
  "elements": [
    {
      "tag": "div",
      "text": {"content": "🤔 请确认您想下载哪部电影：", "tag": "lark_md"}
    },
    {
      "tag": "action",
      "actions": [
        {"tag": "button", "text": {"content": "《误杀》(2019)", "tag": "plain_text"},
         "type": "primary", "value": {"action": "choose", "choice": "A"}},
        {"tag": "button", "text": {"content": "《误杀2》(2021)", "tag": "plain_text"},
         "value": {"action": "choose", "choice": "B"}},
        {"tag": "button", "text": {"content": "取消", "tag": "plain_text"},
         "type": "danger", "value": {"action": "cancel"}}
      ]
    }
  ]
}
```

用户点击后，飞书会向你的 Webhook 地址发送 POST 请求，包含用户选择的 `value`。

### 4.3 Dashboard 前端当前能力

| 能力 | 实现状态 |
|------|---------|
| 实时日志显示 | ✅ WebSocket 推送 |
| 任务状态监控 | ✅ |
| 文件推送到用户浏览器 | ⚠️ **需要实现** |
| 交互确认对话框 | ❌ **未实现** |
| 选项按钮 | ❌ **未实现** |

---

## 五、改造建议（按优先级）

### P1：歧义处理时机前移（最重要）

**改动位置**：`router.py`，在路由决策后、进入执行前

```python
# 改造思路：在 Router 层增加快速歧义检测
async def _pre_flight_ambiguity_check(self, msg, channel) -> Optional[str]:
    """
    在任务开始前，对高歧义任务进行一次简短澄清。
    返回 None 表示任务清晰，可以执行。
    返回 str 表示发出了澄清问题，任务暂停。
    """
    # 只检查任务类任务（DIRECT/REFRAME），不检查聊天任务
    # 使用 Router 模型快速判断（不新增 LLM 调用，在 Triage 中合并）
    ...
```

### P2：飞书卡片确认（体验提升最大）

**改动位置**：`feishu.py` + 新增 card action webhook 路由

```python
# 新增：注册卡片事件回调（飞书卡片按钮点击）
EventDispatcherHandler.builder(...)
    .register_p2_im_message_receive_v1(self._do_recv_message_v2)
    .register_p2_card_action_trigger(self._do_card_action)  # ← 新增
    .build()
    
async def _do_card_action(self, data) -> None:
    """处理飞书卡片按钮点击事件"""
    user_id = data.event.operator.open_id
    action_value = data.event.action.value  # {"action": "choose", "choice": "A"}
    # 注入到 _wait_for_clarification 的等待队列
    self.clarification_replies[f"feishu_{user_id}"] = action_value["choice"]
```

### P3：Dashboard 文件推送

**改动位置**：Dashboard WebSocket + 前端 HTML

```javascript
// 前端：接收文件推送事件
ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    if (data.type === 'file_ready') {
        // 显示下载按钮
        showFileDownload(data.filename, data.download_url);
    }
};
```

```python
# 后端：任务完成后推送文件
await broadcast_event("file_ready", {
    "filename": "下载结果.txt",
    "download_url": f"/api/downloads/{task_id}/result.txt"
})
```

---

## 六、综合建议：Rooster 的交互哲学

**参考 Claude Code 的设计哲学，为 Rooster 制定三条规则：**

### 规则 1：聊天永不打断
`[TALK]` 类任务直接回答，不询问，不给选项，不要求确认。就像 ChatGPT。

### 规则 2：任务前解决歧义，任务中不打断
`[DIRECT]` / `[REFRAME]` 类任务：
- 意图清晰 → 直接执行，完成后报告
- 意图模糊 → **执行前**发一次确认（飞书卡片或文字问题），等回复，然后执行到底不再打断

### 规则 3：高风险操作永远确认，且要展示后果
删除/发送/重启类操作：无论意图是否清晰，在操作前展示"将要做什么"，明确要求用户确认。

---

## 七、实施工作量估计

| 改动 | 工作量 | 优先级 |
|------|--------|--------|
| 歧义检测前移（Router 层） | 3-4小时 | 🔴 P1 |
| 飞书卡片按钮回调注册 | 2-3小时 | 🟠 P2 |
| 飞书歧义确认用卡片替代文字 | 2小时 | 🟠 P2 |
| Dashboard 文件推送 | 4-6小时 | 🟡 P3 |
| Dashboard 交互确认对话框 | 6-8小时 | 🟡 P3 |

> 飞书的 `send_card()` 已经实现，最快的路径是：**先把 card action callback 注册好**，然后把现有的文字确认换成卡片按钮，可以在 1 天内完成 P2 全部。
