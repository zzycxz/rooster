"""
utils 向后兼容层 (Compatibility Shim)
这些 import 别名确保所有旧代码的 from utils.xxx import YYY 调用不会断裂。
在 Rooster v3.0 中将移除这些别名，届时所有调用方需要迁移到新路径。
"""

# Lazy imports for compatibility and avoiding heavy dependency loads (like pyautogui/playwright) under headless/CI environments.
_LAZY_MAPPING = {
    # 浏览器
    # Browser
    "BrowserManager": "utils.browser.manager",
    # 视觉
    # Vision
    "EliteEngine": "utils.vision.engine",
    "ElitePainter": "utils.vision.painter",
    "VisualGrounder": "utils.vision.grounding",
    "UIElement": "utils.vision.grounding",
    "VisualObservation": "utils.vision.grounding",
    "DesktopInteraction": "utils.vision.interaction",
    # 审计
    # Audit
    "audit_manager": "utils.audit.manager",
    # 安全
    # Security
    "PathGuard": "utils.security.path_guard",
    "StateGuard": "utils.security.state_guard",
    "state_guard": "utils.security.state_guard",
    # 系统
    # System
    "TunnelManager": "utils.system.tunnel_manager",
    "PromptManager": "utils.system.prompt_manager",
    "prompt_manager": "utils.system.prompt_manager",
    "sanitize_path_name": "utils.system.path_utils",
    "generate_semantic_filename": "utils.system.path_utils",
}


def __getattr__(name: str):
    if name in _LAZY_MAPPING:
        import importlib

        module_path = _LAZY_MAPPING[name]
        module = importlib.import_module(module_path)
        return getattr(module, name)
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


def __dir__():
    return sorted(list(globals().keys()) + list(_LAZY_MAPPING.keys()))
