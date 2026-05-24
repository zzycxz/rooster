# src/models/factory.py
import hashlib

from .openai_adapter import OpenAILikeClient
from .anthropic_adapter import AnthropicAdapter
from utils.config import settings


class ModelFactory:
    """
    模型工厂类。
    注册表实时从 settings 读取，不缓存，支持运行时配置变更。
    客户端实例按 provider:url:key 缓存，密钥变化时自动重建。

    Model factory class.
    Registry reads from settings in real-time, no caching, supports runtime config changes.
    Client instances are cached by provider:url:key, auto-rebuilt on key change.
    """

    _instances = {}

    @classmethod
    def _get_registry(cls) -> dict:
        """实时从 settings 构建注册表，不缓存。"""  # Build registry from settings in real-time, no caching
        return {
            "local": {
                "url": settings.LOCAL_URL,
                "key": settings.LOCAL_KEY,
                "default_model": settings.LOCAL_MODEL,
            },
            "cloud": {
                "url": settings.CLOUD_URL,
                "key": settings.CLOUD_KEY,
                "default_model": settings.CLOUD_MODEL,
            },
            "zhipu": {
                "url": settings.ZHIPU_URL,
                "key": settings.ZHIPU_KEY,
                "default_model": settings.ZHIPU_MODEL,
            },
            "zhipu_glm": {
                "url": settings.ZHIPU_GLM_URL,
                "key": settings.ZHIPU_GLM_KEY,
                "default_model": settings.ZHIPU_GLM_MODEL,
            },
            "openai": {
                "url": settings.OPENAI_URL,
                "key": settings.OPENAI_KEY,
                "default_model": settings.OPENAI_MODEL,
            },
            "anthropic": {
                "url": settings.ANTHROPIC_URL,
                "key": settings.ANTHROPIC_KEY,
                "default_model": settings.ANTHROPIC_MODEL,
            },
            "kimi": {
                "url": settings.KIMI_URL,
                "key": settings.KIMI_KEY,
                "default_model": settings.KIMI_MODEL,
            },
            "qwen": {
                "url": settings.QWEN_URL,
                "key": settings.QWEN_KEY,
                "default_model": settings.QWEN_MODEL,
            },
            "jiutian": {
                "url": settings.JIUTIAN_URL,
                "key": settings.JIUTIAN_KEY,
                "default_model": settings.JIUTIAN_MODEL,
            },
            "mimo": {
                "url": settings.MIMO_URL,
                "key": settings.MIMO_KEY,
                "default_model": settings.MIMO_MODEL,
            },
        }

    @classmethod
    def get_client(cls, provider: str = "local"):
        """获取客户端单例（按 provider:url:key 缓存，密钥变化自动重建）"""  # Get client singleton (cached by provider:url:key, auto-rebuild on key change)
        registry = cls._get_registry()
        if provider not in registry:
            provider = "local"
        config = registry[provider]
        cache_key = f"{provider}:{config['url']}:{hashlib.sha256(config['key'].encode()).hexdigest()[:12]}"
        if cache_key not in cls._instances:
            if provider == "anthropic":
                cls._instances[cache_key] = AnthropicAdapter(
                    base_url=config["url"],
                    api_key=config["key"],
                )
            else:
                cls._instances[cache_key] = OpenAILikeClient(
                    base_url=config["url"],
                    api_key=config["key"],
                )
        return cls._instances[cache_key]

    @classmethod
    def get_default_model(cls, provider: str):
        """获取指定引擎的默认模型名称"""  # Get default model name for the specified provider
        return cls._get_registry().get(provider, {}).get("default_model", "")

    @classmethod
    def clear_cache(cls):
        """
        清空客户端实例缓存。
        代理切换后调用，下次 get_client() 会用最新 os.environ 重建 httpx 客户端。

        Clear client instance cache.
        Called after proxy switch; next get_client() rebuilds httpx client with latest os.environ.
        """
        cls._instances.clear()
