import re
import math
from typing import List, Optional
from bs4 import BeautifulSoup, Tag


class PruningContentFilter:
    """
    基于 4 维评分的内容提取器（灵感来自 crawl4ai PruningContentFilter）。
    在 HTML 阶段过滤噪声，只保留高文本密度的主体内容区域。

    评分维度：
    - 文本密度 (text_len / html_len)        权重 0.4
    - 链接密度 (link_text / total_text 取反) 权重 0.2
    - 标签权重 (article=1.5, p=1.0, ...)     权重 0.2
    - 负面模式 (nav|footer|sidebar|ads|...)   权重 0.1
    - 文本长度 (log scale)                   权重 0.1
    """

    # 标签权重映射
    TAG_WEIGHTS = {
        "article": 1.5,
        "main": 1.4,
        "section": 1.2,
        "p": 1.0,
        "pre": 1.0,
        "blockquote": 0.9,
        "div": 0.5,
        "span": 0.3,
        "td": 0.6,
        "th": 0.6,
        "li": 0.5,
        "dd": 0.5,
        "dt": 0.5,
        "h1": 1.2,
        "h2": 1.1,
        "h3": 1.0,
        "h4": 0.9,
        "h5": 0.8,
        "h6": 0.8,
        "table": 0.7,
        "ul": 0.5,
        "ol": 0.5,
        "dl": 0.5,
    }

    # 负面模式（class/id 匹配这些关键词的节点扣分）
    NEGATIVE_PATTERNS = re.compile(
        r"nav|footer|header|sidebar|ads|comment|promo|advert|social|share|"
        r"related|recommend|breadcrumb|menu|widget|banner|sponsor|popup|modal|"
        r"tag|category|archive|disqus|utterances|gitalk",
        re.IGNORECASE,
    )

    # 应该完全移除的标签
    STRIP_TAGS = {
        "script",
        "style",
        "noscript",
        "iframe",
        "svg",
        "canvas",
        "form",
        "input",
        "button",
        "select",
        "textarea",
    }

    # 正面内容标签（提取 fit_html 时优先保留）
    CONTENT_TAGS = {
        "article",
        "main",
        "section",
        "p",
        "pre",
        "blockquote",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "table",
        "ul",
        "ol",
        "dl",
        "figure",
        "figcaption",
    }

    def __init__(self, min_word_threshold: int = 5):
        self.min_word_threshold = min_word_threshold

    def filter(self, html: str) -> str:
        """
        主入口：输入原始 HTML，输出过滤后的 fit_html（只保留主体内容）。
        如果无法找到足够好的内容块，回退到全量去噪 HTML。
        """
        soup = BeautifulSoup(html, "html.parser")

        # Phase 1: 移除噪声标签
        for tag_name in self.STRIP_TAGS:
            for tag in soup.find_all(tag_name):
                tag.decompose()

        # Phase 2: 评分所有候选块
        candidates = []
        for element in soup.find_all(["div", "section", "article", "main", "td", "th"]):
            if not isinstance(element, Tag):
                continue
            score = self._score_element(element)
            if score > 0:
                candidates.append((score, element))

        if not candidates:
            # 无候选块，回退到 body 全文（但仍移除负面模式匹配的元素）
            body = soup.find("body")
            if body:
                self._remove_negative_elements(body)
                return str(body)
            return str(soup)

        # Phase 3: 选择策略
        candidates.sort(key=lambda x: x[0], reverse=True)
        best_score, best_element = candidates[0]

        # 计算最佳元素的文本占比
        body = soup.find("body")
        total_text_len = len(body.get_text(strip=True)) if body else 1
        best_text_len = len(best_element.get_text(strip=True))
        coverage = best_text_len / total_text_len if total_text_len > 0 else 0

        if coverage > 0.5:
            # 最佳元素覆盖 > 50% 文本，只保留它（典型的 article/main 容器）
            self._remove_negative_elements(best_element)
            return str(best_element)
        else:
            # 内容分散在多个块中（文档/列表类页面），合并高分块
            threshold = best_score * 0.3
            selected = [el for score, el in candidates if score >= threshold]
            selected = self._deduplicate(selected)
            for el in selected:
                self._remove_negative_elements(el)
            return "\n".join(str(el) for el in selected)

    def _remove_negative_elements(self, root: Tag):
        """移除根元素下所有匹配负面模式的子元素"""
        to_remove = []
        for child in root.find_all(True):
            if not isinstance(child, Tag):
                continue
            classes = " ".join(child.get("class", []))
            child_id = child.get("id", "")
            combined = f"{classes} {child_id}"
            if self.NEGATIVE_PATTERNS.search(combined):
                to_remove.append(child)
        for el in to_remove:
            el.decompose()

    def _score_element(self, element: Tag) -> float:
        """对单个 DOM 元素进行 4 维评分"""
        html_str = str(element)
        html_len = len(html_str)
        if html_len < 50:
            return 0.0

        # 提取文本
        text = element.get_text(separator=" ", strip=True)
        text_len = len(text)
        word_count = len(text.split())

        if word_count < self.min_word_threshold:
            return 0.0

        # 维度 1: 文本密度
        text_density = text_len / html_len if html_len > 0 else 0

        # 维度 2: 链接密度（链接文本占总文本比例，取反）
        link_text_len = sum(len(a.get_text(strip=True)) for a in element.find_all("a"))
        link_density = 1.0 - (link_text_len / text_len if text_len > 0 else 0)

        # 维度 3: 标签权重
        tag_name = element.name
        tag_weight = self.TAG_WEIGHTS.get(tag_name, 0.3)
        # 如果子元素中包含高权重标签，加分
        for child_tag in element.find_all(["article", "p", "pre", "blockquote"]):
            child_weight = self.TAG_WEIGHTS.get(child_tag.name, 0)
            tag_weight = max(tag_weight, child_weight * 0.8)

        # 维度 4: 负面模式
        classes = " ".join(element.get("class", []))
        element_id = element.get("id", "")
        combined_attrs = f"{classes} {element_id}"
        has_negative = bool(self.NEGATIVE_PATTERNS.search(combined_attrs))
        negative_penalty = 0.3 if has_negative else 1.0

        # 维度 5: 文本长度（log scale 归一化）
        length_score = min(1.0, math.log(max(text_len, 1)) / math.log(10000))

        # 加权求和
        score = text_density * 0.4 + link_density * 0.2 + tag_weight * 0.2 + negative_penalty * 0.1 + length_score * 0.1

        return score

    def _deduplicate(self, elements: List[Tag]) -> List[Tag]:
        """去重：子元素被父元素包含时只保留父元素"""
        if len(elements) <= 1:
            return elements

        result = []
        for el in elements:
            is_child = False
            for other in elements:
                if el is other:
                    continue
                if el in other.descendants:
                    is_child = True
                    break
            if not is_child:
                result.append(el)
        return result


class TableExtractor:
    """将 HTML <table> 提取为 Markdown 表格格式"""

    @staticmethod
    def extract_tables(soup: BeautifulSoup) -> List[str]:
        """提取所有 <table> 为 markdown 表格字符串"""
        tables = []
        for table in soup.find_all("table"):
            md = TableExtractor._table_to_markdown(table)
            if md:
                tables.append(md)
        return tables

    @staticmethod
    def _table_to_markdown(table: Tag) -> Optional[str]:
        """单个 <table> → markdown table"""
        rows = table.find_all("tr")
        if not rows:
            return None

        # 提取表头
        header_cells = rows[0].find_all(["th", "td"])
        headers = [cell.get_text(strip=True) for cell in header_cells]
        if not headers or all(not h for h in headers):
            return None

        # 提取数据行
        data_rows = []
        for row in rows[1:]:
            cells = row.find_all(["td", "th"])
            row_data = [cell.get_text(strip=True) for cell in cells]
            # 补齐列数
            while len(row_data) < len(headers):
                row_data.append("")
            data_rows.append(row_data[: len(headers)])

        if not data_rows:
            return None

        # 生成 markdown table
        lines = []
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
        for row in data_rows:
            lines.append("| " + " | ".join(row) + " |")

        return "\n".join(lines)


class CitationLinkConverter:
    """
    将 markdown 中的内联链接 [text](url) 转换为引用式格式：text[1]
    底部生成 References 区域。
    """

    @staticmethod
    def convert(markdown: str) -> tuple[str, str]:
        """
        返回 (converted_markdown, references_section)。
        如果没有链接，返回原文和空字符串。
        """
        link_pattern = re.compile(r"\[([^\]]+)\]\((https?://[^\)]+)\)")
        matches = link_pattern.findall(markdown)

        if not matches:
            return markdown, ""

        # 去重，保持顺序
        seen = {}
        ref_list = []
        for text, url in matches:
            if url not in seen:
                idx = len(ref_list) + 1
                seen[url] = idx
                ref_list.append((idx, text, url))

        # 替换内联链接为 text[N]
        def replace_link(match):
            text = match.group(1)
            url = match.group(2)
            idx = seen.get(url)
            if idx:
                return f"{text}[{idx}]"
            return match.group(0)

        converted = link_pattern.sub(replace_link, markdown)

        # 生成 References 区域
        ref_lines = ["## References"]
        for idx, text, url in ref_list:
            ref_lines.append(f"[{idx}] {text}: {url}")

        return converted, "\n".join(ref_lines)


class MarkdownPruner:
    """
    网页 Markdown 减脂器：保留骨架，丢弃赘肉。
    优化 Token 消耗，提高 LLM 理解效率。
    """

    @staticmethod
    def prune(content: str, max_chars: int = 12000) -> str:
        if not content:
            return ""

        # 移除常见的 HTML 残留与噪音
        content = re.sub(r"\[!(?:NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]", "", content)

        # 按块处理
        lines = content.split("\n")
        pruned_lines = []

        in_code_block = False
        current_paragraph = []

        def flush_paragraph():
            nonlocal current_paragraph
            if not current_paragraph:
                return

            text = "\n".join(current_paragraph).strip()
            if len(text) > 1000:
                sentences = re.split(r"([。！？.!?])", text)
                if len(sentences) > 4:
                    head = "".join(sentences[:4])
                    tail = "".join(sentences[-4:])
                    text = f"{head}\n\n[... {len(text) - len(head) - len(tail)} chars of body text slimmed for brevity ...]\n\n{tail}"

            pruned_lines.append(text)
            current_paragraph = []

        for line in lines:
            stripped = line.strip()

            # 代码块处理：完整保留
            if stripped.startswith("```"):
                flush_paragraph()
                in_code_block = not in_code_block
                pruned_lines.append(line)
                continue

            if in_code_block:
                pruned_lines.append(line)
                continue

            # 标题处理：完整保留
            if re.match(r"^#{1,3}\s", stripped):
                flush_paragraph()
                pruned_lines.append(line)
                continue

            # 空行：刷新段落
            if not stripped:
                flush_paragraph()
                continue

            # 普通行：加入当前段落暂存
            current_paragraph.append(line)

        flush_paragraph()

        # 强制截断兜底
        final_text = "\n\n".join(pruned_lines)
        if len(final_text) > max_chars:
            return (
                final_text[:max_chars]
                + f"\n\n[... Total content exceeded {max_chars} chars and was strictly truncated ...]"
            )

        return final_text

    @staticmethod
    def extract_scent_links(content: str, limit: int = 5) -> List[str]:
        """嗅探高价值链接 (Documentation, Usage, etc.)"""
        links = re.findall(r"\[([^\]]+)\]\((https?://[^\)]+)\)", content)

        scent_keywords = ["doc", "usage", "install", "api", "release", "guide", "tutorial", "example"]
        scents = []

        for text, url in links:
            text_lower = text.lower()
            if any(kw in text_lower for kw in scent_keywords):
                scents.append(f"- **{text}**: {url}")

            if len(scents) >= limit:
                break

        return scents


# --- Anti-bot 签名检测 ---

# 已知反爬框架的 HTML 签名
ANTIBOT_SIGNATURES = {
    "cloudflare": [
        re.compile(r"cf-browser-verification", re.I),
        re.compile(r"cloudflare.*challenge", re.I),
        re.compile(r"__cf_bm", re.I),
        re.compile(r"cf-chl-opt", re.I),
    ],
    "akamai": [
        re.compile(r"akamai.*bot.*manager", re.I),
        re.compile(r"_abck=", re.I),
        re.compile(r"ak_bmsc", re.I),
    ],
    "perimeterx": [
        re.compile(r"perimeterx", re.I),
        re.compile(r"_px[0-9]", re.I),
        re.compile(r"px-captcha", re.I),
    ],
    "datadome": [
        re.compile(r"datadome", re.I),
        re.compile(r"dd_cookie", re.I),
    ],
    "imperva": [
        re.compile(r"incapsula", re.I),
        re.compile(r"reesee_enc", re.I),
    ],
    "sucuri": [
        re.compile(r"sucuri.*cloudproxy", re.I),
        re.compile(r"sucuri_waf", re.I),
    ],
    "kasada": [
        re.compile(r"kasada", re.I),
        re.compile(r"x-kpsdk", re.I),
    ],
    "generic": [
        re.compile(r"access.*denied.*captcha", re.I),
        re.compile(r"verify.*human", re.I),
        re.compile(r"please.*enable.*javascript", re.I),
        re.compile(r"checking.*browser.*before", re.I),
    ],
}

# noscript 重定向提示
NOSCRIPT_REDIRECT = re.compile(
    r"<noscript>.*?(?:redirect|refresh|meta.*http-equiv.*refresh).*?</noscript>", re.IGNORECASE | re.DOTALL
)


def detect_antibot(html: str) -> Optional[str]:
    """
    检测 HTML 中的反爬框架签名。
    返回检测到的框架名称，或 None。
    """
    for framework, patterns in ANTIBOT_SIGNATURES.items():
        for pattern in patterns:
            if pattern.search(html):
                return framework
    return None


def needs_playwright_render(html: str) -> bool:
    """
    综合判断是否需要 Playwright 渲染。
    比简单的 body_text < 300 更精准。
    """
    if not html or len(html) < 500:
        return True

    soup = BeautifulSoup(html, "html.parser")

    # 检查反爬签名
    if detect_antibot(html):
        return True

    # 检查 noscript 重定向
    if NOSCRIPT_REDIRECT.search(html):
        return True

    body = soup.find("body")
    if not body:
        return True

    body_text = body.get_text(strip=True)
    script_count = len(soup.find_all("script"))

    # 主体文本极短
    if len(body_text) < 200:
        return True

    # script 密度异常高（JS 渲染的 SPA）
    if len(body_text) < 1000 and script_count > 6:
        return True

    # body 中 script 标签的字符数占比 > 60%
    body_html_len = len(str(body))
    script_html_len = sum(len(str(s)) for s in soup.find_all("script"))
    if body_html_len > 0 and script_html_len / body_html_len > 0.6:
        return True

    return False
