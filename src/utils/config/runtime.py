"""Runtime behavior configuration — agent limits, context, audit, search, concurrency, timeouts."""

import os
from utils.config._base import (
    _env,
    _env_int,
    _env_float,
    _env_bool,
    _env_list,
)


class RuntimeConfig:
    # --- Agent execution ---
    AGENT_MAX_STEPS: int = _env_int("AGENT_MAX_STEPS", 100)
    AGENT_STUCK_THRESHOLD: int = _env_int("AGENT_STUCK_THRESHOLD", 4)
    AGENT_CONTEXT_LIMIT: int = _env_int("AGENT_CONTEXT_LIMIT", 131072)
    MAX_PARALLEL_SUBTASKS: int = _env_int("MAX_PARALLEL_SUBTASKS", 2)
    LOG_LEVEL: str = _env("LOG_LEVEL", "INFO")

    # --- Context quota ratios ---
    CONTEXT_RATIO_LTM: float = _env_float("CONTEXT_RATIO_LTM", 0.10)
    CONTEXT_RATIO_OBS: float = _env_float("CONTEXT_RATIO_OBS", 0.20)
    CONTEXT_RATIO_HISTORY: float = _env_float("CONTEXT_RATIO_HISTORY", 0.70)
    CONTEXT_RATIO_BUFFER: float = _env_float("CONTEXT_RATIO_BUFFER", 0.00)

    @property
    def OBSERVATION_CHAR_LIMIT(self) -> int:
        return int(self.AGENT_CONTEXT_LIMIT * self.CONTEXT_RATIO_OBS * 3.5)

    # --- Tool output ---
    SINGLE_TOOL_OUTPUT_LIMIT: int = _env_int("SINGLE_TOOL_OUTPUT_LIMIT", 8000)

    # --- Search ---
    SEARXNG_URL: str = _env("SEARXNG_URL", "http://127.0.0.1:18088")
    ENABLE_SEARCH_DDG: bool = _env_bool("ENABLE_SEARCH_DDG", True)
    ENABLE_SEARCH_SEARXNG: bool = _env_bool("ENABLE_SEARCH_SEARXNG", True)
    ENABLE_SEARCH_BAIDU: bool = _env_bool("ENABLE_SEARCH_BAIDU", True)
    ENABLE_SEARCH_TAVILY: bool = _env_bool("ENABLE_SEARCH_TAVILY", True)
    SEARCH_MAX_RESULTS: int = _env_int("SEARCH_MAX_RESULTS", 20)
    TAVILY_API_KEY: str = _env("TAVILY_API_KEY", "")
    EXA_KEY: str = _env("EXA_KEY", "")
    LINKUP_KEY: str = _env("LINKUP_KEY", "")

    # Set HF_ENDPOINT to a mirror (e.g. https://hf-mirror.com) to accelerate downloads in China
    HF_ENDPOINT: str = _env("HF_ENDPOINT", "https://huggingface.co")

    # --- Audit policy ---
    AUDIT_STRICTNESS: str = _env("AUDIT_STRICTNESS", "Medium")
    AUDIT_REQUIRE_SCREENSHOT: bool = _env_bool("AUDIT_REQUIRE_SCREENSHOT", True)
    AUDIT_MAX_REMAND_RETRY: int = _env_int("AUDIT_MAX_REMAND_RETRY", 2)
    AUDIT_CONFIDENCE_THRESHOLD: float = _env_float("AUDIT_CONFIDENCE_THRESHOLD", 0.6)

    # --- Audit logging ---
    AUDIT_LOG_ENABLED: bool = _env_bool("AUDIT_LOG_ENABLED", True)
    AUDIT_LOG_RETENTION_DAYS: int = _env_int("AUDIT_LOG_RETENTION_DAYS", 14)
    EVIDENCE_ROOT: str = _env("EVIDENCE_ROOT", ".rooster/evidence")
    AUDIT_SAVE_PROMPT: bool = _env_bool("AUDIT_SAVE_PROMPT", True)
    AUDIT_SAVE_RAW: bool = _env_bool("AUDIT_SAVE_RAW", True)
    AUDIT_SAVE_SCREENSHOT: bool = _env_bool("AUDIT_SAVE_SCREENSHOT", True)
    AUDIT_SAVE_TELEMETRY: bool = _env_bool("AUDIT_SAVE_TELEMETRY", True)

    # --- Timeouts ---
    STRATEGIST_LLM_TIMEOUT: float = _env_float("STRATEGIST_LLM_TIMEOUT", 300.0)
    SUBTASK_MIN_TIMEOUT: int = _env_int("SUBTASK_MIN_TIMEOUT", 300)
    AUDITOR_TIMEOUT_SECONDS: float = _env_float("AUDITOR_TIMEOUT_SECONDS", 60.0)

    # --- File permissions ---
    _raw_paths = os.getenv("ALLOWED_PATHS")
    if _raw_paths is not None:
        if _raw_paths.strip() in ("", "*"):
            ALLOWED_PATHS = []
        else:
            ALLOWED_PATHS = [p.strip() for p in _raw_paths.split(",") if p.strip()]
    else:
        ALLOWED_PATHS = [os.getcwd()]

    # --- Code interpreter ---
    INTERPRETER_ALLOW_LOCAL: bool = _env_bool("INTERPRETER_ALLOW_LOCAL", False)

    # --- Task checkpoint ---
    CHECKPOINT_ENABLED: bool = _env_bool("CHECKPOINT_ENABLED", False)

    # --- Output ---
    OUTPUT_DIR: str = _env("OUTPUT_DIR", "output")

    # --- Evolution ---
    EVOLUTION_ENABLED: bool = _env_bool("EVOLUTION_ENABLED", False)

    # --- Refusal detection ---
    REFUSAL_PHRASES: list = _env_list(
        "REFUSAL_PHRASES",
        "i'm sorry,i cannot,i can't help,无法帮助,无法协助,抱歉，我无法,sorry, i can",
    )

    # --- Security layer ---
    # 当 permission_policy 要求确认时的行为:
    # When permission_policy requires confirmation:
    #   log    → 仅记录警告并继续执行（默认，不阻断用户）
    #   log    → log warning and continue (default, non-blocking)
    #   block  → 返回错误，要求用户确认后重试
    #   block  → return error, require user confirmation before retry
    CONFIRMATION_BEHAVIOR: str = _env("CONFIRMATION_BEHAVIOR", "log")
    # 工具限速 JSON，格式: {"tool_name": [capacity, refill_rate_per_sec], ...}
    # Tool rate-limit JSON, format: {"tool_name": [capacity, refill_rate_per_sec], ...}
    # 例: {"email_send": [1, 0.016], "web_fetch": [5, 0.5]}
    # Example: {"email_send": [1, 0.016], "web_fetch": [5, 0.5]}
    TOOL_RATE_LIMITS_JSON: str = _env("TOOL_RATE_LIMITS_JSON", "")
    # 允许的 URL scheme（逗号分隔），默认 http,https
    # Allowed URL schemes (comma-separated), default http,https
    ALLOWED_URL_SCHEMES: str = _env("ALLOWED_URL_SCHEMES", "http,https")
    # 封锁的域名（逗号分隔），空表示不限制
    # Blocked domains (comma-separated), empty means no restriction
    BLOCKED_URL_DOMAINS: str = _env("BLOCKED_URL_DOMAINS", "")

    # --- Advanced Security (Round 7, default OFF) ---
    # 主开关：启用全部高级防护（越狱检测 + 工具注入检测 + Skill 投毒检测）
    # Master switch: enable all advanced protection (jailbreak + tool injection + skill poison detection)
    ADVANCED_SECURITY: str = _env("ADVANCED_SECURITY", "false")
    # 专项开关（优先级高于主开关）：单独控制各子模块
    # Per-feature switches (override master switch): individually control sub-modules
    GUARD_JAILBREAK: str = _env("GUARD_JAILBREAK", "")  # true/false/""(跟随主开关)
    # true/false/"" (follow master switch)
    GUARD_PROMPT_INJECTION: str = _env("GUARD_PROMPT_INJECTION", "")  # true/false/""
    # true/false/"" (follow master switch)
    GUARD_SKILL_VERIFY: str = _env("GUARD_SKILL_VERIFY", "")

    # --- Tool Router (Round 8) ---
    # 是否启用 Kit-based FC Schema 路由（关闭时发送全部 schema，行为与 Round 8 之前相同）
    # Enable Kit-based FC Schema routing (off = send all schemas, same as pre-Round 8)
    TOOL_ROUTER_ENABLED: bool = _env_bool("TOOL_ROUTER_ENABLED", True)
    # 每步最多注入的 FC schema 数量（超出时按优先级截断）
    # Max FC schemas injected per step (truncated by priority when exceeded)
    TOOL_ROUTER_MAX_TOOLS: int = _env_int("TOOL_ROUTER_MAX_TOOLS", 20)
    # 自定义路由规则 JSON（格式：{"regex_pattern": ["Kit1", "Kit2"]}），空字符串使用内置默认规则
    # Custom routing rules JSON (format: {"regex_pattern": ["Kit1", "Kit2"]}), empty uses built-in defaults
    TOOL_ROUTER_RULES_JSON: str = _env("TOOL_ROUTER_RULES_JSON", "")
