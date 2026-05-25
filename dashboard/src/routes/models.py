"""Model management routes — Ollama, Hugging Face, model browser."""

import os
import uuid
import time
import asyncio
import logging
from typing import Dict, Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from utils.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/models", tags=["models"])

# Shared state — wired by app.py
_hf_downloads: Dict[str, Dict[str, Any]] = {}
_hf_download_dir: str = ""
_env_local_path_fn = None


class OllamaApplyRequest(BaseModel):
    model: str
    target: str


class OllamaPullRequest(BaseModel):
    model: str


class OllamaDeleteRequest(BaseModel):
    model: str


class HFDownloadRequest(BaseModel):
    model_id: str
    filename: str


class HFImportOllamaRequest(BaseModel):
    download_id: str
    model_name: str


class HFImportLlamaCppRequest(BaseModel):
    download_id: str
    port: int = 9090
    ctx_size: int = 4096


def wire(hf_downloads: Dict, hf_download_dir: str, env_local_path_fn):
    global _hf_downloads, _hf_download_dir, _env_local_path_fn
    _hf_downloads = hf_downloads
    _hf_download_dir = hf_download_dir
    _env_local_path_fn = env_local_path_fn


# ── Ollama ──────────────────────────────────────────────────────────────────


@router.get("/ollama/scan")
async def api_models_ollama_scan():
    import requests as _requests

    try:
        r = _requests.get(f"{settings.OLLAMA_URL}/api/tags", timeout=3.0)
        if r.status_code == 200:
            data = r.json()
            models = []
            for m in data.get("models", []):
                size_gb = round(m.get("size", 0) / (1024 * 1024 * 1024), 2)
                models.append(
                    {
                        "name": m.get("name"),
                        "family": m.get("details", {}).get("family", "unknown"),
                        "parameter_size": m.get("details", {}).get("parameter_size", "unknown"),
                        "size_gb": f"{size_gb} GB",
                    }
                )
            return {"running": True, "models": models}
    except (OSError, ConnectionError):
        pass
    return {"running": False, "models": []}


@router.post("/ollama/apply")
async def api_models_ollama_apply(req: OllamaApplyRequest):
    target_key = req.target.upper()
    model_name = req.model
    env_path = _env_local_path_fn()

    lines = []
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

    ollama_base = settings.OLLAMA_URL.rstrip("/")
    updates = {"LOCAL_URL": f"{ollama_base}/v1", "LOCAL_KEY": "ollama", "LOCAL_MODEL": model_name}
    if target_key == "PRIMARY":
        updates["ROUTER_MODEL_MODE"] = "local"
        updates["STRATEGIST_MODEL_MODE"] = "local"
        updates["AUDITOR_MODEL_MODE"] = "local"
        updates["SOLO_MODEL_MODE"] = "local"
    elif target_key == "EXECUTOR":
        updates["EXECUTOR_MODEL_MODE"] = "local"
        updates["EXECUTOR_MODEL_NAME"] = "local"

    updated_keys = set()
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k = stripped.split("=", 1)[0].strip()
            if k in updates:
                new_lines.append(f"{k}={updates[k]}\n")
                updated_keys.add(k)
                continue
        new_lines.append(line)

    for k, v in updates.items():
        if k not in updated_keys:
            new_lines.append(f"{k}={v}\n")

    try:
        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
        for k, v in updates.items():
            setattr(settings, k, v)
        from models.factory import ModelFactory

        if hasattr(ModelFactory, "clear_cache"):
            ModelFactory.clear_cache()
        logger.info(f"Ollama model '{model_name}' applied to {req.target}")
        return {"ok": True, "message": f"Applied {model_name} to {req.target}"}
    except OSError as e:
        logger.error(f"Failed to apply Ollama model: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ollama/pull")
async def api_models_ollama_pull(req: OllamaPullRequest):
    import requests as _requests

    try:
        r = _requests.post(
            f"{settings.OLLAMA_URL}/api/pull",
            json={"name": req.model, "stream": False},
            timeout=600,
        )
        if r.status_code == 200:
            return {"ok": True, "message": f"Model '{req.model}' pulled"}
        return {"ok": False, "error": r.text[:300]}
    except (OSError, ConnectionError) as e:
        logger.error(f"Ollama pull failed: {e}")
        return {"ok": False, "error": str(e)}


@router.post("/ollama/delete")
async def api_models_ollama_delete(req: OllamaDeleteRequest):
    import requests as _requests

    try:
        r = _requests.request(
            "DELETE",
            f"{settings.OLLAMA_URL}/api/delete",
            json={"name": req.model},
            timeout=10,
        )
        if r.status_code == 200:
            return {"ok": True, "message": f"Model '{req.model}' deleted"}
        return {"ok": False, "error": r.text[:300]}
    except (OSError, ConnectionError) as e:
        logger.error(f"Ollama delete failed: {e}")
        return {"ok": False, "error": str(e)}


@router.get("/ollama/running")
async def api_models_ollama_running():
    import requests as _requests

    try:
        r = _requests.get(f"{settings.OLLAMA_URL}/api/ps", timeout=3)
        if r.status_code == 200:
            return {"ok": True, "models": r.json().get("models", [])}
    except (OSError, ConnectionError):
        pass
    return {"ok": False, "models": []}


# ── Hugging Face ────────────────────────────────────────────────────────────


def _format_size(size_bytes: int) -> str:
    if size_bytes >= 1073741824:
        return f"{size_bytes / 1073741824:.2f} GB"
    elif size_bytes >= 1048576:
        return f"{size_bytes / 1048576:.1f} MB"
    elif size_bytes >= 1024:
        return f"{size_bytes / 1024:.0f} KB"
    return f"{size_bytes} B"


@router.get("/hf/search")
async def api_models_hf_search(
    q: str = Query("", description="Search query"),
    sort: str = Query("downloads", description="Sort by"),
    limit: int = Query(20, ge=1, le=50),
):
    try:
        from huggingface_hub import HfApi

        os.environ.setdefault("HF_ENDPOINT", settings.HF_ENDPOINT)
        hf = HfApi()
        sort_map = {"downloads": "downloads", "likes": "likes", "trending": "trendingScore"}
        hf_sort = sort_map.get(sort, "downloads")

        models_iter = hf.list_models(search=q or None, filter="gguf", sort=hf_sort, limit=limit)
        results = []
        for m in models_iter:
            try:
                info = hf.model_info(m.id, files_metadata=True)
            except Exception:
                continue
            gguf_files = []
            for f in info.siblings or []:
                if f.rfilename and f.rfilename.endswith(".gguf"):
                    gguf_files.append(
                        {
                            "filename": f.rfilename,
                            "size_bytes": f.size or 0,
                            "size_human": _format_size(f.size or 0),
                        }
                    )
            if not gguf_files:
                continue
            results.append(
                {
                    "model_id": m.id,
                    "downloads": m.downloads or 0,
                    "likes": m.likes or 0,
                    "last_modified": str(m.lastModified or ""),
                    "files": gguf_files,
                }
            )
        return {"ok": True, "models": results}
    except Exception as e:
        logger.exception("HF search failed")
        return {"ok": False, "error": str(e), "models": []}


async def _hf_download_worker(download_id: str, model_id: str, filename: str):
    import httpx

    safe_dir = model_id.replace("/", "__")
    dest_dir = os.path.join(_hf_download_dir, safe_dir)
    os.makedirs(dest_dir, exist_ok=True)
    dest_path = os.path.join(dest_dir, filename)
    tmp_path = dest_path + ".incomplete"

    hf_endpoint = getattr(settings, "HF_ENDPOINT", "https://huggingface.co").rstrip("/")
    url = f"{hf_endpoint}/{model_id}/resolve/main/{filename}"
    dl = _hf_downloads[download_id]
    dl["status"] = "downloading"

    try:
        timeout = httpx.Timeout(connect=30.0, read=300.0, write=300.0, pool=30.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("content-length", 0))
                dl["total_bytes"] = total
                dl["downloaded_bytes"] = 0
                last_tick = time.time()
                last_bytes = 0

                with open(tmp_path, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=65536):
                        f.write(chunk)
                        dl["downloaded_bytes"] += len(chunk)
                        now = time.time()
                        elapsed = now - last_tick
                        if elapsed >= 2.0:
                            speed = (dl["downloaded_bytes"] - last_bytes) / elapsed
                            dl["speed_bps"] = round(speed)
                            if total > 0:
                                dl["progress_pct"] = round(dl["downloaded_bytes"] / total * 100, 1)
                            last_tick = now
                            last_bytes = dl["downloaded_bytes"]

        if os.path.exists(dest_path):
            os.remove(dest_path)
        os.rename(tmp_path, dest_path)

        dl["status"] = "completed"
        dl["progress_pct"] = 100.0
        dl["local_path"] = dest_path
        dl["completed_at"] = time.time()
        logger.info(f"HF download completed: {model_id}/{filename}")
    except httpx.HTTPStatusError as e:
        dl["status"] = "failed"
        dl["error"] = f"HTTP {e.response.status_code}: {str(e)[:200]}"
        logger.error(f"HF download failed: {model_id}/{filename} — {e}")
    except (OSError, httpx.RequestError) as e:
        dl["status"] = "failed"
        dl["error"] = str(e)
        logger.error(f"HF download failed: {model_id}/{filename} — {e}")


@router.post("/hf/download")
async def api_models_hf_download(req: HFDownloadRequest):
    download_id = str(uuid.uuid4())[:8]
    _hf_downloads[download_id] = {
        "download_id": download_id,
        "model_id": req.model_id,
        "filename": req.filename,
        "status": "queued",
        "progress_pct": 0,
        "downloaded_bytes": 0,
        "total_bytes": 0,
        "speed_bps": 0,
        "started_at": time.time(),
        "completed_at": None,
        "error": None,
        "local_path": None,
    }
    asyncio.create_task(_hf_download_worker(download_id, req.model_id, req.filename))
    return {"ok": True, "download_id": download_id}


@router.get("/hf/downloads")
async def api_models_hf_downloads():
    return {"ok": True, "downloads": list(_hf_downloads.values())}


@router.post("/hf/import/ollama")
async def api_models_hf_import_ollama(req: HFImportOllamaRequest):
    import httpx

    dl = _hf_downloads.get(req.download_id)
    if not dl or dl.get("status") != "completed" or not dl.get("local_path"):
        raise HTTPException(status_code=400, detail="Download not found or not completed")

    local_path = dl["local_path"]
    if not os.path.exists(local_path):
        raise HTTPException(status_code=400, detail=f"File not found: {local_path}")

    try:
        modelfile = f"FROM {local_path}\nPARAMETER num_ctx 4096"
        async with httpx.AsyncClient(timeout=600.0) as client:
            r = await client.post(
                f"{settings.OLLAMA_URL}/api/create",
                json={"name": req.model_name, "modelfile": modelfile},
            )
            if r.status_code != 200:
                return {"ok": False, "error": f"Ollama returned {r.status_code}: {r.text[:200]}"}
        return {"ok": True, "message": f"Model '{req.model_name}' imported to Ollama"}
    except (httpx.RequestError, OSError) as e:
        logger.error(f"Ollama import failed: {e}")
        return {"ok": False, "error": str(e)}


@router.post("/hf/import/llamacpp")
async def api_models_hf_import_llamacpp(req: HFImportLlamaCppRequest):
    import subprocess

    dl = _hf_downloads.get(req.download_id)
    if not dl or dl.get("status") != "completed" or not dl.get("local_path"):
        raise HTTPException(status_code=400, detail="Download not found or not completed")

    local_path = dl["local_path"]
    if not os.path.exists(local_path):
        raise HTTPException(status_code=400, detail=f"File not found: {local_path}")

    try:
        proc = subprocess.Popen(
            [
                "llama-server",
                "-m",
                local_path,
                "--port",
                str(req.port),
                "--ctx-size",
                str(req.ctx_size),
                "--host",
                "127.0.0.1",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        dl["llama_pid"] = proc.pid
        return {
            "ok": True,
            "message": f"llama.cpp server started on port {req.port}",
            "port": req.port,
            "url": f"http://127.0.0.1:{req.port}/v1",
            "pid": proc.pid,
        }
    except FileNotFoundError:
        return {"ok": False, "error": "llama-server not found. Install llama.cpp and add to PATH."}
    except OSError as e:
        logger.error(f"llama.cpp launch failed: {e}")
        return {"ok": False, "error": str(e)}


# ─── 隐私路由状态 / Privacy Router Status ───


@router.get("/privacy/status")
async def api_privacy_status():
    """查询隐私路由器状态 / Query privacy router status."""
    try:
        from utils.privacy_router import get_privacy_router

        _router = get_privacy_router()
        return {"ok": True, **_router.status()}
    except Exception as exc:
        logger.exception("Failed to get privacy router status")
        return {"ok": False, "error": str(exc)}
