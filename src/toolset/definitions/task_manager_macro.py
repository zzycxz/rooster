"""
[Round 10] task_manager — unified task management macro.

Replaces: task_create, task_get, task_list, task_update
Delegates to existing task_manager.py internals.
"""

import json
import os
import uuid
import datetime
import logging
from typing import Type, Optional, List
from pydantic import BaseModel, Field
from toolset.base import BaseTool

logger = logging.getLogger(__name__)

_TASK_STORE_PATH = os.path.join(".rooster", "tasks", "tasks.json")
_VALID_STATUSES = {"PENDING", "RUNNING", "DONE", "FAILED", "CANCELLED"}


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


class TaskManagerArgs(BaseModel):
    action: str = Field(
        description="Action: 'create' (new task), 'get' (task details), 'list' (all tasks), 'update' (change status)"
    )
    # create
    title: Optional[str] = Field(default=None, description="[create] Task title")
    description: Optional[str] = Field(default="", description="[create / update] Task description")
    priority: Optional[str] = Field(default="normal", description="[create] Priority: low / normal / high / critical")
    tags: Optional[List[str]] = Field(default_factory=list, description="[create] Tags for filtering")
    # get / update
    task_id: Optional[str] = Field(default=None, description="[get / update] Task ID")
    # update
    status: Optional[str] = Field(
        default=None, description="[update] New status: PENDING / RUNNING / DONE / FAILED / CANCELLED"
    )
    result: Optional[str] = Field(default=None, description="[update] Task result summary")
    # list
    filter_status: Optional[str] = Field(
        default=None, description="[list] Filter by status (PENDING/RUNNING/DONE/FAILED)"
    )
    filter_tag: Optional[str] = Field(default=None, description="[list] Filter by tag")
    limit: Optional[int] = Field(default=20, description="[list] Max results to return")


class TaskManagerTool(BaseTool):
    """Unified task management. Use action='create' to add a task, 'get' to see details,
    'list' to review all tasks, 'update' to change status or record results."""

    name: str = "task_manager"
    kit: str = "System"
    description: str = (
        "Manage structured tasks. "
        "action='create': create a new task with title, priority, tags. Returns task_id. "
        "action='get': get full details of a task by task_id. "
        "action='list': list tasks, optionally filtered by status or tag. DONE/FAILED tasks show results. "
        "action='update': change task status (PENDING→RUNNING→DONE/FAILED) or record a result."
    )
    domain: str = "system"
    risk_level: str = "low"
    reversible: bool = True
    args_schema: Type[BaseModel] = TaskManagerArgs

    async def run(self, **kwargs) -> str:
        action = kwargs.get("action", "").lower()

        if action == "create":
            return self._create(kwargs)
        elif action == "get":
            return self._get(kwargs)
        elif action == "list":
            return self._list(kwargs)
        elif action == "update":
            return self._update(kwargs)
        else:
            return f"❌ Unknown action '{action}'. Valid: 'create', 'get', 'list', 'update'."

    def _create(self, kwargs: dict) -> str:
        title = kwargs.get("title")
        if not title:
            return "❌ 'title' is required for action='create'."
        tasks = _load_tasks()
        task_id = str(uuid.uuid4())[:8]
        now = datetime.datetime.now().isoformat()
        tasks[task_id] = {
            "id": task_id,
            "title": title,
            "description": kwargs.get("description", ""),
            "priority": kwargs.get("priority", "normal"),
            "tags": kwargs.get("tags", []),
            "status": "PENDING",
            "created_at": now,
            "updated_at": now,
            "result": None,
        }
        _save_tasks(tasks)
        logger.info(f"[TaskManager] Task created: {task_id} — {title}")
        logger.info(f"[TaskManager] 任务已创建: {task_id} — {title}")
        return (
            f"✅ Task created.\n"
            f"  ID: {task_id}\n"
            f"  Title: {title}\n"
            f"  Status: PENDING\n"
            f"  Priority: {kwargs.get('priority', 'normal')}"
        )

    def _get(self, kwargs: dict) -> str:
        task_id = kwargs.get("task_id")
        if not task_id:
            return "❌ 'task_id' is required for action='get'."
        tasks = _load_tasks()
        task = tasks.get(task_id)
        if not task:
            return f"❌ Task '{task_id}' not found."
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
        return "\n".join(lines)

    def _list(self, kwargs: dict) -> str:
        tasks = _load_tasks()
        results = list(tasks.values())

        filter_status = kwargs.get("filter_status")
        filter_tag = kwargs.get("filter_tag")
        limit = kwargs.get("limit", 20)

        if filter_status:
            results = [t for t in results if t["status"] == filter_status.upper()]
        if filter_tag:
            results = [t for t in results if filter_tag in t.get("tags", [])]

        results.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        results = results[:limit]

        if not results:
            return "📋 No tasks found matching the criteria."

        lines = [f"📋 Tasks ({len(results)} found):"]
        for t in results:
            line = f"  [{t['status']:9s}] {t['id']} | {t['priority']:8s} | {t['title']}"
            if t["status"] in ("DONE", "FAILED") and t.get("result"):
                line += f"\n             ↳ {t['result'][:120]}"
            lines.append(line)
        return "\n".join(lines)

    def _update(self, kwargs: dict) -> str:
        task_id = kwargs.get("task_id")
        if not task_id:
            return "❌ 'task_id' is required for action='update'."
        tasks = _load_tasks()
        task = tasks.get(task_id)
        if not task:
            return f"❌ Task '{task_id}' not found."

        new_status = kwargs.get("status")
        result = kwargs.get("result")
        description = kwargs.get("description")

        if new_status and new_status.upper() not in _VALID_STATUSES:
            return f"❌ Invalid status '{new_status}'. Valid: {_VALID_STATUSES}"

        if new_status:
            task["status"] = new_status.upper()
        if result is not None:
            task["result"] = result
        if description is not None:
            task["description"] = description
        task["updated_at"] = datetime.datetime.now().isoformat()

        _save_tasks(tasks)
        logger.info(f"[TaskManager] {task_id} → status={task['status']}")
        return f"✅ Task {task_id} updated.\n  Status: {task['status']}\n  Result: {task.get('result') or '(not set)'}"
