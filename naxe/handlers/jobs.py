from datetime import datetime, timezone
from typing import Any

from naxe.handlers._common import _ok, _err
from naxe import store, resolver
from naxe.schema import TaskStatus


def handle_create_job(conn, arguments: dict) -> list:
    job = store.create_job(
        conn, arguments["name"],
        arguments.get("max_workers"),
        worktree=arguments.get("worktree", False),
    )
    return _ok(job_id=job["id"], job=job)


def handle_list_jobs(conn, arguments: dict) -> list:
    limit = arguments.get("limit", 50)
    offset = arguments.get("offset", 0)
    id_prefix = arguments.get("id_prefix")
    page = store.list_jobs(conn, limit=limit, offset=offset, id_prefix=id_prefix)
    result = []
    for job in page["jobs"]:
        tasks = store.get_tasks_for_job(conn, job["id"])
        result.append({
            **job,
            "progress": {
                "total": len(tasks),
                "completed": sum(1 for t in tasks if t["status"] == TaskStatus.COMPLETED),
                "in_progress": sum(1 for t in tasks if t["status"] == TaskStatus.IN_PROGRESS),
                "pending": sum(1 for t in tasks if t["status"] == TaskStatus.PENDING),
                "failed": sum(1 for t in tasks if t["status"] == TaskStatus.FAILED),
            },
        })
    return _ok(jobs=result, total=page["total"], has_more=page["has_more"])


def handle_edit_job(conn, arguments: dict) -> list:
    updated = store.edit_job(conn, arguments["job_id"], arguments["name"])
    if updated is None:
        return _err(f"Job '{arguments['job_id']}' not found")
    return _ok(success=True, job=updated)


def handle_get_job_status(conn, arguments: dict) -> list:
    job = store.get_job(conn, arguments["job_id"])
    if not job:
        return _err(f"Job '{arguments['job_id']}' not found")
    job_id = job["id"]
    tasks = store.get_tasks_for_job(conn, job_id)
    blocked_tasks = resolver.get_blocking_reasons(conn, job_id)
    blocked_map = {b["id"]: b["blocked_by"] for b in blocked_tasks}
    for t in tasks:
        if t["status"] == TaskStatus.PENDING and t["id"] in blocked_map:
            t["blocked_by"] = blocked_map[t["id"]]
        else:
            t["blocked_by"] = []
    now_iso = datetime.now(timezone.utc).isoformat()
    for t in tasks:
        status = t["status"]
        if status == TaskStatus.PENDING and t["id"] in blocked_map:
            t["display_status"] = "waiting_on"
        elif status == TaskStatus.PENDING and t.get("start_date") and t["start_date"] > now_iso:
            t["display_status"] = "scheduled"
        elif status == TaskStatus.PENDING:
            t["display_status"] = "next_action"
        else:
            t["display_status"] = status
    progress = {
        "total": len(tasks),
        "completed": sum(1 for t in tasks if t["status"] == TaskStatus.COMPLETED),
        "in_progress": sum(1 for t in tasks if t["status"] == TaskStatus.IN_PROGRESS),
        "pending": sum(1 for t in tasks if t["status"] == TaskStatus.PENDING),
        "failed": sum(1 for t in tasks if t["status"] == TaskStatus.FAILED),
    }
    dep_rows = conn.execute(
        """SELECT jd.depends_on_job_id, j.name, j.status
           FROM job_dependencies jd
           JOIN jobs j ON j.id = jd.depends_on_job_id
           WHERE jd.job_id = %s""",
        (job_id,),
    ).fetchall()
    blocking_jobs = [
        {"id": r["depends_on_job_id"], "name": r["name"], "status": r["status"]}
        for r in dep_rows
        if r["status"] != TaskStatus.COMPLETED
    ]
    active_workers = store.count_active_workers(conn, job_id)
    return _ok(job=job, tasks=tasks, progress=progress, blocking_jobs=blocking_jobs, active_workers=active_workers)


def handle_cancel_job(conn, arguments: dict) -> list:
    job_id = arguments["job_id"]
    if not store.get_job(conn, job_id):
        return _err(f"Job '{job_id}' not found")
    result = store.cancel_job(conn, job_id)
    return _ok(success=True, **result)


def handle_pause_job(conn, arguments: dict) -> list:
    job_id = arguments["job_id"]
    job = store.pause_job(conn, job_id, reason=arguments.get("reason"))
    if job is None:
        return _err(f"Job '{job_id}' not found")
    return _ok(success=True, job=job)


def handle_resume_job(conn, arguments: dict) -> list:
    job_id = arguments["job_id"]
    job = store.resume_job(conn, job_id)
    if job is None:
        return _err(f"Job '{job_id}' not found")
    return _ok(success=True, job=job)
