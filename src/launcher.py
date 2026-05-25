import asyncio
import logging
import os
import socket
import sys
import uvicorn

from gateway.server import app
from channels.registry import ChannelRegistry
from utils.browser import BrowserManager
from utils.system import TunnelManager
from gateway.local_node import RoosterLocalNode

logger = logging.getLogger("RoosterLauncher")


class RoosterLauncher:
    """
    系统启动器。
    负责：
    1. 端口自动避让探测。
    2. 注册并启动各种通讯通道（飞书, CLI）。
    3. 管理内网穿透隧道。
    4. 启动可视化控制网关。
    5. 启动本地代理节点。

    System launcher. Responsible for:
    1. Automatic port conflict avoidance.
    2. Registering and starting communication channels (Feishu, CLI).
    3. Managing intranet tunnels.
    4. Starting the visual control gateway.
    5. Starting the local agent node.
    """

    def __init__(self):
        from utils.config import settings as _s

        self._settings = _s
        self.registry = ChannelRegistry.get_instance()
        self.gateway_port = _s.GATEWAY_PORT
        self.base_url = "http://127.0.0.1"
        self._ready_event = asyncio.Event()  # 预热完成信号  # Warmup complete signal

    def _find_available_port(self):
        """寻找可用端口（使用实际 bind 检测，避免 connect_ex 对 TIME_WAIT 的误判）"""  # Find available port using actual bind check to avoid connect_ex false positives on TIME_WAIT
        port = self.gateway_port

        def can_bind(p: int) -> bool:
            """尝试实际绑定端口，成功则说明端口可用"""  # Try binding the port; success means the port is available
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    s.bind(("127.0.0.1", p))
                    return True
                except OSError:
                    return False

        while not can_bind(port):
            logger.warning(f"⚠️ 端口 {port} 无法绑定，尝试下一个端口 {port + 1}...")
            port += 1
            if port > self.gateway_port + 10:
                logger.error(
                    "❌ 无法在 %d-%d 范围内找到可用端口，请检查占用情况。" % (self.gateway_port, self.gateway_port + 10)
                )
                break
        self.gateway_port = port
        return port

    async def launch_gateway(self):
        """核心启动逻辑"""  # Core startup logic
        port = self._find_available_port()

        # 1. 注册通道
        # 1. Register channels
        try:
            from channels.feishu import FeishuChannel

            feishu_channel = FeishuChannel(channel_id="feishu")
            self.registry.register(feishu_channel)
            await feishu_channel.start()
        except ImportError:
            logger.info("飞书通道未启用 (lark-oapi 未安装)，跳过。")
        except Exception as e:
            logger.warning(f"飞书通道启动失败（不影响主系统）: {e}")

        logger.info(f"📡 网关正在监听: {self.base_url}:{port}")  # Gateway listening

        # 2. 内网穿透
        # 2. Intranet tunnel
        if self._settings.ENABLE_TUNNEL:
            tunnel = TunnelManager.get_instance(port)
            asyncio.create_task(tunnel.start())

        # 3. 启动 Uvicorn（后台任务，不阻塞后续初始化）
        # 3. Start Uvicorn (background task, does not block subsequent initialization)
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        server = uvicorn.Server(config)
        self._server_task = asyncio.create_task(server.serve())

        # 等待服务器就绪
        # Wait for server to be ready
        await asyncio.sleep(1.0)

        # 3b. 自动打开 Dashboard（由 DASHBOARD_AUTO_OPEN 控制，且非看守器自愈模式下才打开）
        # 3c. Auto-open Dashboard (controlled by DASHBOARD_AUTO_OPEN, skipped in guardian self-healing mode)
        is_guardian = os.environ.get("ROOSTER_GUARDIAN_MODE") == "true"
        if self._settings.DASHBOARD_AUTO_OPEN and not is_guardian:
            dashboard_url = f"http://127.0.0.1:{port}/dashboard"
            try:
                import webbrowser

                webbrowser.open(dashboard_url)
                logger.info(f"🌐 Dashboard 已在浏览器中打开: {dashboard_url}")
            except Exception as e:
                logger.warning(f"⚠️ 无法自动打开浏览器: {e}")
            logger.info(f"🖥️  Dashboard 地址: {dashboard_url}")
        elif is_guardian:
            logger.info("🛡️  检测到由 Guardian 守护进程拉起，跳过自动打开浏览器以避免标签页骚扰。")
            logger.info(f"🖥️  Dashboard 访问地址: http://127.0.0.1:{port}/dashboard")

        # 4. 连接 LocalNode
        # 4. Connect LocalNode
        logger.info("💉 正在启动本地虚拟受控节点 (LocalNode) 并尝试挂载...")
        local_node = RoosterLocalNode(gateway_url=f"ws://127.0.0.1:{port}/v1/node/ws")
        asyncio.create_task(local_node.connect())

        # 5. [P2-1] Webhook 通道注册（可选，由 WEBHOOK_ENABLED 控制）
        # 5. [P2-1] Webhook channel registration (optional, controlled by WEBHOOK_ENABLED)
        if self._settings.WEBHOOK_ENABLED:
            try:
                from channels.webhook import WebhookChannel

                webhook_channel = WebhookChannel()
                self.registry.register(webhook_channel)
                await webhook_channel.start()
                logger.info(f"🔗 Webhook 通道已启动，端口: {webhook_channel.port}")
            except Exception as e:
                logger.warning(f"⚠️ Webhook 通道启动失败（可选功能，不影响主系统）: {e}")

        # 6. [P3-1] MCP 工具动态注册（可选，由 MCP_DYNAMIC_ENABLED 控制）
        # 6. [P3-1] MCP dynamic tool registration (optional, controlled by MCP_DYNAMIC_ENABLED)
        if self._settings.MCP_DYNAMIC_ENABLED:
            try:
                from utils.mcp_dynamic import register_mcp_tools_from_servers

                count = await register_mcp_tools_from_servers()
                if count > 0:
                    logger.info(f"🔌 MCP 动态工具注册完成，共 {count} 个工具")
            except Exception as e:
                logger.warning(f"⚠️ MCP 动态注册失败（可选功能，不影响主系统）: {e}")

        # 7. 预热 Router + 嵌入模型
        # 7. Warm up Router + embedding model
        logger.info("🧠 正在预热记忆系统与嵌入模型...")
        from agents.router import Router

        router = Router.get_instance()
        try:
            await asyncio.wait_for(
                router.memory_manager.initialize_async(),
                timeout=120,
            )
            logger.info("✅ 记忆系统就绪。")
        except asyncio.TimeoutError:
            logger.warning("⚠️ 嵌入模型加载超时，系统将继续启动（语义搜索可能不可用）。")
        except Exception as e:
            logger.warning(f"⚠️ 记忆系统初始化异常: {e}，系统将继续启动。")

        # 通知 CLI：预热完成
        # Notify CLI: warmup complete
        self._ready_event.set()

        # 等待服务器退出
        # Wait for server to exit
        await self._server_task

    async def launch_cli(self):
        """启动控制台交互界面"""  # Start the interactive console
        from channels.cli import CLIChannel

        # 等待预热完成后再显示 CLI
        # Wait for warmup to complete before showing CLI
        await self._ready_event.wait()

        cli_channel = CLIChannel(channel_id="cli")
        self.registry.register(cli_channel)

        if sys.stdin.isatty():
            await cli_channel.start()
            logger.info("👋 CLI 会话已交互式退出，正在关闭系统...")  # CLI session exited interactively, shutting down
            # 在交互式退出时，我们可以主动触发关闭
            # On interactive exit, we can proactively trigger shutdown
            sys.exit(0)
        else:
            logger.info("🛰️ 检测到非交互式环境 (Non-TTY)，CLI 进入挂载模式，等待远程指令...")
            while True:
                await asyncio.sleep(3600)

    async def cleanup(self):
        """全局资源回收"""  # Global resource cleanup
        logger.info("👋 正在执行全局资源回收...")
        # 浏览器清理
        # Browser cleanup
        manager = await BrowserManager.get_instance()
        await manager.close()
        # 通道清理
        # Channel cleanup
        await self.registry.stop_all()
        # 隧道清理
        # Tunnel cleanup
        if self._settings.ENABLE_TUNNEL:
            tunnel = TunnelManager.get_instance()
            await tunnel.stop()
