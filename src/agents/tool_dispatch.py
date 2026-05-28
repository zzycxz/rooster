"""
Tool dispatch and parsing — extracted from executor.py.

Handles:
- Tool call extraction from LLM output (native FC + XML fallback)
- JSON robustness repair
- Orchestrated tool execution (pre/post dispatch, vision strategy, verification)
- Self-healing via ReflectionEngine
"""

import asyncio
import io
import os
import re
import json
import base64
import logging
from typing import List, Dict, Any, Optional

from utils.config import settings
from utils.audit import audit_manager

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool call parsing
# ---------------------------------------------------------------------------


def extract_tool_calls_native(tool_calls_data: list) -> List[tuple]:
    """Extract tool calls from native Function Calling response (zero parsing risk)."""
    results = []
    for tc in tool_calls_data:
        fn = tc.get("function", {})
        name = fn.get("name", "").strip()
        raw_args = fn.get("arguments", "{}")
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except json.JSONDecodeError as e:
            _logger.warning(f"[FC] native arguments parse failed, attempting fix: {e}")
            try:
                args = json.loads(raw_args.replace("'", '"'))
            except Exception:
                args = {}
        if name:
            results.append((name, args))
        else:
            _logger.warning(f"[FC] skipping tool_call with empty function name: {tc}")
    return results


def extract_tool_calls(content: str) -> List[tuple]:
    """Extract tool calls from LLM output using balanced-brace algorithm + XML fallback."""
    pattern = r'<tool_code name="(.*?)">(.*?)(?:</tool_code>|$)'
    matches = re.finditer(pattern, content, re.DOTALL | re.IGNORECASE)
    results = []

    for match in matches:
        name = match.group(1).strip()
        raw_args = match.group(2).strip()

        clean_json = None
        if not raw_args:
            clean_json = {}
        else:
            json_candidate = _find_balanced_json(raw_args)
            if json_candidate:
                try:
                    clean_json = json.loads(json_candidate)
                except Exception:
                    try:
                        fixed = json_candidate.replace("\\", "\\\\").replace("'", '"')
                        clean_json = json.loads(fixed)
                    except Exception as e:
                        _logger.debug(f"JSON repair parse failed, will try XML extraction: {e}")

            if clean_json is None:
                xml_params = _extract_from_xml_tags(raw_args)
                if xml_params:
                    _logger.warning(f"JSON parse failed, extracted {len(xml_params)} params via XML tags")
                    clean_json = xml_params

        if clean_json is not None:
            results.append((name, clean_json))
        else:
            _logger.error(f"Cannot parse args for tool {name}: {raw_args[:100]}...")

    return results


def _find_balanced_json(s: str) -> Optional[str]:
    """Extract the first balanced brace block from messy text (with truncation self-heal)."""
    start = s.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(s)):
        if s[i] == "{":
            depth += 1
        elif s[i] == "}":
            depth -= 1
        if depth == 0:
            return s[start : i + 1]
    if depth > 0:
        _logger.warning(f"Truncated JSON detected, patching {depth} brace levels")
        return s[start:] + ("}" * depth)
    return None


def _extract_from_xml_tags(s: str) -> Optional[Dict[str, str]]:
    """Extract parameters from XML-like tags."""
    params = {}
    pc = chr(60) + chr(47) + "parameter" + chr(62)
    pc2 = chr(60) + chr(47) + "param" + chr(62)
    tag_pat = r"<parameter\s*=\s*(.*?)>(.*?)" + pc
    for tm in re.finditer(tag_pat, s, re.DOTALL | re.IGNORECASE):
        params[tm.group(1).strip()] = tm.group(2).strip()
    param_pat = r'<param\s*name\s*="(.*?)">(.*?)' + pc2
    for pm in re.finditer(param_pat, s, re.DOTALL | re.IGNORECASE):
        params[pm.group(1).strip()] = pm.group(2).strip()
    return params if params else None


# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------


async def execute_orchestrated_tool(
    run_id: str,
    config,
    name: str,
    args: Dict,
    step: int = 0,
    session_history: list = None,
    *,
    orchestrator=None,
    tool_registry=None,
    event_handler=None,
    llm_client=None,
    uia_cache=None,
    visual_buffer=None,
    memory_manager=None,
    reflection_engine_getter=None,
    policy_override=None,
) -> str:
    """
    Orchestrated tool execution flow.
    1. Pre-dispatch: arg correction, path expansion.
    2. Resource lock (blackboard): prevent concurrent writes to the same file by peer agents.
    3. Execution: clean tool call with self-healing.
    4. Post-dispatch: result refinement, vision strategy, verification loop.

    policy_override: 若非 None，使用该策略替代全局 PermissionPolicy（SANDBOXED 子代理专用）。
    """
    # --- Step 1: Orchestrator pre-processing ---
    if orchestrator:
        args = await orchestrator.pre_dispatch(name, args)

    tool = tool_registry.get_tool(name)
    if not tool:
        return f'<tool_response name="{name}">Error: Tool not found</tool_response>'

    # --- Permission policy check ---
    try:
        from utils.permission_policy import get_global_policy

        policy = policy_override if policy_override is not None else get_global_policy()
        risk_level = getattr(tool, "risk_level", "low")
        decision = policy.check(name, risk_level)
        if not decision.allowed:
            if getattr(decision, "requires_confirmation", False) and settings.CONFIRMATION_BEHAVIOR == "log":
                # "log" 模式：高风险工具发出警告但不阻断（不禁止用户行为）
                # "log" mode: high-risk tools emit warning but don't block (don't prohibit user behavior)
                _logger.warning(
                    f"[PermissionPolicy][LOG_MODE] Tool '{name}' requires confirmation (proceeding): {decision.reason}"
                )
                await event_handler.emit_event(
                    "security_warning",
                    session_key=config.session_key,
                    client_run_id=run_id,
                    data={"tool": name, "reason": decision.reason, "severity": "requires_confirmation"},
                )
            else:
                _logger.warning(f"[PermissionPolicy] Tool '{name}' blocked: {decision.reason}")
                return f'<tool_response name="{name}">{decision.to_error_message()}</tool_response>'
    except Exception as _perm_err:
        _logger.debug(f"[PermissionPolicy] Permission check skipped (degraded): {_perm_err}")

    # --- Input Guard: path traversal / URL / command injection scan ---
    try:
        from utils.security.input_guard import InputGuard

        guard = InputGuard.get()
        report = guard.scan_args(name, args)
        if not report.is_clean:
            for finding in report.findings:
                issue = getattr(finding, "issue", getattr(finding, "message", "unknown"))
                value_preview = getattr(finding, "value", getattr(finding, "value_preview", ""))
                _logger.warning(
                    f"[InputGuard] {finding.severity.upper()} '{name}'.{finding.field}: "
                    f"{issue} — value={str(value_preview)[:80]}"
                )
            if report.has_critical:
                # 只有 critical 级（真实路径穿越）才硬阻断
                # Only critical level (actual path traversal) triggers hard block
                reasons = "; ".join(
                    getattr(f, "issue", getattr(f, "message", "critical finding"))
                    for f in report.findings
                    if f.severity == "critical"
                )
                return f'<tool_response name="{name}">⚠️ SECURITY: Input validation failed — {reasons}</tool_response>'
    except Exception as _ig_err:
        _logger.debug(f"[InputGuard] Scan skipped (degraded): {_ig_err}")

    # --- Tool Rate Limiter ---
    try:
        from utils.security.tool_rate_limiter import ToolRateLimiter

        allowed_rate, wait_sec = await ToolRateLimiter.get().check_and_consume(name)
        if not allowed_rate:
            _logger.warning(f"[RateLimiter] Tool '{name}' rate-limited, suggested wait: {wait_sec}s")
            return (
                f'<tool_response name="{name}">'
                f"Rate limit reached for '{name}'. Please wait {wait_sec}s before retrying."
                f"</tool_response>"
            )
    except Exception as _rl_err:
        _logger.debug(f"[RateLimiter] Rate limit check skipped (degraded): {_rl_err}")

    # --- Local code execution safety gate ---
    # AST-level check for python_interpreter(kernel="local") — prompt injection cannot bypass this
    if name == "python_interpreter" and args.get("kernel", "e2b") == "local":
        code = args.get("code", "")
        if code and os.getenv("INTERPRETER_ALLOW_LOCAL", "false").lower() != "true":
            from utils.code_safety import ast_safety_check

            safe, violations = ast_safety_check(code)
            if not safe:
                _logger.warning(f"[CodeSafety] Local execution blocked — violations: {violations}")
                return (
                    f'<tool_response name="{name}">'
                    f"⚠️ LOCAL EXECUTION SAFETY GATE: Code contains dangerous operations: "
                    f"{', '.join(violations)}.\n"
                    f"These operations cannot run on the host without user confirmation.\n"
                    f"Options:\n"
                    f"  1. Ask the user to reply 'confirm' to approve local execution\n"
                    f"  2. Rewrite the code to avoid dangerous operations and use kernel='e2b'\n"
                    f"  3. Use kernel='e2b' if the task does not require local system access"
                    f"</tool_response>"
                )

    # --- Blackboard resource locking: prevent concurrent file writes by parallel sub-agents ---
    _WRITE_TOOLS = {
        "excel_write",
        "write_file",
        "office_docx_write",
        "office_excel_write",
        "file_write",
        "create_file",
        "save_file",
        "file_system_op",
    }
    blackboard = getattr(config, "blackboard", None)
    _locked_resource: Optional[str] = None
    if blackboard is not None and name in _WRITE_TOOLS and "path" in args:
        should_lock = True
        if name == "file_system_op" and args.get("action", "").lower() != "write":
            should_lock = False

        if should_lock:
            import os as _os

            resource_key = _os.path.normcase(_os.path.abspath(str(args["path"])))
            owner_id = getattr(config, "agent_id", run_id)
            acquired = await blackboard.wait_for_resource(resource_key, owner_id, poll_interval=0.5, timeout=20.0)
            if acquired:
                _locked_resource = resource_key
                _logger.debug(f"[ResourceLock] '{owner_id}' locked '{resource_key}' before {name}")
            else:
                _logger.warning(f"[ResourceLock] '{owner_id}' could not acquire '{resource_key}' — returning error")
                return f'<tool_response name="{name}">Error: Resource "{resource_key}" is locked by another agent. Please wait and try again.</tool_response>'

    # Emit "calling tool" event — use masked args for display (secrets never shown in UI/logs)
    try:
        from utils.security.secrets_mask import secrets_mask as _sm

        display_args = _sm.mask_dict(args)
    except Exception:
        display_args = args
    await event_handler.emit_tool_call(
        session_key=config.session_key,
        client_run_id=run_id,
        tool_name=name,
        args=display_args,
    )

    try:
        # --- Step 2: Clean execution (with self-healing) ---
        result = await _execute_tool_with_healing(tool, name, args, reflection_engine_getter)

        # --- Advanced Security: prompt injection scan on tool output (default OFF) ---
        try:
            from utils.security.advanced_guard import AdvancedGuard

            pi_report = AdvancedGuard.scan_tool_output(name, str(result))
            if pi_report.has_threats:
                result = pi_report.to_warning_prefix() + str(result)
        except Exception as _pi_err:
            _logger.debug(f"[AdvancedGuard] prompt injection scan skipped (degraded): {_pi_err}")

        # --- Vision strategy: four-tier recognition + safe degradation ---
        if name == "vnode_camera_snap" and "[IMAGE_DATA:" in str(result):
            result = await _handle_vision_strategy(
                result,
                name,
                step,
                config,
                session_history,
                llm_client=llm_client,
                uia_cache=uia_cache,
            )

        # --- Step 3: Result refinement (Orchestrator post-processing) ---
        final_obs_content = result
        if orchestrator:
            final_obs_content = await orchestrator.post_dispatch(name, result)

        # --- Physical action verification loop ---
        if name == "vnode_grounding_click":
            final_obs_content = await _verify_click_action(
                name,
                args,
                tool,
                final_obs_content,
                config=config,
                visual_buffer=visual_buffer,
                reflection_engine_getter=reflection_engine_getter,
            )

        # Emit display result
        display_result = (
            str(final_obs_content)[:500] + "..." if len(str(final_obs_content)) > 500 else str(final_obs_content)
        )
        await event_handler.emit_tool_response(
            session_key=config.session_key,
            client_run_id=run_id,
            tool_name=name,
            response=display_result,
        )

        # Auto-record artifacts
        _auto_record_artifact(name, args, result, memory_manager)

        obs = f'<tool_response name="{name}">\n{final_obs_content}\n</tool_response>'
    except Exception as e:
        obs = f'<tool_response name="{name}">Execution Error: {str(e)}</tool_response>'
    finally:
        # Release resource lock after write tool completes (or fails)
        if blackboard is not None and _locked_resource is not None:
            owner_id = getattr(config, "agent_id", run_id)
            await blackboard.release_resource(_locked_resource, owner_id)
            _logger.debug(f"[ResourceLock] '{owner_id}' released '{_locked_resource}'")

    return obs


async def _execute_tool_with_healing(tool, name: str, args: dict, reflection_engine_getter) -> Any:
    """Self-healing proxy for tool execution: on failure, ReflectionEngine repairs and retries."""
    from agents.reflection_engine import RepairBudgetExhausted

    async def retry_with_args(corrected_args: dict):
        return await tool.run(**corrected_args)

    try:
        return await tool.run(**args)
    except RepairBudgetExhausted as budget_err:
        _logger.error(f"Self-healing budget exhausted: {budget_err}")
        return f"[TOOL_HEAL_EXHAUSTED] {str(budget_err)}"
    except Exception as first_error:
        engine = reflection_engine_getter() if reflection_engine_getter else None
        if engine is None:
            return f"[TOOL_ERROR] {type(first_error).__name__}: {str(first_error)}"
        _logger.warning(
            f"Tool '{name}' error, delegating to ReflectionEngine: "
            f"{type(first_error).__name__}: {str(first_error)[:100]}"
        )
        try:
            return await engine.heal(
                tool_name=name,
                args=args,
                error=first_error,
                retry_callable=retry_with_args,
            )
        except RepairBudgetExhausted:
            return f"[TOOL_HEAL_EXHAUSTED] Tool='{name}' original: {type(first_error).__name__}: {str(first_error)}"
        except Exception as heal_err:
            return f"[TOOL_FATAL_ERROR] Tool='{name}' | original: {type(first_error).__name__} | heal: {heal_err}"


async def _handle_vision_strategy(result, name, step, config, session_history, *, llm_client=None, uia_cache=None):
    """Four-tier vision recognition strategy with safe degradation."""
    import re as _re
    from models.vision_analyzer import VisionAnalyzer
    from models.vision_strategy import VisionStrategy
    from utils.vision.engine import EliteEngine
    from utils.vision.grounding import VisualGrounder
    from PIL import Image as _PILImage

    match = _re.search(r"\[IMAGE_DATA: (.*?)\]", str(result))
    if not match:
        return result

    base64_img = match.group(1)
    _logger.info("[VisionStrategy] Dispatching four-tier vision strategy (engine: %s)" % llm_client.provider)

    # 1. Decode screenshot
    img_bytes = base64.b64decode(base64_img)
    screenshot = _PILImage.open(io.BytesIO(img_bytes)).convert("RGB")

    # 2. UIA scan (with 3s cache) + labeled image generation
    uia_elements = []
    labeled_image = None
    try:
        cached = uia_cache.get() if uia_cache else None
        if cached is not None:
            uia_elements = cached
            _logger.info("[VisionStrategy] UIA cache hit (%d elements)" % len(uia_elements))
        else:
            engine = EliteEngine()
            uia_elements = engine.dump()
            if uia_cache:
                uia_cache.put(uia_elements)
            _logger.info("[VisionStrategy] UIA full scan (%d elements)" % len(uia_elements))
        if uia_elements:
            grounder = VisualGrounder()
            obs = grounder.scan("camera_snap", screenshot.copy(), uia_elements)
            labeled_image = obs.screenshot
            _logger.info("[VisionStrategy] Tier %d" % VisionStrategy.classify_tier(uia_elements))
    except Exception as scan_err:
        _logger.warning("[VisionStrategy] UIA scan failed: %s" % scan_err)

    # 3. Extract reasoning context
    logic_context = "UI automation recognition in progress"
    if session_history:
        for msg in reversed(session_history):
            if msg["role"] == "assistant" and "thought" in msg.get("content", "").lower():
                logic_context = msg["content"]
                break
    task_ctx = "Current engine: %s. Reasoning: %s. Identify core UI interaction elements." % (
        llm_client.provider,
        logic_context,
    )

    # 4. Execute four-tier vision strategy
    strategy = VisionStrategy()
    vis_result = await strategy.execute(
        screenshot=screenshot,
        uia_elements=uia_elements,
        labeled_image=labeled_image,
        task_context=task_ctx,
        analyzer_fn=VisionAnalyzer.analyze_screen,
    )

    # 5. Build report
    cloud_report = vis_result.report
    tier_info = "[Tier %d, attempts %d]" % (vis_result.tier_used, vis_result.attempts)
    if vis_result.tier_log:
        tier_info += " trajectory: " + " -> ".join(vis_result.tier_log)
    _logger.info("[VisionStrategy] %s" % tier_info)

    # Audit: save screenshot and vision report
    try:
        audit_manager.log_step_detail(config.session_id, step, f"vision_snap_{name}.png", img_bytes, binary=True)
        audit_manager.log_step_detail(
            config.session_id, step, f"vision_report_{name}.md", f"{tier_info}\n\n{cloud_report}"
        )
    except Exception as e:
        _logger.debug(f"Failed to save vision audit data: {e}")

    # Remove original massive Base64 data, keep text report only
    result = _re.sub(r"\[IMAGE_DATA: .*?\]", "[Screenshot data extracted by vision engine]", str(result))

    # Inject structured report
    coord_line = ""
    if vis_result.coordinates:
        coord_line = "\n[Coordinates]: x=%.1f, y=%.1f (normalized 0-1000)" % vis_result.coordinates
    conf_line = ""
    if vis_result.confidence > 0:
        conf_line = "\n[Confidence]: %d/100" % vis_result.confidence
    result = "%s\n\n[Vision Analysis Report](%s):\n%s%s%s" % (result, tier_info, cloud_report, coord_line, conf_line)

    return result


async def _verify_click_action(name, args, tool, current_result, *, config, visual_buffer, reflection_engine_getter):
    """Verify that a click action actually changed the screen, with auto-retry."""
    from utils.vision.grounding import VisualGrounder

    node_id = args.get("nodeId")
    MAX_UI_RETRY = 2

    for ui_attempt in range(MAX_UI_RETRY + 1):
        grounder = VisualGrounder()
        pre_obs = grounder.get_last_observation(node_id)
        if pre_obs:
            visual_buffer.push(node_id, pre_obs, action="click")

        await asyncio.sleep(settings.ACTION_WAIT_MS / 1000.0)

        # Trigger silent scan for comparison
        snap_tool = config.tool_registry.get_tool("vnode_grounding_scan")
        if snap_tool:
            await snap_tool.run(nodeId=node_id)

        post_obs = grounder.get_last_observation(node_id)
        if pre_obs and post_obs:
            visual_buffer.push(node_id, post_obs)
            delta = visual_buffer.calculate_delta(node_id)

            if delta < (1 - settings.ACTION_HASH_SIMILARITY):
                if ui_attempt < MAX_UI_RETRY:
                    _logger.warning(
                        f"Click ineffective (similarity too high: {1 - delta:.4f}), "
                        f"auto-retry ({ui_attempt + 2}/{MAX_UI_RETRY + 1})..."
                    )
                    await asyncio.sleep(1.0)
                    try:
                        await _execute_tool_with_healing(tool, name, args, reflection_engine_getter)
                    except Exception as e:
                        _logger.warning(f"Click retry execution failed: {e}")
                    continue
                else:
                    _logger.warning(f"Click ineffective after retries (similarity: {1 - delta:.4f}).")
                    current_result += (
                        "\n\n[Diagnostic]: Screen unchanged after multiple clicks. "
                        "Possible occlusion or click not penetrating."
                    )
        break  # Click succeeded or no observation data

    return current_result


def _auto_record_artifact(name: str, args: Dict, result: Any, memory_manager):
    """Auto-record produced artifacts in long-term memory."""
    if memory_manager is None:
        return
    target_tools = ["excel_write", "write_file", "office_docx_write", "create_directory"]
    if name in target_tools and "path" in args:
        path = args["path"]
        if "Success" in str(result) or "✅" in str(result):
            desc = f"Artifact generated via {name}"
            memory_manager.record_artifact(path, desc)
