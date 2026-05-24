import os
import logging

logger = logging.getLogger(__name__)

# Hard line ceilings — if a file exceeds these, auto-condense fires.
_SOUL_LINE_LIMIT = 200  # SOUL identity/style should stay crisp
_USER_LINE_LIMIT = 150  # USER profile similarly compact
# Target after condensing (aim for ~60% of limit)
_SOUL_LINE_TARGET = 120
_USER_LINE_TARGET = 90


class SoulLoader:
    """
    五层 System Prompt 架构组装器。
    按 Layer 1 (SOUL) -> Layer 2 (USER) -> Layer 3 (SKILLS) -> Layer 4 (LTM) -> Layer 5 (BASE) 顺序合并。

    自动维护 SOUL.md / USER.md 的长度：
    - 超过行数阈值时，调用 LLM 对各 ## 章节就地精简。
    - 不删除任何章节，不添加新章节；仅压缩冗余词句、保留核心条目。
    - 精简结果直接写回原文件（原子覆盖），不影响本次注入（仍注入原文）。
    """

    def __init__(
        self, rooster_dir: str = ".rooster", prompts_dir: str = "src/prompts", llm_client=None, model: str = ""
    ):
        self.rooster_dir = os.path.abspath(rooster_dir)
        self.prompts_dir = os.path.abspath(prompts_dir)
        self.soul_path = os.path.join(self.rooster_dir, "SOUL.md")
        self.user_path = os.path.join(self.rooster_dir, "USER.md")
        self._llm_client = llm_client
        self._model = model
        self._background_tasks: set = set()

    def _read_file(self, path: str, label: str) -> str:
        """通用文件读取逻辑，包含错误日志。"""
        if not os.path.exists(path):
            logger.warning(f"⚠️ {label} 文件不存在: {path}")
            return ""
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception as e:
            logger.error(f"❌ 读取 {label} 失败: {str(e)}")
            return ""

    def _check_and_schedule_condense(self, path: str, label: str, line_limit: int, line_target: int) -> None:
        """
        检查文件行数，超限时后台异步精简。
        精简由 LLM 执行，本次调用不等待，下次加载时生效。
        """
        if not self._llm_client:
            logger.debug(f"[SoulLoader] {label} 行数守门：无 LLM client，跳过自动精简")
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.read().splitlines()
        except Exception:
            return

        count = len(lines)
        logger.info(f"[SoulLoader] {label}.md 当前 {count} 行 (上限 {line_limit})")
        if count <= line_limit:
            return

        logger.warning(f"[SoulLoader] {label}.md 超过 {line_limit} 行，触发后台自动精简 → 目标 {line_target} 行")
        import asyncio

        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(self._condense_file(path, label, line_target))
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)
        except RuntimeError:
            # No running event loop (e.g. called from a sync context at startup)
            asyncio.run(self._condense_file(path, label, line_target))
        except Exception as e:
            logger.warning(f"[SoulLoader] 调度精简任务失败: {e}")

    async def _condense_file(self, path: str, label: str, line_target: int) -> None:
        """路由到 SOUL 或 USER 专用精简策略。"""
        if label == "SOUL":
            await self._condense_soul(path, line_target)
        else:
            await self._condense_user(path, line_target)

    async def _condense_soul(self, path: str, line_target: int) -> None:
        """
        SOUL.md 精简策略：
        仅允许压缩 ## Core Behavior 和 ## Tone & Style 两个章节。
        ## Identity / ## Hard Limits / ## Memory Protocol / ## Evolution
        均为人类只读保护字段，原文完整保留，一字不改。
        """
        try:
            with open(path, "r", encoding="utf-8") as f:
                original = f.read()
        except Exception as e:
            logger.error(f"[SoulLoader] 读取 SOUL 失败: {e}")
            return

        prompt = (
            f"你是 Rooster AI 系统的 SOUL.md 维护专家。\n"
            f"SOUL.md 定义 AI 的身份、行为、硬限制和记忆协议。\n\n"
            f"**保护字段（原文完整保留，绝对不能修改任何内容）：**\n"
            f"- ## Identity（身份定义）\n"
            f"- ## Hard Limits（硬限制）\n"
            f"- ## Memory Protocol（记忆协议）\n"
            f"- ## Evolution（演进规则）\n\n"
            f"**允许精简的字段（目标总行数 ≤ {line_target} 行）：**\n"
            f"- ## Core Behavior：删除已被后续条目覆盖/重复的旧条目，保留最新的行为约束\n"
            f"- ## Tone & Style：合并表达相同意思的条目，压缩为 ≤15 字的短句\n\n"
            f"**精简规则：**\n"
            f"1. 保护字段的内容一字不差地原样输出\n"
            f"2. 允许精简的字段：删除重复/被覆盖的条目，保留每条独特约束\n"
            f"3. 不新增任何条目，不删除任何章节标题\n"
            f"4. 只输出完整的精简后 SOUL.md 内容，不加任何解释\n\n"
            f"原文件内容：\n\n{original}"
        )

        await self._run_condense_llm(path, "SOUL", original, prompt)

    async def _condense_user(self, path: str, line_target: int) -> None:
        """
        USER.md 精简策略：
        仅允许处理 ## Active Projects 和 ## Preferences 两个章节。
        ## Basic Info / ## Hard Requirements / ## Evolution Triggers
        均为稳定字段，原文完整保留。
        """
        try:
            with open(path, "r", encoding="utf-8") as f:
                original = f.read()
        except Exception as e:
            logger.error(f"[SoulLoader] 读取 USER 失败: {e}")
            return

        prompt = (
            f"你是 Rooster AI 系统的 USER.md 维护专家。\n"
            f"USER.md 是用户的动态档案，记录项目状态和偏好。\n\n"
            f"**保护字段（原文完整保留，绝对不能修改任何内容）：**\n"
            f"- ## Basic Info（用户基本信息）\n"
            f"- ## Hard Requirements（硬性要求）\n"
            f"- ## Evolution Triggers（演进触发规则）\n\n"
            f"**允许精简的字段（目标总行数 ≤ {line_target} 行）：**\n"
            f"- ## Active Projects：移除已明确完成/不再活跃的项目条目；保留所有仍在进行的项目\n"
            f"- ## Preferences：合并表达相同偏好的重复条目，每个偏好主题只保留最新/最具体的描述\n\n"
            f"**精简规则：**\n"
            f"1. 保护字段的内容一字不差地原样输出\n"
            f"2. Active Projects：只删除有明确完成信号的条目（'done'/'shipped'/'live' 或日期超过 90 天）\n"
            f"3. Preferences：若两条条目表达相同意思，保留更具体/更新的一条\n"
            f"4. 不新增任何条目，不删除任何章节标题\n"
            f"5. 只输出完整的精简后 USER.md 内容，不加任何解释\n\n"
            f"原文件内容：\n\n{original}"
        )

        await self._run_condense_llm(path, "USER", original, prompt)

    async def _run_condense_llm(self, path: str, label: str, original: str, prompt: str) -> None:
        """调用 LLM 执行精简并原子写回文件。"""
        try:
            if hasattr(self._llm_client, "chat_non_stream"):
                resp = await self._llm_client.chat_non_stream(
                    messages=[{"role": "user", "content": prompt}],
                    model=self._model,
                )
                condensed = resp.content.strip()
            elif hasattr(self._llm_client, "chat_stream"):
                condensed = ""
                async for delta in self._llm_client.chat_stream(
                    model=self._model,
                    messages=[{"role": "user", "content": prompt}],
                ):
                    if delta.content:
                        condensed += delta.content
                condensed = condensed.strip()
            else:
                logger.warning("[SoulLoader] LLM client 不支持调用，跳过精简")
                return

            if not condensed or len(condensed) < 50:
                logger.warning(f"[SoulLoader] LLM 返回内容过短，放弃写回 {label}.md")
                return

            orig_lines = len(original.splitlines())
            new_lines = len(condensed.splitlines())
            # 安全检查：精简结果不应短于原文的 30%（防止 LLM 丢失保护字段）
            if new_lines < orig_lines * 0.3:
                logger.warning(f"[SoulLoader] {label}.md 精简结果异常 ({orig_lines} → {new_lines} 行，< 30%)，放弃写回")
                return

            logger.info(f"[SoulLoader] {label}.md 精简完成: {orig_lines} → {new_lines} 行，写回")
            tmp_path = path + ".tmp"
            try:
                with open(tmp_path, "w", encoding="utf-8") as f:
                    f.write(condensed)
                os.replace(tmp_path, path)
            except Exception as write_err:
                logger.error(f"[SoulLoader] {label}.md 写回失败: {write_err}")
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
                raise

        except Exception as e:
            logger.error(f"[SoulLoader] {label} 自动精简失败: {e}")

    def load_soul(self) -> str:
        """Layer 1: 读取 SOUL.md，超限时后台调度精简（本次仍注入原文）"""
        content = self._read_file(self.soul_path, "SOUL")
        if content and os.path.exists(self.soul_path):
            self._check_and_schedule_condense(self.soul_path, "SOUL", _SOUL_LINE_LIMIT, _SOUL_LINE_TARGET)
        return content

    def load_user(self) -> str:
        """Layer 2: 读取 USER.md，超限时后台调度精简（本次仍注入原文）"""
        content = self._read_file(self.user_path, "USER")
        if content and os.path.exists(self.user_path):
            self._check_and_schedule_condense(self.user_path, "USER", _USER_LINE_LIMIT, _USER_LINE_TARGET)
        return content

    def load_base_prompt(self, filename: str = "base.md") -> str:
        """Layer 5: 读取固定的执行原则层"""
        path = os.path.join(self.prompts_dir, filename)
        return self._read_file(path, f"Prompt({filename})")

    def build_system_prompt(
        self, base_prompt_name: str = "base.md", ltm_context: str = "", skills_digest: str = ""
    ) -> str:
        """
        核心出口：按五层架构合并所有层。
        """
        layers = []

        # Layer 1: SOUL 层（身份锚定）
        soul_content = self.load_soul()
        if soul_content:
            layers.append(f"# LAYER 1: SOUL (IDENTITY & STYLE)\n\n{soul_content}")

        # Layer 2: USER 层（用户画像）
        user_content = self.load_user()
        if user_content:
            layers.append(f"# LAYER 2: USER PROFILE & CONTEXT\n\n{user_content}")

        # Layer 3: SKILLS 层（技能摘要）
        if skills_digest:
            layers.append(f"# LAYER 3: AVAILABLE SKILLS\n\n{skills_digest}")

        # Layer 4: 动态记忆层（LTM 召回）
        if ltm_context:
            layers.append(f"# LAYER 4: RELEVANT LONG-TERM MEMORY\n\n{ltm_context}")

        # Layer 5: 执行原则层（兜底原则）
        base_content = self.load_base_prompt(base_prompt_name)
        if base_content:
            layers.append(f"# LAYER 5: EXECUTION PRINCIPLES\n\n{base_content}")

        # 使用清晰的分隔符连接，便于日志调试
        separator = "\n\n" + "-" * 40 + "\n\n"
        return separator.join(layers)
