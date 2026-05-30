"""
LLM 提供商集成测试 — 验证 MiMo / 智谱 GLM / 九天的连通性、功能和时延。
Usage: python tests/test_llm_providers.py
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

PROMPT = "Say 'hello' in one word."
TIMEOUT_BUDGET = 30.0  # seconds per provider


async def _run_provider_test(provider: str):
    from models.factory import ModelFactory

    # Check key exists
    registry = ModelFactory._get_registry()
    cfg = registry.get(provider, {})
    key = cfg.get("key", "")
    url = cfg.get("url", "")
    model = cfg.get("default_model", "")

    if not key:
        return "SKIP", 0, f"No key configured ({provider})"
    if not url:
        return "SKIP", 0, f"No URL configured ({provider})"
    if not model:
        return "SKIP", 0, f"No default model configured ({provider})"

    client = ModelFactory.get_client(provider)
    messages = [{"role": "user", "content": PROMPT}]

    t0 = time.monotonic()
    try:
        content_parts = []

        async def _consume():
            async for delta in client.chat_stream(model=model, messages=messages):
                if delta.content:
                    content_parts.append(delta.content)

        await asyncio.wait_for(_consume(), timeout=TIMEOUT_BUDGET)

        elapsed = time.monotonic() - t0
        full_content = "".join(content_parts)

        if not full_content.strip():
            return "FAIL", elapsed, "Empty response"

        return "PASS", elapsed, f'model={model}, reply: "{full_content.strip()[:80]}"'

    except asyncio.TimeoutError:
        return "FAIL", TIMEOUT_BUDGET, "Timeout"
    except Exception as e:
        elapsed = time.monotonic() - t0
        err = str(e)
        # Truncate long error messages
        if len(err) > 200:
            err = err[:200] + "..."
        return "FAIL", elapsed, err


def fmt(status, elapsed, detail):
    icon = {"PASS": "+", "FAIL": "x", "SKIP": "~"}[status]
    time_str = f"{elapsed:.2f}s" if elapsed > 0 else "-"
    return f"  [{icon}] {status:4s}  {time_str:>7s}  {detail}"


async def main():
    print("=" * 70)
    print("  LLM Provider Integration Test")
    print("=" * 70)
    print(f'  Prompt: "{PROMPT}"')
    print(f"  Timeout budget: {TIMEOUT_BUDGET}s per provider")
    print()

    providers = [
        ("mimo", "Xiaomi MiMo"),
        ("zhipu", "Zhipu GLM (CodingPlan)"),
        ("jiutian", "Jiutian MoMA"),
    ]

    results = {}
    for i, (key, label) in enumerate(providers, 1):
        print(f"[{i}/{len(providers)}] {label} ({key})")
        status, elapsed, detail = await _run_provider_test(key)
        results[key] = status
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
