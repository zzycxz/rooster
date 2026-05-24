---
name: web-search
description: "网页搜索与资料调研 (Use when: 需要查找外部实时信息、技术文档、最新新闻等). NOT for: 操作本地文件、控制 GUI 应用."
metadata:
  rooster:
    emoji: "🔍"
    platform: ["any"]
    category: "search"
    requires:
      python_packages: ["httpx"]
      bins: []
      env_vars: ["SEARXNG_URL"]
---

# Web Search — 网页搜索

通过搜索引擎获取互联网上的海量信息。

## 工具选择

- **`exa_search`** — 首选入口，内置 4 级 fallback（Exa → Linkup → GLM → 7路并发）
- **`linkup_search`** — 深度搜索（`depth='deep'`），适合需要多轮迭代的复杂问题
- **`web_fetch`** — 精读单个网页，提取详情
- **`batch_web_fetch`** — 同时读取多个 URL（最多 5 个）

## 典型使用场景

- `exa_search(query="Python 3.12 新特性")` — 标准查询
- `linkup_search(query="量子计算最新进展", depth="deep")` — 深度研究
- `web_fetch(url="https://...", prompt="提取价格信息")` — 读取具体页面
- `batch_web_fetch(urls=[...], prompt="对比各产品优缺点")` — 批量读取对比

## 注意事项

- 确保网络代理配置正确（默认 127.0.0.1:7897）
- `exa_search` 结果已经过 LLM 重排序，优先使用
- 需要翻页时：使用 `browser_nav` + `browser_act(action="scroll")` 组合
