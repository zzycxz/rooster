# src/utils/privacy_router.py
"""隐私路由器：检测敏感数据，决定发往本地模型还是云端。"""  # Privacy router: detect sensitive data, route to local or cloud.
# 最高原则：宁可漏检，不能卡住用户。任何异常都放行。

import hashlib
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


class PrivacyRouter:
    """四层漏斗隐私路由器 / Four-layer funnel privacy router.

    L0: 文件夹路径匹配（0ms）
    缓存旁路（0ms）
    L1: Presidio PII 扫描（5-20ms）
    L3: 策略规则兜底（0ms）

    任何一层命中 → 走本地（如果本地可用）
    全部未命中或任何异常 → 放行到云端
    """

    _MAX_SCAN_TEXT = 5000  # Presidio 扫描最大字符数 / Max chars for Presidio scan
    _MAX_HASH_KB = 512  # 文件 hash 最大读取 KB / Max KB to read for file hash

    def __init__(self):
        self._analyzer = None
        self._local_dirs: list[Path] = []
        self._FILE_CACHE: dict[str, bool] = {}  # file_hash → is_sensitive
        self._SCAN_CACHE: dict[str, bool] = {}  # text_hash → is_sensitive
        self._load_config()
        self._init_presidio()

    # ─── 配置加载 / Config loading ───

    def _load_config(self):
        dirs_str = os.getenv("LOCAL_DIRS", "")
        for d in dirs_str.split(","):
            d = d.strip()
            if d:
                self._local_dirs.append(Path(d).expanduser().resolve())

    def _init_presidio(self):
        try:
            from presidio_analyzer import AnalyzerEngine

            self._analyzer = AnalyzerEngine()
            self._register_custom_recognizers()
            logger.info("[PrivacyRouter] Presidio 初始化成功，L1 PII 扫描可用")
        except ImportError:
            logger.info("[PrivacyRouter] Presidio 未安装，L1 PII 扫描不可用（pip install presidio-analyzer）")
        except Exception as e:
            logger.warning(f"[PrivacyRouter] Presidio 初始化失败: {e}")

    def _register_custom_recognizers(self):
        """注册中文 PII 识别器 / Register Chinese PII recognizers."""
        from presidio_analyzer import Pattern, PatternRecognizer

        # 中国手机号 / Chinese phone number
        self._analyzer.registry.add_recognizer(
            PatternRecognizer(
                supported_entity="CN_PHONE",
                patterns=[Pattern(name="cn_phone", regex=r"1[3-9]\d{9}", score=0.8)],
            )
        )
        # 中国身份证号 / Chinese ID card number
        self._analyzer.registry.add_recognizer(
            PatternRecognizer(
                supported_entity="CN_ID_CARD",
                patterns=[
                    Pattern(
                        name="cn_id",
                        regex=r"[1-9]\d{5}(19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\d{3}[\dXx]",
                        score=0.85,
                    )
                ],
            )
        )
        # 银行卡号 / Bank card number
        self._analyzer.registry.add_recognizer(
            PatternRecognizer(
                supported_entity="CN_BANK_CARD",
                patterns=[Pattern(name="cn_bank", regex=r"6[0-9]{15,18}", score=0.6)],
            )
        )

    # ─── 文本路由 / Text routing ───

    def route_text(self, text: str, file_path: str | None = None) -> tuple[str, str]:
        """判断文本应走本地还是云端 / Decide local or cloud for text.

        Returns: ("local"|"cloud", reason)
        任何异常直接放行，不卡用户。/ Any exception → pass through, never block.
        """
        try:
            # L0: 文件夹路径匹配 / Folder path match
            if file_path and self._local_dirs and self._in_local_zone(file_path):
                return "local", "folder_rule"

            # 缓存旁路 / Cache bypass
            if file_path:
                h = self._file_hash(file_path)
                if h in self._FILE_CACHE:
                    return ("local" if self._FILE_CACHE[h] else "cloud"), "cache"

            # L1: Presidio 扫描 / Presidio scan
            if self._analyzer and text and self._presidio_scan(text):
                self._cache_result(file_path, True)
                return "local", "pii_detected"

            # 全部未命中 → 云端 / All missed → cloud
            self._cache_result(file_path, False)
            return "cloud", "safe"

        except Exception as e:
            logger.warning(f"[PrivacyRouter] route_text 异常，放行: {e}")
            return "cloud", "error_fallback"

    # ─── 图片路由 / Image routing ───

    def route_image(self, source_tool: str, ocr_text: str | None = None) -> tuple[str, str]:
        """判断截图应发原图还是文字描述 / Decide original image or text description.

        Returns: ("local"|"cloud", reason)
        任何异常直接放行，不卡用户。/ Any exception → pass through.
        """
        try:
            # 有 OCR 文字 → 过 Presidio / Has OCR text → scan with Presidio
            if ocr_text and self._analyzer and self._presidio_scan(ocr_text):
                return "local", "image_pii"

            # 无 PII 或无 OCR → 放行 / No PII or no OCR → allow
            return "cloud", "image_safe"

        except Exception as e:
            logger.warning(f"[PrivacyRouter] route_image 异常，放行: {e}")
            return "cloud", "error_fallback"

    # ─── 用户覆盖 / User override ───

    def override(self, file_path: str, is_sensitive: bool):
        """用户手动覆盖路由结果 / User manually override routing result.

        注意：L0 文件夹规则（LOCAL_DIRS）优先级高于缓存，位于 LOCAL_DIRS 目录内的
        文件即使调用 override(False) 也仍会命中 folder_rule 走本地。
        Note: L0 folder rule (LOCAL_DIRS) takes priority over cache; files inside
        LOCAL_DIRS still route local even after override(False).
        """
        try:
            h = self._file_hash(file_path)
            self._FILE_CACHE[h] = is_sensitive
        except Exception:
            pass

    # ─── 状态查询 / Status query ───

    def status(self) -> dict:
        """返回当前隐私路由器状态 / Return current privacy router status."""
        return {
            "presidio_available": self._analyzer is not None,
            "local_dirs": [str(d) for d in self._local_dirs],
            "file_cache_size": len(self._FILE_CACHE),
            "scan_cache_size": len(self._SCAN_CACHE),
        }

    # ─── 内部方法 / Internal methods ───

    def _in_local_zone(self, path: str) -> bool:
        p = Path(path).expanduser().resolve()
        return any(p.is_relative_to(d) for d in self._local_dirs)

    def _presidio_scan(self, text: str) -> bool:
        key = hashlib.md5(text[:2000].encode()).hexdigest()
        if key in self._SCAN_CACHE:
            return self._SCAN_CACHE[key]
        truncated = text[: self._MAX_SCAN_TEXT]
        # 先扫中文，再扫英文（邮箱、信用卡等英文格式 PII）/ Scan Chinese then English
        # 某些环境可能没有中文识别器，需逐个 try / Some envs lack Chinese recognizers
        results = []
        for lang in ("zh", "en"):
            try:
                results = self._analyzer.analyze(text=truncated, language=lang)
                if results:
                    break
            except Exception:
                continue
        hit = len(results) > 0
        self._SCAN_CACHE[key] = hit
        return hit

    def _file_hash(self, path: str) -> str:
        """流式读取文件头部计算 hash / Stream-read file head for hash."""
        h = hashlib.md5()
        with open(path, "rb") as f:
            h.update(f.read(self._MAX_HASH_KB * 1024))
        return h.hexdigest()

    def _cache_result(self, file_path: str | None, is_sensitive: bool):
        if file_path:
            h = self._file_hash(file_path)
            self._FILE_CACHE[h] = is_sensitive


# 全局单例 / Global singleton
_router: PrivacyRouter | None = None


def get_privacy_router() -> PrivacyRouter:
    """获取全局 PrivacyRouter 实例 / Get global PrivacyRouter instance."""
    global _router
    if _router is None:
        _router = PrivacyRouter()
    return _router
