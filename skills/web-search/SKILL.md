---
name: web-search
description: "网页搜索与资料调研 (Use when: 需要查找外部实时信息、技术文档、最新新闻等). NOT for: 操作本地文件、控制 GUI 应用."
metadata:
  rooster:
    emoji: "🔍"
    platform: ["any"]
    category: "search"
---

# Web Search — 网页搜索

## 可用工具

- **`web_search`** — 统一搜索入口，内置 5 级降级链（Linkup → Exa → GLM → 7路并发 → Playwright）
  - `query` (必填): 搜索关键词
  - `en_keywords` (可选): 英文关键词，用于中英混合搜索优化
  - `domain_filter` (可选): 限定域名，如 `"gov.cn"`, `"openai.com"`（仅在用户明确要求或需要权威来源时使用）
  - `time_range` (可选): 时间范围，`"day"`, `"week"`, `"month"`, `"year"`, `"any"`
  - `deep_research` (可选): 设为 `true` 启用 Linkup 深度多轮研究
- **`web_fetch`** — 精读单个网页全文，AI 摘要
- **`batch_web_fetch`** — 批量读取多个 URL（最多 5 个）

## Agentic 搜索策略

1. **首次搜索**：用简洁关键词 + `web_search`
2. **结果不满意**：修改关键词、添加 `time_range` 重新搜索
3. **看到高度相关标题**：必须用 `web_fetch` 提取全文交叉验证，不要仅依赖摘要
4. **深度调研**：设置 `deep_research=true`，或多次搜索不同角度 + `batch_web_fetch` 对比
5. **需要权威来源**：使用 `domain_filter="gov.cn"` 或 `domain_filter="edu"`

## 注意事项

- `web_search` 结果已过智能排序和 LLM 重排序，优先使用
- 需要翻页时：使用 `browser_nav` + `browser_act(action="scroll")` 组合
- 不要滥用 `domain_filter`，除非用户明确要求
