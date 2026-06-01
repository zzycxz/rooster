import os
import json
import logging
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)

@dataclass
class DependencyObservationDigest:
    status: str             # SUCCESS / FAILED
    artifact_paths: List[str] # 落盘的 .stdout.log / .stderr.log 路径
    summary: str            # exit_code, exception 摘要
    diagnostics: List[str]  # 提取的 traceback 块、命中的 error 行
    preview: str            # stderr tail 等高度提纯预览
    retrieval_hint: str     # 提示模型去读取文件的指令

    def to_prompt_string(self) -> str:
        """格式化为适合作为下游提示词的字符串"""
        lines = [
            f"Dependency Task Status: {self.status}",
        ]
        
        if self.artifact_paths:
            lines.append("Full output saved to environment artifacts:")
            for path in self.artifact_paths:
                # 简单估算文件大小
                size = 0
                if os.path.exists(path):
                    size = os.path.getsize(path)
                lines.append(f"  - {path} ({size} bytes)")
                
        if self.summary:
            lines.append(f"\nDiagnostic Summary:\n  {self.summary}")
            
        if self.diagnostics:
            lines.append("\nKey Diagnostics (Traceback / Errors):")
            for diag in self.diagnostics:
                lines.append(f"  {diag}")
                
        if self.preview:
            lines.append(f"\nTail Preview:\n{self.preview}")
            
        if self.retrieval_hint:
            lines.append(f"\n({self.retrieval_hint})")
            
        return "\n".join(lines)


def summarize_dependency_observation(
    observation: str, 
    task_id: str, 
    run_dir: str, 
    token_budget: int = 2000
) -> DependencyObservationDigest:
    """
    Codex 风格：将过长的 observation 对象化，落盘并生成诊断索引。
    """
    if not observation:
        return DependencyObservationDigest(
            status="UNKNOWN", artifact_paths=[], summary="", diagnostics=[], preview="", retrieval_hint=""
        )

    # 如果小于 2000，基本是安全的，直接作为 preview 返回，不需要强制落盘（避免细碎日志过多）
    if len(observation) <= token_budget:
        return DependencyObservationDigest(
            status="COMPLETED",
            artifact_paths=[],
            summary="Output fits in budget.",
            diagnostics=[],
            preview=observation,
            retrieval_hint=""
        )

    # 1. 如果超出预算，自动落盘
    obs_dir = os.path.join(run_dir, "observations")
    os.makedirs(obs_dir, exist_ok=True)
    
    log_path = os.path.join(obs_dir, f"{task_id}.full.log")
    try:
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(observation)
    except Exception as e:
        logger.error(f"Failed to write observation log for {task_id}: {e}")
        
    artifact_paths = [log_path]
    
    # 2. 诊断信息提取
    diagnostics = []
    summary = []
    
    lines = observation.splitlines()
    
    # 2.1 尝试寻找 Traceback 块
    traceback_lines = []
    in_traceback = False
    for line in lines:
        if "Traceback (most recent call last):" in line:
            in_traceback = True
            traceback_lines = [line]
        elif in_traceback:
            if line.startswith(" ") or line.startswith("  ") or ":" in line:
                traceback_lines.append(line)
            else:
                traceback_lines.append(line)
                in_traceback = False # Exception details is usually the last line of a traceback block
                
    if traceback_lines:
        diagnostics.append("\n".join(traceback_lines))
        exception_line = traceback_lines[-1]
        summary.append(f"exception: {exception_line}")
        status = "FAILED"
    else:
        status = "COMPLETED_WITH_LARGE_OUTPUT"

    # 2.2 尝试如果是 JSON 数组/对象的特征
    if observation.strip().startswith("{") and observation.strip().endswith("}"):
        try:
            data = json.loads(observation)
            summary.append(f"Data type: JSON Object, Keys: {list(data.keys())[:10]}...")
        except:
            pass
    elif observation.strip().startswith("[") and observation.strip().endswith("]"):
        try:
            data = json.loads(observation)
            summary.append(f"Data type: JSON Array, Length: {len(data)}")
        except:
            pass
            
    # 3. 构造 Tail Preview
    # 取最后 500 个字符
    preview = "...\n" + observation[-500:]
    
    retrieval_hint = "If you need the full context, please use file reading tools to inspect the artifact paths above."
    
    return DependencyObservationDigest(
        status=status,
        artifact_paths=artifact_paths,
        summary="\n".join(summary) if summary else "Large output generated. See preview.",
        diagnostics=diagnostics,
        preview=preview,
        retrieval_hint=retrieval_hint
    )
