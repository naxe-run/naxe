from typing import Any

from naxe.handlers._common import _ok, _err
from naxe import store


def handle_request_approval(conn, arguments: dict) -> list:
    task_id = arguments["task_id"]
    agent_id = arguments["agent_id"]
    task = store.request_approval(conn, task_id, agent_id, arguments.get("notes"))
    if task is None:
        return _err("Task not found or not eligible for approval request (must be in_progress and owned by this agent)")
    return _ok(success=True, task=task)


def handle_approve_task(conn, arguments: dict) -> list:
    task_id = arguments["task_id"]
    approver_id = arguments["approver_id"]
    result = store.approve_task(conn, task_id, approver_id, arguments.get("notes"))
    if result is None:
        return _err("Task not found or not awaiting approval")
    task = result["task"]
    newly_unblocked = result["newly_unblocked"]
    ret: dict[str, Any] = {"success": True, "task": task, "newly_unblocked": newly_unblocked}
    if task and "_newly_unblocked_jobs" in task:
        ret["newly_unblocked_jobs"] = task.pop("_newly_unblocked_jobs")
    return _ok(**ret)


def handle_reject_task(conn, arguments: dict) -> list:
    task_id = arguments["task_id"]
    approver_id = arguments["approver_id"]
    reason = arguments["reason"]
    task = store.reject_task(conn, task_id, approver_id, reason)
    if task is None:
        return _err("Task not found or not awaiting approval")
    return _ok(success=True, task=task)


def handle_return_task(conn, arguments: dict) -> list:
    task_id = arguments["task_id"]
    approver_id = arguments["approver_id"]
    feedback = arguments["feedback"]
    task = store.return_task(conn, task_id, approver_id, feedback)
    if task is None:
        return _err("Task not found or not awaiting approval")
    return _ok(success=True, task=task)
