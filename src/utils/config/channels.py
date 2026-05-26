"""Channel configuration — Feishu, gateway, webhook, MCP, browser, email, downloader."""

from utils.config._base import (
    _env,
    _env_int,
    _env_bool,
)


class ChannelsConfig:
    # --- General ---
    ROOSTER_LANG: str = _env("ROOSTER_LANG", "en")

    # --- Feishu ---
    CH_FEISHU_ID: str = _env("FEISHU_APP_ID", "")
    CH_FEISHU_SECRET: str = _env("FEISHU_APP_SECRET", "")

    # --- Gateway ---
    GATEWAY_PORT: int = _env_int("GATEWAY_PORT", 8765)
    GATEWAY_LOCALHOST_AUTH: bool = _env_bool("GATEWAY_LOCALHOST_AUTH", True)
    GATEWAY_API_KEY: str = _env("GATEWAY_API_KEY", "")
    WEBHOOK_HMAC_SECRET: str = _env("WEBHOOK_HMAC_SECRET", "")

    # --- Tunnel / dashboard ---
    ENABLE_TUNNEL: bool = _env_bool("ENABLE_TUNNEL", False)
    DASHBOARD_AUTO_OPEN: bool = _env_bool("DASHBOARD_AUTO_OPEN", True)

    # --- Webhook channel ---
    WEBHOOK_ENABLED: bool = _env_bool("WEBHOOK_ENABLED", False)
    WEBHOOK_PORT: int = _env_int("WEBHOOK_PORT", 8099)
    WEBHOOK_SECRET_TOKEN: str = _env("WEBHOOK_SECRET_TOKEN", "")

    # --- MCP ---
    MCP_DYNAMIC_ENABLED: bool = _env_bool("MCP_DYNAMIC_ENABLED", True)  # Default ON for better UX
    MCP_SERVER_URLS: str = _env("MCP_SERVER_URLS", "")

    # --- Browser ---
    BROWSER_MAX_PAGES: int = _env_int("BROWSER_MAX_PAGES", 5)

    # --- Email / SMTP ---
    SMTP_DEFAULT_HOST: str = _env("SMTP_DEFAULT_HOST", "")
    SMTP_DEFAULT_USER: str = _env("SMTP_DEFAULT_USER", "")
    SMTP_DEFAULT_PASS: str = _env("SMTP_DEFAULT_PASS", "")

    # --- Downloader ---
    DOWNLOADER_PROVIDER: str = _env("DOWNLOADER_PROVIDER", "system_default")
    DOWNLOADER_ENABLED: bool = _env_bool("DOWNLOADER_ENABLED", True)
    ARIA2_RPC_URL: str = _env("ARIA2_RPC_URL", "http://localhost:6800/jsonrpc")
    ARIA2_RPC_SECRET: str = _env("ARIA2_RPC_SECRET", "")
