"""
clean_session_magnets.py
一次性清理脚本：将 .rooster/sessions/ 中残留的 magnet/ED2K URI 替换为安全占位符。

背景：Bug 修复前，executor.py 的 session history 写回逻辑会将完整的 magnet 哈希
存入持久化文件。下次对话时，LLM 会从 session history 中读取旧的磁力链接并直接复用，
导致下载了错误的资源。此脚本对已存在的历史文件进行一次性清理。
"""

import json
import re
import os
import glob
import sys

MAGNET_RE = re.compile(
    r"magnet:\?xt=urn:btih:[a-fA-F0-9]{32,40}[^\s'\"\]]*",
    re.IGNORECASE,
)
ED2K_RE = re.compile(r"ed2k://[^\s'\"\]]*", re.IGNORECASE)

PLACEHOLDER_MAGNET = "[magnet_uri_redacted_by_cleanup_script]"
PLACEHOLDER_ED2K   = "[ed2k_uri_redacted_by_cleanup_script]"


def sanitize_str(text: str) -> tuple[str, bool]:
    """Replace any magnet/ED2K URI in text. Returns (cleaned_text, was_changed)."""
    result = MAGNET_RE.sub(PLACEHOLDER_MAGNET, text)
    result = ED2K_RE.sub(PLACEHOLDER_ED2K, result)
    return result, result != text


def sanitize_value(value):
    """Recursively sanitize strings inside dicts/lists."""
    if isinstance(value, str):
        cleaned, changed = sanitize_str(value)
        return cleaned, changed
    if isinstance(value, dict):
        changed_any = False
        for k, v in value.items():
            new_v, changed = sanitize_value(v)
            if changed:
                value[k] = new_v
                changed_any = True
        return value, changed_any
    if isinstance(value, list):
        changed_any = False
        for i, item in enumerate(value):
            new_item, changed = sanitize_value(item)
            if changed:
                value[i] = new_item
                changed_any = True
        return value, changed_any
    return value, False


def clean_sessions(rooster_dir: str) -> None:
    sessions_dir = os.path.join(rooster_dir, "sessions")
    if not os.path.isdir(sessions_dir):
        print(f"[INFO] No sessions directory found at: {sessions_dir}")
        print("[INFO] Nothing to clean.")
        return

    pattern = os.path.join(sessions_dir, "*.json")
    session_files = glob.glob(pattern)
    if not session_files:
        print(f"[INFO] No session JSON files found in: {sessions_dir}")
        return

    total = len(session_files)
    cleaned = 0
    skipped = 0

    for fpath in session_files:
        fname = os.path.basename(fpath)
        try:
            with open(fpath, "r", encoding="utf-8") as fp:
                data = json.load(fp)

            _, changed = sanitize_value(data)

            if changed:
                tmp = fpath + ".tmp"
                with open(tmp, "w", encoding="utf-8") as fp:
                    json.dump(data, fp, ensure_ascii=False, indent=2)
                os.replace(tmp, fpath)
                cleaned += 1
                print(f"  [CLEANED] {fname}")
            else:
                print(f"  [OK]      {fname} — no URIs found")

        except Exception as exc:
            skipped += 1
            print(f"  [SKIP]    {fname} — error: {exc}")

    print()
    print(f"Done. Scanned {total} sessions, cleaned {cleaned}, skipped {skipped}.")


if __name__ == "__main__":
    # Run from the rooster/ directory, or pass path as argument
    base = sys.argv[1] if len(sys.argv) > 1 else ".rooster"
    clean_sessions(base)
