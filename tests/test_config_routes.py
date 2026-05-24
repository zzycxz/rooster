from fastapi.testclient import TestClient
from gateway.server import app
from utils.config import settings


def _get_headers():
    headers = {}
    if settings.GATEWAY_API_KEY:
        headers["X-API-Key"] = settings.GATEWAY_API_KEY
    return headers


def test_api_config_yaml_resolution():
    """验证 /api/config/yaml 能够正确读取 .env 配置"""
    client = TestClient(app)
    response = client.get("/api/config/yaml", headers=_get_headers())

    assert response.status_code == 200
    json_data = response.json()
    assert json_data.get("ok") is True
    assert "config" in json_data

    config = json_data["config"]
    # 确保关键 .env 变量存在
    assert "AGENT_MAX_STEPS" in config
    assert "GATEWAY_PORT" in config
    assert "OLLAMA_URL" in config


def test_api_version_resolution():
    """验证 /api/version 能够正确解析项目根目录的 pyproject.toml 并读取版本号"""
    client = TestClient(app)
    response = client.get("/api/version", headers=_get_headers())

    assert response.status_code == 200
    json_data = response.json()
    assert "version" in json_data
    assert json_data["version"] != "unknown"  # 不应该返回 unknown 降级值


def test_api_guardian_status_resolution():
    """验证 /api/guardian/status 的路径解析逻辑"""
    client = TestClient(app)
    response = client.get("/api/guardian/status", headers=_get_headers())

    # 即使 Guardian 未启动，也应该正确返回状态信息或 "Guardian not running"，而不是路径错误抛出异常
    assert response.status_code == 200
    json_data = response.json()
    assert json_data.get("ok") is True
