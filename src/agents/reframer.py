import logging
import os
import re
from .llm_client import LLMClient
from utils.config import settings

logger = logging.getLogger(__name__)



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

    async def reframe(self, user_input: str, session_id: str = None) -> str:
        """执行重构动作"""  # Execute reframing action
        
        history_context = ""
        if session_id:
            try:
                from sessions.store import global_session_store
                session = global_session_store.get_or_create(session_id)
                if session.history:
                    lines = []
                    for m in session.history[-6:]:
                        role_str = "用户" if m.role == "user" else "AI助手"
                        content_preview = m.content[:500] + "..." if len(m.content) > 500 else m.content
                        lines.append(f"{role_str}: {content_preview}")
                    history_context = "\n".join(lines)
            except Exception as e:
                logger.warning(f"⚠️ [Reframer] 获取 Session 历史失败: {e}")

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

            messages = [{"role": "system", "content": system_prompt}]
            
            if history_context:
                user_msg = f"【历史对话上下文】\n{history_context}\n\n【当前用户输入】\n{user_input}\n\n请结合上下文，明确用户当前输入中代词（如“这个”、“刚才的”）所指代的具体对象，并将其重构为包含明确对象的完整独立指令。如果是独立的全新请求，请忽略上下文直接重构。"
            else:
                user_msg = user_input
                
            messages.append({"role": "user", "content": user_msg})

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
                    if data.get("status") == "REDIRECT":
                        logger.info(f"🔄 [Reframer] 检测到路由误判，自动纠偏回退。原因: {data.get('reason')}")
                        return user_input

                    # --- [V10.1] 歧义拦截：CLARIFICATION_NEEDED ---
                    # LLM 判定实体存在多版本歧义，Reframer 向上传递问询信号。
                    # 使用特殊前缀让 Router 识别，而非将其当成正常指令交给 MissionRunner。
                    if data.get("status") == "CLARIFICATION_NEEDED":
                        question = data.get("question", "请问您想要哪个版本？")
                        options = data.get("options", [])
                        logger.info(f"🤔 [Reframer] 检测到歧义实体，需要用户确认：{question}")
                        # 用 JSON 序列化保留结构，Router 可完整解析并格式化展示
                        import json as _json
                        payload = _json.dumps({"question": question, "options": options}, ensure_ascii=False)
                        return f"__CLARIFICATION_NEEDED__:{payload}"

                    # 获取核心指令 (优先)
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
        """LLM 不可用时的关键词兜底重构，确保下载任务使用 resource-downloader 技能一步完成。"""  # Keyword fallback reframing when LLM is unavailable
        _dl_kw = ["下载", "download", "install", "安装", "获取", "找一下"]
        if not any(k in user_input.lower() for k in _dl_kw):
            return user_input
        logger.info("🔧 [Reframer] 关键词兜底：注入 resource-downloader 单步模板。")
        # 提取目标名称：去掉动词前缀词（帮我下载/下载电影/etc）
        # Extract target name: remove verb prefix (帮我下载/下载电影/etc)
        import re as _re

        title = _re.sub(
            r"^(帮我|请|帮|麻烦)?[\s]*(下载|download|获取|找一下)[\s]*(电影|视频|movie|软件|安装包)?[\s]*",
            "",
            user_input,
            flags=_re.IGNORECASE,
        ).strip()
        if not title:
            title = user_input
        return (
            f'调用 resource-downloader 技能，参数 title="{title}"，type="movie"，quality="1080p"。'
            f"该技能将自动并发检索各大种子站最优资源并唤起迅雷完成自愈下载。"
        )
