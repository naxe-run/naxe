from typing import Any

from naxe.handlers._common import _ok, _err
from naxe import store, resolver
from naxe.schema import TaskStatus


def handle_add_tasks(conn, arguments: dict) -> list:
    import uuid
    job_id = arguments.get("job_id")
    tasks = arguments["tasks"]
    for t in tasks:
        if not t.get("id"):
            t["id"] = str(uuid.uuid4())
        t["_resolved_id"] = t["id"]
    if resolver.detect_cycle(tasks):
        return _err("Dependency cycle detected in task batch — no tasks were added.")
    try:
        result = store.add_tasks(conn, job_id, tasks, context=arguments.get("_context"))
    except ValueError as e:
        return _err(str(e))
    return _ok(added=len(result["task_ids"]), task_ids=result["task_ids"], job_id=result["job_id"])


def handle_get_next_actions(conn, arguments: dict) -> list:
    job = store.get_job(conn, arguments["job_id"])
    if not job:
        return _err(f"Job '{arguments['job_id']}' not found")
    actions = resolver.get_next_actions(conn, job["id"], arguments.get("repo"))
    return _ok(next_actions=actions)


def handle_claim_task(conn, arguments: dict) -> list:
    task_id = arguments["task_id"]
    agent_id = arguments["agent_id"]
    success = store.claim_task(conn, task_id, agent_id)
    task = store.get_task(conn, task_id) if success else None
    return _ok(success=success, task=task)


def handle_claim_next_action(conn, arguments: dict) -> list:
    task = store.claim_next_action(conn, arguments["job_id"], arguments["agent_id"], arguments.get("repo"))
    return _ok(task=task)


def handle_complete_task(conn, arguments: dict) -> list:
    task_id = arguments["task_id"]
    agent_id = arguments["agent_id"]
    task = store.get_task(conn, task_id)
    if not task:
        return _err(f"Task '{task_id}' not found")
    result = store.complete_task(conn, task_id, agent_id, arguments.get("output"))
    if "error" in result:
        return _err(result["error"])
    if result.get("routed_to_approval"):
        return _ok(success=True, task=result["task"], routed_to_approval=True,
                   message="Task requires approval — routed to awaiting_approval instead of completing.")
    warning = None
    if task.get("owner_agent_id") and task["owner_agent_id"] != agent_id:
        warning = f"Task owned by '{task['owner_agent_id']}', completing anyway (orchestrator override)"
    updated = result["task"]
    newly_unblocked = resolver.get_newly_unblocked(conn, task["job_id"], task_id)
    ret: dict[str, Any] = {"success": True, "task": updated, "newly_unblocked": newly_unblocked}
    if updated and "_newly_unblocked_jobs" in updated:
        ret["newly_unblocked_jobs"] = updated.pop("_newly_unblocked_jobs")
    if warning:
        ret["warning"] = warning
    if result.get("recurrence_spawned"):
        ret["recurrence_spawned"] = result["recurrence_spawned"]
    return _ok(**ret)


def handle_fail_task(conn, arguments: dict) -> list:
    task_id = arguments["task_id"]
    agent_id = arguments["agent_id"]
    task = store.get_task(conn, task_id)
    if not task:
        return _err(f"Task '{task_id}' not found")
    warning = None
    if task.get("owner_agent_id") and task["owner_agent_id"] != agent_id:
        warning = f"Task owned by '{task['owner_agent_id']}', failing anyway (orchestrator override)"
    store.update_task_status(conn, task_id, TaskStatus.FAILED, agent_id, arguments.get("output"))
    retried_task = store.retry_task(conn, task_id)
    result = {"success": True}
    if retried_task:
        result["retried"] = True
        result["task"] = retried_task
    if warning:
        result["warning"] = warning
    if arguments.get("reason"):
        result["reason"] = arguments["reason"]
    return _ok(**result)


def handle_heartbeat_task(conn, arguments: dict) -> list:
    task = store.heartbeat_task(conn, arguments["task_id"], arguments["agent_id"])
    return _ok(task=task)


def handle_update_task_progress(conn, arguments: dict) -> list:
    result = store.update_task_progress(
        conn, arguments["task_id"], arguments["agent_id"], arguments["progress_percent"]
    )
    if result and "error" in result:
        return _err(result["error"])
    return _ok(task=result)


def handle_cancel_task(conn, arguments: dict) -> list:
    task = store.cancel_task(conn, arguments["task_id"])
    if task is None:
        return _err(f"Task '{arguments['task_id']}' not found or already in a terminal state")
    return _ok(success=True, task=task)


def handle_edit_task(conn, arguments: dict) -> list:
    task_id = arguments["task_id"]
    updates = {k: v for k, v in arguments.items() if k != "task_id"}
    if not updates:
        return _err("No fields to update")
    try:
        updated = store.edit_task(conn, task_id, updates)
    except ValueError as e:
        return _err(str(e))
    if updated is None:
        task = store.get_task(conn, task_id)
        if task is None:
            return _err(f"Task '{task_id}' not found")
        return _err(f"Task '{task_id}' is {task['status']} — only pending tasks can be edited")
    return _ok(success=True, task=updated)


def handle_requeue_task(conn, arguments: dict) -> list:
    task_id = arguments["task_id"]
    task = store.requeue_task(conn, task_id, arguments.get("input"))
    if task is None:
        return _err("Task not found or not in a requeue-able state (must be failed or cancelled)")
    return _ok(success=True, task=task)
