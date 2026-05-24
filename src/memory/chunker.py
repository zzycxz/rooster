"""文本分块工具：将长文本切分为带重叠的小块。"""  # Text chunking utility: splits long text into overlapping chunks

import hashlib
import logging
import re
from typing import List, Optional

from .models import TextChunk

logger = logging.getLogger(__name__)

# CJK 范围：统一汉字 + 扩展A + 兼容 + 标点 + 全角符号
_CJK_RE = re.compile(r"[一-鿿㐀-䶿豈-﫿　-〿＀-￯]")


def _estimate_tokens(text: str) -> int:
    """估算 token 数：中文 ~1.5 token/字，英文 ~1.3 token/词。"""  # Estimate token count: Chinese ~1.5 token/char, English ~1.3 token/word
    cn_chars = len(_CJK_RE.findall(text))
    en_words = len(re.findall(r"[a-zA-Z]+", text))
    other = len(text) - cn_chars - sum(len(w) for w in re.findall(r"[a-zA-Z]+", text))
    return round(cn_chars * 1.5 + en_words * 1.3 + max(other, 0) * 0.5)


def _chars_for_tokens(max_tokens: int, text_sample: str = "") -> int:
    """根据文本组成估算 max_tokens 对应的字符数上限。

    对中文文本 ~0.67 字/token，英文 ~4 字/token。
    取样本的 CJK 比率来动态计算。

    Estimate character limit for max_tokens based on text composition.
    Chinese ~0.67 char/token, English ~4 char/token.
    Dynamically calculated from the sample's CJK ratio.
    """
    if not text_sample:
        # 无样本时保守取 2.5（中文偏多场景）
        # Conservative fallback of 2.5 (Chinese-heavy scenario)
        return int(max_tokens * 2.5)
    cn_chars = len(_CJK_RE.findall(text_sample))
    ratio = cn_chars / max(len(text_sample), 1)
    # ratio=0 纯英文 → 4 字/token；ratio=1 纯中文 → 0.67 字/token
    chars_per_token = 4.0 * (1 - ratio) + 0.67 * ratio
    return max(int(max_tokens * chars_per_token), 64)


def _make_chunk_id(content: str, source_path: str = "") -> str:
    """生成 chunk_id，混合 source_path 防止跨源碰撞。"""  # Generate chunk_id, mix in source_path to prevent cross-source collisions
    payload = f"{source_path}||{content}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def chunk_text(
    text: str,
    max_tokens: int = 400,
    overlap_tokens: int = 80,
    source_path: str = "",
    fact_id: Optional[str] = None,
    created_at: Optional[str] = None,
) -> List[TextChunk]:
    """
    将文本切分为带重叠的块。

    策略：
    1. 按行分割（统一换行符）
    2. 如果单行超长，强制切分
    3. 合并小行直到接近 max_chars
    4. 从前一块尾部取 overlap

    Strategy:
    1. Split by line (normalize newlines)
    2. Force-split lines exceeding max_chars
    3. Merge small lines until near max_chars
    4. Take overlap from the tail of the previous chunk
    """
    if not text or not text.strip():
        return []

    # 统一换行符
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")

    # 动态计算字符上限（基于前 2000 字符样本）
    sample = text[:2000]
    max_chars = _chars_for_tokens(max_tokens, sample)
    overlap_chars = _chars_for_tokens(overlap_tokens, sample)

    chunks: List[TextChunk] = []
    current_lines: List[str] = []
    current_chars = 0
    chunk_start = 0

    for i, line in enumerate(lines):
        line_chars = len(line)

        # 如果当前行本身就超长，强制切分
        if line_chars > max_chars:
            # 先 flush 当前积累
            if current_lines:
                chunks.append(
                    _make_chunk(
                        current_lines,
                        source_path,
                        chunk_start,
                        chunk_start + len(current_lines),
                        fact_id,
                        created_at,
                    )
                )
                # overlap
                overlap_lines = _tail_lines(current_lines, overlap_chars)
                current_lines = overlap_lines
                current_chars = sum(len(l) + 1 for l in overlap_lines)
                chunk_start = i - len(overlap_lines)

            # 切分长行
            for seg_start in range(0, line_chars, max_chars):
                seg = line[seg_start : seg_start + max_chars]
                chunks.append(
                    _make_chunk(
                        [seg],
                        source_path,
                        i,
                        i + 1,
                        fact_id,
                        created_at,
                    )
                )
            current_lines = []
            current_chars = 0
            chunk_start = i + 1
            continue

        # 加入当前行后是否超限
        if current_chars + line_chars + 1 > max_chars and current_lines:
            chunks.append(
                _make_chunk(
                    current_lines,
                    source_path,
                    chunk_start,
                    chunk_start + len(current_lines),
                    fact_id,
                    created_at,
                )
            )
            # overlap
            overlap_lines = _tail_lines(current_lines, overlap_chars)
            current_lines = overlap_lines
            current_chars = sum(len(l) + 1 for l in overlap_lines)
            chunk_start = i - len(overlap_lines)

        current_lines.append(line)
        current_chars += line_chars + 1  # +1 for \n

    # 最后一块
    if current_lines:
        chunks.append(
            _make_chunk(
                current_lines,
                source_path,
                chunk_start,
                chunk_start + len(current_lines),
                fact_id,
                created_at,
            )
        )

    return chunks


def _make_chunk(
    lines: List[str],
    source_path: str,
    start_line: int,
    end_line: int,
    fact_id: Optional[str],
    created_at: Optional[str],
) -> TextChunk:
    content = "\n".join(lines)
    return TextChunk(
        chunk_id=_make_chunk_id(content, source_path),
        content=content,
        source_path=source_path,
        start_line=start_line,
        end_line=end_line,
        token_count=_estimate_tokens(content),
        fact_id=fact_id,
        created_at=created_at,
    )


def _tail_lines(lines: List[str], max_chars: int) -> List[str]:
    """从尾部取不超过 max_chars 字符的行。"""  # Take lines from the tail not exceeding max_chars characters
    result = []
    total = 0
    for line in reversed(lines):
        if total + len(line) + 1 > max_chars:
            break
        result.append(line)
        total += len(line) + 1
    result.reverse()
    return result
