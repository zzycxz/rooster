---
name: pdf-tools
description: "PDF 文件处理 (Use when: 需要读取/合并/分割/转换 PDF，或从 PDF 提取文字/表格). NOT for: 网页内容抓取."
metadata:
  rooster:
    emoji: "📄"
    platform: ["any"]
    category: "document"
    requires:
      python_packages: ["pypdf", "pdfplumber"]
      bins: []
      env_vars: []
---

# PDF Tools — PDF 文件处理

本技能提供处理 PDF 文件的最佳实践。

## 场景 1：提取文字（优先使用宏工具）

当需要从 PDF 文件中读取和提取所有可读文本时，**必须优先调用 `pdf_op` 宏工具**：

```json
{
  "name": "pdf_op",
  "arguments": {
    "action": "read",
    "path": "C:\\path\\to\\document.pdf"
  }
}
```

如果需要更高级的自定义提取（按页处理、条件提取），可以使用 `pypdf` 库：

```python
from pypdf import PdfReader

pdf_path = r"C:\path\to\document.pdf"
reader = PdfReader(pdf_path)
for i, page in enumerate(reader.pages):
    text = page.extract_text()
    print(f"--- 第 {i+1} 页 ---")
    print(text)
```

## 场景 2：提取表格

```python
import pdfplumber
import pandas as pd

pdf_path = r"C:\path\to\report.pdf"
all_tables = []
with pdfplumber.open(pdf_path) as pdf:
    for page in pdf.pages:
        tables = page.extract_tables()
        for table in tables:
            df = pd.DataFrame(table[1:], columns=table[0])
            all_tables.append(df)

if all_tables:
    result = pd.concat(all_tables, ignore_index=True)
    output_path = r"C:\path\to\output.xlsx"
    result.to_excel(output_path, index=False)
    print(f"[RESULT_PATH: {output_path}]")
```

## 场景 3：合并多个 PDF

```python
from pypdf import PdfWriter, PdfReader as _R

merger_pages = []
for path in [
    r"C:\path\to\part1.pdf",
    r"C:\path\to\part2.pdf",
    r"C:\path\to\part3.pdf",
]:
    reader = _R(path)
    merger_pages.extend(reader.pages)

writer = PdfWriter()
for page in merger_pages:
    writer.add_page(page)

output_path = r"C:\path\to\merged.pdf"
with open(output_path, "wb") as f:
    writer.write(f)
print(f"[RESULT_PATH: {output_path}]")
```

## 场景 4：分割 PDF（按页范围）

```python
from pypdf import PdfReader, PdfWriter

reader = PdfReader(r"C:\path\to\original.pdf")
writer = PdfWriter()

# 提取第 3-7 页（索引 2-6）
for page_num in range(2, 7):
    writer.add_page(reader.pages[page_num])

output_path = r"C:\path\to\extracted_pages.pdf"
with open(output_path, "wb") as f:
    writer.write(f)
print(f"[RESULT_PATH: {output_path}]")
```

## 场景 5：生成新 PDF

```python
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

output_path = r"C:\path\to\new_report.pdf"
c = canvas.Canvas(output_path, pagesize=A4)
width, height = A4

# 注册中文字体（需要字体文件）
# pdfmetrics.registerFont(TTFont("SimHei", "SimHei.ttf"))
# c.setFont("SimHei", 14)

c.setFont("Helvetica-Bold", 16)
c.drawString(50, height - 80, "Report Title")
c.setFont("Helvetica", 12)
c.drawString(50, height - 120, "Content goes here...")
c.save()
print(f"[RESULT_PATH: {output_path}]")
```

## 注意事项

1. 扫描版 PDF（图片型）无法直接提取文字，需配合 OCR（`ocr` 工具）
2. 加密 PDF 需先解密：`reader.decrypt("password")`
3. pdfplumber 比 PyPDF2 对复杂版面的文字提取更准确
