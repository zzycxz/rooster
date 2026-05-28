# Rooster 工具合并精简报告 (Round 10)

## 变更摘要

| 指标 | 修改前 | 修改后 |
|------|--------|--------|
| 注册总数 | 47 | 55 (+8 宏工具) |
| LLM 可见 | ~40 | **29** |
| fc_hidden | 7 | **26** |
| 每步路由后预估 | 15-18 | **10-12** |

## 合并方案：宏工具 + fc_hidden

每个宏工具用 `action` 参数合并同族操作，原工具标 `fc_hidden=True` 从 LLM 的 Function Calling 列表中隐藏。

### 1. browser_act — 浏览器交互

| 原工具 | 状态 |
|--------|------|
| browser_click | fc_hidden |
| browser_type | fc_hidden |
| browser_scroll | fc_hidden |
| browser_next_page | fc_hidden |
| browser_read | fc_hidden (Round 9) |
| browser_explore_links | fc_hidden (Round 9) |

```python
browser_act(action="click", index=3)
browser_act(action="type", index=5, text="搜索内容")
browser_act(action="scroll", direction="down", amount=800)
```

保留独立: `browser_nav` (URL 导航)

### 2. desktop_act — 桌面交互

| 原工具 | 状态 |
|--------|------|
| desktop_click | fc_hidden |
| desktop_type | fc_hidden |
| desktop_snap | fc_hidden (Round 9) |

```python
desktop_act(action="click", element_id="A", scan_cache="...")
desktop_act(action="type", text="用户名")
```

保留独立: `desktop_grounding_scan` (扫描 + 打标), `desktop_read_screen` (截图 + OCR)

### 3. excel_op — Excel 操作

| 原工具 | 状态 |
|--------|------|
| excel_read | fc_hidden |
| excel_write | fc_hidden |

```python
excel_op(action="read", path="data.xlsx")
excel_op(action="write", path="output.xlsx", data=[{"name": "test"}])
```

### 4. pdf_op — PDF 操作

| 原工具 | 状态 |
|--------|------|
| office_pdf_read | fc_hidden |
| office_pdf_write | fc_hidden |

```python
pdf_op(action="read", path="report.pdf")
pdf_op(action="write", path="output.pdf", content="...", title="报告")
```

### 5. task_manager — 任务管理

| 原工具 | 状态 |
|--------|------|
| task_create | fc_hidden |
| task_get | fc_hidden (Round 9) |
| task_list | fc_hidden |
| task_update | fc_hidden |

```python
task_manager(action="create", title="数据清洗", priority="high")
task_manager(action="list", filter_status="PENDING")
task_manager(action="update", task_id="abc123", status="DONE", result="完成")
```

### 6. task_scheduler — 定时任务

| 原工具 | 状态 |
|--------|------|
| task_scheduler_create | fc_hidden |
| task_scheduler_delete | fc_hidden |

```python
task_scheduler(action="create", task_name="DailyWeather", script_path="daily_weather", run_time="08:00")
task_scheduler(action="delete", task_name="DailyWeather")
```

### 7. tool_info — 工具发现

| 原工具 | 状态 |
|--------|------|
| tool_list | fc_hidden |
| tool_search | fc_hidden |

```python
tool_info(action="list", kit_filter="Browser")
tool_info(action="search", query="截图")
```

### 8. plan_mode — 规划模式

| 原工具 | 状态 |
|--------|------|
| enter_plan_mode | fc_hidden |
| exit_plan_mode | fc_hidden |

```python
plan_mode(action="enter")
plan_mode(action="exit")
```

## 其他隐藏

| 工具 | 原因 |
|------|------|
| wait_until | python_interpreter 替代 |
| web_search | exa_search 链内调用 |
| glm_plan_search | exa_search fallback 内部调用 |

## LLM 可见工具清单 (29 个)

### Browser (4)
- browser_act, browser_nav, batch_web_fetch, web_fetch

### Search (2)
- exa_search, linkup_search

### FileSystem (1)
- file_system_op

### Office (3)
- excel_op, office_docx_write, pdf_op

### Interpreter (1)
- python_interpreter

### Vision (4)
- desktop_act, desktop_grounding_scan, desktop_read_screen, ocr_extract

### Memory (1)
- memory_add_fact

### Network (2)
- feishu_push_file, resource_fetch

### Comms (1)
- email_send

### Multimedia (2)
- movie_downloader, multimedia_download

### System (8)
- escalate_to_strategist, plan_mode, skill_read, subagent_result, subagent_spawn, task_manager, task_scheduler, tool_info

## 关键修改文件

| 文件 | 改动 |
|------|------|
| `src/toolset/definitions/task_manager_macro.py` | 新建 — task_manager 宏工具 |
| `src/toolset/definitions/task_manager.py` | task_create/list/update 加 fc_hidden |
| `src/toolset/registry.py` | get_tools_by_kit 过滤 fc_hidden + 更新提示文本 |
| `src/toolset/router.py` | _META_TOOL_NAMES 更新为 tool_info + skill_read |
| `src/agents/prompt_builder.py` | META_TOOLS 更新为 tool_info + skill_read |

## System Prompt Kit 概览

```
[Browser] → batch_web_fetch, browser_act, browser_nav, web_fetch
[Comms] → email_send
[FileSystem] → file_system_op
[Interpreter] → python_interpreter
[Memory] → memory_add_fact
[Multimedia] → movie_downloader, multimedia_download
[Network] → feishu_push_file, resource_fetch
[Office] → office_docx_write, excel_op, pdf_op
[Search] → exa_search, linkup_search
[System] → escalate_to_strategist, plan_mode, skill_read, subagent_result, subagent_spawn, task_manager, task_scheduler, tool_info
[Vision] → ocr_extract, desktop_act, desktop_grounding_scan, desktop_read_screen
```
