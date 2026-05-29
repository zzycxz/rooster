import asyncio
import os
import uuid
import logging
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from .base import BaseChannel, InboundMessage

logger = logging.getLogger(__name__)


class CLIChannel(BaseChannel):
    """
    极简从容的命令行交互通道 (Rooster CLI)。
    支持 Rich 渲染、多会话管理与优雅退出。
    """

    # Minimalist CLI interaction channel (Rooster CLI).
    # Supports Rich rendering, multi-session management and graceful exit

    # Bilingual messages — Chinese first, English below each
    _MSG = {
        "welcome": "Rooster Agent Console",
        "welcome_zh": "Rooster Agent 控制台",
        "help_line": "Type to chat | /help for commands | /exit to quit",
        "help_line_zh": "输入文字即可聊天 | 输入 /help 查看指令 | 输入 /exit 退出",
        "welcome_title": "Welcome to Rooster",
        "greeting_zh": "欢迎使用 Rooster",
        "cmd_new": "New session: {}",
        "cmd_new_zh": "✨ 已开启新会话: {}",
        "cmd_switch": "Switched to session: {}",
        "cmd_switch_zh": "已切换到会话: {}",
        "cmd_switch_nf": "Session not found: {}",
        "cmd_switch_nf_zh": "未找到会话: {}",
        "cmd_switch_fmt": "Usage: /switch <ID>",
        "cmd_switch_fmt_zh": "格式: /switch <ID>",
        "cmd_model": "Current model: {}",
        "cmd_model_zh": "当前模型: {}",
        "cmd_model_set": "Switched model to: {}",
        "cmd_model_set_zh": "已切换模型为: {}",
        "cmd_unknown": "Unknown command: {}",
        "cmd_unknown_zh": "未知指令: {}",
        "cmd_exiting": "Shutting down...",
        "cmd_exiting_zh": "正在收尾并退出...",
        "sys_error": "System error: {}",
        "sys_error_zh": "系统故障: {}",
    }

    def __init__(self, channel_id: str = "cli"):
        super().__init__(channel_id)
        self.console = Console()
        self.current_session_id = "cli_default"
        from utils.config import settings

        self.current_model = settings.LOCAL_MODEL
        self.recent_sessions = [self.current_session_id]
        self.router = None
        self._should_stop = False
        self._lang = os.getenv("ROOSTER_LANG", "en").lower()
        self.supports_streaming = True

    def t(self, key: str) -> str:
        """Get message in current language (default English, zh for Chinese)."""
        if self._lang == "zh":
            return self._MSG.get(key + "_zh", self._MSG.get(key, key))
        return self._MSG.get(key, key)

    async def start(self):
        """启动 CLI 交互主循环"""  # Start CLI interaction main loop
        self.console.print(
            Panel.fit(
                f"[bold magenta]{self.t('welcome')}[/bold magenta]\n{self.t('help_line')}",
                title=self.t("welcome_title"),
                border_style="magenta",
            )
        )

        # 懒加载 Router，避免循环依赖
        # Lazy-load Router to avoid circular dependency
        from agents.router import Router

        self.router = Router.get_instance()

        while not self._should_stop:
            try:
                # 异步捕获输入（不阻塞后台服务）
                # Async capture input (non-blocking background services)
                user_text = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: Prompt.ask(f"[bold green]{self.current_session_id}[/bold green] >")
                )

                if not user_text.strip():
                    continue

                # [V8.4 Fix] 清理输入文本，移除可能导致 LLM 请求编码失败的不合法代理项
                # [V8.4 Fix] Clean input text, remove illegal surrogate items that could cause LLM encoding failure
                user_text = user_text.encode("utf-8", "ignore").decode("utf-8", "ignore")

                if user_text.startswith("/"):
                    await self._handle_command(user_text)
                    continue

                # 包装并发送给 Router
                # Package and send to Router
                msg = InboundMessage(
                    sender_id="console_user",
                    text=user_text,
                    channel_id=self.channel_id,
                    session_id=self.current_session_id,
                    raw_data={"device": "terminal", "model": self.current_model},
                )

                await self.router.handle_inbound(msg, self)

            except (EOFError, KeyboardInterrupt):
                await self._handle_command("/exit")
            except Exception as e:
                self.console.print(f"[red]{self.t('sys_error').format(e)}[/red]")
                # [Fix] 遇到普通的 LLM 调用失败（如模型冷却或网络异常）不应该导致整个 CLI 和服务崩溃退出。
                # 仅打印错误，并继续下一轮循环，允许用户重试。
                # Do not raise exception here, as it will crash the entire application via asyncio.gather.

    async def stop(self):
        self._should_stop = True

    async def send_message(self, to: str, text: str, **kwargs):
        """处理回复：Thought/Tool/进度/普通文本分路径渲染"""  # Handle reply: Thought/Tool/Progress/Plain text routed to separate render paths
        if isinstance(text, str):
            text = text.encode("utf-8", "ignore").decode("utf-8", "ignore")

        is_thought = kwargs.get("is_thought", False)
        is_tool = kwargs.get("is_tool", False)
        is_progress = kwargs.get("is_progress", False)

        if is_thought:
            self.console.print(f"\n[dim italic grey]󱜙 Thinking: {text}[/dim italic grey]")
        elif is_progress or text.startswith("--- ⚡ 正在执行"):
            clean = text.strip("- ").strip()
            self.console.print(Panel(clean, border_style="cyan", title="[bold cyan]Progress[/bold cyan]"))
        elif text.startswith("🧠 [规划中]"):
            self.console.print(f"  {text}", style="dim cyan")
        elif text.startswith("✅ [审计通过]") or text.startswith("✅ **[任务结案]**"):
            self.console.print(f"\n{text}", style="bold green")
        elif text.startswith("🛡️ [补救") or text.startswith("🔍 [审计纠错]"):
            self.console.print(f"\n{text}", style="bold yellow")
        elif text.startswith("🚧 [战略重组]") or text.startswith("❌ [系统异常]"):
            self.console.print(f"\n{text}", style="bold red")
        elif is_tool:
            tool_data = kwargs.get("tool_data", {})
            tool_name = tool_data.get("tool") or tool_data.get("name")
            event_type = tool_data.get("type") or tool_data.get("status")

            if event_type in ["call", "executing"]:
                args = tool_data.get("args") or tool_data.get("arguments")
                self.console.print(f"\n[bold yellow]🛠️ Calling tool {tool_name}[/bold yellow](args={args})")
            elif event_type in ["response", "done"]:
                response = tool_data.get("response") or tool_data.get("result")
                if isinstance(response, str):
                    response = response.encode("utf-8", "ignore").decode("utf-8", "ignore")
                self.console.print(f"[dim green]📥 Result: {response}[/dim green]")
        else:
            # 流式输出：确保关闭 markup 解析，防止大模型输出的中括号导致渲染失败
            # Streaming output: ensure markup parsing is off to prevent LLM output brackets from causing render failure
            self.console.print(text, end="", highlight=False, markup=False)
            # 显式刷新缓冲区，确保微小片段也能立即上屏
            # Explicitly flush buffer to ensure small fragments appear immediately on screen
            import sys

            sys.stdout.flush()

    async def _handle_command(self, cmd_line: str):
        parts = cmd_line.split()
        cmd = parts[0].lower()

        if cmd == "/exit":
            self.console.print(f"[yellow]{self.t('cmd_exiting')}[/yellow]")
            self._should_stop = True

        elif cmd == "/new":
            new_id = f"cli_{str(uuid.uuid4())[:8]}"
            self.current_session_id = new_id
            self.recent_sessions.append(new_id)
            self.console.print(f"[bold cyan]{self.t('cmd_new').format(new_id)}[/bold cyan]")

        elif cmd == "/list":
            table = Table(title=self.t("list_title") if "list_title" in self._MSG else "Sessions")
            table.add_column("ID", style="cyan")
            table.add_column("Status", style="green")
            for sid in self.recent_sessions:
                status = "Current" if sid == self.current_session_id else ""
                table.add_row(sid, status)
            self.console.print(table)

        elif cmd == "/switch":
            if len(parts) < 2:
                self.console.print(f"[red]{self.t('cmd_switch_fmt')}[/red]")
                return
            target_id = parts[1]
            if target_id in self.recent_sessions:
                self.current_session_id = target_id
                self.console.print(f"[green]{self.t('cmd_switch').format(target_id)}[/green]")
            else:
                self.console.print(f"[red]{self.t('cmd_switch_nf').format(target_id)}[/red]")

        elif cmd == "/model":
            if len(parts) < 2:
                self.console.print(f"[bold cyan]{self.t('cmd_model').format(self.current_model)}[/bold cyan]")
                return
            self.current_model = parts[1]
            self.console.print(f"[green]{self.t('cmd_model_set').format(self.current_model)}[/green]")

        elif cmd == "/help":
            if self._lang == "en":
                self.console.print(
                    "Available commands:\n"
                    "  /new    - Start a new session\n"
                    "  /list   - List recent sessions\n"
                    "  /switch - Switch to a session  (/switch <ID>)\n"
                    "  /model  - Show or switch model  (/model <name>)\n"
                    "  /distill- Distill memory  (/distill [session_id])\n"
                    "  /proxy  - Proxy control  (/proxy on|off|status)\n"
                    "  /lang   - Switch language  (/lang zh|en)\n"
                    "  /exit   - Quit Rooster"
                )
            else:
                self.console.print(
                    "可用指令:\n"
                    "  /new    - 开启全新话题\n"
                    "  /list   - 查看最近聊过的会话\n"
                    "  /switch - 切换到指定会话\n"
                    "  /model  - 查看或切换模型 (如 /model deepseek-r1:8b)\n"
                    "  /distill- 手动蒸馏记忆 (/distill [session_id])\n"
                    "  /proxy  - 查看或切换代理 (如 /proxy on | /proxy off | /proxy status)\n"
                    "  /lang   - 切换语言 (/lang zh|en)\n"
                    "  /exit   - 优雅退出系统"
                )

        elif cmd == "/lang":
            if len(parts) >= 2 and parts[1].lower() in ("zh", "en"):
                self._lang = parts[1].lower()
                lang_name = "English" if self._lang == "en" else "中文"
                self.console.print(f"[green]Language: {lang_name}[/green]")
            else:
                self.console.print("[red]Usage: /lang zh|en[/red]")

        elif cmd == "/proxy":
            await self._handle_proxy_command(parts)

        elif cmd == "/distill":
            await self._handle_distill_command(parts)

        else:
            self.console.print(f"[red]{self.t('cmd_unknown').format(cmd)}[/red]")

    async def _handle_proxy_command(self, parts: list):
        """Proxy hot-swap command: /proxy status|on|off"""
        import os
        from utils.config import settings
        from utils.browser.manager import BrowserManager
        from models.factory import ModelFactory

        sub = parts[1].lower() if len(parts) > 1 else "status"

        if sub == "status":
            enabled = settings.ENABLE_REGIONAL_PROXY
            proxy = settings.HTTP_PROXY or "(not configured)"
            status_str = "[bold green]ON[/bold green]" if enabled else "[bold red]OFF[/bold red]"
            self.console.print(f"Proxy: {status_str} | Address: [cyan]{proxy}[/cyan]")

        elif sub == "on":
            os.environ["ENABLE_REGIONAL_PROXY"] = "true"
            proxy = settings.HTTP_PROXY
            if not proxy:
                self.console.print("[yellow]⚠️ Proxy enabled, but HTTP_PROXY is not set. Configure in .env.[/yellow]")
            else:
                self.console.print(f"[green]✅ Proxy enabled: {proxy}[/green]")
            await BrowserManager.restart_with_proxy()
            ModelFactory.clear_cache()
            self.console.print("[dim]Browser context and LLM client rebuilt.[/dim]")

        elif sub == "off":
            os.environ["ENABLE_REGIONAL_PROXY"] = "false"
            self.console.print("[yellow]Proxy disabled (direct mode).[/yellow]")
            await BrowserManager.restart_with_proxy()
            ModelFactory.clear_cache()
            self.console.print("[dim]Browser context and LLM client rebuilt.[/dim]")

        else:
            self.console.print("[red]Usage: /proxy [status|on|off][/red]")

    async def _handle_distill_command(self, parts: list):
        """手动触发记忆蒸馏: /distill [session_id]"""
        from launcher import RoosterLauncher

        # 获取全局 launcher 实例中的调度器
        # 这里直接通过 import 获取，因为 launcher 是全局单例模式
        try:
            from memory.distillation_scheduler import DistillationScheduler
            from agents.router import Router

            router = Router.get_instance()
            # 从 router 所在的 launcher 获取 scheduler — 通过模块级变量
            import launcher as _launcher_mod

            scheduler = getattr(_launcher_mod, "_global_distill_scheduler", None)
            if scheduler is None:
                self.console.print("[yellow]蒸馏调度器未启动。请检查 DISTILLATION_ENABLED 配置。[/yellow]")
                return

            if len(parts) >= 2:
                session_id = parts[1]
                self.console.print(f"[cyan]正在蒸馏 session: {session_id} ...[/cyan]")
                ok = await scheduler.distill_now(session_id)
                if ok:
                    self.console.print(f"[green]✅ session {session_id} 蒸馏完成[/green]")
                else:
                    self.console.print(f"[red]❌ 蒸馏失败: session {session_id} 不存在[/red]")
            else:
                self.console.print("[cyan]正在蒸馏所有待处理的 session ...[/cyan]")
                count = await scheduler.distill_all()
                if count > 0:
                    self.console.print(f"[green]✅ 蒸馏完成，共处理 {count} 个 session[/green]")
                else:
                    self.console.print("[dim]没有需要蒸馏的 session[/dim]")
        except Exception as e:
            self.console.print(f"[red]蒸馏失败: {e}[/red]")
