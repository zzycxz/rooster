"""
src/toolset/definitions/task_manager.py

[P1-1] 结构化任务状态机工具集。
提供持久化任务管理：PENDING → RUNNING → DONE / FAILED。

存储后端：本地 JSON 文件（.rooster/tasks/tasks.json），
无需额外依赖，崩溃后可恢复。
"""

import json
import os
import uuid
import datetime
import logging
from typing import Type, Optional, List
from pydantic import BaseModel, Field
from toolset.base import BaseTool, ToolResult

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #
_TASK_STORE_PATH = os.path.join(".rooster", "tasks", "tasks.json")
_VALID_STATUSES = {"PENDING", "RUNNING", "DONE", "FAILED", "CANCELLED"}


# --------------------------------------------------------------------------- #
# 内部存储助手
# --------------------------------------------------------------------------- #


def _load_tasks() -> dict:
    os.makedirs(os.path.dirname(_TASK_STORE_PATH), exist_ok=True)
    if not os.path.exists(_TASK_STORE_PATH):
        return {}
    try:
        with open(_TASK_STORE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"[TaskStore] Failed to read task file: {e}")
        logger.warning(f"[TaskStore] 读取任务文件失败: {e}")
        return {}


def _save_tasks(tasks: dict) -> None:
    os.makedirs(os.path.dirname(_TASK_STORE_PATH), exist_ok=True)
    try:
        with open(_TASK_STORE_PATH, "w", encoding="utf-8") as f:
            json.dump(tasks, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"[TaskStore] Failed to write task file: {e}")
        logger.error(f"[TaskStore] 写入任务文件失败: {e}")


# --------------------------------------------------------------------------- #
# TaskCreate
# --------------------------------------------------------------------------- #


class TaskCreateArgs(BaseModel):
    title: str = Field(..., description="任务标题，简短描述任务目标")
    description: str = Field("", description="任务详细说明（可选）")
    priority: str = Field("normal", description="优先级：low / normal / high / critical")
    tags: List[str] = Field(default_factory=list, description="标签列表，便于分类筛选")


class TaskCreateTool(BaseTool):
    """
    创建一个新的结构化任务，返回唯一 task_id。
    适用场景：将大型目标拆解为多个可追踪的子任务时。
    """

    name = "task_create"
    kit = "System"
    domain = "system"
    risk_level = "low"
    reversible = True
    fc_hidden: bool = True  # [Round 10] Use task_manager(action="create") instead
    description = (
        "Create a new structured task with a title, description and priority. "
        "Returns a unique task_id for subsequent tracking."
    )
    args_schema: Type[BaseModel] = TaskCreateArgs

    async def execute(self, args: TaskCreateArgs) -> ToolResult:
        tasks = _load_tasks()
        task_id = str(uuid.uuid4())[:8]
        now = datetime.datetime.now().isoformat()
        tasks[task_id] = {
            "id": task_id,
            "title": args.title,
            "description": args.description,
            "priority": args.priority,
            "tags": args.tags,
            "status": "PENDING",
            "created_at": now,
            "updated_at": now,
            "result": None,
        }
        _save_tasks(tasks)
        logger.info(f"[TaskCreate] Task created: {task_id} — {args.title}")
        logger.info(f"[TaskCreate] 任务已创建: {task_id} — {args.title}")
        return ToolResult.success(
            f"✅ Task created.\n  ID: {task_id}\n  Title: {args.title}\n  Status: PENDING\n  Priority: {args.priority}"
        )


# --------------------------------------------------------------------------- #
# TaskGet
# --------------------------------------------------------------------------- #


class TaskGetArgs(BaseModel):
    task_id: str = Field(..., description="任务 ID（由 task_create 返回）")


class TaskGetTool(BaseTool):
    """
    查询指定任务的当前状态和详情。
    """

    name = "task_get"
    kit = "System"
    domain = "system"
    risk_level = "low"
    reversible = True
    description = "Get details and current status of a task by its task_id."
    fc_hidden: bool = True  # [Round 9] task_list 现已显示 DONE/FAILED 任务的 result 字段，task_get 对 LLM 冗余
    args_schema: Type[BaseModel] = TaskGetArgs

    async def execute(self, args: TaskGetArgs) -> ToolResult:
        tasks = _load_tasks()
        task = tasks.get(args.task_id)
        if not task:
            return ToolResult.error(f"❌ Task '{args.task_id}' not found.")
        lines = [
            f"Task: {task['id']}",
            f"  Title:       {task['title']}",
            f"  Status:      {task['status']}",
            f"  Priority:    {task['priority']}",
            f"  Tags:        {', '.join(task.get('tags', []))}",
            f"  Created:     {task['created_at']}",
            f"  Updated:     {task['updated_at']}",
            f"  Description: {task.get('description', '')}",
            f"  Result:      {task.get('result') or '(none yet)'}",
        ]
        return ToolResult.success("\n".join(lines))


# --------------------------------------------------------------------------- #
# TaskUpdate
# --------------------------------------------------------------------------- #


class TaskUpdateArgs(BaseModel):
    task_id: str = Field(..., description="要更新的任务 ID")
    status: Optional[str] = Field(None, description=f"新状态：{_VALID_STATUSES}")
    result: Optional[str] = Field(None, description="任务执行结果摘要（可选）")
    description: Optional[str] = Field(None, description="更新任务描述（可选）")


class TaskUpdateTool(BaseTool):
    """
    更新任务状态或结果。状态机：PENDING → RUNNING → DONE / FAILED / CANCELLED。
    """

    name = "task_update"
    kit = "System"
    domain = "system"
    risk_level = "low"
    reversible = False
    fc_hidden: bool = True  # [Round 10] Use task_manager(action="update") instead
    description = (
        "Update the status or result of an existing task. Valid statuses: PENDING, RUNNING, DONE, FAILED, CANCELLED."
    )
    args_schema: Type[BaseModel] = TaskUpdateArgs

    async def execute(self, args: TaskUpdateArgs) -> ToolResult:
        tasks = _load_tasks()
        task = tasks.get(args.task_id)
        if not task:
            return ToolResult.error(f"❌ Task '{args.task_id}' not found.")

        if args.status and args.status.upper() not in _VALID_STATUSES:
            return ToolResult.error(f"❌ Invalid status '{args.status}'. Valid values: {_VALID_STATUSES}")

        if args.status:
            task["status"] = args.status.upper()
        if args.result is not None:
            task["result"] = args.result
        if args.description is not None:
            task["description"] = args.description
        task["updated_at"] = datetime.datetime.now().isoformat()

        _save_tasks(tasks)
        logger.info(f"[TaskUpdate] {args.task_id} → status={task['status']}")
        return ToolResult.success(
            f"✅ Task {args.task_id} updated.\n"
            f"  Status: {task['status']}\n"
            f"  Result: {task.get('result') or '(not set)'}"
        )


# --------------------------------------------------------------------------- #
# TaskList
# --------------------------------------------------------------------------- #


class TaskListArgs(BaseModel):
    status: Optional[str] = Field(None, description="按状态筛选（PENDING/RUNNING/DONE/FAILED，空=全部）")
    tag: Optional[str] = Field(None, description="按标签筛选（空=全部）")
    limit: int = Field(20, description="最多返回条数")
    show_results: bool = Field(True, description="是否显示 DONE/FAILED 任务的 result 字段（默认 True）")


class TaskListTool(BaseTool):
    """
    列出所有任务，支持按状态和标签筛选。DONE/FAILED 任务会附加显示 result 字段。
    """

    name = "task_list"
    kit = "System"
    domain = "system"
    risk_level = "low"
    reversible = True
    fc_hidden: bool = True  # [Round 10] Use task_manager(action="list") instead
    description = (
        "List all tracked tasks, optionally filtered by status or tag. "
        "Completed (DONE/FAILED) tasks also show their result field. "
        "Useful for reviewing progress across multiple subtasks."
    )
    args_schema: Type[BaseModel] = TaskListArgs

    async def execute(self, args: TaskListArgs) -> ToolResult:
        tasks = _load_tasks()
        results = list(tasks.values())

        if args.status:
            results = [t for t in results if t["status"] == args.status.upper()]
        if args.tag:
            results = [t for t in results if args.tag in t.get("tags", [])]

        # Sort by creation time descending
        # 按创建时间倒序
        results.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        results = results[: args.limit]

        if not results:
            return ToolResult.success("📋 No tasks found matching the criteria.")

        lines = [f"📋 Tasks ({len(results)} found):"]
        for t in results:
            line = f"  [{t['status']:9s}] {t['id']} | {t['priority']:8s} | {t['title']}"
            if args.show_results and t["status"] in ("DONE", "FAILED") and t.get("result"):
                line += f"\n             ↳ {t['result'][:120]}"
            lines.append(line)
        return ToolResult.success("\n".join(lines))
