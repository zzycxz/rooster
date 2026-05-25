import pytest
from sessions.store import SessionStore
from sessions.models import Session
from gateway.server_methods import MethodHandler
from dashboard.src.routes.system import api_sessions_list

@pytest.fixture
def tmp_store(tmp_path, monkeypatch):
    """Create a temporary SessionStore and patch it globally."""
    store = SessionStore(storage_dir=str(tmp_path))
    store._sessions.clear()
    monkeypatch.setattr("sessions.store.global_session_store", store)
    monkeypatch.setattr("sessions.store.SessionStore.get_instance", lambda: store)
    monkeypatch.setattr("gateway.server_methods.global_session_store", store)
    return store

def test_session_write_back_filtering(tmp_store):
    """验证包含 _internal: True 的中间消息在 write-back 逻辑中被成功过滤"""
    session = tmp_store.get_or_create("test-sess")
    
    # 模拟 ReAct 单步执行中的 session_history，包括原始输入、内部工具响应、内部辅助提示以及最后的助手回答
    session_history = [
        # 注意：首条消息（用户输入）已经被 server_methods 保存了，长度为 1
        {"role": "user", "content": "我的指令"},
        {"role": "user", "content": "<tool_response name=\"cmd\">执行成功</tool_response>", "_internal": True},
        {"role": "user", "content": "请根据以上工具执行结果，继续完成任务。", "_internal": True},
        {"role": "assistant", "content": "这是最终完成结果。"}
    ]
    
    # 模拟在 handle_chat_send 时已经预先将首条用户输入写入 history
    session.add_message(session_history[0]["role"], session_history[0]["content"])
    assert len(session.history) == 1
    
    # 模拟 write-back 逻辑
    for msg in session_history[len(session.history):]:
        if msg.get("role") == "tool":
            continue
        if msg.get("_internal"):
            continue
        if isinstance(msg.get("content"), str):
            session.add_message(msg["role"], msg["content"])
            
    # 验证最终持久化写入的历史消息只包含“我的指令”和“这是最终完成结果”，且顺序正确
    assert len(session.history) == 2
    assert session.history[0].role == "user"
    assert session.history[0].content == "我的指令"
    assert session.history[1].role == "assistant"
    assert session.history[1].content == "这是最终完成结果。"

@pytest.mark.asyncio
async def test_handle_chat_send_persistence(tmp_store):
    """验证用户发送消息时，消息文本会立即持久化写入 session.history"""
    # 初始化 MethodHandler
    methods = MethodHandler(broadcast_callback=None, manager=None, connection_id="test_conn")
    
    async def dummy_respond(success, data=None, error=None):
        pass

    params = {
        "sessionKey": "sess-persistence-test",
        "message": "用户的高层真实输入指令",
        "modelOverride": ""
    }
    
    # 模拟创建 Run 相关的 global_run_manager
    class DummyRun:
        def __init__(self):
            self.run_id = "run-123"
    class DummyRunManager:
        def create_run(self, session_id):
            return DummyRun()
        def register_task(self, run_id, task):
            pass
            
    import gateway.server_methods as sm
    sm.global_run_manager = DummyRunManager()
    
    # 模拟路由调用
    class DummyRouter:
        async def process_run(self, run, session, message_text, event_handler):
            pass
    sm.global_router = DummyRouter()
    
    # 模拟 event_handler
    class DummyEventHandler:
        async def emit(self, run_id, session_id, stream, data):
            pass
    methods.event_handler = DummyEventHandler()
    
    # 执行发送
    await methods.handle_chat_send(params, dummy_respond)
    
    # 验证 Session 中是否正确且立即添加了第一条 user 消息
    sess = tmp_store.get_session("sess-persistence-test")
    assert sess is not None
    assert len(sess.history) == 1
    assert sess.history[0].role == "user"
    assert sess.history[0].content == "用户的高层真实输入指令"

@pytest.mark.asyncio
async def test_session_list_title_and_last_msg_filtering(tmp_store):
    """验证会话列表提取标题和最后消息时，过滤了 <tool_response 形式的内容，并且有非工具消息兜底"""
    # 场景 1：只有工具响应和助手回答
    session1 = tmp_store.get_or_create("sess-1")
    session1.add_message("user", "<tool_response name=\"cmd\">工具输出内容</tool_response>")
    session1.add_message("assistant", "最终回答消息内容")
    
    # 场景 2：有真实的用户输入、中间工具响应以及助手回答
    session2 = tmp_store.get_or_create("sess-2")
    session2.add_message("user", "真实的原始用户输入")
    session2.add_message("user", "<tool_response name=\"cmd\">中间工具输出</tool_response>")
    session2.add_message("assistant", "助手回答")
    
    methods = MethodHandler(broadcast_callback=None, manager=None, connection_id="test_conn")
    
    # 测试 Gateway 的 handle_session_list
    gateway_sessions = []
    async def dummy_respond_gateway(success, data=None, error=None):
        nonlocal gateway_sessions
        gateway_sessions = data["sessions"]
        
    await methods.handle_session_list({}, dummy_respond_gateway)
    
    # 获取特定会话的返回属性
    sess1_gate = next(s for s in gateway_sessions if s["sessionId"] == "sess-1")
    sess2_gate = next(s for s in gateway_sessions if s["sessionId"] == "sess-2")
    
    # 对于 sess-1：由于没有纯净的 user 消息，标题应该退化为最后的助手回答，且 lastMessage 也应该正确过滤或保留
    assert "<tool_response" not in sess1_gate["title"]
    assert sess1_gate["title"] == "最终回答消息内容"
    assert "<tool_response" not in sess1_gate["lastMessage"]
    
    # 对于 sess-2：标题应该是真实的原始输入，lastMessage 应该是助手回答
    assert sess2_gate["title"] == "真实的原始用户输入"
    assert sess2_gate["lastMessage"] == "助手回答"
    
    # 测试 Dashboard 后端的 api_sessions_list API
    dash_resp = await api_sessions_list()
    assert dash_resp["ok"] is True
    dash_sessions = dash_resp["sessions"]
    
    sess1_dash = next(s for s in dash_sessions if s["session_id"] == "sess-1")
    sess2_dash = next(s for s in dash_sessions if s["session_id"] == "sess-2")
    
    assert "<tool_response" not in sess1_dash["title"]
    assert sess1_dash["title"] == "最终回答消息内容"
    assert sess2_dash["title"] == "真实的原始用户输入"
