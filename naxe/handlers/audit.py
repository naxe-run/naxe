from naxe.handlers._common import _ok, _err
from naxe import store, resolver


def handle_add_task_comment(conn, arguments: dict) -> list:
    task_id = arguments["task_id"]
    author_type = arguments["author_type"]
    if author_type not in ("agent", "human"):
        return _err("author_type must be 'agent' or 'human'")
    comment = store.add_task_comment(
        conn, task_id, arguments["author_id"], author_type, arguments["content"]
    )
    if comment is None:
        return _err(f"Task '{task_id}' not found")
    return _ok(success=True, comment=comment)


def handle_get_task_comments(conn, arguments: dict) -> list:
    task_id = arguments["task_id"]
    task = store.get_task(conn, task_id)
    if not task:
        return _err(f"Task '{task_id}' not found")
    comments = store.get_task_comments(conn, task_id, arguments.get("approval_round"))
    return _ok(task_id=task_id, comments=comments)


def handle_get_task_events(conn, arguments: dict) -> list:
    task_id = arguments["task_id"]
    if not store.get_task(conn, task_id):
        return _err(f"Task '{task_id}' not found")
    events = store.get_task_events(conn, task_id)
    return _ok(task_id=task_id, events=events)


def handle_get_job_audit_trail(conn, arguments: dict) -> list:
    job = store.get_job(conn, arguments["job_id"])
    if not job:
        return _err(f"Job '{arguments['job_id']}' not found")
    events = store.get_job_events(conn, job["id"])
    return _ok(job_id=job["id"], events=events)


def handle_get_blocked_tasks(conn, arguments: dict) -> list:
    job = store.get_job(conn, arguments["job_id"])
    if not job:
        return _err(f"Job '{arguments['job_id']}' not found")
    blocked = resolver.get_blocking_reasons(conn, job["id"])
    return _ok(job_id=job["id"], blocked_tasks=blocked)
