---
name: summarize
description: "智能内容摘要与总结 (Use when: 需要总结长文档、提取要点、生成摘要). NOT for: 数据分析、代码生成."
metadata:
  rooster:
    emoji: "📝"
    platform: ["any"]
    category: "document"
    requires:
      python_packages: []
      bins: []
      env_vars: []
---

# Summarize — 智能内容摘要与总结

利用 Rooster 的 LLM 能力，对长文本、文档、网页内容进行结构化摘要和要点提取。

## 适用场景

- 总结长篇文档/报告的核心内容
- 提取会议纪要的行动项和关键决策
- 将多篇文档合并为一份概览
- 生成技术文档的 TL;DR 版本
- 提取文章中的关键数据和结论

## 文本摘要模板

```python
# 通过 Executor 的 ReAct 循环调用 LLM 完成
# Strategist 会自动构建摘要提示词

# 输入格式示例（由 Router 解析后传入）
input_text = """
[长文本内容...]
"""

# 输出格式建议
summary_structure = """
## 核心摘要
[1-2 段话概括主旨]

## 关键要点
1. [要点 1]
2. [要点 2]
3. [要点 3]

## 行动项（如适用）
- [ ] [待办 1]
- [ ] [待办 2]

## 关键数据
- 指标 A: 数值
- 指标 B: 数值
"""
```

## 文档批量摘要

```python
import os
import json

def batch_summarize(doc_dir, output_path):
    """批量处理目录下的文本文件"""
    results = []
    for fname in sorted(os.listdir(doc_dir)):
        if not fname.endswith((".txt", ".md")):
            continue
        with open(os.path.join(doc_dir, fname), "r", encoding="utf-8") as f:
            content = f.read()

        # 每个 doc 生成摘要提示
        results.append({
            "file": fname,
            "length": len(content),
            "preview": content[:200],
        })

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"[RESULT_PATH: {os.path.abspath(output_path)}]")
```

## 注意事项

1. **长度控制**：超过模型上下文窗口的文本需要分段摘要再汇总
2. **语言检测**：自动识别输入语言，摘要保持同语言输出
3. **格式保留**：输出结果保留 Markdown 格式，方便后续转 Word/PDF
4. **事实准确性**：摘要不得添加原文不存在的信息
5. **可导出**：摘要结果可通过 `office_docx_write` 或 `pdf_op(action="write")` 工具导出为正式文档
