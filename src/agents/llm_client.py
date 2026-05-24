# src/agents/llm_client.py
import asyncio
import os
import random
import time
import logging
from typing import List, Dict, AsyncGenerator, Optional
from models.factory import ModelFactory
from models.base import LLMResponseDelta
from utils.config import settings

logger = logging.getLogger(__name__)

# ===== 全局速率控制 =====
# ===== Global rate control =====
_global_rate_lock = asyncio.Lock()
_last_request_time = 0.0
# _MIN_INTERVAL 从 settings 动态读取，可通过 .env 中 LLM_MIN_INTERVAL 调整
# _MIN_INTERVAL is read dynamically from settings, adjustable via LLM_MIN_INTERVAL in .env

# ===== 轻量级速率控制（web_fetch 等辅助调用） =====
# ===== Lightweight rate control (for auxiliary calls like web_fetch) =====
_fast_rate_lock = asyncio.Lock()
_fast_last_request_time = 0.0
# _FAST_MIN_INTERVAL 从 settings 动态读取，可通过 .env 中 LLM_FAST_MIN_INTERVAL 调整
# _FAST_MIN_INTERVAL is read dynamically from settings, adjustable via LLM_FAST_MIN_INTERVAL in .env

# ===== Per-Provider 冷却追踪 =====
# ===== Per-Provider cooldown tracking =====
_provider_cooldowns: Dict[str, float] = {}  # provider -> cooldown_until 时间戳 / cooldown_until timestamp
_provider_last_used: Dict[str, float] = {}  # provider -> 上次使用时间 / last used time
_COOLDOWN_BASE = 30.0  # 429 后基础冷却 30 秒 / base cooldown 30s after 429

# ===== Per-Provider 速率控制 =====
# 独立于全局锁，确保单一 provider 的请求间隔满足其 RPM 限制
# ===== Per-Provider rate control =====
# Independent of global lock, ensure request interval for single provider meets its RPM limit
_provider_rate_locks: Dict[str, asyncio.Lock] = {}
_provider_last_req: Dict[str, float] = {}  # provider -> 上次实际发请求时间

# ===== Per-Provider 连续失败计数 (circuit-breaker) =====
# ===== Per-Provider consecutive failure count (circuit-breaker) =====
_provider_fail_counts: Dict[str, int] = {}  # provider -> 连续失败次数 / consecutive failure count

# ===== 冷却状态持久化 =====
# ===== Cooldown state persistence =====
_COOLDOWN_STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".rooster", "state", "provider_cooldowns.json"
)


def _persist_cooldowns():
    """将冷却和失败计数写入磁盘（供 Guardian 重启后恢复）。"""
    try:
        os.makedirs(os.path.dirname(_COOLDOWN_STATE_FILE), exist_ok=True)
        data = {
            "cooldowns": dict(_provider_cooldowns),
            "fail_counts": dict(_provider_fail_counts),
            "saved_at": time.time(),
        }
        import json

        with open(_COOLDOWN_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception as e:
        logger.warning(f"⚠️ [Pool] 冷却状态持久化失败: {e}")


def _load_cooldowns():
    """启动时从磁盘恢复冷却状态（过期的自动丢弃）。"""
    global _provider_cooldowns, _provider_fail_counts
    try:
        if not os.path.exists(_COOLDOWN_STATE_FILE):
            return
        import json

        with open(_COOLDOWN_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        now = time.time()
        loaded_cooldowns = data.get("cooldowns", {})
        _provider_cooldowns = {k: v for k, v in loaded_cooldowns.items() if v > now}
        _provider_fail_counts = data.get("fail_counts", {})
        if _provider_cooldowns:
            logger.info(f"🔄 [Pool] 从磁盘恢复冷却状态: {list(_provider_cooldowns.keys())}")
    except Exception as e:
        logger.warning(f"⚠️ [Pool] 冷却状态加载失败: {e}")


# 启动时自动恢复
# Auto-restore on startup
_load_cooldowns()


def _get_provider_lock(provider: str) -> asyncio.Lock:
    """懒加载 per-provider 速率锁（asyncio 环境下线程安全）"""  # Lazy-load per-provider rate lock (thread-safe in asyncio)
    if provider not in _provider_rate_locks:
        _provider_rate_locks[provider] = asyncio.Lock()
    return _provider_rate_locks[provider]


def _get_provider_min_interval(provider: str) -> float:
    """每个 provider 的最小请求间隔（秒）"""  # Minimum request interval per provider (seconds)
    if provider == "zhipu":
        return settings.ZHIPU_MIN_INTERVAL
    return settings.LLM_MIN_INTERVAL


# ===== Provider 轮转池 =====
# 所有可用 provider，每次调用选最空闲的
# 按响应速度从快到慢排列；local 作为最后兜底
# ===== Provider rotation pool =====
# All available providers, select the least busy each call
# Ordered by response speed, fastest to slowest; local as final fallback
PROVIDER_POOL = ["mimo", "zhipu", "jiutian", "local"]


def _get_provider_cooldown(provider: str) -> float:
    """获取 provider 剩余冷却时间（秒）"""  # Get remaining provider cooldown time (seconds)
    until = _provider_cooldowns.get(provider, 0)
    remaining = until - time.time()
    return max(0, remaining)


def _set_provider_cooldown(provider: str, seconds: float):
    """设置 provider 冷却"""  # Set provider cooldown
    _provider_cooldowns[provider] = time.time() + seconds
    logger.warning(f"❄️ [Pool] {provider} 进入冷却 {seconds:.0f}s")
    _persist_cooldowns()


def _classify_error_cooldown(error_str: str) -> float:
    """根据错误类型返回基础冷却秒数（自适应退避策略）"""  # Return base cooldown seconds by error type (adaptive backoff strategy)
    e = error_str.lower()
    if "429" in error_str or "rate" in e or "quota" in e:
        return _COOLDOWN_BASE  # 30s — 速率限制 / rate limit
    if "empty content" in e or "empty body" in e:
        return 30.0  # 空响应，短暂冷却后重试
    if any(k in e for k in ("context", "token", "too long", "maximum")):
        return 180.0  # 明确的上下文超限错误 / explicit context overflow error
    if any(k in e for k in ("timeout", "timed out", "connection", "connect")):
        return 20.0  # 网络超时，短冷却 / network timeout, short cooldown
    return 10.0  # 通用错误 / generic error


def _set_provider_cooldown_adaptive(provider: str, error_str: str):
    """自适应冷却：连续失败次数越多冷却倍增 (circuit-breaker 模式)。"""  # Adaptive cooldown: consecutive failures multiply cooldown (circuit-breaker mode)
    _provider_fail_counts[provider] = _provider_fail_counts.get(provider, 0) + 1
    fail_n = _provider_fail_counts[provider]
    base = _classify_error_cooldown(error_str)
    actual = min(base * (2 ** (fail_n - 1)), 300.0)
    _provider_cooldowns[provider] = time.time() + actual
    logger.warning(
        f"❄️ [Circuit] {provider} 连续第{fail_n}次失败，冷却 {actual:.0f}s (base={base:.0f}s, error={error_str[:60]})"
    )
    _persist_cooldowns()


def _reset_provider_fail_count(provider: str):
    """成功调用后重置连续失败计数。"""  # Reset consecutive failure count after successful call
    if _provider_fail_counts.pop(provider, None) is not None:
        logger.debug(f"✅ [Circuit] {provider} 成功，连续失败计数已重置")


class LLMClient:
    """
    LLM 客户端 — 支持 Provider 轮转、Per-Provider 冷却、指数退避。
    多 Key 轮转 + 自适应冷却 + 速率限制。
    """

    def __init__(
        self,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        lightweight: bool = False,
        failover_order: Optional[List[str]] = None,
    ):
        self._lightweight = lightweight  # 必须在 _get_default_model 之前赋值
        self.preferred_provider = provider or ("cloud" if settings.CLOUD_KEY else "zhipu")
        self.provider = self.preferred_provider
        self._internal_client = ModelFactory.get_client(self.provider)
        self.model_name = model or self._get_default_model(self.provider)
        self.failover_order = failover_order  # 自定义故障转移顺序

    def _get_default_model(self, provider: str) -> str:
        """根据提供商获取默认模型名。jiutian 按轻量/重量路径分别选模型。"""
        if provider == "zhipu":
            return settings.ZHIPU_MODEL
        if provider == "zhipu_glm":
            return settings.ZHIPU_GLM_MODEL
        if provider == "openai":
            return settings.OPENAI_MODEL
        if provider == "anthropic":
            return settings.ANTHROPIC_MODEL
        if provider == "kimi":
            return settings.KIMI_MODEL
        if provider == "qwen":
            return settings.QWEN_MODEL
        if provider == "cloud":
            return settings.CLOUD_MODEL
        if provider == "mimo":
            return settings.MIMO_MODEL
        if provider == "jiutian":
            # 轻量路径 (lightweight=True) 选 qwen3.6-35b；重要任务路径选 gpt-oss-120b
            # Lightweight path selects qwen3.6-35b; important task path selects gpt-oss-120b
            return settings.JIUTIAN_MODEL_FAST if self._lightweight else settings.JIUTIAN_MODEL
        return settings.LOCAL_MODEL

    def _build_pipeline(self) -> List[str]:
        """
        构建执行梯队：首选 provider + failover 链。
        结合轮转池，优先使用未冷却的 provider。
        """
        # Build execution pipeline: preferred provider + failover chain
        # Combined with rotation pool, prioritize non-cooling providers
        pipeline = [self.preferred_provider]

        # 优先使用实例自定义的 failover 链，否则使用全局配置
        # Prioritize instance's custom failover chain, otherwise use global config
        source_order = self.failover_order if self.failover_order is not None else settings.LLM_FAILOVER_ORDER

        for p in source_order:
            if p not in pipeline:
                pipeline.append(p)
        # 补充轮转池中不在 pipeline 里的 provider
        # Supplement providers from rotation pool not in pipeline
        for p in PROVIDER_POOL:
            if p not in pipeline:
                pipeline.append(p)
        return pipeline

    async def _wait_rate_limit(self):
        """速率控制 — lightweight 模式用独立限速器，主流程用全局限速器。间隔从 settings 动态读取。"""  # Rate control — lightweight mode uses independent limiter, main flow uses global limiter
        global _last_request_time, _fast_last_request_time
        if self._lightweight:
            async with _fast_rate_lock:
                interval = settings.LLM_FAST_MIN_INTERVAL
                elapsed = time.time() - _fast_last_request_time
                if elapsed < interval:
                    await asyncio.sleep(interval - elapsed)
                _fast_last_request_time = time.time()
        else:
            async with _global_rate_lock:
                interval = settings.LLM_MIN_INTERVAL
                elapsed = time.time() - _last_request_time
                if elapsed < interval:
                    await asyncio.sleep(interval - elapsed)
                _last_request_time = time.time()

    async def _prepare_provider(self, provider: str) -> str:
        """切换到指定 provider 并返回对应的 model 名。"""  # Switch to specified provider and return corresponding model name
        model = self._get_default_model(provider)
        if provider != self.provider:
            self.switch_provider(provider)
        _provider_last_used[provider] = time.time()
        return model

    async def _wait_provider_rate_limit(self, provider: str):
        """Per-provider 速率控制 — 确保同一 provider 的请求间隔不低于其 RPM 阈值。"""  # Per-provider rate control — ensure same-provider request interval meets RPM threshold
        lock = _get_provider_lock(provider)
        async with lock:
            interval = _get_provider_min_interval(provider)
            elapsed = time.time() - _provider_last_req.get(provider, 0)
            if elapsed < interval:
                wait = interval - elapsed
                logger.debug(f"⏱️ [RateLimit] {provider} 等待 {wait:.1f}s (间隔 {interval}s)")
                await asyncio.sleep(wait)
            _provider_last_req[provider] = time.time()

    async def _retry_call(self, provider: str, call_fn):
        """单 provider 重试逻辑（指数退避 + 冷却）。成功返回结果，耗尽重试则 raise。"""  # Single provider retry logic (exponential backoff + cooldown)
        last_exc = None
        for retry_count in range(settings.LLM_FAILOVER_RETRY_MAX):
            try:
                return await call_fn()
            except Exception as e:
                last_exc = e
                _set_provider_cooldown_adaptive(provider, str(e))
                if retry_count == settings.LLM_FAILOVER_RETRY_MAX - 1:
                    logger.error(f"❌ [Client] {provider} 重试耗尽: {e}")
                else:
                    backoff = min(2 ** (retry_count + 1), 16) * (1 + random.uniform(0, 0.5))
                    logger.warning(
                        f"⚠️ [Client] {provider} 重试 {retry_count + 1}/{settings.LLM_FAILOVER_RETRY_MAX}: {e}. 退避 {backoff:.1f}s"
                    )
                    await asyncio.sleep(backoff)
        raise last_exc

    def _iter_pipeline(self):
        """遍历 pipeline，跳过冷却中的 provider。"""  # Iterate pipeline, skip providers in cooldown
        for p in self._build_pipeline():
            if _get_provider_cooldown(p) > 0:
                logger.debug(f"⏭️ [Pool] {p} 冷却中，跳过")
                continue
            yield p

    @staticmethod
    def _estimate_total_chars(messages: List[Dict]) -> int:
        """估算 messages 总字符数（用于上下文预路由）。"""  # Estimate total characters in messages (for context pre-routing)
        return sum(len(str(m.get("content") or "")) for m in messages)

    def _iter_pipeline_for(self, messages: Optional[List[Dict]] = None):
        """上下文感知的 pipeline 遍历：跳过冷却中或上下文超限的 provider。"""  # Context-aware pipeline iteration: skip providers in cooldown or with context overflow
        total_chars = self._estimate_total_chars(messages) if messages else 0
        for p in self._build_pipeline():
            if _get_provider_cooldown(p) > 0:
                logger.debug(f"⏭️ [Pool] {p} 冷却中，跳过")
                continue
            limit = settings.PROVIDER_CONTEXT_LIMITS.get(p, 0)
            if limit > 0 and total_chars > limit:
                logger.warning(f"⏭️ [Pool] {p} 预测上下文超限 ({total_chars:,} chars > {limit:,})，直接跳过")
                continue
            yield p

    async def chat_stream(self, messages: List[Dict[str, str]], **kwargs) -> AsyncGenerator[LLMResponseDelta, None]:
        """流式对话 — 支持 Provider 轮转 + Per-Provider 冷却"""  # Streaming chat — supports Provider rotation + Per-Provider cooldown
        await self._wait_rate_limit()
        model = kwargs.pop("model", self.model_name)

        if not settings.LLM_FAILOVER_ENABLED:
            async for delta in self._internal_client.chat_stream(model, messages, **kwargs):
                yield delta
            return

        last_exc = None
        committed = False
        for current_p in self._iter_pipeline_for(messages):
            try:
                await self._wait_provider_rate_limit(current_p)
                model = await self._prepare_provider(current_p)

                # Strip reasoning_content from assistant messages when sending to providers
                # that don't support it (everyone except MiMo).  MiMo *requires* it to be
                # echoed back in thinking mode; other providers reject it with 400.
                if current_p == "mimo":
                    send_messages = messages
                else:
                    send_messages = [{k: v for k, v in m.items() if k != "reasoning_content"} for m in messages]

                async def _do_stream(msgs=send_messages):
                    nonlocal committed
                    yielded_any = False
                    async for delta in self._internal_client.chat_stream(model, msgs, **kwargs):
                        if delta.content or delta.tool_calls:
                            yielded_any = True
                            committed = True
                        yield delta
                    if not yielded_any and not committed:
                        raise Exception(f"Empty content from {current_p} (model: {model})")

                async for delta in _do_stream():
                    yield delta
                _reset_provider_fail_count(current_p)
                return
            except Exception as e:
                last_exc = e
                if committed:
                    logger.error(f"❌ [Client] {current_p} 在交付内容后失败: {e}")
                    raise
                err_str = str(e)
                _set_provider_cooldown_adaptive(current_p, err_str)
                logger.error(f"❌ [Client] {current_p} 崩溃: {e}")

        if committed:
            return
        if last_exc is None:
            # _iter_pipeline 一个 provider 都没有产出（全部在冷却中），构造明确异常
            # _iter_pipeline yielded no providers (all in cooldown), construct explicit exception
            all_providers = self._build_pipeline()
            cooldown_info = {
                p: f"{_get_provider_cooldown(p):.1f}s" for p in all_providers if _get_provider_cooldown(p) > 0
            }
            logger.error(f"🚫 [Client] 所有 Provider 均在冷却中，无法发起请求。冷却状态: {cooldown_info}")
            raise RuntimeError(f"所有可用的 AI 大模型均在冷却或网络超时中，请稍等片刻再试。等待状态: {cooldown_info}")
        logger.error(f"🚫 [Client] 所有 Provider 折损。最后异常: {last_exc}")
        raise last_exc

    async def chat_non_stream(self, messages: List[Dict[str, str]], **kwargs) -> LLMResponseDelta:
        """非流式对话 — 支持 Provider 轮转 + Per-Provider 冷却"""  # Non-streaming chat — supports Provider rotation + Per-Provider cooldown
        await self._wait_rate_limit()
        model = kwargs.pop("model", self.model_name)

        if not settings.LLM_FAILOVER_ENABLED:
            return await self._internal_client.chat_non_stream(model, messages, **kwargs)

        last_exc = None
        for current_p in self._iter_pipeline_for(messages):
            try:
                await self._wait_provider_rate_limit(current_p)
                model = await self._prepare_provider(current_p)
                send_messages = (
                    messages
                    if current_p == "mimo"
                    else [{k: v for k, v in m.items() if k != "reasoning_content"} for m in messages]
                )
                result = await self._retry_call(
                    current_p,
                    lambda _m=model, _msgs=send_messages, _kw=dict(kwargs): self._internal_client.chat_non_stream(
                        _m, _msgs, **_kw
                    ),
                )
                _reset_provider_fail_count(current_p)
                return result
            except Exception as e:
                last_exc = e
                _set_provider_cooldown_adaptive(current_p, str(e))
                logger.error(f"❌ [Client] {current_p} 崩溃: {e}")

        logger.error(f"🚫 [Client] 所有 Provider 折损。最后异常: {last_exc}")
        if last_exc is None:
            all_providers = self._build_pipeline()
            cooldown_info = {
                p: f"{_get_provider_cooldown(p):.1f}s" for p in all_providers if _get_provider_cooldown(p) > 0
            }
            raise RuntimeError(f"所有可用的 AI 大模型均在冷却或网络超时中，请稍等片刻再试。等待状态: {cooldown_info}")
        raise last_exc

    async def close(self):
        await self._internal_client.close()

    def switch_provider(self, provider: str):
        if provider != self.provider:
            logger.info(f"🔄 [Client] 引擎重定向: {self.provider} -> {provider}")
            self.provider = provider
            self._internal_client = ModelFactory.get_client(provider)
            self.model_name = self._get_default_model(provider)
