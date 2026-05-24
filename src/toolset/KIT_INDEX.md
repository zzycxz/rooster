# Rooster Kit Index

| Kit | 关键词 | 内含工具 | 适用场景 |
|-----|--------|----------|----------|
| Browser | 网页,搜索,抓取,fetch,html,url,google,baidu | web_search, web_fetch, browser_nav, browser_read, browser_click, browser_scroll, browser_explore_links, browser_next_page | 调研/爬取网页、模拟浏览器操作、读取文档链接 |
| Vision | 屏幕,截图,点击,窗口,IM,app,微信,程序,扫描,打标,元素,迅雷,弹窗,确认按钮 | desktop_snap, desktop_grounding_scan, desktop_click, desktop_type, vnode_grounding_scan, vnode_grounding_click, vnode_grounding_type, vnode_camera_snap | 控制桌面 App、点击 UI、截图打标分析。本地直连优先用 desktop_* 工具 |
| FileSystem | 文件,目录,读写,创建,删除,列表,清单 | list_files, read_file, write_file, create_directory, search_files, download_file, calculate_file_hash | 读写本地文件、管理工作区目录 |
| Office | excel,word,表格,文档,sheet,单元格 | excel_read, excel_write, office_docx_write, office_pdf_write, office_pdf_read | 处理 Excel 数据、生成 Word 报告 |
| Interpreter | 执行,python,代码,计算,脚本 | python_interpreter | 运行 Python 代码、数学计算、数据处理 |
| Memory | 记忆,历史,总结,ltm,知识 | memory_add_fact | 记录长期事实、持久化配置 |
| Multimedia | 视频,音频,下载,媒体 | multimedia_download | 下载视频和音频 |
| Network | mcp,协议,外部服务,协同,推送,飞书 | feishu_push_file, resource_fetch, collaboration_init | 接入外部服务、推送文件、搜索资源 |
| System | 技能,skill,调度,搜索,定时,计划任务,schtasks,cron | skill_read, tool_list, tool_search, task_scheduler_create, task_scheduler_delete | 管理工具发现、读取技能文档、创建/删除 Windows 定时任务 |
| Task | 任务,状态,追踪,子任务,进度,todo | task_create, task_get, task_update, task_list | 创建和追踪结构化任务，适用于多步骤复杂任务管理 |
| Orchestration | 子agent,隔离,并行,规划,计划确认 | subagent_spawn, subagent_result, enter_plan_mode, exit_plan_mode | 启动隔离 SubAgent、Plan Mode 规划确认后再执行 |
| MCP | mcp,外部工具,动态,插件 | (运行时动态注册，由 MCP_DYNAMIC_ENABLED 控制) | 接入外部 MCP Server 暴露的工具，自动扩展工具集 |
