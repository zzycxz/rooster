"""
搜索工具集成测试 — 验证 Exa / Linkup / GLM Plan Search 的连通性、功能和时延。
Usage: python tests/test_search_providers.py
"""

import asyncio
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Load env
from dotenv import load_dotenv

_env_local = os.path.join(ROOT, ".env.local")
if os.path.exists(_env_local):
    load_dotenv(_env_local)
load_dotenv(os.path.join(ROOT, ".env"))

QUERY = "Python 3.13 release date"
TIMEOUT_BUDGET = 30.0  # seconds per provider


async def test_exa():
    from toolset.definitions.exa_search import ExaSearchTool, get_exa_active

    key = os.getenv("EXA_KEY", "")
    if not key:
        return "SKIP", 0, "EXA_KEY not set"

    active = get_exa_active()
    if not active:
        return "SKIP", 0, "Monthly quota exhausted"

    tool = ExaSearchTool()
    t0 = time.monotonic()
    try:
        result = await asyncio.wait_for(tool.run(query=QUERY, num_results=3), timeout=TIMEOUT_BUDGET)
        elapsed = time.monotonic() - t0
        if result.startswith("Error"):
            return "FAIL", elapsed, result[:200]
        return "PASS", elapsed, f"{len(result)} chars, snippet: {result[:120]}..."
    except asyncio.TimeoutError:
        return "FAIL", TIMEOUT_BUDGET, "Timeout"
    except Exception as e:
        return "FAIL", time.monotonic() - t0, str(e)[:200]


async def test_linkup():
    from toolset.definitions.linkup_search import LinkupSearchTool

    key = os.getenv("LINKUP_KEY", "")
    if not key:
        return "SKIP", 0, "LINKUP_KEY not set"

    tool = LinkupSearchTool()
    t0 = time.monotonic()
    try:
        result = await asyncio.wait_for(tool.run(query=QUERY, depth="fast", max_results=3), timeout=TIMEOUT_BUDGET)
        elapsed = time.monotonic() - t0
        if result.startswith("Error"):
            return "FAIL", elapsed, result[:200]
        return "PASS", elapsed, f"{len(result)} chars, snippet: {result[:120]}..."
    except asyncio.TimeoutError:
        return "FAIL", TIMEOUT_BUDGET, "Timeout"
    except Exception as e:
        return "FAIL", time.monotonic() - t0, str(e)[:200]


async def test_glm():
    from toolset.definitions.glm_plan_search import GLMPlanSearchTool, get_mcp_status

    key = os.getenv("ZHIPU_KEY", "")
    if not key:
        return "SKIP", 0, "ZHIPU_KEY not set"

    if not get_mcp_status():
        return "SKIP", 0, "MCP status inactive"

    tool = GLMPlanSearchTool()
    t0 = time.monotonic()
    try:
        result = await asyncio.wait_for(tool.run(query=QUERY), timeout=TIMEOUT_BUDGET)
        elapsed = time.monotonic() - t0
        if result.startswith("Error"):
            return "FAIL", elapsed, result[:200]
        return "PASS", elapsed, f"{len(result)} chars, snippet: {result[:120]}..."
    except asyncio.TimeoutError:
        return "FAIL", TIMEOUT_BUDGET, "Timeout"
    except Exception as e:
        return "FAIL", time.monotonic() - t0, str(e)[:200]


async def test_search_chain():
    """Test the full exa_search 4-tier chain end-to-end."""
    from toolset.definitions.exa_search import ExaSearchTool

    tool = ExaSearchTool()
    t0 = time.monotonic()
    try:
        result = await asyncio.wait_for(tool.run(query=QUERY, num_results=3), timeout=45.0)
        elapsed = time.monotonic() - t0
        if result.startswith("Error"):
            return "FAIL", elapsed, result[:200]
        # Detect which backend actually responded
        backend = "unknown"
        if "Exa Search Results" in result:
            backend = "exa"
        elif "Linkup" in result:
            backend = "linkup"
        elif "GLM" in result or "智谱" in result:
            backend = "glm"
        elif "web_search" in result or "Search results" in result:
            backend = "web_search"
        return "PASS", elapsed, f"backend={backend}, {len(result)} chars"
    except asyncio.TimeoutError:
        return "FAIL", 45.0, "Timeout on full chain"
    except Exception as e:
        return "FAIL", time.monotonic() - t0, str(e)[:200]


def fmt(status, elapsed, detail):
    icon = {"PASS": "+", "FAIL": "x", "SKIP": "~"}[status]
    time_str = f"{elapsed:.2f}s" if elapsed > 0 else "-"
    return f"  [{icon}] {status:4s}  {time_str:>7s}  {detail}"


async def main():
    print("=" * 70)
    print("  Search Provider Integration Test")
    print("=" * 70)
    print(f'  Query: "{QUERY}"')
    print(f"  Timeout budget: {TIMEOUT_BUDGET}s per provider")
    print()

    results = {}

    print("[1/4] Exa Search (api.exa.ai)")
    status, elapsed, detail = await test_exa()
    results["exa"] = status
    print(fmt(status, elapsed, detail))

    print("[2/4] Linkup Search (api.linkup.so)")
    status, elapsed, detail = await test_linkup()
    results["linkup"] = status
    print(fmt(status, elapsed, detail))

    print("[3/4] GLM Plan Search (Zhipu MCP SSE)")
    status, elapsed, detail = await test_glm()
    results["glm"] = status
    print(fmt(status, elapsed, detail))

    print("[4/4] searchChain (exa_search end-to-end)")
    status, elapsed, detail = await test_search_chain()
    results["searchChain"] = status
    print(fmt(status, elapsed, detail))

    print()
    print("-" * 70)
    passed = sum(1 for v in results.values() if v == "PASS")
    failed = sum(1 for v in results.values() if v == "FAIL")
    skipped = sum(1 for v in results.values() if v == "SKIP")
    print(f"  Result: {passed} passed, {failed} failed, {skipped} skipped")
    if failed > 0:
        print("  FAILED providers need investigation.")
    elif passed == 0:
        print("  All skipped — no API keys configured?")
    else:
        print("  All tested providers operational.")
    print("-" * 70)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
