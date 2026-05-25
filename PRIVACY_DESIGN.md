# Rooster Privacy Router 设计文档

## 核心矛盾

截图发云端 = 隐私风险，截图走本地 = 能力不够。

解决方案：**不二选一，拆开处理。**

截图里真正敏感的不是图片本身，而是图片里的文字内容（账号、人名、地址、金额）。视觉理解能力（"这里有个按钮"、"这个图标是什么"）不涉及隐私。

```
截图 → 本地 OCR → Presidio 扫描
         ↓                ↓
      无 PII            有 PII
         ↓                ↓
    截图直发云端      本地模型理解布局
    强模型视觉分析    文字描述发云端
                     强模型文本推理
```

- 无 PII 时：`{"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}`
- 有 PII 时：`"屏幕上有一个登录按钮在坐标(320,480)，一个输入框在(320,400)"`

## 最高原则：宁可漏检，不能卡住用户

PrivacyRouter 是顾问，不是门卫。它能建议走本地，但绝不能阻止请求发出。

- 本地模型不可用 → 不走本地，直接放行到云端
- OCR 提取失败 → 不保守猜测，直接放行到云端
- Presidio 扫描异常 → 直接放行，不阻塞主流程
- 任何检测环节报错 → 记录警告日志，继续执行

## 触发范围：只拦截外发，不拦截落地

PrivacyRouter 只在**数据发往 LLM API** 时触发，不干预本地写入操作。

| 操作 | 是否触发路由 | 原因 |
|------|-------------|------|
| 文件读取 → 发给 LLM | **是** | 数据出本机，需要检测 |
| 截图 → 发给云端 Vision API | **是** | 数据出本机，需要检测 |
| 工具输出 → 发给 LLM | **是** | 数据出本机，需要检测 |
| 搜索结果存盘 | **否** | 数据只写入本地磁盘 |
| 大模型回复写入文件 | **否** | 数据只写入本地磁盘 |
| 下载文件到本地 | **否** | 数据只写入本地磁盘 |
| 网页内容缓存 | **否** | 数据只写入本地磁盘 |
| Memory 写入 SQLite | **否** | 数据只写入本地磁盘 |

简单说：**写磁盘不管，发网络才查。**

## 四层漏斗路由

PrivacyRouter 是一个分层漏斗，任何一层命中敏感就路由到本地，全部未命中才走云端。

```
请求 → L0 文件夹路径匹配 (0ms)
         ↓ 未命中
       缓存旁路 (0ms)
         ↓ 未命中
       L1 Presidio PII 扫描 (5-20ms)
         ↓ 未命中
       L3 策略规则兜底 (0ms)
         ↓ 未命中
       走云端
```

### 各层说明

| 层 | 触发条件 | 延迟 | 说明 |
|----|---------|------|------|
| L0 文件夹 | 路径匹配 `LOCAL_DIRS` | 0ms | 70%+ 请求在这里结束 |
| 缓存旁路 | 文件 hash 命中 | 0ms | 重复文件完全跳过 |
| L1 Presidio | PII 实体检测 | 5-20ms | 结构化文档大多在这里结束 |
| L3 策略规则 | 角色/来源类规则 | 0ms | 兜底补漏 |

关键：大多数请求在 L0 结束，整体平均延迟 < 5ms。

### L0 文件夹匹配

用户配置敏感目录，路径匹配即路由本地。这是整个设计里最稳定、成本最低的锚点。

```python
LOCAL_DIRS = os.getenv("LOCAL_DIRS", "").split(",")
# .env: LOCAL_DIRS=~/private,~/confidential,~/medical,~/财务
```

### L1 Presidio 扫描

使用 Microsoft Presidio 进行 PII 检测，支持自定义中文识别器。

```python
from presidio_analyzer import AnalyzerEngine

analyzer = AnalyzerEngine()
results = analyzer.analyze(text="我的手机号是 138xxxx1234", language="zh")
# → [PHONE_NUMBER]
```

自定义中文识别器：手机号、身份证号、银行卡号、地址模式等。

### L3 策略规则

硬编码的兜底规则：

- 记忆蒸馏/去重/审计操作 → 强制本地
- Evolution Engine 进化分析 → 强制本地

注意：桌面截图不再一刀切标记为敏感，而是走 OCR → Presidio 扫描流程，只有检测到 PII 才走本地。

## PrivacyRouter 核心类

```python
import hashlib
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


class PrivacyRouter:
    """四层漏斗隐私路由器：检测敏感数据，决定走本地还是云端。"""

    LOCAL_DIRS: list[str] = []  # 从 .env 加载
    _FILE_CACHE: dict[str, bool] = {}  # file_hash → is_sensitive
    _SCAN_CACHE: dict[str, bool] = {}  # text_hash → is_sensitive
    _MAX_SCAN_TEXT = 5000  # Presidio 扫描最大字符数
    _MAX_HASH_KB = 512  # 文件 hash 最大读取 KB

    def __init__(self):
        self._load_dirs()
        self._init_presidio()

    def _load_dirs(self):
        dirs = os.getenv("LOCAL_DIRS", "")
        self.LOCAL_DIRS = [d.strip() for d in dirs.split(",") if d.strip()]

    def _init_presidio(self):
        try:
            from presidio_analyzer import AnalyzerEngine

            self._analyzer = AnalyzerEngine()
            self._register_custom_recognizers()
        except ImportError:
            logger.warning("[PrivacyRouter] Presidio 未安装，L1 PII 扫描不可用")
            self._analyzer = None
        except Exception as e:
            logger.warning(f"[PrivacyRouter] Presidio 初始化失败: {e}")
            self._analyzer = None

    def _register_custom_recognizers(self):
        """注册中文 PII 识别器：手机号、身份证、银行卡等。"""
        from presidio_analyzer import Pattern, PatternRecognizer

        # 中国手机号
        phone_pattern = Pattern(name="cn_phone", regex=r"1[3-9]\d{9}", score=0.8)
        self._analyzer.registry.add_recognizer(
            PatternRecognizer(supported_entity="CN_PHONE", patterns=[phone_pattern])
        )

        # 中国身份证号
        id_pattern = Pattern(name="cn_id", regex=r"[1-9]\d{5}(19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\d{3}[\dXx]", score=0.85)
        self._analyzer.registry.add_recognizer(
            PatternRecognizer(supported_entity="CN_ID_CARD", patterns=[id_pattern])
        )

        # 银行卡号
        bank_pattern = Pattern(name="cn_bank", regex=r"6[0-9]{15,18}", score=0.6)
        self._analyzer.registry.add_recognizer(
            PatternRecognizer(supported_entity="CN_BANK_CARD", patterns=[bank_pattern])
        )

    # --- 文本路由 ---

    def route_text(self, text: str, file_path: str = None) -> tuple[str, str]:
        """
        判断文本应走本地还是云端。
        返回: ("local"|"cloud", reason)
        任何异常直接放行，不卡用户。
        """
        try:
            # L0: 文件夹路径匹配
            if file_path and self._in_local_zone(file_path):
                return "local", "folder_rule"

            # 缓存旁路
            if file_path:
                h = self._file_hash(file_path)
                if h in self._FILE_CACHE:
                    return ("local" if self._FILE_CACHE[h] else "cloud"), "cache"

            # L1: Presidio 扫描
            if self._analyzer and self._presidio_scan(text):
                self._cache_result(file_path, True)
                return "local", "pii_detected"

            # L3: 策略兜底（无额外规则时直接走云端）
            self._cache_result(file_path, False)
            return "cloud", "safe"

        except Exception as e:
            logger.warning(f"[PrivacyRouter] route_text 异常，放行: {e}")
            return "cloud", "error_fallback"

    # --- 图片路由 ---

    def route_image(self, image_base64: str, source_tool: str, ocr_text: str = None) -> tuple[str, str]:
        """
        判断截图应发原图还是文字描述。
        返回: ("local"|"cloud", reason)
        任何异常直接放行，不卡用户。
        """
        try:
            # 有 OCR 文字 → 过 Presidio
            if ocr_text and self._analyzer and self._presidio_scan(ocr_text):
                return "local", "image_pii"

            # 无 OCR 文字或无 PII → 允许发云端
            return "cloud", "image_safe"

        except Exception as e:
            logger.warning(f"[PrivacyRouter] route_image 异常，放行: {e}")
            return "cloud", "error_fallback"

    # --- 用户覆盖 ---

    def override(self, file_path: str, is_sensitive: bool):
        """用户手动覆盖路由结果。"""
        if file_path:
            h = self._file_hash(file_path)
            self._FILE_CACHE[h] = is_sensitive

    # --- 内部方法 ---

    def _in_local_zone(self, path: str) -> bool:
        p = Path(path).expanduser().resolve()
        return any(
            p.is_relative_to(Path(d).expanduser().resolve())
            for d in self.LOCAL_DIRS
        )

    def _presidio_scan(self, text: str) -> bool:
        key = hashlib.md5(text[:2000].encode()).hexdigest()
        if key in self._SCAN_CACHE:
            return self._SCAN_CACHE[key]
        results = self._analyzer.analyze(text=text[: self._MAX_SCAN_TEXT], language="zh")
        hit = len(results) > 0
        self._SCAN_CACHE[key] = hit
        return hit

    def _file_hash(self, path: str) -> str:
        """流式读取文件头部计算 hash，避免大文件撑爆内存。"""
        h = hashlib.md5()
        with open(path, "rb") as f:
            h.update(f.read(self._MAX_HASH_KB * 1024))
        return h.hexdigest()

    def _cache_result(self, file_path: str | None, is_sensitive: bool):
        if file_path:
            h = self._file_hash(file_path)
            self._FILE_CACHE[h] = is_sensitive
```

## 改造清单

### Step 1：基础设施（被所有上层依赖）

| 任务 | 文件 |
|------|------|
| PrivacyRouter 核心类 | `src/utils/privacy_router.py`（新建） |
| Presidio 依赖 | `pyproject.toml` 添加 `presidio-analyzer` |
| 配置项 | `.env` 添加 `LOCAL_DIRS` |

### Step 2：数据出口改造（并行）

#### Executor 截图脱敏

**文件**: `src/agents/executor.py`

- 首步图片注入前，调用 `PrivacyRouter.route_image()` 判断
  - 无 PII → 正常发 base64
  - 有 PII → 本地 OCR + 布局描述替代 base64
  - 本地模型不可用 → 直接放行发云端，不卡用户
- 工具输出中的 `[IMAGE_DATA:...]` strip 不再区分 provider，所有 provider 都 strip
- brain switch（local→cloud）保留，但有截图数据时禁止切换

#### Vision Analyzer 本地优先

**文件**: `src/models/vision_analyzer.py`

- 重排优先级：先尝试 local → 失败再走 cloud cascade
- 截图发云端前：本地 OCR → Presidio 检测
  - 有 PII → 不发原图，传 OCR 文字描述给云端做推理
  - 无 PII → 正常发原图
  - OCR 失败 → 直接放行，不卡用户

#### Auditor 截图→文字描述

**文件**: `src/agents/auditor.py`

- 读取截图后不传 base64
- 改为本地 OCR 提取文字 + 坐标信息
- 把文字描述作为审计证据发给云端
- OCR 失败 → 跳过截图审计，仅用文本审计，不卡用户

### Step 3：内部组件本地化（并行）

| 组件 | 文件 | 改动 |
|------|------|------|
| Memory Manager | `src/memory/manager.py` | deduplicator/auditor 使用 local client |
| Compactor | `src/agents/executor.py` | 使用 local client 创建 compactor |
| Evolution Engine | `src/evolution/engine.py` | 去除硬编码 `STRATEGIST_MODEL_NAME`，用传入的 client |
| Prompt Builder | `src/agents/prompt_builder.py` | 云端 provider 脱敏 Home/Desktop/Documents 路径 |

### Step 4：暴露 + 验证

| 任务 | 文件 |
|------|------|
| Gateway 隐私状态 API | `src/gateway/routes/models.py` 添加 `GET /api/privacy/status` |
| 测试 | `tests/test_privacy_router.py`（新建） |
| Lint | `ruff check src/ tests/` |

## 数据流总览

```
用户请求
  │
  ├─ 文本推理任务
  │    PrivacyRouter.route_text()
  │    ├─ L0 命中 → 本地模型（如果可用，否则放行云端）
  │    ├─ L1 命中 → 本地模型（如果可用，否则放行云端）
  │    ├─ 异常 → 放行云端
  │    └─ 全未命中 → 云端强模型（能力不降级）
  │
  ├─ 桌面操作（截图）
  │    PrivacyRouter.route_image()
  │    ├─ OCR 提取文字 → Presidio 扫描
  │    │   ├─ 有 PII → 本地模型理解布局 → 文字描述发云端
  │    │   ├─ 无 PII → 截图直发云端
  │    │   └─ OCR/Presidio 异常 → 放行云端
  │    └─ 本地模型不可用 → 放行云端
  │
  ├─ 文件操作
  │    PrivacyRouter.route_text(file_path=...)
  │    ├─ L0 命中（~/private/ 下）→ 本地模型
  │    └─ 全未命中 → 云端强模型
  │
  └─ 内部整理（记忆/压缩/进化）
       强制本地模型（不经过路由判断）
```

## 设计原则

1. **宁可漏检，不能卡住** — 任何检测环节异常都放行，PrivacyRouter 是顾问不是门卫
2. **按数据敏感度路由，不是全局开关** — 文本推理仍用云端强模型，不降级
3. **任何一层命中就走本地** — 宁可多走本地，不漏隐私
4. **文件夹是锚点** — 用户显式意图是最可靠的信号，70%+ 请求零成本结束
5. **截图拆开处理** — 敏感的是文字不是像素，OCR+描述替代原图
6. **内部整理强制本地** — 记忆/压缩/进化不需要强模型，本地足够
7. **用户可覆盖** — 自动判断不是最终答案，用户可以手动修正
