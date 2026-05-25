"""Memory API routes — facts, stats, decay, SOUL.md, USER.md."""

import os
import logging
from typing import Dict, Any, List

from fastapi import APIRouter, Body, Query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/memory", tags=["memory"])

# ── wire ────────────────────────────────────────────────────────────────
_rooster_dir: str = ".rooster"


def wire(rooster_dir: str):
    global _rooster_dir
    _rooster_dir = rooster_dir


def _soul_path() -> str:
    return os.path.join(_rooster_dir, "SOUL.md")


def _user_path() -> str:
    return os.path.join(_rooster_dir, "USER.md")


_SOUL_EVOLVABLE: List[str] = ["Core Behavior", "Tone & Style"]
_SOUL_PROTECTED: List[str] = ["Identity", "Hard Limits", "Memory Protocol", "Evolution"]
_SOUL_LINE_LIMIT = 200

_USER_EVOLVABLE: List[str] = ["Active Projects", "Preferences"]
_USER_PROTECTED: List[str] = ["Basic Info", "Hard Requirements", "Evolution Triggers"]
_USER_LINE_LIMIT = 150


def _read_md_file(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {"content": "", "line_count": 0, "exists": False}
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    return {
        "content": content,
        "line_count": len(content.splitlines()),
        "exists": True,
        "modified_at": os.path.getmtime(path),
    }


def _atomic_write(path: str, content: str) -> None:
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp_path, path)


@router.get("/stats")
async def api_memory_stats():
    try:
        from memory.manager import MemoryManager

        mm = MemoryManager()
        return {"ok": True, **mm.stats()}
    except Exception as exc:
        logger.exception("Failed to get memory stats")
        return {"ok": False, "error": str(exc)}


@router.get("/facts")
async def api_memory_facts(
    page: int = Query(1, ge=1), limit: int = Query(50, ge=1, le=500), q: str = Query("", max_length=100)
):
    try:
        from memory.manager import MemoryManager

        mm = MemoryManager()
        facts = mm.backend.get_all_facts()

        if q.strip():
            query = q.strip().lower()
            facts = [
                f
                for f in facts
                if query in f.content.lower()
                or query in (f.fact_id or "").lower()
                or any(query in t.lower() for t in (f.tags or []))
            ]

        sorted_facts = sorted(facts, key=lambda f: getattr(f, "created_at", ""), reverse=True)

        total_items = len(sorted_facts)
        start_idx = (page - 1) * limit
        end_idx = start_idx + limit
        paginated_facts = sorted_facts[start_idx:end_idx]

        items = []
        for f in paginated_facts:
            items.append(
                {
                    "fact_id": f.fact_id,
                    "fact_type": f.fact_type.value if hasattr(f.fact_type, "value") else str(f.fact_type),
                    "content": f.content[:500] + ("..." if len(f.content) > 500 else ""),
                    "source_agent": f.source_agent,
                    "confidence": f.confidence,
                    "weight": round(f.weight, 3),
                    "locked": f.locked,
                    "access_count": f.access_count,
                    "created_at": f.created_at,
                    "last_accessed": f.last_accessed,
                    "tags": f.tags or [],
                }
            )
        return {"ok": True, "facts": items, "total": total_items, "page": page, "limit": limit}
    except Exception as exc:
        logger.exception("Failed to get memory facts")
        return {"ok": False, "error": str(exc)}


@router.delete("/facts/{fact_id}")
async def api_delete_memory_fact(fact_id: str):
    try:
        from memory.manager import MemoryManager

        mm = MemoryManager()
        if mm.backend.remove_fact(fact_id):
            return {"ok": True, "message": f"Fact {fact_id} deleted."}
        return {"ok": False, "error": "Fact not found or could not be deleted."}
    except Exception as exc:
        logger.exception(f"Failed to delete memory fact {fact_id}")
        return {"ok": False, "error": str(exc)}


@router.post("/decay")
async def api_memory_decay():
    try:
        from memory.manager import MemoryManager

        mm = MemoryManager()
        mm.apply_decay()
        return {"ok": True}
    except Exception as exc:
        logger.exception("Failed to apply memory decay")
        return {"ok": False, "error": str(exc)}


# ── SOUL.md / USER.md endpoints ─────────────────────────────────────────


@router.get("/soul")
async def api_get_soul():
    info = _read_md_file(_soul_path())
    return {
        "ok": True,
        **info,
        "line_limit": _SOUL_LINE_LIMIT,
        "evolvable_sections": _SOUL_EVOLVABLE,
        "protected_sections": _SOUL_PROTECTED,
    }


@router.get("/user")
async def api_get_user():
    info = _read_md_file(_user_path())
    return {
        "ok": True,
        **info,
        "line_limit": _USER_LINE_LIMIT,
        "evolvable_sections": _USER_EVOLVABLE,
        "protected_sections": _USER_PROTECTED,
    }


@router.put("/soul")
async def api_put_soul(data: Dict[str, Any] = Body(...)):
    return await _handle_md_save(_soul_path(), "SOUL", data, _SOUL_EVOLVABLE, _SOUL_PROTECTED)


@router.put("/user")
async def api_put_user(data: Dict[str, Any] = Body(...)):
    return await _handle_md_save(_user_path(), "USER", data, _USER_EVOLVABLE, _USER_PROTECTED)


_SOUL_WARNING = (
    "SOUL.md 由进化引擎自动维护。手动修改后可能与自动更新产生冲突。\n\n"
    "受保护段落（进化引擎不会修改，你的修改将永久生效）：\n"
    "- " + "\n- ".join(_SOUL_PROTECTED) + "\n\n"
    "可进化段落（进化引擎会在此追加内容）：\n"
    "- " + "\n- ".join(_SOUL_EVOLVABLE) + "\n\n"
    "如果你修改了可进化段落，进化引擎的后续追加将在你的修改基础上继续。"
)

_USER_WARNING = (
    "USER.md 由进化引擎自动维护。手动修改后可能与自动更新产生冲突。\n\n"
    "受保护段落（进化引擎不会修改，你的修改将永久生效）：\n"
    "- " + "\n- ".join(_USER_PROTECTED) + "\n\n"
    "可进化段落（进化引擎会在此追加内容）：\n"
    "- " + "\n- ".join(_USER_EVOLVABLE) + "\n\n"
    "如果你修改了可进化段落，进化引擎的后续追加将在你的修改基础上继续。"
)


async def _handle_md_save(
    path: str, label: str, data: Dict[str, Any], evolvable: List[str], protected: List[str]
) -> Dict[str, Any]:
    content = data.get("content", "")
    confirmed = data.get("confirmed", False)

    if not confirmed:
        warning = _SOUL_WARNING if label == "SOUL" else _USER_WARNING
        return {
            "ok": True,
            "needs_confirm": True,
            "warning": warning,
            "evolvable_sections": evolvable,
            "protected_sections": protected,
        }

    try:
        old_lines = 0
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                old_lines = len(f.read().splitlines())

        _atomic_write(path, content)
        new_lines = len(content.splitlines())
        logger.info(f"[memory.{label.lower()}.save] {old_lines} → {new_lines} lines")
        return {"ok": True, "line_count": new_lines}
    except OSError as exc:
        logger.error(f"[memory.{label.lower()}.save] Write failed: {exc}")
        return {"ok": False, "error": str(exc)}
