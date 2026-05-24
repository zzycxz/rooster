import os
from typing import List, Dict, Any, Type, Optional
from pydantic import BaseModel, Field
from toolset.base import BaseTool


# --- 1. EXCEL 工具 ---
class ExcelWriteArgs(BaseModel):
    path: str = Field(description="保存 Excel 的绝对路径。禁止在文件名中添加随机数或时间戳，确保任务周期内路径唯一。")
    data: List[Dict[str, Any]] = Field(description="列表形式的 JSON 数据，例如：[{'公司': 'OpenAI', '估值': '800亿'}]")
    sheet_name: str = Field(description="工作表名称", default="Sheet1")


class ExcelWriteTool(BaseTool):
    """机械化 Excel 写入工具"""

    name: str = "excel_write"
    kit: str = "Office"
    fc_hidden: bool = True  # [Round 10] Use excel_op(action="write") instead
    description: str = "Save structured JSON data as an Excel (.xlsx) file. Input must be a JSON array of objects with consistent keys."
    domain: str = "craft"
    args_schema: Type[BaseModel] = ExcelWriteArgs

    async def run(self, **kwargs) -> str:
        try:
            import pandas as pd
        except ImportError:
            return "Error: 'pandas' or 'openpyxl' not installed. Please run 'pip install pandas openpyxl'."

        path = kwargs.get("path")
        data = kwargs.get("data", [])
        sheet_name = kwargs.get("sheet_name", "Sheet1")

        try:
            df = pd.DataFrame(data)
            # Ensure parent directory exists (orchestrator handles path validity, but tool layer still creates physically)
            # 确保父目录存在 (编排器会负责路径合法性，但工具层依然做物理创建)
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            df.to_excel(path, index=False, sheet_name=sheet_name)
            return f"Successfully saved {len(data)} rows to Excel. [RESULT_PATH: {os.path.abspath(path)}]"
        except Exception as e:
            return f"Excel Write Error: {str(e)}"


class ExcelReadArgs(BaseModel):
    path: str = Field(description="Excel 文件路径")


class ExcelReadTool(BaseTool):
    """机械化 Excel 读取工具"""

    name: str = "excel_read"
    kit: str = "Office"
    fc_hidden: bool = True  # [Round 10] Use excel_op(action="read") instead
    description: str = (
        "Read an Excel file and return its content as CSV-style text. Use this to inspect spreadsheet data."
    )
    domain: str = "craft"
    args_schema: Type[BaseModel] = ExcelReadArgs

    async def run(self, **kwargs) -> str:
        try:
            import pandas as pd
        except ImportError:
            return "Error: 'pandas' or 'openpyxl' not installed. Please run 'pip install pandas openpyxl'."

        path = kwargs.get("path")
        try:
            df = pd.read_excel(path)
            # Return first 20 rows to prevent context overflow (orchestrator applies secondary truncation)
            # 返回前 20 条，防止上下文溢出（编排器会有二次截断，此处做初步控制）
            csv_preview = df.to_csv(index=False)
            return f"Excel Content Preview (Total {len(df)} rows):\n{csv_preview}"
        except Exception as e:
            return f"Excel Read Error: {str(e)}"


# --- 2. WORD (DOCX) 工具 ---
class DocxWriteArgs(BaseModel):
    path: str = Field(description="保存 Word (.docx) 的路径")
    markdown_content: str = Field(description="包含 Markdown 语法的内容，支持标题、列表、表格、图片")
    style: str = Field(
        default="default",
        description='文档样式。预设: "公文"(中国公文格式), "学术"(论文格式), "商务"(商务报告), "default"(通用专业格式)。'
        '也可传 JSON 字符串自定义，如: {"title_font":"黑体","title_size":22,"body_font":"仿宋","body_size":16,"line_spacing":30}',
    )


class DocxWriteTool(BaseTool):
    """通用 Word 文档生成工具 — 支持多种样式预设和自定义格式"""

    name: str = "office_docx_write"
    kit: str = "Office"
    description: str = (
        "将 Markdown 内容转换为格式化的 Word (.docx) 文档。"
        "【追加写入特性】：如果目标文件路径已存在，此工具将自动把新内容**追加**在文档末尾！这允许你分多次、分章节调用此工具来生成超长报告，而无需一次性输出全部内容。"
        "支持样式预设：公文（中国公文排版标准）、学术（论文格式）、商务（商务报告）、default（通用专业）。"
        "用户可在对话中指定字体、字号、行距等格式要求，通过 style 参数传入。"
    )
    domain: str = "craft"
    args_schema: Type[BaseModel] = DocxWriteArgs

    # === 样式预设 ===
    STYLE_PRESETS = {
        "公文": {
            "title_font": "华文中宋",
            "title_size": 22,
            "title_bold": True,
            "body_font": "仿宋",
            "body_size": 16,
            "body_bold": False,
            "level1_font": "黑体",
            "level2_font": "楷体",
            "level3_font": "仿宋",
            "level4_font": "仿宋",
            "line_spacing": 30,
            "first_line_indent_chars": 2,
            "title_align": "center",
            "margins": {"top": 3.7, "bottom": 3.5, "left": 2.8, "right": 2.6},
        },
        "学术": {
            "title_font": "黑体",
            "title_size": 18,
            "title_bold": True,
            "body_font": "宋体",
            "body_size": 12,
            "body_bold": False,
            "level1_font": "黑体",
            "level2_font": "黑体",
            "level3_font": "宋体",
            "level4_font": "宋体",
            "line_spacing": 22,
            "first_line_indent_chars": 2,
            "title_align": "center",
            "margins": {"top": 2.54, "bottom": 2.54, "left": 3.18, "right": 3.18},
        },
        "商务": {
            "title_font": "微软雅黑",
            "title_size": 18,
            "title_bold": True,
            "body_font": "微软雅黑",
            "body_size": 11,
            "body_bold": False,
            "level1_font": "微软雅黑",
            "level2_font": "微软雅黑",
            "level3_font": "微软雅黑",
            "level4_font": "微软雅黑",
            "line_spacing": 26,
            "first_line_indent_chars": 2,
            "title_align": "center",
            "margins": {"top": 2.54, "bottom": 2.54, "left": 2.54, "right": 2.54},
        },
        "default": {
            "title_font": "宋体",
            "title_size": 18,
            "title_bold": True,
            "body_font": "宋体",
            "body_size": 12,
            "body_bold": False,
            "level1_font": "黑体",
            "level2_font": "楷体",
            "level3_font": "宋体",
            "level4_font": "宋体",
            "line_spacing": 26,
            "first_line_indent_chars": 2,
            "title_align": "center",
            "margins": {"top": 2.54, "bottom": 2.54, "left": 3.18, "right": 3.18},
        },
    }

    async def run(self, **kwargs) -> str:
        try:
            from docx import Document
            from docx.shared import Pt, Inches, Cm
            from docx.enum.text import WD_ALIGN_PARAGRAPH
            from docx.oxml.ns import qn
        except ImportError:
            return "Error: 'python-docx' not installed. Please run 'pip install python-docx'."

        path = kwargs.get("path")
        md = kwargs.get("markdown_content", "")
        style_str = kwargs.get("style", "default")

        try:
            import re

            # --- 解析样式 ---
            style = self._resolve_style(style_str)

            # --- 从 style 提取参数 ---
            title_font = style.get("title_font", "宋体")
            title_size = Pt(style.get("title_size", 18))
            title_bold = style.get("title_bold", True)
            body_font = style.get("body_font", "宋体")
            body_size = Pt(style.get("body_size", 12))
            body_bold = style.get("body_bold", False)
            l1_font = style.get("level1_font", "黑体")
            l2_font = style.get("level2_font", "楷体")
            l3_font = style.get("level3_font", body_font)
            l4_font = style.get("level4_font", body_font)
            line_sp = Pt(style.get("line_spacing", 26))
            indent_chars = style.get("first_line_indent_chars", 2)
            indent = Pt(body_size.pt * indent_chars) if indent_chars else None
            title_align_str = style.get("title_align", "center")
            title_align = WD_ALIGN_PARAGRAPH.CENTER if title_align_str == "center" else WD_ALIGN_PARAGRAPH.LEFT
            margins = style.get("margins", {"top": 2.54, "bottom": 2.54, "left": 3.18, "right": 3.18})

            is_append = False
            if os.path.exists(path):
                try:
                    doc = Document(path)
                    is_append = True
                except Exception:
                    doc = Document()
            else:
                doc = Document()

            def set_font(run, font_name, size, bold=False):
                run.font.size = size
                run.font.bold = bold
                run.font.name = font_name
                r = run._element
                rPr = r.find(qn("w:rPr"))
                if rPr is None:
                    rPr = r.makeelement(qn("w:rPr"), {})
                    r.insert(0, rPr)
                rFonts = rPr.find(qn("w:rFonts"))
                if rFonts is None:
                    rFonts = rPr.makeelement(qn("w:rFonts"), {})
                    rPr.insert(0, rFonts)
                rFonts.set(qn("w:eastAsia"), font_name)

            def set_para(
                p,
                alignment=WD_ALIGN_PARAGRAPH.JUSTIFY,
                line_spacing=line_sp,
                first_indent=None,
                space_before=Pt(0),
                space_after=Pt(0),
            ):
                pf = p.paragraph_format
                pf.alignment = alignment
                pf.line_spacing = line_spacing
                pf.space_before = space_before
                pf.space_after = space_after
                if first_indent is not None:
                    pf.first_line_indent = first_indent

            def add_paragraph(
                doc,
                text,
                font,
                size,
                bold=False,
                alignment=WD_ALIGN_PARAGRAPH.JUSTIFY,
                first_indent=None,
                space_before=Pt(0),
                space_after=Pt(0),
            ):
                p = doc.add_paragraph()
                set_para(
                    p,
                    alignment=alignment,
                    first_indent=first_indent,
                    space_before=space_before,
                    space_after=space_after,
                )
                run = p.add_run(text)
                set_font(run, font, size, bold)
                return p

            def add_rich_paragraph(doc, text, font, size, first_indent=None, bold_font=None):
                """处理含 **粗体** 的段落"""
                p = doc.add_paragraph()
                set_para(p, first_indent=first_indent)
                bold_pattern = re.compile(r"\*\*(.*?)\*\*")
                if bold_pattern.search(text):
                    last_end = 0
                    for m in bold_pattern.finditer(text):
                        if m.start() > last_end:
                            run = p.add_run(text[last_end : m.start()])
                            set_font(run, font, size)
                        run = p.add_run(m.group(1))
                        set_font(run, bold_font or font, size, bold=True)
                        last_end = m.end()
                    if last_end < len(text):
                        run = p.add_run(text[last_end:])
                        set_font(run, font, size)
                else:
                    run = p.add_run(text)
                    set_font(run, font, size)
                return p

            # === Parse Markdown ===
            # === 解析 Markdown ===
            lines = md.split("\n")
            i = 0
            is_first_heading = True

            while i < len(lines):
                line = lines[i].strip()
                if not line:
                    i += 1
                    continue

                # 1. Table
                # 1. 表格
                if line.startswith("|") and i + 1 < len(lines) and "|---" in lines[i + 1]:
                    headers = [c.strip() for c in line.split("|") if c.strip()]
                    table = doc.add_table(rows=1, cols=len(headers))
                    for idx, h in enumerate(headers):
                        cell = table.rows[0].cells[idx]
                        cell.text = ""
                        run = cell.paragraphs[0].add_run(h)
                        set_font(run, body_font, body_size, bold=True)
                        cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
                    i += 2
                    while i < len(lines) and lines[i].strip().startswith("|"):
                        row_data = [c.strip() for c in lines[i].split("|") if c.strip()]
                        if len(row_data) == len(headers):
                            row_cells = table.add_row().cells
                            for idx, val in enumerate(row_data):
                                row_cells[idx].text = ""
                                run = row_cells[idx].paragraphs[0].add_run(val)
                                set_font(run, body_font, body_size)
                        i += 1
                    doc.add_paragraph()
                    continue

                # 2. Image
                # 2. 图片
                image_match = re.match(r"!\[(.*?)\]\((.*?)\)", line)
                if image_match:
                    try:
                        doc.add_picture(image_match.group(2), width=Inches(4))
                        if image_match.group(1):
                            add_paragraph(
                                doc, image_match.group(1), body_font, Pt(10), alignment=WD_ALIGN_PARAGRAPH.CENTER
                            )
                    except Exception as img_e:
                        add_paragraph(doc, f"[图片插入失败: {img_e}]", body_font, body_size)
                    i += 1
                    continue

                # 3. Markdown heading
                # 3. Markdown 标题
                if line.startswith("# "):
                    text = line[2:].strip()
                    if is_first_heading and not is_append:
                        add_paragraph(
                            doc,
                            text,
                            title_font,
                            title_size,
                            bold=title_bold,
                            alignment=title_align,
                            space_before=Pt(12),
                            space_after=Pt(12),
                        )
                        is_first_heading = False
                    else:
                        add_paragraph(doc, text, l1_font, body_size, space_before=Pt(6), space_after=Pt(3))
                        is_first_heading = False
                elif line.startswith("## "):
                    add_paragraph(doc, line[3:].strip(), l1_font, body_size, space_before=Pt(6), space_after=Pt(3))
                elif line.startswith("### "):
                    add_paragraph(
                        doc,
                        line[4:].strip(),
                        l2_font,
                        body_size,
                        first_indent=indent,
                        space_before=Pt(3),
                        space_after=Pt(3),
                    )
                elif line.startswith("#### "):
                    add_paragraph(doc, line[5:].strip(), l3_font, body_size, first_indent=indent)
                # 4. Official document native hierarchy
                # 4. 公文原生层次
                elif re.match(r"^[一二三四五六七八九十]+、", line):
                    add_paragraph(doc, line, l1_font, body_size, space_before=Pt(6), space_after=Pt(3))
                elif re.match(r"^（[一二三四五六七八九十]+）", line):
                    add_paragraph(doc, line, l2_font, body_size, first_indent=indent)
                elif re.match(r"^\d+\.\s", line):
                    add_paragraph(doc, line, l3_font, body_size, first_indent=indent)
                elif re.match(r"^（\d+）", line):
                    add_paragraph(doc, line, l4_font, body_size, first_indent=indent)
                # 5. List
                # 5. 列表
                elif line.startswith("- ") or line.startswith("* "):
                    add_paragraph(doc, "· " + line[2:], body_font, body_size, first_indent=indent)
                # 6. Attachment
                # 6. 附件
                elif line.startswith("附件") or line.startswith("**附件**"):
                    add_paragraph(doc, line, body_font, body_size, first_indent=indent, space_before=Pt(12))
                # 7. Body text
                # 7. 正文
                else:
                    add_rich_paragraph(doc, line, body_font, body_size, first_indent=indent, bold_font=l1_font)
                i += 1

            # === Page margins ===
            # === 页面边距 ===
            if not is_append:
                for section in doc.sections:
                    section.top_margin = Cm(margins.get("top", 2.54))
                    section.bottom_margin = Cm(margins.get("bottom", 2.54))
                    section.left_margin = Cm(margins.get("left", 3.18))
                    section.right_margin = Cm(margins.get("right", 3.18))

            doc.save(path)
            return f"Successfully created Word document (style: {style_str}). [RESULT_PATH: {os.path.abspath(path)}]"
        except Exception as e:
            return f"Word Write Error: {str(e)}"

    def _resolve_style(self, style_str: str) -> dict:
        """Resolve style parameter: preset name or custom JSON.
        解析样式参数：预设名或自定义 JSON"""
        import json as _json

        # Try as preset name
        # 尝试作为预设名
        if style_str in self.STYLE_PRESETS:
            return dict(self.STYLE_PRESETS[style_str])
        # Try parsing as JSON
        # 尝试作为 JSON 解析
        try:
            custom = _json.loads(style_str)
            if isinstance(custom, dict):
                # Fill in missing fields based on default
                # 基于 default 补全缺失字段
                base = dict(self.STYLE_PRESETS["default"])
                base.update(custom)
                return base
        except (ValueError, TypeError):
            pass
        # Fall back to default
        # 回退到 default
        return dict(self.STYLE_PRESETS["default"])


# --- 3. PDF 工具 ---
class PdfWriteArgs(BaseModel):
    path: str = Field(description="保存 PDF (.pdf) 的路径")
    content: str = Field(description="PDF 的文本内容")
    title: str = Field(description="文档标题", default="Rooster Investigation Report")


class PdfWriteTool(BaseTool):
    """职业化 PDF 生成工具 (基于 fpdf2)"""

    name: str = "office_pdf_write"
    kit: str = "Office"
    fc_hidden: bool = True  # [Round 10] Use pdf_op(action="write") instead
    description: str = "Export text content as a formatted PDF report. Use this to generate deliverable documents."
    domain: str = "craft"
    args_schema: Type[BaseModel] = PdfWriteArgs

    async def run(self, **kwargs) -> str:
        try:
            from fpdf import FPDF
        except ImportError:
            return "Error: 'fpdf2' not installed. Please run 'pip install fpdf2'."

        path = kwargs.get("path")
        content = kwargs.get("content", "")
        title = kwargs.get("title", "Rooster Report")

        try:
            pdf = FPDF()
            pdf.add_page()
            # Try to load a Chinese-compatible font (common Windows paths)
            # 尝试加载能显示中文的字体（Windows 常用路径）
            font_added = False
            for font_p in [
                "C:\\Windows\\Fonts\\simhei.ttf",
                "C:\\Windows\\Fonts\\msyh.ttc",
                "C:\\Windows\\Fonts\\simsun.ttc",
                "/System/Library/Fonts/PingFang.ttc",
                "/System/Library/Fonts/STHeiti Light.ttc",
                "/Library/Fonts/Arial Unicode.ttf",
            ]:
                if os.path.exists(font_p):
                    pdf.add_font("Sans", "", font_p)
                    pdf.add_font("Sans", "B", font_p)  # Reuse for Bold
                    pdf.set_font("Sans", size=12)
                    font_added = True
                    break

            if not font_added:
                pdf.set_font("Helvetica", size=12)

            # Title
            if font_added:
                pdf.set_font("Sans", "B", 16)
            else:
                pdf.set_font("Helvetica", "B", 16)
            pdf.cell(0, 10, title, ln=True, align="C")
            pdf.ln(10)

            # Content
            if font_added:
                pdf.set_font("Sans", "", 12)
            else:
                pdf.set_font("Helvetica", "", 12)
            pdf.multi_cell(0, 10, content)

            pdf.output(path)
            return f"Successfully generated Professional PDF report. [RESULT_PATH: {os.path.abspath(path)}]"
        except Exception as e:
            return f"PDF Write Error: {str(e)}"


class PdfReadArgs(BaseModel):
    path: str = Field(description="PDF 文件路径")


class PdfReadTool(BaseTool):
    """机械化 PDF 读取工具"""

    name: str = "office_pdf_read"
    kit: str = "Office"
    fc_hidden: bool = True  # [Round 10] Use pdf_op(action="read") instead
    description: str = "Extract plain text content from a PDF file. Returns all readable text from the document."
    domain: str = "craft"
    args_schema: Type[BaseModel] = PdfReadArgs

    async def run(self, **kwargs) -> str:
        try:
            from pypdf import PdfReader
        except ImportError:
            return "Error: 'pypdf' not installed. Please run 'pip install pypdf'."

        path = kwargs.get("path")
        try:
            reader = PdfReader(path)
            text = ""
            for page in reader.pages:
                text += page.extract_text() + "\n"
            return text if text.strip() else "Empty PDF or text not extractable."
        except Exception as e:
            return f"PDF Read Error: {str(e)}"


# ---------------------------------------------------------------------------
# [Round 10] excel_op — unified Excel macro
# Replaces: excel_read, excel_write
# ---------------------------------------------------------------------------


class ExcelOpArgs(BaseModel):
    action: str = Field(description="'read' to read a spreadsheet, 'write' to save data as Excel")
    path: str = Field(description="Excel file path (.xlsx)")
    data: Optional[List[Dict[str, Any]]] = Field(
        default=None, description="[write] JSON array of objects, e.g. [{'Company': 'OpenAI', 'Value': '80B'}]"
    )
    sheet_name: Optional[str] = Field(default="Sheet1", description="[write] Sheet name (default: Sheet1)")


class ExcelOpTool(BaseTool):
    """[Round 10] Unified Excel macro: read or write spreadsheets."""

    name: str = "excel_op"
    kit: str = "Office"
    description: str = (
        "Unified Excel tool. Use action='read' to read an existing Excel file and return CSV-style content. "
        "Use action='write' to save structured JSON data as an Excel (.xlsx) file."
    )
    domain: str = "craft"
    args_schema: Type[BaseModel] = ExcelOpArgs

    async def run(self, **kwargs) -> str:
        try:
            import pandas as pd
        except ImportError:
            return "Error: 'pandas' or 'openpyxl' not installed. Please run 'pip install pandas openpyxl'."

        action = kwargs.get("action", "").lower()
        path = kwargs.get("path")

        if action == "read":
            try:
                df = pd.read_excel(path)
                csv_preview = df.to_csv(index=False)
                return f"Excel Content Preview (Total {len(df)} rows):\n{csv_preview}"
            except Exception as e:
                return f"Excel Read Error: {str(e)}"

        elif action == "write":
            data = kwargs.get("data", [])
            sheet_name = kwargs.get("sheet_name", "Sheet1")
            try:
                df = pd.DataFrame(data)
                os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
                df.to_excel(path, index=False, sheet_name=sheet_name)
                return f"Successfully saved {len(data)} rows to Excel. [RESULT_PATH: {os.path.abspath(path)}]"
            except Exception as e:
                return f"Excel Write Error: {str(e)}"

        else:
            return f"Error: Unknown action '{action}'. Valid: 'read', 'write'."


# ---------------------------------------------------------------------------
# [Round 10] pdf_op — unified PDF macro
# Replaces: office_pdf_read, office_pdf_write
# ---------------------------------------------------------------------------


class PdfOpArgs(BaseModel):
    action: str = Field(description="'read' to extract text from a PDF, 'write' to generate a PDF report")
    path: str = Field(description="PDF file path")
    content: Optional[str] = Field(default=None, description="[write] Text content for the PDF")
    title: Optional[str] = Field(
        default="Rooster Investigation Report",
        description="[write] Document title (default: Rooster Investigation Report)",
    )


class PdfOpTool(BaseTool):
    """[Round 10] Unified PDF macro: read or write PDF files."""

    name: str = "pdf_op"
    kit: str = "Office"
    description: str = (
        "Unified PDF tool. Use action='read' to extract all readable text from a PDF file. "
        "Use action='write' to generate a formatted PDF report from text content."
    )
    domain: str = "craft"
    args_schema: Type[BaseModel] = PdfOpArgs

    async def run(self, **kwargs) -> str:
        action = kwargs.get("action", "").lower()
        path = kwargs.get("path")

        if action == "read":
            try:
                from pypdf import PdfReader
            except ImportError:
                return "Error: 'pypdf' not installed. Please run 'pip install pypdf'."
            try:
                reader = PdfReader(path)
                text = ""
                for page in reader.pages:
                    text += page.extract_text() + "\n"
                return text if text.strip() else "Empty PDF or text not extractable."
            except Exception as e:
                return f"PDF Read Error: {str(e)}"

        elif action == "write":
            try:
                from fpdf import FPDF
            except ImportError:
                return "Error: 'fpdf2' not installed. Please run 'pip install fpdf2'."
            content = kwargs.get("content", "")
            title = kwargs.get("title", "Rooster Report")
            try:
                pdf = FPDF()
                pdf.add_page()
                font_added = False
                for font_p in [
                    "C:\\Windows\\Fonts\\simhei.ttf",
                    "C:\\Windows\\Fonts\\msyh.ttc",
                    "C:\\Windows\\Fonts\\simsun.ttc",
                    "/System/Library/Fonts/PingFang.ttc",
                    "/System/Library/Fonts/STHeiti Light.ttc",
                    "/Library/Fonts/Arial Unicode.ttf",
                ]:
                    if os.path.exists(font_p):
                        pdf.add_font("Sans", "", font_p)
                        pdf.add_font("Sans", "B", font_p)
                        pdf.set_font("Sans", size=12)
                        font_added = True
                        break
                if not font_added:
                    pdf.set_font("Helvetica", size=12)
                pdf.set_font("Sans" if font_added else "Helvetica", "B", 16)
                pdf.cell(0, 10, title, ln=True, align="C")
                pdf.ln(10)
                pdf.set_font("Sans" if font_added else "Helvetica", "", 12)
                pdf.multi_cell(0, 10, content)
                pdf.output(path)
                return f"Successfully generated PDF report. [RESULT_PATH: {os.path.abspath(path)}]"
            except Exception as e:
                return f"PDF Write Error: {str(e)}"

        else:
            return f"Error: Unknown action '{action}'. Valid: 'read', 'write'."
