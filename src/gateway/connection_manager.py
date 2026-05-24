import asyncio
import logging
from typing import Dict, List, Optional, Any
from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionMetadata:
    def __init__(self, connection_id: str, websocket: WebSocket):
        self.connection_id = connection_id
        self.websocket = websocket
        self.role: str = "unknown"  # agent | node
        self.node_id: Optional[str] = None
        self.caps: List[str] = []
        self.display_name: Optional[str] = None


class ConnectionManager:
    def __init__(self):
        # 存储 connection_id -> ConnectionMetadata
        # Store connection_id -> ConnectionMetadata
        self.connections: Dict[str, ConnectionMetadata] = {}
        # 建立索引：node_id -> connection_id (用于快速路由指令)
        # Index: node_id -> connection_id (for fast command routing)
        self.node_map: Dict[str, str] = {}
        # --- 核心改进：响应等候室 (Future 注册集) ---
        # --- Core improvement: response waiting room (Future registry) ---
        self.pending_futures: Dict[str, asyncio.Future] = {}

    async def connect(self, connection_id: str, websocket: WebSocket, role: str = "agent"):
        self.connections[connection_id] = ConnectionMetadata(connection_id, websocket)
        self.connections[connection_id].role = role
        logger.info(f"Client connected: {connection_id} (Role: {role})")

    def register_node(self, connection_id: str, node_id: str, caps: List[str], display_name: Optional[str] = None):
        """将连接正式注册为受控节点"""
        if connection_id in self.connections:
            meta = self.connections[connection_id]
            meta.role = "node"
            meta.node_id = node_id
            meta.caps = caps
            meta.display_name = display_name
            self.node_map[node_id] = connection_id
            logger.info(f"Node registered: {node_id} (caps: {caps})")

    def disconnect(self, connection_id: str):
        if connection_id in self.connections:
            meta = self.connections[connection_id]
            if meta.node_id and meta.node_id in self.node_map:
                del self.node_map[meta.node_id]
            del self.connections[connection_id]
            logger.info(f"Client disconnected: {connection_id}")

    def get_node_connection(self, node_id: str) -> Optional[WebSocket]:
        """根据 Node ID 获取对应的 WebSocket"""
        conn_id = self.node_map.get(node_id)
        if conn_id and conn_id in self.connections:
            return self.connections[conn_id].websocket
        return None

    def list_nodes(self) -> List[Dict[str, Any]]:
        """列出所有在线受控节点"""
        nodes = []
        for meta in self.connections.values():
            if meta.role == "node":
                nodes.append(
                    {
                        "nodeId": meta.node_id,
                        "caps": meta.caps,
                        "displayName": meta.display_name or meta.node_id,
                        "connected": True,
                    }
                )
        return nodes

    async def broadcast(self, message: str):
        for meta in self.connections.values():
            try:
                await meta.websocket.send_text(message)
            except Exception as e:
                logger.error(f"Error sending message to {meta.connection_id}: {e}")

    async def send_personal_message(self, message: str, connection_id: str):
        if connection_id in self.connections:
            websocket = self.connections[connection_id].websocket
            try:
                await websocket.send_text(message)
            except Exception as e:
                logger.error(f"Error sending message to {connection_id}: {e}")

    async def wait_for_response(self, msg_id: str, timeout: float = 30.0) -> Optional[Any]:
        """关键方法：挂起当前任务并等待 VNode 的回传结果"""
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self.pending_futures[msg_id] = future

        try:
            # 开启超时竞速
            # Start timeout race
            result = await asyncio.wait_for(future, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            logger.warning(f"⏰ 等待响应超时 [ID: {msg_id}]")
            return {"error": "TIMEOUT", "message": "The node did not respond in time."}
        finally:
            # 无论成功还是超时都要打扫"等候室"
            # Clean up the "waiting room" regardless of success or timeout
            if msg_id in self.pending_futures:
                del self.pending_futures[msg_id]

    async def handle_node_message(self, connection_id: str, websocket: WebSocket, data: Dict[str, Any]):
        """解析并分发受控端传来的消息"""
        if connection_id not in self.connections:
            self.connections[connection_id] = ConnectionMetadata(connection_id, websocket)

        msg_id = data.get("id")
        result = data.get("result")

        # 1. 注册逻辑
        # 1. Registration logic
        if isinstance(result, dict) and "nodeId" in result:
            node_id = result["nodeId"]
            caps = result.get("capabilities", [])
            display_name = result.get("displayName")
            self.register_node(connection_id, node_id, caps, display_name)

            # 如果这个注册包也带了 ID 且有人在等它
            # If this registration packet also has an ID and someone is waiting for it
            if msg_id and msg_id in self.pending_futures:
                self.pending_futures[msg_id].set_result(result)
            return

        # 2. 指令回传逻辑：通过 msg_id 唤醒等候中的 Future
        # 2. Command response: wake up waiting Future via msg_id
        if msg_id and msg_id in self.pending_futures:
            future = self.pending_futures[msg_id]
            if not future.done():
                logger.info(f"🎯 成功匹配响应 [ID: {msg_id}]")
                future.set_result(result)
            return

        logger.debug(f"Node {connection_id} unhandled message: {data}")

    def remove_node(self, connection_id: str):
        self.disconnect(connection_id)
