import os
import logging
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings", tags=["Settings"])


class ProxyConfig(BaseModel):
    enabled: bool
    url: Optional[str] = None


@router.get("/proxy")
async def get_proxy():
    """获取当前的全局代理状态 / Get current global proxy state"""
    enabled = os.environ.get("SYSTEM_PROXY_ENABLED", "false").lower() == "true"
    url = os.environ.get("SYSTEM_PROXY_URL", "http://127.0.0.1:7890")
    return {"enabled": enabled, "url": url}


@router.post("/proxy")
async def set_proxy(config: ProxyConfig):
    """设置全局代理状态并应用 / Set global proxy state and apply"""
    from models.factory import ModelFactory

    os.environ["SYSTEM_PROXY_ENABLED"] = "true" if config.enabled else "false"
    if config.url is not None:
        os.environ["SYSTEM_PROXY_URL"] = config.url

    # 尝试持久化到 .env.local / Attempt to persist to .env.local
    try:
        from dotenv import set_key

        env_file = ".env.local"
        if not os.path.exists(env_file):
            open(env_file, "a").close()  # Create if not exists

        set_key(env_file, "SYSTEM_PROXY_ENABLED", os.environ["SYSTEM_PROXY_ENABLED"])
        if config.url is not None:
            set_key(env_file, "SYSTEM_PROXY_URL", os.environ["SYSTEM_PROXY_URL"])
    except ImportError:
        logger.warning("dotenv.set_key not available, could not persist proxy settings.")
    except Exception as e:
        logger.error(f"Failed to persist proxy settings: {e}")

    # 清空模型工厂的旧客户端实例，强制重新加载代理配置
    # Clear old client instances in ModelFactory, forcing proxy config reload
    await ModelFactory.clear_instances()

    logger.info(f"🔄 [Proxy API] Global proxy updated: enabled={config.enabled}, url={config.url}")
    return {"status": "success", "enabled": config.enabled, "url": os.environ.get("SYSTEM_PROXY_URL")}
