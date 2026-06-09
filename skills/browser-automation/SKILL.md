---
name: browser-automation
description: "浏览器自动化操作 (Use when: 需要交互式浏览网页、填写表单、点击按钮、翻页抓取动态内容). NOT for: 静态网页抓取(用 web_fetch)、文件下载(用 multimedia_download)."
metadata:
  rooster:
    emoji: "🌐"
    platform: ["windows", "darwin"]
    category: "automation"
    kits: ["Browser"]
    requires:
      python_packages: ["playwright"]
      bins: []
      env_vars: []
---

# Browser Automation — 浏览器自动化

基于 Playwright 的浏览器自动化操作，支持导航、交互、内容提取。

## 使用场景

✅ **以下情况使用此技能：**

- 需要与网页交互（点击按钮、填写表单、提交数据）
- 需要抓取动态加载的页面内容（JS 渲染）
- 需要在多个页面间导航、翻页浏览
- 需要提取页面中的链接列表

## 不适用场景

❌ **不使用此技能：**

- 静态网页内容抓取 → 改用 `web_fetch`（更快、无需启动浏览器）
- 搜索引擎查询 → 改用 `web_search`
- 文件下载 → 改用 `multimedia_download` 或 `file_system_op(action="download")`

## 可用工具

| 工具 | 用途 | 说明 |
|------|------|------|
| `browser_nav` | 导航到 URL | 第一步必须调用，初始化浏览器并打开页面 |
| `browser_act` | 统一交互宏工具 | **推荐**：合并了 click/type/scroll，一个工具搞定所有交互 |
| `browser_explore_links` | 提取页面链接 | 按关键词筛选当前页面上的链接 |
| `browser_next_page` | 翻页 | 自动识别并点击"下一页"按钮 |

> **注意**：`browser_read`、`browser_click`、`browser_type`、`browser_scroll` 已设为 `fc_hidden=True`，
> 统一通过 `browser_act` 调用。请勿尝试直接调用这些隐藏工具。

## 标准工作流

### 基本交互流程

```
步骤 1：打开页面
  browser_nav(url="https://example.com")
  → 返回页面内容和带 data-rooster-id 标注的交互元素列表

步骤 2：交互操作
  browser_act(action="click", index=5)   # 点击第 5 个交互元素
  browser_act(action="type", index=3, text="搜索关键词")  # 在第 3 个元素输入文字

步骤 3：验证结果
  browser_nav 会自动返回操作后的页面内容，检查是否符合预期
```

### 表单填写

```
步骤 1：browser_nav(url="...") 打开表单页面
步骤 2：browser_act(action="type", index=1, text="用户名")
步骤 3：browser_act(action="type", index=2, text="密码")
步骤 4：browser_act(action="click", index=0)  # 点击提交按钮
```

### 链接提取与翻页

```
步骤 1：browser_nav(url="...") 打开列表页
步骤 2：browser_explore_links(keyword="详情")  # 筛选含"详情"的链接
步骤 3：browser_next_page()  # 翻到下一页（或用 browser_act 点击特定翻页按钮）
```

## 关键原则

- **先 nav 后交互**：所有浏览器操作必须先调用 `browser_nav` 初始化页面。未初始化时其他工具会报错。
- **用 index 定位元素**：`browser_nav` 返回的页面中，每个可交互元素都有 `data-rooster-id` 编号（从 0 开始）。用这个编号作为 `index` 参数。
- **每次操作后检查**：`browser_act` 和 `browser_nav` 会返回当前页面内容，务必检查确认操作生效。
- **web_fetch vs browser_nav**：
  - 只需要读取内容，不需要交互 → `web_fetch`（更快、无浏览器开销）
  - 需要交互、处理动态内容 → `browser_nav` + `browser_act`

## 注意事项

- 浏览器实例在会话内复用，连续操作时不需要重复 `browser_nav` 打开同一页面。
- 部分网站有反自动化检测，如遇拦截可以尝试先 `web_fetch` 获取静态内容。
- 页面加载较慢时，操作可能需要等待。`browser_nav` 默认等待 `domcontentloaded` 事件。
- 长页面内容会被截断显示，使用 `browser_explore_links` 可以精确提取所需链接。
