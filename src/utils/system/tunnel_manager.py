import subprocess
import requests
import logging
import os
import asyncio

logger = logging.getLogger("TunnelManager")


class TunnelManager:
    """
    内网穿透管理器。
    负责在 Rooster 启动时自动建立公网隧道，并实时捕获公网 URL。
    支持解耦调用，通过配置文件开启/关闭。
    """

    _instance = None

    def __init__(self, port: int):
        self.port = port
        self.process = None
        self.public_url = None

    @classmethod
    def get_instance(cls, port: int = 8001):
        if not cls._instance:
            cls._instance = cls(port)
        return cls._instance

    async def start(self):
        """
        异步拉起穿透进程并捕获 URL。
        """
        logger.info(f"🚀 正在尝试自动建立对准端口 {self.port} 的内网穿透...")

        try:
            # 1. 尝试拉起进程（支持 Windows / Linux / Mac）
            # 使用 subprocess.DEVNULL 避免输出打断 Rooster 的流式日志
            self.process = subprocess.Popen(
                ["ngrok", "http", str(self.port)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=True if os.name == "nt" else False,
            )

            # 2. 等待 ngrok 初始化并注册隧道（通常需要几秒）
            # 我们最多等待 10 秒
            max_retries = 10
            for i in range(max_retries):
                await asyncio.sleep(1)  # 异步等待
                try:
                    # ngrok 默认会在 127.0.0.1:4040 开启诊断 API
                    resp = requests.get("http://127.0.0.1:4040/api/tunnels", timeout=2)
                    if resp.status_code == 200:
                        tunnels = resp.json().get("tunnels", [])
                        for t in tunnels:
                            # 优先选择 https 协议的地址
                            if t.get("proto") == "https":
                                self.public_url = t.get("public_url")
                                break
                        if self.public_url:
                            break
                except Exception:
                    continue

            if self.public_url:
                logger.info("=" * 50)
                logger.info("✅ 内网穿透已成功建立！")
                logger.info(f"🔗 公网 Hook 地址: {self.public_url}/webhook/feishu")
                logger.info('⚠️ 请将上述完整链接填入飞书开放平台的"事件订阅"请求地址。')
                logger.info("=" * 50)
            else:
                logger.warning("⚠️ ngrok 进程已启动，但未能在 10s 内捕获到公网 URL。")
                logger.warning("请确保：1. ngrok 已安装；2. 您的网络环境允许外联；3. ngrok 的 authtoken 已正确配置。")

        except FileNotFoundError:
            logger.error("❌ 启动失败：未在系统 PATH 中找到 ngrok 命令。")
        except Exception as e:
            logger.error(f"❌ 穿透自动化过程发生异常: {e}")

    async def stop(self):
        """
        安全停止穿透进程。
        """
        if self.process:
            self.process.terminate()
            logger.info("👋 正在关闭内网穿透隧道...")
