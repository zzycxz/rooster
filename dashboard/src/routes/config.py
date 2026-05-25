"""Config API routes — .env.local read/write, provider listing, security status."""

import os
import asyncio
import logging
from typing import Dict, Any

from fastapi import APIRouter, Body, HTTPException

from gateway.auth import mask_secret
from gateway.security import validate_config_keys, validate_config_values
from utils.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/config", tags=["config"])

# Keys that should be masked when returned to the frontend
MASK_KEYS = frozenset(
    {
        "ZHIPU_KEY",
        "ZHIPU_GLM_KEY",
        "OPENAI_KEY",
        "ANTHROPIC_KEY",
        "KIMI_KEY",
        "QWEN_KEY",
        "CLOUD_KEY",
        "MIMO_KEY",
        "JIUTIAN_KEY",
        "EMBEDDING_KEY",
        "TAVILY_API_KEY",
        "E2B_API_KEY",
        "LOCAL_KEY",
        "GATEWAY_API_KEY",
        "WEBHOOK_HMAC_SECRET",
        "FEISHU_APP_SECRET",
        "FEISHU_USER_OPEN_ID",
        "ARIA2_TOKEN",
    }
)


def _get_env_local_path() -> str:
    # 动态探测 .env.local 路径以适应不同的目录结构 (Dynamic check to support different layout structures)
    d4 = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    if os.path.exists(os.path.join(d4, ".env.local")):
        return os.path.join(d4, ".env.local")
    d5 = os.path.dirname(d4)
    if os.path.exists(os.path.join(d5, ".env.local")):
        return os.path.join(d5, ".env.local")
    if os.path.exists(os.path.join(os.getcwd(), ".env.local")):
        return os.path.join(os.getcwd(), ".env.local")
    return os.path.join(d4, ".env.local")


def _mask_env_dict(data: Dict[str, Any]) -> Dict[str, Any]:
    masked = {}
    for k, v in data.items():
        if k in MASK_KEYS and isinstance(v, str) and v:
            masked[k] = mask_secret(v)
        else:
            masked[k] = v
    return masked


async def handle_config_save(data: Dict[str, Any]) -> Dict[str, Any]:
    """Write key=value pairs into .env.local (append/update, never delete)."""
    env_path = _get_env_local_path()
    try:
        existing: Dict[str, str] = {}
        lines: list = []
        if os.path.exists(env_path):
            with open(env_path, encoding="utf-8") as f:
                lines = f.readlines()
            for line in lines:
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and "=" in stripped:
                    k, _, v = stripped.partition("=")
                    existing[k.strip()] = v.strip()

        for k, v in data.items():
            existing[k] = str(v)

        # Smart Role Allocation
        available = []
        keys_mapping = {
            "OPENAI_KEY": "openai",
            "ANTHROPIC_KEY": "anthropic",
            "CLOUD_KEY": "cloud",
            "KIMI_KEY": "kimi",
            "QWEN_KEY": "qwen",
            "JIUTIAN_KEY": "jiutian",
            "MIMO_KEY": "mimo",
            "ZHIPU_GLM_KEY": "zhipu_glm",
            "ZHIPU_KEY": "zhipu",
            "LOCAL_MODEL": "local",
        }
        for k, name in keys_mapping.items():
            if existing.get(k):
                available.append(name)

        if available:
            roles = [
                "ROUTER_MODEL_MODE",
                "STRATEGIST_MODEL_MODE",
                "EXECUTOR_MODEL_MODE",
                "AUDITOR_MODEL_MODE",
                "SOLO_MODEL_MODE",
            ]
            is_explicit_any = any(data.get(r) for r in roles)

            heavy_priority = [
                "openai",
                "anthropic",
                "cloud",
                "qwen",
                "kimi",
                "jiutian",
                "zhipu_glm",
                "zhipu",
                "mimo",
                "local",
            ]
            fast_priority = [
                "zhipu",
                "local",
                "mimo",
                "qwen",
                "kimi",
                "jiutian",
                "openai",
                "cloud",
                "anthropic",
                "zhipu_glm",
            ]

            if not is_explicit_any:
                if len(available) == 1:
                    single_mode = available[0]
                    for r in roles:
                        data[r] = single_mode
                        existing[r] = single_mode
                else:
                    heavy_model = next((m for m in heavy_priority if m in available), available[0])
                    fast_model = next((m for m in fast_priority if m in available), available[0])
                    for r in ["ROUTER_MODEL_MODE", "STRATEGIST_MODEL_MODE", "AUDITOR_MODEL_MODE", "SOLO_MODEL_MODE"]:
                        data[r] = heavy_model
                        existing[r] = heavy_model
                    data["EXECUTOR_MODEL_MODE"] = fast_model
                    existing["EXECUTOR_MODEL_MODE"] = fast_model
            else:
                if len(available) == 1:
                    single_mode = available[0]
                    for r in roles:
                        if not data.get(r) and not existing.get(r):
                            data[r] = single_mode
                            existing[r] = single_mode
                else:
                    heavy_model = next((m for m in heavy_priority if m in available), available[0])
                    fast_model = next((m for m in fast_priority if m in available), available[0])
                    for r in ["ROUTER_MODEL_MODE", "STRATEGIST_MODEL_MODE", "AUDITOR_MODEL_MODE", "SOLO_MODEL_MODE"]:
                        if not data.get(r) and not existing.get(r):
                            data[r] = heavy_model
                            existing[r] = heavy_model
                    if not data.get("EXECUTOR_MODEL_MODE") and not existing.get("EXECUTOR_MODEL_MODE"):
                        data["EXECUTOR_MODEL_MODE"] = fast_model
                        existing["EXECUTOR_MODEL_MODE"] = fast_model

        if existing.get("EXECUTOR_MODEL_MODE") and not data.get("EXECUTOR_MODEL_NAME"):
            data["EXECUTOR_MODEL_NAME"] = existing["EXECUTOR_MODEL_MODE"]
            existing["EXECUTOR_MODEL_NAME"] = existing["EXECUTOR_MODEL_MODE"]

        written_keys: set = set()
        new_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                k = stripped.partition("=")[0].strip()
                if k in data:
                    new_lines.append(f"{k}={data[k]}\n")
                    written_keys.add(k)
                    continue
            new_lines.append(line)

        for k, v in data.items():
            if k not in written_keys:
                new_lines.append(f"{k}={v}\n")

        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)

        # Auto-reload into os.environ so changes take effect immediately
        from dotenv import load_dotenv

        load_dotenv(env_path, override=True)
        try:
            from utils.security.path_guard import PathGuard

            PathGuard.refresh()
        except Exception:
            pass

        logger.info(f"[config.save] Updated .env.local: {list(data.keys())}")
        masked = {k: (mask_secret(str(v)) if k in MASK_KEYS else v) for k, v in data.items()}

        # Schedule auto-restart so new config takes effect (guardian will restart child)
        async def _delayed_restart():
            await asyncio.sleep(1.5)
            logger.info("[config.save] Auto-restarting to apply config changes...")
            os._exit(0)

        asyncio.create_task(_delayed_restart())
        return {"ok": True, "saved": list(data.keys()), "masked": masked}
    except OSError as exc:
        logger.error(f"[config.save] Failed to write .env.local: {exc}")
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        logger.exception("[config.save] Unexpected error writing .env.local")
        return {"ok": False, "error": str(exc)}


def get_env_local_path() -> str:
    return _get_env_local_path()


@router.post("/save")
async def api_config_save(data: Dict[str, Any] = Body(...)):
    rejected = validate_config_keys(data)
    if rejected:
        raise HTTPException(status_code=400, detail=f"Keys not allowed: {rejected}")
    oversized = validate_config_values(data)
    if oversized:
        raise HTTPException(status_code=400, detail=f"Values too long for keys: {oversized}")
    return await handle_config_save(data)


@router.post("/reload")
async def api_config_reload():
    """Hot-reload .env and .env.local into os.environ without restart."""
    from dotenv import load_dotenv

    env_path = os.path.join(os.path.dirname(_get_env_local_path()), ".env")
    env_local_path = _get_env_local_path()
    reloaded = []
    if os.path.exists(env_path):
        load_dotenv(env_path, override=True)
        reloaded.append(".env")
    if os.path.exists(env_local_path):
        load_dotenv(env_local_path, override=True)
        reloaded.append(".env.local")
    # Refresh PathGuard to pick up new ALLOWED_PATHS
    try:
        from utils.security.path_guard import PathGuard

        PathGuard.refresh()
        reloaded.append("PathGuard")
    except Exception:
        pass
    return {"ok": True, "reloaded": reloaded}


@router.get("/yaml")
async def api_config_yaml():
    """Return .env config with secrets masked."""
    base_dir = os.path.dirname(_get_env_local_path())
    env_path = os.path.join(base_dir, ".env")

    if not os.path.exists(env_path):
        return {"ok": False, "error": ".env not found"}

    config = {}
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" not in stripped:
                continue
            k, _, v = stripped.partition("=")
            k = k.strip()
            v = v.strip()
            if k in MASK_KEYS and v:
                v = mask_secret(v)
            config[k] = v

    return {"ok": True, "config": config}


@router.get("/models")
async def api_config_models():
    _providers = [
        ("mimo", "MIMO_KEY", "MiMo"),
        ("zhipu", "ZHIPU_KEY", "智谱 CodingPlan"),
        ("zhipu_glm", "ZHIPU_GLM_KEY", "智谱 GLM 标准版"),
        ("openai", "OPENAI_KEY", "OpenAI"),
        ("anthropic", "ANTHROPIC_KEY", "Anthropic Claude"),
        ("kimi", "KIMI_KEY", "Kimi 月之暗面"),
        ("qwen", "QWEN_KEY", "通义千问"),
        ("jiutian", "JIUTIAN_KEY", "Jiutian 九天"),
        ("cloud", "CLOUD_KEY", "Cloud LLM"),
        ("local", "LOCAL_MODEL", "Local llama.cpp"),
    ]
    providers = [
        {"id": pid, "label": label, "configured": bool(getattr(settings, key, ""))} for pid, key, label in _providers
    ]
    return {"providers": providers, "default": settings.SOLO_MODEL_MODE}


@router.get("/masked")
async def api_config_masked():
    """Return effective config for dashboard setup form.

    Reads .env.local overrides first, then fills gaps from .env fallback,
    so the form always shows what's actually running.
    """
    base_dir = os.path.dirname(_get_env_local_path())

    def _parse_env_file(path: str) -> Dict[str, str]:
        data: Dict[str, str] = {}
        if not os.path.exists(path):
            return data
        with open(path, encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and "=" in stripped:
                    k, _, v = stripped.partition("=")
                    val = v.strip()
                    if val:  # skip empty values
                        data[k.strip()] = val
        return data

    # .env as base (skip empties), .env.local overrides
    env_data = _parse_env_file(os.path.join(base_dir, ".env"))
    env_data.update(_parse_env_file(os.path.join(base_dir, ".env.local")))
    return {"env": _mask_env_dict(env_data)}


@router.get("/test")
async def api_config_test():
    """Test connectivity to each configured LLM provider with a lightweight API call."""
    import httpx as _httpx

    _providers = [
        ("mimo", "MIMO_KEY", "MIMO_URL", "MIMO_MODEL", "https://api.xiaomimimo.com/v1"),
        ("zhipu", "ZHIPU_KEY", "ZHIPU_URL", "ZHIPU_MODEL", "https://open.bigmodel.cn/api/coding/paas/v4"),
        ("openai", "OPENAI_KEY", "OPENAI_URL", "OPENAI_MODEL", "https://api.openai.com/v1"),
        ("anthropic", "ANTHROPIC_KEY", "ANTHROPIC_URL", "ANTHROPIC_MODEL", "https://api.anthropic.com"),
        ("kimi", "KIMI_KEY", "KIMI_URL", "KIMI_MODEL", "https://api.moonshot.cn/v1"),
        ("qwen", "QWEN_KEY", "QWEN_URL", "QWEN_MODEL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        ("jiutian", "JIUTIAN_KEY", "JIUTIAN_URL", "JIUTIAN_MODEL", "https://jiutian.10086.cn/largemodel/moma/api/v3"),
        ("cloud", "CLOUD_KEY", "CLOUD_URL", "CLOUD_MODEL", ""),
    ]

    results = {}
    async with _httpx.AsyncClient(timeout=8.0) as client:
        for pid, key_env, url_env, model_env, default_url in _providers:
            key = os.getenv(key_env, "").strip()
            if not key:
                continue
            base_url = os.getenv(url_env, default_url).rstrip("/")
            if not base_url:
                continue

            # Anthropic uses /v1/messages, not /models
            if pid == "anthropic":
                try:
                    resp = await client.get(
                        f"{base_url}/v1/models",
                        headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                    )
                    results[pid] = {"ok": resp.status_code < 500, "status": resp.status_code}
                except Exception as e:
                    results[pid] = {"ok": False, "error": str(e)}
            else:
                # OpenAI-compatible: GET /models
                try:
                    resp = await client.get(
                        f"{base_url}/models",
                        headers={"Authorization": f"Bearer {key}"},
                    )
                    results[pid] = {"ok": resp.status_code < 500, "status": resp.status_code}
                except Exception as e:
                    results[pid] = {"ok": False, "error": str(e)}

    return {"results": results}
