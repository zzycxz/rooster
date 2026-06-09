---
name: office-documents
description: "Office 文档处理，包括 Word/Excel/PDF/PPT (Use when: 生成报告、处理表格数据、读取或合并 PDF、制作幻灯片). NOT for: 纯文本编辑(用 file_system_op)、网页内容(用 web_fetch)."
metadata:
  rooster:
    emoji: "📄"
    platform: ["windows", "darwin"]
    category: "document"
    kits: ["Office", "FileSystem"]
    requires:
      python_packages: ["python-docx", "openpyxl", "PyPDF2", "python-pptx"]
      bins: []
      env_vars: []
---

# Office Documents — Word / Excel / PDF / PPT 文档处理

处理四种主流办公文档格式的统一技能包。

## 使用场景

✅ **以下情况使用此技能：**

- 用户要求生成 Word 报告或文档
- 需要读取、写入、分析 Excel 数据
- 需要合并、拆分、提取 PDF 内容
- 需要创建或修改 PPT 演示文稿

## 不适用场景

❌ **不使用此技能：**

- 纯文本文件读写 → 改用 `file_system_op`
- 抓取网页内容 → 改用 `web_fetch`
- 执行数据分析脚本 → 改用 `python_interpreter`（但可以先用 `excel_op` 读数据）

## 可用工具

| 工具 | 用途 | 常用操作 |
|------|------|---------|
| `office_docx_write` | Word 文档生成 | 写入报告、公文、合同等格式化文档 |
| `excel_op` | Excel 表格操作 | read（读取数据）、write（写入数据）、analyze（分析） |
| `pdf_op` | PDF 处理 | read（提取文本）、merge（合并）、split（拆分） |
| `pptx_op` | PPT 演示文稿 | 创建幻灯片、插入文本/图片 |

## 标准工作流

### Word 文档生成

```
步骤 1：确认文档内容和格式要求
步骤 2：office_docx_write(title="标题", content=[...], output_path="...")
  → 支持标题层级、段落、列表、表格等富文本元素
步骤 3：file_system_op(action="read", path="...") 验证输出
```

### Excel 数据处理

```
步骤 1：读取数据
  excel_op(action="read", path="data.xlsx")
步骤 2：用 python_interpreter 处理数据（pandas/matplotlib）
步骤 3：写回结果
  excel_op(action="write", path="result.xlsx", data=<处理后的数据>)
```

### PDF 操作

```
读取：pdf_op(action="read", path="file.pdf")
合并：pdf_op(action="merge", paths=["a.pdf", "b.pdf"], output_path="merged.pdf")
拆分：pdf_op(action="split", path="file.pdf", page_ranges="1-3,5-7")
```

### PPT 生成

```
pptx_op(action="create", title="演示标题", slides=[...], output_path="...")
```

## 关键原则

- **先读再写**：修改现有文件时，先用 read 操作确认文件内容和结构。
- **绝对路径**：所有路径参数使用绝对路径，确保文件定位准确。
- **中文排版**：生成中文 Word 文档时，遵循公文格式规范（标题用方正小标宋、正文用仿宋）。
- **大数据处理**：Excel 数据量较大时，优先使用 `python_interpreter` + pandas 处理，效率更高。

## 注意事项

- Word 文档生成支持多种段落样式，但复杂排版可能需要通过 `python_interpreter` 直接调用 python-docx 库获得更精细控制。
- PDF 读取对扫描件（图片 PDF）效果有限，如需 OCR 请配合 `ocr_extract` 工具。
- Excel 文件较大时（>10MB），建议先用 `file_system_op(action="hash")` 确认文件完整性。
