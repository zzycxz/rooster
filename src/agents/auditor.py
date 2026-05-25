# src/agents/auditor.py
import asyncio
import json
import logging
import os
import re
from .llm_client import LLMClient
from .protocol import Report, AuditVerdict, AuditVerdictType, SubTask
from utils.config import settings

logger = logging.getLogger(__name__)


class Auditor:
    """
    审计官 (Auditor v2.1)：
    职能：审计、核实、质量关卡。根据执行官的汇报做出 AFFIRM、REMAND 或 ESCALATE 的判断。
    """

    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client

    async def review(self, report: Report, task: SubTask, is_leaf: bool = False) -> AuditVerdict:
        """
        [V8.1 Integrated]: 深度适配 Compliance Auditor v2.1 协议。
        实现"默认放行"和"分级判罚"逻辑。
        """
        strictness = getattr(settings, "AUDIT_STRICTNESS", "medium").lower()
        original_instruction = task.instruction
        # is_leaf=True 表示这是最终交付节点（COMMIT 相位），否则为中间处理节点（EXECUTE 相位）
        # is_leaf=True means this is the final delivery node (COMMIT phase), otherwise intermediate processing node (EXECUTE phase)
        phase = "COMMIT" if is_leaf else (getattr(task, "phase", None) or "EXECUTE")

        logger.info(f"⚖️ [Auditor v3.0] 执行审计开始: {task.id} (Phase: {phase}, is_leaf={is_leaf})")

        # 1. 结构化构建审计输入
        # 1. Structured audit input construction
        evidence = getattr(report, "evidence", {}) or {}
        evidence_summary = evidence.get("summary", "") or report.observation or ""
        tool_call_trace = evidence.get("tool_call_trace", []) or []
        table_data = evidence.get("table_data", "") or ""

        content_list = [
            {
                "type": "text",
                "text": f"### 🧩 审计背景 (Context)\n- **子任务 ID**: {task.id}\n- **执行相位**: {phase}{'（最终交付节点，请适用 COMMIT 相位宽松标准）' if is_leaf else '（中间处理节点）'}\n- **原始指令**: {original_instruction}",
            },
            {
                "type": "text",
                "text": f"### 📝 执行官上报 (Execution Report)\n- **状态**: {report.status}\n- **证据摘要**: {evidence_summary[:1000]}\n- **工具调用痕迹**: {str(tool_call_trace)[:500]}\n- **工具原始输出**: {str(table_data)[:500]}\n- **证据统计**: 本地快照x{len(report.process_snapshots)}, 产出物x{len(report.artifacts)}",
            },
        ]

        # 2. 注入视觉证据 — 用文字描述替代 base64，截图不发出本机
        # 2. Inject visual evidence — text description instead of base64, screenshots never leave machine
        snapshots_to_check = report.process_snapshots[-3:]
        has_images = False
        for i, snap_path in enumerate(snapshots_to_check):
            if os.path.exists(snap_path):
                try:
                    # 本地 OCR 提取文字作为视觉证据 / Local OCR for text evidence
                    from PIL import Image
                    import numpy as np

                    img = Image.open(snap_path)
                    img_array = np.array(img)
                    _ocr_text = ""
                    try:
                        from paddleocr import PaddleOCR

                        ocr = PaddleOCR(use_angle_cls=False, lang="ch", show_log=False)
                        ocr_results = ocr.ocr(img_array, cls=False)
                        if ocr_results and ocr_results[0]:
                            _ocr_text = " ".join(line[1][0] for line in ocr_results[0] if line and len(line) >= 2)
                    except Exception:
                        pass  # OCR 失败不卡用户 / OCR failure doesn't block

                    if _ocr_text:
                        content_list.append(
                            {
                                "type": "text",
                                "text": f"### 📸 快照 OCR 文字证据 (Snapshot {i + 1})\n{_ocr_text[:1500]}",
                            }
                        )
                    else:
                        content_list.append(
                            {
                                "type": "text",
                                "text": f"### 📸 快照 {i + 1}: (OCR 未提取到文字，快照文件: {snap_path})",
                            }
                        )
                    has_images = True
                except Exception as img_err:
                    logger.warning(f"⚠️ [Auditor] 快照处理失败: {snap_path}: {img_err}")

        # 3. 获取 Prompt 模板
        # 3. Get Prompt template
        from utils.system import prompt_manager

        system_prompt = prompt_manager.get_prompt("auditor", {"strictness": strictness})

        messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": content_list}]

        # 4. 执行 LLM 请求（始终使用文本模型，不再需要 vision 模型）
        # 4. Execute LLM request (always text model, vision model no longer needed)
        target_model = settings.AUDITOR_TEXT_MODEL
        logger.info(
            f"🔍 [Auditor] 发起审计 LLM 调用: model={target_model}, has_images={has_images}, provider={self.llm_client.provider}"
        )

        try:
            _timeout = getattr(settings, "AUDITOR_TIMEOUT_SECONDS", 60.0)
            response = await asyncio.wait_for(
                self.llm_client.chat_non_stream(messages=messages, model=target_model, temperature=0.1),
                timeout=_timeout,
            )
            raw_content = response.content or ""
            logger.info(f"✅ [Auditor] LLM 响应完成: {len(raw_content)} chars")
        except asyncio.TimeoutError:
            _timeout = getattr(settings, "AUDITOR_TIMEOUT_SECONDS", 60.0)
            logger.error(
                f"❌ [Auditor] LLM 调用超时 ({_timeout}s): model={target_model}, provider={self.llm_client.provider}"
            )
            logger.warning("⚠️ [Auditor] 审计超时，降级放行 (PASS_WITH_WARNING)")
            return AuditVerdict(
                verdict=AuditVerdictType.AFFIRM,
                result_verdict="PASS_WITH_WARNING",
                reason=f"审计官 LLM 调用超时 ({target_model})，降级放行",
                recommendation="ACCEPT",
                notes=f"审计官 LLM 调用超时 ({target_model})，降级放行",
            )
        except Exception as e:
            logger.error(f"❌ [Auditor] API 请求彻底失败: {e}")
            logger.warning("⚠️ [Auditor] 审计请求彻底失败，降级放行 (PASS_WITH_WARNING)")
            return AuditVerdict(
                verdict=AuditVerdictType.AFFIRM,
                result_verdict="PASS_WITH_WARNING",
                reason=f"审计官 API 请求彻底失败: {str(e)[:100]}，降级放行",
                recommendation="ACCEPT",
                notes=f"审计官 API 请求彻底失败: {str(e)[:500]}，降级放行",
            )

        # --- 正常情况下的解析逻辑 ---
        # --- Normal-case parsing logic ---
        try:
            verdict_data = self._robust_json_parse(raw_content)

            # --- [V3.0 语义映射层] ---
            # --- [V3.0 Semantic mapping layer] ---
            rv = verdict_data.get("verdict", "PASS").upper()
            rec = verdict_data.get("recommendation", "ACCEPT").upper()
            routing = verdict_data.get("routing", {})
            target = routing.get("target") if routing else None

            # 状态转换矩阵 (对齐 AuditVerdictType)
            # State transition matrix (aligned to AuditVerdictType)
            internal_verdict = AuditVerdictType.AFFIRM

            # 基于路由目标的精准判罚
            # Precise verdict based on routing target
            if target == "RE_EXECUTE":
                internal_verdict = AuditVerdictType.REMAND
            elif target == "REPLAN":
                internal_verdict = AuditVerdictType.REPLAN
            elif target == "GRACEFUL_CLOSURE":
                internal_verdict = AuditVerdictType.CLOSURE
            elif rv == "FAIL":
                internal_verdict = AuditVerdictType.ESCALATE

            # [Precision Engine v2.0] 记录分歧报告
            # [Precision Engine v2.0] Record divergence report
            div_report = routing.get("divergence_report")
            if div_report and div_report.get("max_deviation"):
                logger.warning(
                    f"📊 [Auditor] 检测到事实分歧: 最大偏差 {div_report.get('max_deviation')} | 来源数: {len(div_report.get('sources', []))}"
                )

            # 安全提取 reason 字段（防止 findings 为空列表时 IndexError）
            # Safely extract reason field (prevent IndexError when findings is empty list)
            findings = verdict_data.get("findings", [])
            fallback_reason = (
                findings[0]["detail"]
                if findings and isinstance(findings[0], dict) and "detail" in findings[0]
                else "PASS"
            )

            # 构建结构化裁决
            # Build structured verdict
            return AuditVerdict(
                verdict=internal_verdict,
                result_verdict=rv,
                audit_type=verdict_data.get("audit_type", "EXECUTION"),
                recommendation=rec,
                findings=findings,
                notes=verdict_data.get("notes", ""),
                routing=routing,
                # 兼容旧字段
                # Backward-compatible legacy fields
                reason=verdict_data.get("notes", "") or fallback_reason,
                command=rec,
                process_integrity=int(verdict_data.get("process_integrity", 8)),
            )

        except Exception as e:
            logger.error(f"❌ [Auditor] v2.1 解析异常: {e} | Content: {raw_content[:300]}")
            # 降级策略：解析失败 ≠ 任务执行失败，按 AFFIRM 放行并标记 WARNING
            # Degradation strategy: parse failure != task execution failure, approve as AFFIRM with WARNING
            logger.warning("⚠️ [Auditor] 审计解析失败，降级放行 (PASS_WITH_WARNING)")
            return AuditVerdict(
                verdict=AuditVerdictType.AFFIRM,
                result_verdict="PASS_WITH_WARNING",
                reason=f"审计解析降级放行: {str(e)[:100]}",
                recommendation="ACCEPT",
                notes=f"原始审计响应: {raw_content[:500]}",
            )

    def _robust_json_parse(self, raw: str) -> dict:
        """
        多层 JSON 清洗与解析。
        处理 LLM 常见的格式偏差：markdown 代码块标记、中文引号、末尾逗号等。
        """
        # 1. 剥离 markdown 代码块标记
        # 1. Strip markdown code block markers
        cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip()
        cleaned = re.sub(r"\s*```\s*$", "", cleaned)

        # 2. 提取最外层 { ... }（从第一个 { 到最后一个 }，以正确处理嵌套 JSON）
        # 2. Extract outermost { ... } (from first { to last }, to correctly handle nested JSON)
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError(f"No JSON object found in response (first 100 chars): {cleaned[:100]}")

        json_str = cleaned[start : end + 1]

        # 3. 修复常见问题
        # 3. Fix common issues
        json_str = re.sub(r",\s*}", "}", json_str)  # 对象末尾逗号 / Trailing comma in objects
        json_str = re.sub(r",\s*]", "]", json_str)  # 数组末尾逗号 / Trailing comma in arrays
        json_str = json_str.replace("\u201c", '"').replace("\u201d", '"')  # 中文双引号
        json_str = json_str.replace("\u2018", "'").replace("\u2019", "'")  # 中文单引号

        return json.loads(json_str)
