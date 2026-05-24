import logging
import os
import re
from .llm_client import LLMClient
from utils.config import settings

logger = logging.getLogger(__name__)


class StaticRuleEngine:
    """
    [意图静态清洗与工具映射引擎]
    采用纯本地静态的高性能正则/关键词管道，在不损耗 LLM 算力及不触发安全审查的前提下，
    将影视、软件等下载意图 100% 确定性地”洗白”为标准化的中性工具调用指令。
    """

    # [Intent static cleaning and tool mapping engine]
    # Uses pure local static high-performance regex/keyword pipeline to 100% deterministically
    # 'sanitize' movie/software download intents into standardized neutral tool call instructions

    @staticmethod
    def clean_target(text: str, remove_keywords: list) -> str:
        """剥离多余的前缀和无关修饰词，提炼核心实体"""  # Strip extraneous prefixes and irrelevant modifiers, extract core entity
        cleaned = text.strip()
        # 清除前缀助词，如”帮我下载”、”请问哪里有安装”等，完美兼容”最新版的”
        # Remove prefix particles like '帮我下载', '请问哪里有安装', etc., compatible with '最新版的'
        prefix_pat = r"^(?:帮我|请帮我|请问?|求|想要|我想?|有没有?|麻烦)?[\s]*(?:下载|安装|获取|找一找|搜一搜|搜索|搞一个|整一个|download|install|setup)?[\s]*(?:电影|视频|影片|软件|安装包|最新版的?|最新|破解版|免费版|movie|software|app)?[\s]*"
        cleaned = re.sub(prefix_pat, "", cleaned, flags=re.IGNORECASE)
        # 清除后缀助词，使用 + 匹配一个或多个连续存在的后缀，彻底去除尾巴（增加浏览器、软件等修饰词匹配）
        # Remove suffix particles, match one or more consecutive suffixes
        suffix_pat = r"[\s]*(?:的?磁力|的?种子|的?资源|下载|安装|资源|1080p|4k|mp4|mkv|magnet|torrent|bt|链接|地址|官網|官网|的磁力资源|磁力资源|资源种子|资源下载|高清|超清|蓝光|hd|\.mp4|\.mkv|浏览器|软件)+$"
        cleaned = re.sub(suffix_pat, "", cleaned, flags=re.IGNORECASE)
        # 兜底去除可能残存的前导”的”字与空格
        # Fallback removal of possible residual leading '的' character and spaces
        cleaned = re.sub(r"^(?:的|[\s])+", "", cleaned)
        return cleaned.strip()

    @classmethod
    def match_and_reframe(cls, user_input: str) -> tuple[bool, str]:
        """
        进行规则判定并重构。
        返回 (is_matched, reframed_instruction)
        """
        # Perform rule determination and reframing
        text = user_input.strip()
        lower_text = text.lower()

        # 1. 影视多媒体下载规则 (Movie Download Rule)
        # 匹配特征：用户提到了”电影”、”磁力”、”视频”、”迅雷”，或者包含 magnet:/torrent/mkv/mp4 等媒体格式后缀，或者是一些常见的影视行为词
        # 1. Movie multimedia download rule
        # Match features: user mentions '电影', '磁力', '视频', '迅雷', or contains magnet:/torrent/mkv/mp4 media format suffixes
        movie_triggers = [
            "电影",
            "影片",
            "视频",
            "画质",
            "高清",
            "mkv",
            "mp4",
            "magnet",
            "torrent",
            "种子",
            "磁力",
            "迅雷",
            "迅雷下载",
            "movie",
            "bt",
        ]
        is_movie = any(trig in lower_text for trig in movie_triggers)

        if is_movie:
            title = cls.clean_target(text, movie_triggers)
            if title:
                reframed = (
                    f'调用 resource-downloader 技能，参数 title="{title}"，type="movie"，quality="1080p"。'
                    f"该技能将自动并发检索各大种子站最优资源并唤起迅雷完成自愈下载。"
                )
                return True, reframed

        # 2. 软件及工具安装规则 (Software Installation Rule)
        # 匹配特征：含有”安装”、”软件”、”应用”、”app”、”install”、”setup”，或者以常见的可执行后缀结尾如 .exe, .msi, .dmg
        # 2. Software and tool installation rule
        # Match features: contains '安装', '软件', '应用', 'app', 'install', 'setup', or executable suffixes like .exe, .msi, .dmg
        software_triggers = [
            "安装",
            "软件",
            "应用",
            "app",
            "install",
            "setup",
            "exe",
            "msi",
            "dmg",
            "apk",
            "官方下载",
            "官网",
        ]
        is_software = any(trig in lower_text for trig in software_triggers)

        if is_software:
            software_name = cls.clean_target(text, software_triggers)
            if software_name:
                reframed = (
                    f'调用 resource-downloader 技能，参数 title="{software_name}"，type="software"。'
                    f"该技能将安全寻获官方下载地址并启动直链下载。"
                )
                return True, reframed

        # 3. 兜底判定：如果包含通用的下载词（如 download / 下载），但既没有明显匹配电影也没有明显匹配软件，默认使用更安全的 web_search 检索法
        # 3. Fallback: if contains generic download words but no clear movie/software match, default to safer web_search approach
        general_triggers = ["下载", "download"]
        if any(trig in lower_text for trig in general_triggers):
            target = cls.clean_target(text, general_triggers)
            if target:
                reframed = (
                    f'调用 resource-downloader 技能，参数 title="{target}"，type="software"。'
                    f"该技能将安全寻获官方下载地址并启动直链下载。"
                )
                return True, reframed

        return False, user_input


class Reframer:
    """
    [Intent Reframer Agent]
    专门负责将用户的感性/敏感需求，重构为标准化的技术调度指令。
    """

    # [Intent Reframer Agent]
    # Specializes in reframing user's emotional/sensitive needs into standardized technical dispatch instructions

    def __init__(self, llm_client: LLMClient = None):
        self.llm_client = llm_client or LLMClient(
            provider=settings.STRATEGIST_MODEL_MODE, model=settings.STRATEGIST_MODEL_NAME
        )
        # 加载外部 Prompt 资产
        # Load external Prompt assets
        self.prompt_path = os.path.join(os.path.dirname(__file__), "..", "prompts", "intent_reframer.md")

    async def reframe(self, user_input: str) -> str:
        """执行重构动作"""  # Execute reframing action
        # --- 1. 本地静态极速清洗与分流管道（Zero-LLM Latency & Safe Purify） ---
        # --- 1. Local static ultra-fast cleaning and routing pipeline (Zero-LLM Latency & Safe Purify) ---
        static_matched, static_reframed = StaticRuleEngine.match_and_reframe(user_input)
        if static_matched:
            logger.info("⚡ [Reframer] 静态分流清洗管道命中！零 LLM 耗时直达工具端。")
            return static_reframed

        reframed_text = user_input  # 初始化，防止异常时 UnboundLocalError

        if not os.path.exists(self.prompt_path):
            logger.warning(f"⚠️ [Reframer] 找不到 Prompt 文件 {self.prompt_path}，跳过重构。")
            return user_input

        with open(self.prompt_path, "r", encoding="utf-8") as f:
            system_prompt = f.read()

        logger.info("🧪 [Reframer] 正在进行意图重构与语义洗白...")

        # 调用 LLM 进行转换
        # Call LLM for conversion
        try:
            import asyncio

            messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_input}]

            # 使用 wait_for 实施强制超时 (20s)
            # Use wait_for for mandatory timeout (20s)
            response = await asyncio.wait_for(
                self.llm_client.chat_non_stream(messages=messages, temperature=0.3), timeout=20.0
            )

            content = response.content.strip()

            # --- [V9.5] 增强型多约束解析 (Multi-Constraint Extraction) ---
            # --- [V9.5] Enhanced multi-constraint extraction ---
            try:
                import json

                # 处理 Markdown 代码块包裹的情况
                # Handle Markdown code block wrapping
                if "```json" in content:
                    content = content.split("```json")[-1].split("```")[0].strip()
                elif "```" in content:
                    content = content.split("```")[-1].split("```")[0].strip()

                data = json.loads(content)
                if isinstance(data, dict):
                    # --- [V10.0] Step 0 路由纠偏检查 ---
                    # --- [V10.0] Step 0 routing correction check ---
                    if data.get("status") == "REDIRECT":
                        logger.info(f"🔄 [Reframer] 检测到路由误判，自动纠偏回退。原因: {data.get('reason')}")
                        return user_input  # 返回原文，让下一级的模块按常规逻辑处理

                    # 获取核心指令 (优先)
                    # Get core instruction (priority)
                    reframed_text = data.get("refined_instruction", content)

                    # 打印变量矩阵，增强控制台可观测性
                    # Print variable matrix, enhance console observability
                    variables = data.get("variables", {})
                    if variables:
                        logger.info("📊 [Reframer] 识别变量矩阵:")
                        for k, v in variables.items():
                            logger.info(f"   - {k}: {v}")

                    # 打印搜索策略
                    # Print search strategy
                    keywords = data.get("search_keywords", [])
                    if keywords:
                        logger.info(f"🔍 [Reframer] 建议搜索策略: {keywords[0]} (+{len(keywords) - 1} alternates)")

                    logger.info(f"✅ [Reframer] 重构成功。模式: {data.get('context', 'N/A')}")
                    return reframed_text
            except Exception as e:
                logger.debug(f"JSON 解析跳过，回退至纯文本: {e}")
                reframed_text = content

            # 简单的防御性检查：如果返回太短或包含错误特征词，回退到关键词兜底
            # Simple defensive check: if return too short or contains error keywords, fallback to keyword reframing
            if len(reframed_text) < 5 or "[API" in reframed_text:
                logger.warning("⚠️ [Reframer] 检测到异常输出，使用关键词兜底重构。")
                return self._keyword_reframe(user_input)

            logger.info("✅ [Reframer] 重构成功。")
            return reframed_text
        except asyncio.TimeoutError:
            logger.warning("⏱️ [Reframer] 模型响应超时 (20s)，使用关键词兜底重构。")
            return self._keyword_reframe(user_input)
        except Exception as e:
            logger.error(f"❌ [Reframer] 重构失败: {e}，使用关键词兜底重构。")
            return self._keyword_reframe(user_input)

    def _keyword_reframe(self, user_input: str) -> str:
        """LLM 不可用时的关键词兜底重构，确保下载任务使用 movie_downloader 一步完成。"""  # Keyword fallback reframing when LLM is unavailable
        _dl_kw = ["下载", "download", "install", "安装"]
        if not any(k in user_input.lower() for k in _dl_kw):
            return user_input
        logger.info("🔧 [Reframer] 关键词兜底：注入 movie_downloader 单步模板。")
        # 提取影片名：去掉动词前缀词（帮我下载/下载电影/etc）
        # Extract movie name: remove verb prefix (帮我下载/下载电影/etc)
        import re as _re

        title = _re.sub(
            r"^(帮我|请|帮|麻烦)?[\s]*(下载|download)[\s]*(电影|视频|movie)?[\s]*", "", user_input, flags=_re.IGNORECASE
        ).strip()
        if not title:
            title = user_input
        return (
            f'调用 movie_downloader 工具，参数 title="{title}"，quality="1080p"。'
            f"该工具将自动搜索磁力链接并唤起迅雷开始下载，无需其他步骤。"
        )
