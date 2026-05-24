# src/agents/protocol.py
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from enum import Enum


class ActionType(str, Enum):
    TOOL_CALL = "tool_call"
    OBSERVATION = "observation"
    THOUGHT = "thought"


class SubTask(BaseModel):
    """
    Rooster 任务蓝图中的具体子步骤 (Strategist v10.0)。
    phase 由 Executor 从 DAG 拓扑自动推导，LLM 不再输出。
    """

    id: str = Field(..., description="任务步骤 ID")
    phase: Optional[str] = Field(None, description="由 Executor 从 DAG 拓扑自动推导: EXECUTE(非叶) | COMMIT(叶节点)")
    domain: str = Field(..., description="职能域: UI | RESOURCE | SYSTEM | COMMS | MEMORY")
    tool: str = Field(..., description="具体工具 ID")
    instruction: str = Field(..., description="具体的执行指令")
    depends_on: List[str] = Field(default_factory=list, description="依赖的前置 SubTask ID")
    on_failure: str = Field("RETRY", description="失败决策: RETRY | ABORT | REPLAN | REROUTE")
    requires_confirm: bool = Field(False, description="是否需要人工确认")
    mode: str = Field("ATOMIC", description="执行模式: ATOMIC | CONCURRENT")
    timeout: int = Field(120, description="超时限制(秒)")
    sub_agent_mode: str = Field(
        "NORMAL",
        description=(
            "子代理执行模式: "
            "NORMAL(普通，共享注册表) | "
            "ISOLATED(迷宫隔离，独立工具注册表，防上下文污染) | "
            "PARALLEL(并行加速，共享注册表，与其他子任务并发) | "
            "SANDBOXED(沙箱安全，strict权限策略，阻断高危工具) | "
            "RACE(竞速，同 race_group 内并发执行，第一个通过审计的任务取消其余兄弟)"
        ),
    )
    race_group: str = Field(
        "",
        description=(
            "RACE 模式专用：同组竞速标识。"
            "sub_agent_mode=RACE 且 race_group 相同的子任务并发执行，"
            "第一个成功的结果会被采用，其余任务被取消。"
            "空字符串表示不参与任何竞速组。"
        ),
    )


class MissionPlan(BaseModel):
    """
    由战略官 (Strategist v9.2) 生成的宏观任务执行计划蓝图。
    """

    task_id: str
    os_context: str = Field("unknown", description="操作系统上下文: windows | linux | macos | unknown")
    goal: str
    original_goal: Optional[str] = Field(None, description="任务初始化时绝对不可变的锚点北极星")
    autonomy: str = Field("AUTO", description="自主级别: AUTO | SUPERVISED")
    replan_count: int = Field(0, description="蓝图已被重构的次数")
    max_replan: int = Field(2, description="支持的最大重规划阈值极值")
    replan_history: List[Dict[str, Any]] = Field(
        default_factory=list, description="Historical roadblocks and failed plans"
    )
    mode: str = Field("SERIAL", description="整体执行模式: SERIAL | PARALLEL")
    subtasks: List[SubTask] = Field(default_factory=list, description="具体的执行子任务序列")

    def is_leaf(self, subtask_id: str) -> bool:
        """判断子任务是否为叶节点（无下游依赖）"""
        return not any(subtask_id in st.depends_on for st in self.subtasks)


class Report(BaseModel):
    """
    执行官 (Executor v6.0) 提交给审计官的执行汇报。
    支持 FINAL_REPORT | ABORT_REPORT | REPLAN_REQUEST | CONFIRM_REQUIRED | PRELIMINARY_EVIDENCE | ERROR。
    """

    type: str = Field("FINAL_REPORT", description="汇报类型")
    subtask_id: str
    status: Optional[str] = Field(None, description="SUCCESS | FAILED | TIMEOUT")
    evidence: Dict[str, Any] = Field(default_factory=dict, description="证据负载")
    failure_code: Optional[str] = Field(None, description="错误码")

    # v6.1 精准搜索扩展字段
    # v6.1 precision search extension fields
    observed_state: Optional[str] = Field(None, description="REPLAN_REQUEST 时观察到的状态")
    invalidated_assumption: Optional[str] = Field(None, description="REPLAN_REQUEST 时失效的假设")
    blocked_dependents: List[str] = Field(default_factory=list, description="ABORT_REPORT 时受阻的依赖任务")
    action_preview: Optional[str] = Field(None, description="CONFIRM_REQUIRED 时的动作预览")
    risk_level: Optional[str] = Field(None, description="CONFIRM_REQUIRED 时的风险等级")
    inability_reason: Optional[str] = Field(None, description="无法完成的客观理由")

    # [Precision Engine v2.0]
    evidence_confidence: Optional[str] = Field(None, description="HIGH | MEDIUM | LOW")
    common_facts: List[Dict[str, Any]] = Field(default_factory=list)
    divergent_points: List[Dict[str, Any]] = Field(default_factory=list)

    # 兼容性冗余字段 (V2 兼容性补丁)
    # Backward-compatible redundant fields (V2 compatibility patch)
    observation: str = ""
    process_snapshots: List[str] = Field(default_factory=list)
    artifacts: List[str] = Field(default_factory=list)
    provider_used: Optional[str] = Field(
        None, description="local | cloud | zhipu — which LLM provider served this subtask"
    )


class AuditVerdictType(str, Enum):
    # 系统内部状态码 (映射至 v3.0 语义)
    # System internal status codes (mapped to v3.0 semantics)
    AFFIRM = "affirm"  # 对应 PASS / Corresponds to PASS
    REMAND = "remand"  # 对应 RE_EXECUTE (原 FAIL/RETRY) / Corresponds to RE_EXECUTE (original FAIL/RETRY)
    REPLAN = "replan"  # 对应 REPLAN / Corresponds to REPLAN
    CLOSURE = "closure"  # 对应 GRACEFUL_CLOSURE / Corresponds to GRACEFUL_CLOSURE
    ESCALATE = "escalate"  # 对应 FAIL (ESCALATE) / Corresponds to FAIL (ESCALATE)


class AuditVerdict(BaseModel):
    """
    审计官 (Auditor v3.0) 下发的最终审计裁决书。
    """

    # 核心映射字段
    # Core mapping fields
    verdict: AuditVerdictType
    result_verdict: str = Field("PASS", description="PASS | PASS_WITH_WARNING | FAIL")

    # 扩展信息 (v3.0)
    # Extended info (v3.0)
    audit_type: str = Field("EXECUTION", description="PLAN | EXECUTION | FULL_CYCLE")
    recommendation: str = Field("PROCEED", description="PROCEED | ACCEPT | RETRY | ESCALATE | CLOSE_UNABLE")
    findings: List[Dict[str, Any]] = Field(default_factory=list)
    notes: str = ""

    # 动态路由 (v3.0 核心)
    # Dynamic routing (v3.0 core)
    routing: Optional[Dict[str, Any]] = Field(None, description="审计后的路由决策")

    # 兼容性旧字段
    # Backward-compatible legacy fields
    concurrency_action: str = Field("none", description="terminate_siblings | none")
    reason: str = Field("", description="判定理由")  # Verdict reason
    command: Optional[str] = Field(None, description="修正建议")  # Correction suggestion
    process_integrity: int = Field(0)
    artifact_quality: int = Field(0)
