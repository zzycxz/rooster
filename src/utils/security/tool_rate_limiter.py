"""
src/utils/security/tool_rate_limiter.py

工具级异步 Token Bucket 限速器。
防止 LLM 在循环中无限制调用网络/邮件等外部资源型工具。

设计原则：
- 只对有外部副作用的工具（网络、邮件、文件批量）施加默认限速
- 纯本地工具（搜索内存、截图等）不限速
- 默认配置非常宽松，用户通过 TOOL_RATE_LIMITS_JSON 收紧
- 超限时返回 (False, wait_seconds)，由调用方决定是否等待或跳过
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# 内置保守默认限速（每分钟调用次数上限）
# 格式：tool_name -> (capacity, refill_rate)
#   capacity：令牌桶容量（burst）
#   refill_rate：每秒补充令牌数
_DEFAULT_LIMITS: Dict[str, Tuple[int, float]] = {
    # 邮件：每分钟最多 2 封（防止 spam loop）
    "email_send": (2, 2 / 60),
    # 网页抓取：每 10 秒最多 5 次，burst 10
    "web_fetch": (10, 0.5),
    "batch_web_fetch": (3, 3 / 60),
    # 浏览器导航：每 10 秒最多 8 次，burst 15
    "browser_nav": (15, 0.8),
    # 系统信息扫描：每分钟最多 3 次
    "system_discovery": (3, 3 / 60),
    # 子代理生成：每分钟最多 5 个（防止递归爆炸）
    "subagent_spawn": (5, 5 / 60),
    # HF 模型列表查询：每分钟最多 5 次（API 有限速）
    "hf_model_list": (5, 5 / 60),
}


@dataclass
class _Bucket:
    """单个工具的令牌桶状态。"""

    capacity: int
    refill_rate: float  # tokens per second
    tokens: float = field(init=False)
    last_refill: float = field(init=False)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, compare=False, repr=False)

    def __post_init__(self) -> None:
        self.tokens = float(self.capacity)
        self.last_refill = time.monotonic()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now

    def try_consume(self) -> Tuple[bool, float]:
        """尝试消耗一个令牌。返回 (allowed, wait_seconds)。"""
        self._refill()
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True, 0.0
        wait = (1.0 - self.tokens) / self.refill_rate
        return False, round(wait, 1)


class ToolRateLimiter:
    """
    工具限速器（单例）。
    使用异步 Token Bucket 算法，支持 burst 和平滑补充。
    """

    _instance: Optional["ToolRateLimiter"] = None

    def __init__(self, limits: Dict[str, Tuple[int, float]]):
        self._limits = limits
        self._buckets: Dict[str, _Bucket] = {}
        self._lock = asyncio.Lock()

    @classmethod
    def get(cls) -> "ToolRateLimiter":
        """懒加载单例，从 settings 合并用户配置。"""
        if cls._instance is None:
            limits = dict(_DEFAULT_LIMITS)
            try:
                from utils.config import settings

                raw = getattr(settings, "TOOL_RATE_LIMITS_JSON", "").strip()
                if raw:
                    user_limits = json.loads(raw)
                    for name, cfg in user_limits.items():
                        if isinstance(cfg, list) and len(cfg) == 2:
                            limits[name] = (int(cfg[0]), float(cfg[1]))
                        elif isinstance(cfg, dict):
                            limits[name] = (int(cfg.get("capacity", 10)), float(cfg.get("refill_rate", 1.0)))
            except Exception as e:
                logger.warning(f"[ToolRateLimiter] 解析 TOOL_RATE_LIMITS_JSON 失败: {e}")
            cls._instance = cls(limits)
            logger.info(f"[ToolRateLimiter] 初始化完成，限速工具: {list(limits.keys())}")
        return cls._instance

    async def check_and_consume(self, tool_name: str) -> Tuple[bool, float]:
        """
        检查并消耗一个令牌。
        返回 (allowed, wait_seconds)。
        - allowed=True：可以执行
        - allowed=False：超出限速，wait_seconds 为建议等待时间
        """
        if tool_name not in self._limits:
            return True, 0.0  # 未配置限速的工具无限制

        async with self._lock:
            if tool_name not in self._buckets:
                cap, rate = self._limits[tool_name]
                self._buckets[tool_name] = _Bucket(capacity=cap, refill_rate=rate)
            bucket = self._buckets[tool_name]

        async with bucket.lock:
            return bucket.try_consume()

    def is_rate_limited(self, tool_name: str) -> bool:
        """同步快速检查（不消耗令牌，仅查询状态）。"""
        if tool_name not in self._buckets:
            return False
        bucket = self._buckets[tool_name]
        bucket._refill()
        return bucket.tokens < 1.0
