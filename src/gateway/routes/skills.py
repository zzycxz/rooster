"""Skills API routes — marketplace, install, uninstall, toggle, search, test."""

import os
import json
import asyncio
import time
import logging
from typing import Dict, Any

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/skills", tags=["skills"])

# Shared state references — set by server.py during wiring
_skills_dir: str = ""
_get_skill_loader_fn = None
_invalidate_skill_loader_fn = None
CORE_SKILLS = {
    "coding-agent",
    "data-analysis",
    "dev-tools",
    "git-ops",
    "github",
    "pdf-tools",
    "resource-downloader",
    "self-improving",
    "summarize",
    "visual-control",
    "web-search",
}


class InstallRequest(BaseModel):
    name: str


class UninstallRequest(BaseModel):
    name: str


class TestRequest(BaseModel):
    name: str


def wire(skills_dir: str, get_skill_loader_fn, invalidate_skill_loader_fn):
    """Called by server.py to inject shared state."""
    global _skills_dir, _get_skill_loader_fn, _invalidate_skill_loader_fn
    _skills_dir = skills_dir
    _get_skill_loader_fn = get_skill_loader_fn
    _invalidate_skill_loader_fn = invalidate_skill_loader_fn


def _get_loader():
    return _get_skill_loader_fn()


def _invalidate():
    return _invalidate_skill_loader_fn()


# ---------------------------------------------------------------------------
# ClawHub Community Skill Registry
# ---------------------------------------------------------------------------
CLAWHUB_MARKET: dict = {
    "weather-query": {
        "emoji": "🌤️",
        "category": "utility",
        "description": "智能天气与生活指数分析。并发查询多源天气状况，为用户出行提供地衣级穿衣与防晒建议。",
        "author": "rooster-community",
        "requires": {},
        "skill_body": "# 🌤️ weather-query\n全球精细化天气实况及生活指数（穿衣、防晒、运动、出行）分析。\n\n## 适用场景\n- 查询当前/未来天气状况\n- 获取生活建议（穿衣/雨伞/防晒指数）\n- 多城市对比天气\n",
    },
    "translation-helper": {
        "emoji": "🗣️",
        "category": "collaboration",
        "description": "多语种同声传译与地道润色。支持 20+ 语种的物理双向互译，智能识别句式语调，提供极其本土化的精修建议。",
        "author": "rooster-community",
        "requires": {},
        "skill_body": "# 🗣️ translation-helper\n多国语种间超高保真段落级润色与翻译。\n\n## 适用场景\n- 中英/中日/中法等双向互译\n- 商务文书、学术摘要地道化润色\n- 实时字幕式对话翻译\n",
    },
    "financial-analyzer": {
        "emoji": "📊",
        "category": "finance",
        "description": "企业财报深度解析与投资信号提取。自动抓取年报/季报，输出盈利质量评分、现金流健康度及估值锚点。",
        "author": "rooster-community",
        "requires": {"python_packages": ["pandas", "openpyxl"]},
        "skill_body": "# 📊 financial-analyzer\n企业财务报告（年报/季报）深度量化分析与投资信号自动提取。\n\n## 适用场景\n- 解析上市公司 PDF/Excel 财报\n- 计算 ROE/ROIC/FCF 等核心指标\n- 生成多维估值锚点与风险警示报告\n\n## 依赖\n- `pandas`: 数据清洗与表格计算\n- `openpyxl`: Excel 财报读取\n",
    },
    "system-monitor": {
        "emoji": "🖥️",
        "category": "system",
        "description": "实时系统健康巡检与异常预警。持续采集 CPU/内存/磁盘/网络指标，自动生成健康仪表板与告警摘要。",
        "author": "rooster-community",
        "requires": {"python_packages": ["psutil"]},
        "skill_body": "# 🖥️ system-monitor\n本机系统资源实时采集与多维健康评分。\n\n## 适用场景\n- CPU/内存/磁盘/网络多维实时监控\n- 进程资源占用排行与异常检测\n- 自动生成系统健康报告\n\n## 依赖\n- `psutil`: 系统级资源采集\n",
    },
    "video-compress": {
        "emoji": "🎬",
        "category": "media",
        "description": "高效音视频转码与压制。调用 FFmpeg 实现批量格式转换、分辨率缩放、码率控制及字幕内嵌。",
        "author": "rooster-community",
        "requires": {"bins": ["ffmpeg"]},
        "skill_body": "# 🎬 video-compress\n基于 FFmpeg 的高效批量音视频转码与压制工作流。\n\n## 适用场景\n- MP4/MKV/AVI 格式互转\n- 分辨率缩放（4K→1080P）与码率控制\n- 字幕内嵌与音轨提取\n- 批量压缩以减少存储占用\n\n## 依赖\n- `ffmpeg`: 需已安装于系统 PATH\n",
    },
    "knowledge-graph": {
        "emoji": "🕸️",
        "category": "analysis",
        "description": "文本知识图谱自动构建与实体关系抽取。从文档/网页中提取实体、关系与事件，输出可视化图谱 JSON。",
        "author": "rooster-community",
        "requires": {"python_packages": ["networkx"]},
        "skill_body": "# 🕸️ knowledge-graph\n从非结构化文本自动抽取实体与关系，构建可查询知识图谱。\n\n## 适用场景\n- 论文/报告实体关系自动抽取\n- 企业关系图谱（股东/子公司/产品）构建\n- 事件链路梳理与时间轴推断\n\n## 依赖\n- `networkx`: 图结构存储与路径分析\n",
    },
    "packet-analyzer": {
        "emoji": "📡",
        "category": "security",
        "description": "网络流量抓包与协议分析。解析 PCAP 文件，统计会话流量分布，识别异常协议与潜在渗透特征。",
        "author": "rooster-community",
        "requires": {"python_packages": ["scapy"]},
        "skill_body": "# 📡 packet-analyzer\nPCAP 网络抓包解析与异常流量快速诊断。\n\n## 适用场景\n- 解析 .pcap/.pcapng 抓包文件\n- HTTP/DNS/TLS 会话流量统计\n- 识别端口扫描、暴力破解等异常行为特征\n\n## 依赖\n- `scapy`: 网络包解析引擎\n",
    },
    "code-reviewer": {
        "emoji": "🔍",
        "category": "development",
        "description": "代码质量深度审查与安全漏洞扫描。检测潜在 Bug、反模式、SQL 注入/XSS 等安全风险，输出带行号的修复建议。",
        "author": "rooster-community",
        "requires": {},
        "skill_body": "# 🔍 code-reviewer\n多语言代码质量评审与安全漏洞静态分析。\n\n## 适用场景\n- PR 代码差异安全审查\n- SQL 注入 / XSS / 路径遍历漏洞扫描\n- 反模式识别（全局变量、裸 except、硬编码密钥）\n- 输出带行号的精准修复建议\n",
    },
    "email-composer": {
        "emoji": "✉️",
        "category": "collaboration",
        "description": "专业商务邮件智能撰写与风格适配。支持中英文正式/非正式风格，自动处理称谓、开头、正文与结尾的标准化结构。",
        "author": "rooster-community",
        "requires": {},
        "skill_body": "# ✉️ email-composer\n跨语言专业商务邮件结构化撰写助手。\n\n## 适用场景\n- 中英文正式商务邮件起草\n- 会议邀请、报价回复、投诉处理等模板化场景\n- 智能润色（语气匹配、称谓规范、格式校正）\n- 批量生成个性化营销/通知邮件\n",
    },
}


# Background caching for ClawHub Cloud Skills
CLAW_SKILLS_CACHE = {"items": [], "last_fetched": 0.0}
CLAW_SKILLS_LOCK = asyncio.Lock()


async def fetch_cloud_skills_background():
    global CLAW_SKILLS_CACHE
    async with CLAW_SKILLS_LOCK:
        if time.time() - CLAW_SKILLS_CACHE["last_fetched"] < 600:
            return
        try:
            import urllib.request

            url = "https://clawhub.ai/api/v1/skills?limit=500"
            req = urllib.request.Request(url, headers={"User-Agent": "RoosterAgent/1.0"})
            loop = asyncio.get_event_loop()

            def sync_fetch():
                with urllib.request.urlopen(req, timeout=5) as response:
                    return json.loads(response.read().decode("utf-8"))

            data = await loop.run_in_executor(None, sync_fetch)
            items = data.get("items", [])
            CLAW_SKILLS_CACHE["items"] = items
            CLAW_SKILLS_CACHE["last_fetched"] = time.time()
            logger.info(f"Cached {len(items)} live Clawhub skills in background.")
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"Failed to fetch live Clawhub skills: {e}")
        except Exception:
            logger.exception("Unexpected error fetching Clawhub skills")


@router.get("")
async def api_skills_list():
    loader = _get_loader()
    skills = []
    for name, meta in loader.skills.items():
        skills.append(
            {
                "name": meta.name,
                "description": meta.description,
                "emoji": meta.emoji,
                "category": meta.category,
                "platform": meta.platform,
                "missing_deps": meta.missing_deps,
                "healthy": len(meta.missing_deps) == 0,
                "path": meta.full_path,
                "enabled": meta.enabled,
                "can_uninstall": name not in CORE_SKILLS,
                "skill_type": "skill"
                if (name in CORE_SKILLS or name in ("dev-tools", "hack-and-crack"))
                else "community",
            }
        )
    return {"skills": skills, "total": len(skills)}


@router.get("/market")
async def api_skills_market():
    local_loader = _get_loader()
    local_names = set(local_loader.skills.keys())

    market_skills = []
    for name, meta in local_loader.skills.items():
        if name in CORE_SKILLS:
            continue
        market_skills.append(
            {
                "name": name,
                "emoji": meta.emoji,
                "category": meta.category,
                "description": meta.description,
                "author": "local",
                "requires": {},
                "installed": True,
                "can_uninstall": True,
                "downloads": 0,
            }
        )

    existing_names = set(s["name"] for s in market_skills)
    for name, meta in CLAWHUB_MARKET.items():
        if name in existing_names:
            continue
        market_skills.append(
            {
                "name": name,
                "emoji": meta["emoji"],
                "category": meta["category"],
                "description": meta["description"],
                "author": meta["author"],
                "requires": meta.get("requires", {}),
                "installed": name in local_names,
                "can_uninstall": name not in CORE_SKILLS,
                "downloads": meta.get("downloads", 0),
            }
        )

    now = time.time()
    if now - CLAW_SKILLS_CACHE["last_fetched"] > 600:
        asyncio.create_task(fetch_cloud_skills_background())

    cached_items = CLAW_SKILLS_CACHE["items"]
    existing_names = set(s["name"] for s in market_skills)
    for item in cached_items:
        slug = item.get("slug")
        if not slug or slug in existing_names:
            continue
        disp_name = item.get("displayName", slug)
        summary = item.get("summary", "")
        desc = f"{disp_name} - {summary}" if summary else disp_name
        tags = item.get("tags", {})
        category = "utility"
        if "monitoring" in tags or "system" in tags:
            category = "system"
        elif "ai" in tags or "llm" in tags:
            category = "analysis"
        elif "collaboration" in tags:
            category = "collaboration"
        market_skills.append(
            {
                "name": slug,
                "emoji": "☁️",
                "category": category,
                "description": desc,
                "author": item.get("owner", {}).get("displayName") or "clawhub",
                "requires": {},
                "installed": slug in local_names,
                "can_uninstall": slug not in CORE_SKILLS,
                "downloads": item.get("downloads", 0) or item.get("downloadCount", 0),
            }
        )

    return {"skills": market_skills}


@router.get("/search")
async def api_skills_search(q: str = "", limit: int = 20):
    if not q.strip():
        return {"results": []}
    try:
        import urllib.request
        import urllib.parse

        url = f"https://clawhub.ai/api/v1/search?q={urllib.parse.quote(q)}&limit={limit}"
        req = urllib.request.Request(url, headers={"User-Agent": "RoosterAgent/1.0"})
        loop = asyncio.get_event_loop()

        def _fetch():
            with urllib.request.urlopen(req, timeout=8) as resp:
                return json.loads(resp.read().decode("utf-8"))

        data = await loop.run_in_executor(None, _fetch)
        local_loader = _get_loader()
        results = []
        for item in data.get("results", []):
            slug = item.get("slug", "")
            results.append(
                {
                    "name": slug,
                    "emoji": "☁️",
                    "category": "utility",
                    "description": item.get("summary", ""),
                    "author": item.get("owner", {}).get("displayName") or item.get("ownerHandle", "unknown"),
                    "requires": {},
                    "installed": slug in local_loader.skills,
                    "can_uninstall": slug not in CORE_SKILLS,
                    "downloads": item.get("downloads", 0) or item.get("downloadCount", 0),
                }
            )
        return {"results": results}
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"ClawHub search failed: {e}")
        return {"results": [], "error": str(e)}
    except Exception:
        logger.exception("Unexpected error in ClawHub search")
        return {"results": [], "error": "Internal error"}


@router.post("/install")
async def api_skills_install(req: InstallRequest):
    name = req.name
    import urllib.request

    if name not in CLAWHUB_MARKET:
        if not name or "/" in name or "\\" in name:
            raise HTTPException(status_code=400, detail="Invalid skill name format")

    base_skills_dir = _skills_dir
    target_dir = os.path.join(base_skills_dir, name)
    os.makedirs(target_dir, exist_ok=True)
    skill_file_path = os.path.join(target_dir, "SKILL.md")

    if name in CLAWHUB_MARKET:
        meta = CLAWHUB_MARKET[name]
        requires = meta.get("requires", {})
        py_pkgs = requires.get("python_packages", [])
        bins = requires.get("bins", [])
        env_vars = requires.get("env_vars", [])

        req_yaml = ""
        if py_pkgs or bins or env_vars:
            req_yaml = "    requires:\n"
            if py_pkgs:
                req_yaml += f"      python_packages: [{', '.join(py_pkgs)}]\n"
            if bins:
                req_yaml += f"      bins: [{', '.join(bins)}]\n"
            if env_vars:
                req_yaml += f"      env_vars: [{', '.join(env_vars)}]\n"

        fm = f"""---
name: {name}
description: "{meta["description"]}"
metadata:
  rooster:
    emoji: "{meta["emoji"]}"
    category: "{meta["category"]}"
    platform: ["any"]
    author: "{meta["author"]}"
{req_yaml}---
{meta["skill_body"]}"""
        try:
            with open(skill_file_path, "w", encoding="utf-8") as f:
                f.write(fm)
        except OSError as e:
            logger.error(f"Failed to write local skill: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    else:
        try:
            url = f"https://clawhub.ai/api/v1/skills/{name}/file?path=SKILL.md"
            req_dl = urllib.request.Request(url, headers={"User-Agent": "RoosterAgent/1.0"})
            with urllib.request.urlopen(req_dl, timeout=8) as response:
                skill_content = response.read().decode("utf-8")
            with open(skill_file_path, "w", encoding="utf-8") as f:
                f.write(skill_content)
        except OSError as e:
            logger.error(f"Failed to download online skill '{name}' from Clawhub: {e}")
            raise HTTPException(status_code=500, detail=f"Download failed: {e}")
        except Exception as e:
            logger.exception(f"Unexpected error installing skill '{name}'")
            raise HTTPException(status_code=500, detail=f"Install failed: {e}")

    _invalidate()
    logger.info(f"Installed skill '{name}' -> {target_dir}")
    return {"ok": True, "name": name}


@router.post("/uninstall")
async def api_skills_uninstall(req: UninstallRequest):
    import shutil

    name = req.name

    if name in CORE_SKILLS:
        raise HTTPException(status_code=400, detail="Core system skills cannot be uninstalled!")

    base_skills_dir = _skills_dir
    target_dir = os.path.join(base_skills_dir, name)

    if not os.path.exists(target_dir):
        raise HTTPException(status_code=404, detail=f"Skill '{name}' is not found locally.")

    try:
        shutil.rmtree(target_dir)
        _invalidate()
        logger.info(f"Uninstalled skill '{name}' from {target_dir}")
        return {"ok": True, "name": name}
    except OSError as e:
        logger.error(f"Failed to uninstall skill '{name}': {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/reload")
async def api_skills_reload():
    _invalidate()
    return {"ok": True, "message": "Skill registry reloaded"}


@router.post("/toggle")
async def api_skills_toggle(payload: Dict[str, Any] = Body(...)):
    name = payload.get("name")
    if not name:
        raise HTTPException(status_code=400, detail="Missing skill name")

    loader = _get_loader()
    skill = loader.skills.get(name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")

    old_path = skill.full_path
    if skill.enabled:
        new_path = old_path.replace("SKILL.md", "SKILL.md.disabled")
        action = "disabled"
    else:
        new_path = old_path.replace("SKILL.md.disabled", "SKILL.md")
        action = "enabled"

    try:
        os.rename(old_path, new_path)
        _invalidate()
        logger.info(f"Skill '{name}' toggled: {action}")
        return {"ok": True, "action": action, "name": name}
    except OSError as e:
        logger.error(f"Failed to toggle skill '{name}': {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/fix_deps")
async def api_skills_fix_deps(req: UninstallRequest):
    import subprocess
    import sys
    import importlib

    name = req.name
    loader = _get_loader()
    skill = loader.skills.get(name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found.")

    pip_pkgs = []
    bin_deps = []
    env_deps = []
    for dep in skill.missing_deps:
        if dep.startswith("pip:"):
            pip_pkgs.append(dep.split(":", 1)[1])
        elif dep.startswith("bin:"):
            bin_deps.append(dep.split(":", 1)[1])
        elif dep.startswith("env:"):
            env_deps.append(dep.split(":", 1)[1])

    if not pip_pkgs:
        hints = []
        if bin_deps:
            hints.append(
                f"Missing system commands: {', '.join(bin_deps)}. Install them manually (e.g. winget install {bin_deps[0]} or brew install {bin_deps[0]})."
            )
        if env_deps:
            hints.append(f"Missing environment variables: {', '.join(env_deps)}. Set them in .env.local.")
        msg = " ".join(hints) if hints else "No fixable dependencies found."
        return {"ok": False, "error": msg}

    try:
        cmd = [sys.executable, "-m", "pip", "install"] + pip_pkgs
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)

        importlib.invalidate_caches()
        for pkg in pip_pkgs:
            if pkg in sys.modules:
                del sys.modules[pkg]
            prefix = pkg + "."
            for k in list(sys.modules.keys()):
                if k.startswith(prefix):
                    del sys.modules[k]

        _invalidate()
        logger.info(f"Auto-installed missing deps: {pip_pkgs} for '{name}'")
        return {"ok": True, "message": f"Successfully installed: {', '.join(pip_pkgs)}", "log": result.stdout}
    except subprocess.CalledProcessError as e:
        err_msg = e.stderr or e.stdout or "Pip install failed."
        logger.error(f"pip install failed for '{name}': {err_msg}")
        raise HTTPException(status_code=500, detail=f"Installation failed: {err_msg}")


@router.post("/test")
async def api_skills_test(req: TestRequest):
    name = req.name
    loader = _get_loader()

    if name not in loader.skills:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not loaded in current registry.")

    meta = loader.skills[name]
    if len(meta.missing_deps) > 0:
        return {
            "ok": False,
            "status": "unhealthy",
            "message": f"Missing Python libraries: {', '.join(meta.missing_deps)}",
            "missing_deps": meta.missing_deps,
        }

    return {
        "ok": True,
        "status": "healthy",
        "message": f"Skill '{name}' diagnostic test PASSED!",
        "details": {
            "name": meta.name,
            "category": meta.category,
            "emoji": meta.emoji,
            "platform": meta.platform,
            "enabled": meta.enabled,
            "healthy": True,
        },
    }
