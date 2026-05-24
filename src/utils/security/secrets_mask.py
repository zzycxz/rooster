"""
src/utils/security/secrets_mask.py

正则表达式敏感信息脱敏器。
防止 API key、令牌、密码等敏感内容泄露到审计日志或 LLM 上下文中。

设计原则：
- 只替换，不删除：[MASKED_<type>] 保留上下文可读性
- 编译一次，多次复用：线程安全
- 误报容忍：宁可误报（脱敏无害数据）也不漏报
- 不修改原始工具行为，只作用于日志/展示层
"""

import re
import logging
from typing import List, Tuple

logger = logging.getLogger(__name__)

# (pattern_name, compiled_regex, replacement)
_PATTERNS: List[Tuple[str, re.Pattern, str]] = []


def _add(name: str, pattern: str) -> None:
    try:
        _PATTERNS.append((name, re.compile(pattern, re.IGNORECASE), f"[MASKED_{name.upper()}]"))
    except re.error as e:
        logger.warning(f"[SecretsMask] 无效正则 '{name}': {e}")


# OpenAI / Anthropic / 通用 sk- 前缀 API key
_add("OPENAI_KEY", r"sk-[a-zA-Z0-9]{20,}")
# GitHub personal access tokens
_add("GITHUB_PAT", r"gh[pousr]_[A-Za-z0-9_]{36,}")
_add("GITHUB_PAT2", r"github_pat_[A-Za-z0-9_]{60,}")
# AWS access key
_add("AWS_KEY", r"AKIA[0-9A-Z]{16}")
# AWS secret key (40-char base64-like after =)
_add("AWS_SECRET", r"(?<=[Aa][Ww][Ss][_\-][Ss][Ee][Cc][Rr][Ee][Tt]\s*[=:]\s*)[A-Za-z0-9/+=]{40}")
# Bearer tokens in headers/code
_add("BEARER_TOKEN", r"[Bb]earer\s+[A-Za-z0-9\-._~+/]+=*")
# Generic api_key / apikey / api-key assignments
_add("API_KEY_ASSIGN", r'(?:api[_\-]?key|apikey)\s*[=:]\s*["\']?[A-Za-z0-9\-_.]{12,}["\']?')
# Password assignments
_add("PASSWORD", r'(?:password|passwd|pwd)\s*[=:]\s*["\'][^"\']{4,}["\']')
# Generic secret assignments
_add("SECRET_ASSIGN", r'(?:secret|token|access_token|auth_token)\s*[=:]\s*["\'][A-Za-z0-9\-_.+/]{10,}["\']')
# Zhipu / Feishu / common Chinese API platforms
_add("ZHIPU_KEY", r"[0-9a-f]{32}\.[A-Za-z0-9]{6,}")
# Feishu app_secret / verification_token format
_add("FEISHU_SECRET", r"[A-Za-z0-9]{16,}[A-Za-z0-9]{16,}")
# Credit card numbers (4×4 digit groups, with spaces or dashes)
_add("CREDIT_CARD", r"\b(?:\d{4}[\s\-]){3}\d{4}\b")
# RSA / PEM private key headers
_add("PEM_KEY", r"-----BEGIN (?:RSA |EC |DSA )?PRIVATE KEY-----[\s\S]+?-----END (?:RSA |EC |DSA )?PRIVATE KEY-----")
# Connection strings with embedded credentials
_add("CONN_STRING", r'(?:mysql|postgresql|postgres|mongodb|redis|amqp)://[^:@/]+:[^@/]+@[^\s"\']+')


class SecretsMask:
    """
    敏感信息脱敏器（单例模式，线程安全）。
    仅对字符串类型的内容应用脱敏，二进制内容不处理。
    """

    _instance: "SecretsMask | None" = None

    def __new__(cls) -> "SecretsMask":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def mask(self, text: str) -> str:
        """将文本中所有匹配的敏感模式替换为 [MASKED_TYPE]。"""
        if not text or not isinstance(text, str):
            return text
        result = text
        for name, pattern, replacement in _PATTERNS:
            try:
                result = pattern.sub(replacement, result)
            except Exception:
                pass
        return result

    def has_secrets(self, text: str) -> bool:
        """快速检查文本中是否含有敏感内容（不替换）。"""
        if not text or not isinstance(text, str):
            return False
        return any(pattern.search(text) for _, pattern, _ in _PATTERNS)

    def mask_dict(self, data: dict) -> dict:
        """对字典中的所有字符串值递归应用脱敏（用于日志记录 tool args）。"""
        result = {}
        for k, v in data.items():
            if isinstance(v, str):
                result[k] = self.mask(v)
            elif isinstance(v, dict):
                result[k] = self.mask_dict(v)
            elif isinstance(v, list):
                result[k] = [self.mask(i) if isinstance(i, str) else i for i in v]
            else:
                result[k] = v
        return result


# 模块级单例
secrets_mask = SecretsMask()
