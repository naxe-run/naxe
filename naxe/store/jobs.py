import json
import uuid

from naxe.schema import TaskStatus, JobStatus
from naxe.store.core import _now, _row, get_job, log_event, update_job_status


def create_job(conn, name: str, max_workers: int | None = None, worktree: bool = False, context: str | None = None) -> dict:
    job_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO jobs (id, name, created_at, status, max_workers, worktree, context) VALUES (%s, %s, %s, 'active', %s, %s, %s)",
        (job_id, name, _now(), max_workers, int(worktree), context),
    )
    return _row(conn.execute("SELECT * FROM jobs WHERE id = %s", (job_id,)).fetchone())


def list_jobs(conn, limit: int = 50, offset: int = 0, id_prefix: str | None = None, context: str | None = None) -> dict:
    ctx_clause = "context IS NULL" if context is None else "context = %s"
    ctx_params: tuple = () if context is None else (context,)

    if id_prefix:
        pattern = id_prefix + "%"
        total = conn.execute(
            f"SELECT COUNT(*) as n FROM jobs WHERE id LIKE %s AND {ctx_clause}",
            (pattern,) + ctx_params,
        ).fetchone()["n"]
        jobs = [
            dict(r)
            for r in conn.execute(
                f"SELECT * FROM jobs WHERE id LIKE %s AND {ctx_clause} ORDER BY created_at DESC LIMIT %s OFFSET %s",
                (pattern,) + ctx_params + (limit, offset),
            ).fetchall()
        ]
    else:
        total = conn.execute(
            f"SELECT COUNT(*) as n FROM jobs WHERE {ctx_clause}", ctx_params
        ).fetchone()["n"]
        jobs = [
            dict(r)
            for r in conn.execute(
                f"SELECT * FROM jobs WHERE {ctx_clause} ORDER BY created_at DESC LIMIT %s OFFSET %s",
                ctx_params + (limit, offset),
            ).fetchall()
        ]
    return {"jobs": jobs, "total": total, "has_more": offset + len(jobs) < total}


def list_watch_jobs(conn, session_start: str) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            """SELECT DISTINCT j.* FROM jobs j
               LEFT JOIN tasks t ON t.job_id = j.id
               WHERE j.created_at >= %s
                  OR t.status IN ('pending', 'in_progress')
               ORDER BY j.created_at ASC""",
            (session_start,),
        ).fetchall()
    ]


def edit_job(conn, job_id: str, name: str) -> dict | None:
    """Rename a job. Returns the updated job, or None if not found."""
    job = get_job(conn, job_id)
    if not job:
        return None
    job_id = job["id"]
    conn.execute("UPDATE jobs SET name = %s WHERE id = %s", (name, job_id))
    return get_job(conn, job_id)


def set_job_concurrency(conn, job_id: str, max_workers: int | None) -> dict | None:
    """Set or clear the max_workers limit on a job. Returns the updated job, or None if not found."""
    job = get_job(conn, job_id)
    if not job:
        return None
    job_id = job["id"]
    conn.execute("UPDATE jobs SET max_workers = %s WHERE id = %s", (max_workers, job_id))
    return get_job(conn, job_id)


def set_worktree_paths(conn, job_id: str, paths: dict) -> dict | None:
    job = get_job(conn, job_id)
    if not job:
        return None
    job_id = job["id"]
    conn.execute(
        "UPDATE jobs SET worktree_paths = %s WHERE id = %s",
        (json.dumps(paths) if paths else None, job_id),
    )
    return get_job(conn, job_id)


def pause_job(conn, job_id: str, reason=None) -> dict | None:
    job = get_job(conn, job_id)
    if not job:
        return None
    job_id = job["id"]
    conn.execute("UPDATE jobs SET paused = 1, pause_reason = %s WHERE id = %s", (reason, job_id))
    return get_job(conn, job_id)


def resume_job(conn, job_id: str) -> dict | None:
    job = get_job(conn, job_id)
    if not job:
        return None
    job_id = job["id"]
    conn.execute("UPDATE jobs SET paused = 0, pause_reason = NULL WHERE id = %s", (job_id,))
    return get_job(conn, job_id)


def cancel_job(conn, job_id: str) -> dict:
    """Cancel a job and all its non-terminal tasks atomically."""
    job = get_job(conn, job_id)
    if job:
        job_id = job["id"]
    now = _now()
    non_terminal = (TaskStatus.PENDING, TaskStatus.IN_PROGRESS, TaskStatus.AWAITING_APPROVAL)
    to_cancel = [
        r["id"]
        for r in conn.execute(
            f"SELECT id FROM tasks WHERE job_id = %s AND status IN ({','.join(['%s']*len(non_terminal))})",
            (job_id, *non_terminal),
        ).fetchall()
    ]
    conn.execute("UPDATE jobs SET status = 'cancelled' WHERE id = %s", (job_id,))
    cur = conn.execute(
        f"""UPDATE tasks SET status = 'cancelled', updated_at = %s
           WHERE job_id = %s AND status IN ({','.join(['%s']*len(non_terminal))})""",
        (now, job_id, *non_terminal),
    )
    tasks_cancelled = cur.rowcount
    job = get_job(conn, job_id)
    for task_id in to_cancel:
        log_event(conn, task_id, job_id, "cancelled")
    return {"job": job, "tasks_cancelled": tasks_cancelled}


def add_job_dependency(conn, job_id: str, depends_on_job_id: str) -> None:
    """Add a dependency edge between jobs, raising ValueError on cycle or missing jobs."""
    from naxe import resolver as _resolver

    j1 = get_job(conn, job_id)
    if not j1:
        raise ValueError(f"Job '{job_id}' not found")
    job_id = j1["id"]
    j2 = get_job(conn, depends_on_job_id)
    if not j2:
        raise ValueError(f"Job '{depends_on_job_id}' not found")
    depends_on_job_id = j2["id"]
    if _resolver.detect_job_cycle(conn, job_id, depends_on_job_id):
        raise ValueError("Dependency would create a cycle in job dependencies")
    conn.execute(
        "INSERT INTO job_dependencies (job_id, depends_on_job_id) VALUES (%s, %s)",
        (job_id, depends_on_job_id),
    )
