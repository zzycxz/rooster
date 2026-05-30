"""LLM provider configuration — model URLs, keys, names, failover, rate limits."""

from typing import Dict, Optional
from utils.config._base import (
    _env,
    _env_int,
    _env_float,
    _env_bool,
    _env_list,
)


class ProvidersConfig:
    # --- Local ---
    LOCAL_URL: str = _env("LOCAL_URL", "http://127.0.0.1:9090/v1")
    LOCAL_KEY: str = _env("LOCAL_KEY", "llama.cpp")
    LOCAL_MODEL: str = _env("LOCAL_MODEL", "")

    # --- Ollama (local model server management) ---
    OLLAMA_URL: str = _env("OLLAMA_URL", "http://localhost:11434")

    # --- Cloud ---
    CLOUD_URL: str = _env("CLOUD_URL", "")
    CLOUD_KEY: str = _env("CLOUD_KEY", "")
    CLOUD_MODEL: str = _env("CLOUD_MODEL", "")

    # --- Zhipu AI (CodingPlan — 当前使用的编程增强版) ---
    # --- Zhipu AI (CodingPlan — current coding-enhanced version) ---
    ZHIPU_URL: str = _env("ZHIPU_URL", "https://open.bigmodel.cn/api/coding/paas/v4")
    ZHIPU_KEY: str = _env("ZHIPU_KEY", "")
    ZHIPU_MODEL: str = _env("ZHIPU_MODEL", "GLM5.1")

    # --- Zhipu AI (Standard API — 传统智谱 API) ---
    # --- Zhipu AI (Standard API — traditional Zhipu API) ---
    ZHIPU_GLM_URL: str = _env("ZHIPU_GLM_URL", "https://open.bigmodel.cn/api/paas/v4")
    ZHIPU_GLM_KEY: str = _env("ZHIPU_GLM_KEY", "")
    ZHIPU_GLM_MODEL: str = _env("ZHIPU_GLM_MODEL", "GLM5.1")

    # --- OpenAI ---
    OPENAI_URL: str = _env("OPENAI_URL", "https://api.openai.com/v1")
    OPENAI_KEY: str = _env("OPENAI_KEY", "")
    OPENAI_MODEL: str = _env("OPENAI_MODEL", "gpt-4o")

    # --- Anthropic Claude (native Messages API) ---
    ANTHROPIC_URL: str = _env("ANTHROPIC_URL", "https://api.anthropic.com")
    ANTHROPIC_KEY: str = _env("ANTHROPIC_KEY", "")
    ANTHROPIC_MODEL: str = _env("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")

    # --- Kimi (Moonshot AI) ---
    KIMI_URL: str = _env("KIMI_URL", "https://api.moonshot.cn/v1")
    KIMI_KEY: str = _env("KIMI_KEY", "")
    KIMI_MODEL: str = _env("KIMI_MODEL", "moonshot-v1-8k")

    # --- Qwen (通义千问 / DashScope) ---
    # --- Qwen (Tongyi Qianwen / DashScope) ---
    QWEN_URL: str = _env("QWEN_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    QWEN_KEY: str = _env("QWEN_KEY", "")
    QWEN_MODEL: str = _env("QWEN_MODEL", "qwen-plus")

    # --- Jiutian ---
    JIUTIAN_URL: str = _env("JIUTIAN_URL", "https://jiutian.10086.cn/largemodel/moma/api/v3")
    JIUTIAN_KEY: str = _env("JIUTIAN_KEY", "")
    JIUTIAN_MODEL: str = _env("JIUTIAN_MODEL", "openai/gpt-oss-120b")
    JIUTIAN_MODEL_FAST: str = _env("JIUTIAN_MODEL_FAST", "qwen/qwen3.6-35b")

    # --- Xiaomi MiMo ---
    MIMO_URL: str = _env("MIMO_URL", "https://api.xiaomimimo.com/v1")
    MIMO_KEY: str = _env("MIMO_KEY", "")
    MIMO_MODEL: str = _env("MIMO_MODEL", "mimo-v2.5")
    MIMO_THINKING_ENABLED: bool = _env_bool("MIMO_THINKING_ENABLED", False)

    # --- Role-specific model overrides ---
    STRATEGIST_MODEL_MODE: str = _env("STRATEGIST_MODEL_MODE", "zhipu")
    STRATEGIST_MODEL_NAME: str = _env("STRATEGIST_MODEL_NAME", "")

    EXECUTOR_MODEL_MODE: str = _env("EXECUTOR_MODEL_MODE", "jiutian")
    EXECUTOR_MODEL_NAME: str = _env("EXECUTOR_MODEL_NAME", "")

    AUDITOR_MODEL_MODE: str = _env("AUDITOR_MODEL_MODE", "jiutian")
    AUDITOR_MODEL_NAME: str = _env("AUDITOR_MODEL_NAME", "")
    AUDITOR_VISION_MODEL: str = _env("AUDITOR_VISION_MODEL", "openai/gpt-oss-120b")
    AUDITOR_TEXT_MODEL: str = _env("AUDITOR_TEXT_MODEL", "qwen/qwen3.6-35b")

    ROUTER_MODEL_MODE: str = _env("ROUTER_MODEL_MODE", "zhipu")
    ROUTER_MODEL_NAME: str = _env("ROUTER_MODEL_NAME", "")

    ENABLE_REFRAMER: bool = _env_bool("ENABLE_REFRAMER", False)
    REFRAMER_MODEL_MODE: str = _env("REFRAMER_MODEL_MODE", "local")
    REFRAMER_MODEL_NAME: str = _env("REFRAMER_MODEL_NAME", _env("LOCAL_MODEL", ""))

    SOLO_MODEL_MODE: str = _env("SOLO_MODEL_MODE", "jiutian")
    SOLO_MODEL_NAME: str = _env("SOLO_MODEL_NAME", "openai/gpt-oss-120b")
    SOLO_FAILOVER_ORDER: list = _env_list("SOLO_FAILOVER_ORDER", "jiutian,zhipu,mimo,local")

    # --- Fast model (lightweight for summarization) ---
    FAST_MODEL_PROVIDER: str = _env("FAST_MODEL_PROVIDER", "jiutian")
    FAST_MODEL_NAME: str = _env("FAST_MODEL_NAME", "qwen/qwen3.6-35b")

    # --- Ollama local routing ---
    OLLAMA_DOMAINS: list = _env_list("OLLAMA_DOMAINS", "recon,craft")

    # --- Local lightweight domains ---
    # Comma-separated keywords; when a SOLO message matches any keyword AND no explicit
    # model override is set, traffic is automatically routed to the local provider.
    LOCAL_LIGHTWEIGHT_DOMAINS: list = _env_list("LOCAL_LIGHTWEIGHT_DOMAINS", "")

    # --- Failover ---
    LLM_FAILOVER_ENABLED: bool = _env_bool("LLM_FAILOVER_ENABLED", True)
    LLM_FAILOVER_ORDER: list = _env_list("LLM_FAILOVER_ORDER", "mimo,zhipu,jiutian,local")
    LLM_FAILOVER_RETRY_MAX: int = _env_int("LLM_FAILOVER_RETRY_MAX", 2)
    # Keep fallback exception-driven by default.  When disabled, providers are not
    # skipped merely because the prompt looks large; they are tried in order and
    # fallback only after a real provider error.
    LLM_PREVENTIVE_CONTEXT_ROUTING: bool = _env_bool("LLM_PREVENTIVE_CONTEXT_ROUTING", False)

    # --- Rate limiting ---
    LLM_MIN_INTERVAL: float = _env_float("LLM_MIN_INTERVAL", 1.5)
    LLM_FAST_MIN_INTERVAL: float = _env_float("LLM_FAST_MIN_INTERVAL", 1.0)
    ZHIPU_MIN_INTERVAL: float = _env_float("ZHIPU_MIN_INTERVAL", 6.0)
    LLM_GLOBAL_MAX_CONCURRENT: int = _env_int("LLM_GLOBAL_MAX_CONCURRENT", 6)
    LLM_PROVIDER_MAX_CONCURRENT_DEFAULT: int = _env_int("LLM_PROVIDER_MAX_CONCURRENT_DEFAULT", 2)

    @property
    def LLM_PROVIDER_MAX_CONCURRENT(self) -> Dict[str, int]:
        import os

        raw = os.environ.get(
            "LLM_PROVIDER_MAX_CONCURRENT",
            "zhipu:1,zhipu_glm:1,jiutian:2,mimo:2,openai:2,anthropic:2,kimi:2,qwen:2,cloud:2,local:1",
        )
        result: Dict[str, int] = {}
        for item in raw.split(","):
            if ":" not in item:
                continue
            provider, value = item.strip().split(":", 1)
            try:
                result[provider.strip()] = max(1, int(value.strip()))
            except ValueError:
                pass
        return result

    @property
    def PROVIDER_CONTEXT_LIMITS(self) -> Dict[str, int]:
        import os

        raw = os.environ.get("PROVIDER_CONTEXT_LIMITS", "mimo:500000")
        result: Dict[str, int] = {}
        for item in raw.split(","):
            if ":" in item:
                p, v = item.strip().split(":", 1)
                try:
                    result[p.strip()] = int(v.strip())
                except ValueError:
                    pass
        return result

    @property
    def SYSTEM_PROXY_ENABLED(self) -> bool:
        import os

        return os.environ.get("SYSTEM_PROXY_ENABLED", "false").lower() == "true"

    @property
    def SYSTEM_PROXY_URL(self) -> Optional[str]:
        import os

        return os.environ.get("SYSTEM_PROXY_URL")

    @property
    def ENABLE_REGIONAL_PROXY(self) -> bool:
        import os

        if "SYSTEM_PROXY_ENABLED" in os.environ:
            return self.SYSTEM_PROXY_ENABLED
        return os.environ.get("ENABLE_REGIONAL_PROXY", "True").lower() == "true"

    @property
    def HTTP_PROXY(self) -> Optional[str]:
        import os

        if self.ENABLE_REGIONAL_PROXY:
            return self.SYSTEM_PROXY_URL or os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
        return None

    @property
    def HTTPS_PROXY(self) -> Optional[str]:
        import os

        if self.ENABLE_REGIONAL_PROXY:
            return self.SYSTEM_PROXY_URL or os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        return None
