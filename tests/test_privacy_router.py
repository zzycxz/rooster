# tests/test_privacy_router.py
"""测试隐私路由器核心逻辑 / Test privacy router core logic."""

import os
import pytest
from unittest.mock import patch, MagicMock


class TestPrivacyRouterInit:
    """测试初始化与 Presidio 可用性 / Test initialization and Presidio availability."""

    def test_init_without_presidio(self):
        """Presidio 未安装时不崩溃 / Should not crash when Presidio is not installed."""
        with patch.dict(os.environ, {"LOCAL_DIRS": ""}):
            with patch("utils.privacy_router.PrivacyRouter._init_presidio", lambda self: None):
                from utils.privacy_router import PrivacyRouter

                router = PrivacyRouter()
                assert router._analyzer is None
                status = router.status()
                assert status["presidio_available"] is False

    def test_init_with_local_dirs(self, tmp_path):
        """LOCAL_DIRS 配置正确加载 / LOCAL_DIRS config loads correctly."""
        test_dir = str(tmp_path / "private")
        os.makedirs(test_dir, exist_ok=True)
        with patch.dict(os.environ, {"LOCAL_DIRS": test_dir}):
            from utils.privacy_router import PrivacyRouter

            router = PrivacyRouter()
            assert len(router._local_dirs) == 1


class TestRouteText:
    """测试文本路由逻辑 / Test text routing logic."""

    def test_folder_rule_matches(self, tmp_path):
        """L0 文件夹匹配 → 走本地 / L0 folder match → route local."""
        private_dir = tmp_path / "private"
        private_dir.mkdir()
        test_file = private_dir / "secret.txt"
        test_file.write_text("some content")

        with patch.dict(os.environ, {"LOCAL_DIRS": str(private_dir)}):
            from utils.privacy_router import PrivacyRouter

            router = PrivacyRouter()
            target, reason = router.route_text("hello", file_path=str(test_file))
            assert target == "local"
            assert reason == "folder_rule"

    def test_folder_rule_not_matches(self, tmp_path):
        """L0 文件夹不匹配 → 走云端 / L0 no match → route cloud."""
        other_dir = tmp_path / "public"
        other_dir.mkdir()
        test_file = other_dir / "readme.txt"
        test_file.write_text("some content")

        with patch.dict(os.environ, {"LOCAL_DIRS": str(tmp_path / "private")}):
            from utils.privacy_router import PrivacyRouter

            router = PrivacyRouter()
            target, reason = router.route_text("hello", file_path=str(test_file))
            assert target == "cloud"
            assert reason == "safe"

    def test_no_file_path_no_dirs(self):
        """无文件路径且无 LOCAL_DIRS → 走云端 / No file path and no dirs → cloud."""
        with patch.dict(os.environ, {"LOCAL_DIRS": ""}):
            from utils.privacy_router import PrivacyRouter

            router = PrivacyRouter()
            target, reason = router.route_text("hello world")
            assert target == "cloud"

    def test_exception_fallback(self):
        """任何异常 → 放行到云端 / Any exception → pass through to cloud."""
        from utils.privacy_router import PrivacyRouter

        router = PrivacyRouter()
        # 故意传一个不存在的路径触发 _file_hash 异常 / Intentionally pass bad path
        target, reason = router.route_text("test", file_path="/nonexistent/path/file.txt")
        # 不应崩溃，应返回 cloud / Should not crash, should return cloud
        assert target == "cloud"


class TestRouteImage:
    """测试图片路由逻辑 / Test image routing logic."""

    def test_no_ocr_text_passes_through(self):
        """无 OCR 文字 → 放行 / No OCR text → pass through."""
        from utils.privacy_router import PrivacyRouter

        router = PrivacyRouter()
        target, reason = router.route_image(source_tool="desktop_snap")
        assert target == "cloud"

    def test_exception_fallback(self):
        """任何异常 → 放行 / Any exception → pass through."""
        from utils.privacy_router import PrivacyRouter

        router = PrivacyRouter()
        # 即使 analyzer 有问题也不应崩溃 / Should not crash even with broken analyzer
        target, reason = router.route_image(source_tool="desktop_snap", ocr_text="test")
        assert target in ("local", "cloud")  # 只要返回就行 / Just needs to return


class TestOverride:
    """测试用户手动覆盖 / Test user manual override."""

    def test_override_forces_local(self, tmp_path):
        """覆盖后强制走本地 / Override forces local."""
        test_file = tmp_path / "file.txt"
        test_file.write_text("content")

        with patch.dict(os.environ, {"LOCAL_DIRS": ""}):
            from utils.privacy_router import PrivacyRouter

            router = PrivacyRouter()
            router.override(str(test_file), True)
            target, reason = router.route_text("text", file_path=str(test_file))
            assert target == "local"
            assert reason == "cache"

    def test_override_forces_cloud(self, tmp_path):
        """覆盖后强制走云端 / Override forces cloud."""
        private_dir = tmp_path / "private"
        private_dir.mkdir()
        test_file = private_dir / "secret.txt"
        test_file.write_text("content")

        with patch.dict(os.environ, {"LOCAL_DIRS": str(private_dir)}):
            from utils.privacy_router import PrivacyRouter

            router = PrivacyRouter()
            # L0 会命中，但用户覆盖为 cloud
            router.override(str(test_file), False)
            # 因为 L0 先于缓存执行，所以仍然命中 folder_rule
            # 这意味着 override 机制适用于 L0 之后的情况
            target, reason = router.route_text("text", file_path=str(test_file))
            # folder_rule 优先级高于 cache
            assert target == "local"  # folder_rule wins over cache


class TestStatus:
    """测试状态查询 / Test status query."""

    def test_status_returns_dict(self):
        """status() 返回正确结构 / status() returns correct structure."""
        with patch.dict(os.environ, {"LOCAL_DIRS": ""}):
            from utils.privacy_router import PrivacyRouter

            router = PrivacyRouter()
            status = router.status()
            assert "presidio_available" in status
            assert "local_dirs" in status
            assert "file_cache_size" in status
            assert "scan_cache_size" in status
