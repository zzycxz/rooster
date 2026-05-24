import asyncio
import json
import uuid
import logging
import platform
from typing import Optional, Any
from toolset.definitions.visual_control import DesktopController

logger = logging.getLogger("LocalNode")


class RoosterLocalNode:
    def __init__(self, gateway_url: str = "ws://127.0.0.1:8765/v1/node/ws"):
        self.gateway_url = gateway_url
        self.node_id = f"local_{platform.system().lower()}_{uuid.uuid4().hex[:6]}"
        self.display_name = f"Local Desktop ({platform.node()})"
        self.ws: Optional[Any] = None

    async def handle_command(self, command: str, params: dict) -> dict:
        """执行网关发来的指令 (通过工具控制器执行)"""
        try:
            if command == "screenshot" or command == "camera.snap":
                logger.info("📸 正在执行屏显抓取...")
                return await DesktopController.get_screenshot()

            elif command == "uia.dump":
                logger.info("📐 正在执行 UIA 精准扫描...")
                depth = params.get("depth", 3)
                return await DesktopController.dump_uia(depth=depth)

            elif command == "input.click":
                # paramsJSON 里的 elementId 如果包含坐标，或者直接传入了坐标
                # If elementId in paramsJSON contains coordinates, or coordinates are passed directly
                # 这里为了简化，我们假设直接处理坐标或通过 grounding 获取
                # Simplified: directly process coordinates or obtain via grounding
                x = params.get("x")
                y = params.get("y")
                double = params.get("doubleClick", False)
                if x is not None and y is not None:
                    return await DesktopController.perform_click(x, y, double=double)
                return {"status": "error", "message": "Missing coordinates (x, y)"}

            elif command == "input.type_standard":
                content = params.get("content")
                if content:
                    return await DesktopController.perform_type(content)
                return {"status": "error", "message": "Missing content"}

            elif command == "input.scroll":
                direction = params.get("direction", "down")
                amount = params.get("amount", 600)
                return await DesktopController.perform_scroll(direction, amount)

            elif command == "input.drag":
                s_x, s_y = params.get("startX"), params.get("startY")
                e_x, e_y = params.get("endX"), params.get("endY")
                if all(v is not None for v in [s_x, s_y, e_x, e_y]):
                    return await DesktopController.perform_drag(s_x, s_y, e_x, e_y)
                return {"status": "error", "message": "Missing drag coordinates"}

            elif command == "input.hotkey":
                keys = params.get("keys", [])
                if keys:
                    return await DesktopController.perform_hotkey(keys)
                return {"status": "error", "message": "Missing keys"}

            return {"status": "error", "message": f"指令 {command} 未实现"}

        except Exception as e:
            logger.error(f"指令分发失败: {e}")
            return {"status": "error", "message": str(e)}

    async def _process_message(self, websocket, data: dict):
        """处理单条网关消息"""
        msg_id = data.get("id")
        method = data.get("method")

        # 1. 处理注册查询 (Gateway 主动拉起 node.query)
        # 1. Handle registration query (Gateway initiates node.query)
        if method == "node.query":
            await self.send_response(
                msg_id,
                {
                    "nodeId": self.node_id,
                    "displayName": self.display_name,
                    "capabilities": [
                        "screenshot",
                        "uia.dump",
                        "input.click",
                        "input.type",
                        "input.scroll",
                        "input.drag",
                        "input.hotkey",
                    ],
                },
            )
            return

        # 2. 处理指令分发 (node.invoke)
        # 2. Handle command dispatch (node.invoke)
        if method == "node.invoke":
            params = data.get("params", {})
            cmd = params.get("command")
            cmd_params = params.get("paramsJSON")
            if isinstance(cmd_params, str):
                cmd_params = json.loads(cmd_params)
            else:
                cmd_params = params.get("params", {})

            logger.info(f"📥 收到指令: {cmd}")
            result = await self.handle_command(cmd, cmd_params)
            await self.send_response(msg_id, result)

    async def connect(self):
        """
        建立与网关的 WebSocket 长连接，并进入消息监听循环。
        """
        import websockets
        import json
        import os

        retry_delay = 1
        while True:
            try:
                logger.info(f"🔗 正在尝试连接至网关: {self.gateway_url}...")
                async with websockets.connect(self.gateway_url) as websocket:
                    self.ws = websocket
                    logger.info("✅ 已成功连接到网关端口。")

                    # 优先读取首帧判定是否需要鉴权
                    # Read the first frame first to determine if authentication is needed
                    first_msg = await websocket.recv()
                    data = json.loads(first_msg)

                    if isinstance(data, dict) and data.get("type") == "auth_required":
                        api_key = os.getenv("GATEWAY_API_KEY", "").strip()
                        await websocket.send(json.dumps({"type": "auth", "token": api_key}))

                        auth_resp = await websocket.recv()
                        auth_data = json.loads(auth_resp)
                        if isinstance(auth_data, dict) and auth_data.get("type") == "auth_ok":
                            logger.info("🔑 LocalNode WebSocket 鉴权成功！")
                        else:
                            raise Exception(f"鉴权握手失败: {auth_data}")
                    else:
                        # 如果没有 auth_required 协议，则第一条消息是标准的网关注册查询，直接处理
                        # If no auth_required protocol, the first message is a standard gateway registration query; process directly
                        await self._process_message(websocket, data)

                    # 监听循环
                    # Listen loop
                    async for message in websocket:
                        data = json.loads(message)
                        await self._process_message(websocket, data)

            except Exception as e:
                logger.error(f"❌ WebSocket 连接异常: {e}. {retry_delay}s 后尝试重连...")
                self.ws = None
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60)  # 指数回避
                # Exponential backoff

    async def send_response(self, msg_id: str, payload: dict):
        if self.ws:
            response = {"id": msg_id, "result": payload}
            try:
                await self.ws.send(json.dumps(response, ensure_ascii=False))
            except Exception as e:
                logger.error(f"发送响应失败: {e}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    node = RoosterLocalNode()
    print("Node ID:", node.node_id)
    asyncio.run(node.connect())
