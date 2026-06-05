import json
import uuid
from datetime import datetime, timezone

from naxe.schema import TaskStatus, JobStatus


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row(row) -> dict | None:
    return dict(row) if row else None


def get_job(conn, job_id: str) -> dict | None:
    row = _row(conn.execute("SELECT * FROM jobs WHERE id = %s", (job_id,)).fetchone())
    if row is None:
        # Fall back to prefix search so callers can use short IDs (e.g. first 8 chars).
        rows = conn.execute("SELECT * FROM jobs WHERE id LIKE %s", (job_id + "%",)).fetchall()
        if len(rows) > 1:
            raise ValueError(f"Ambiguous job ID prefix '{job_id}' matches {len(rows)} jobs")
        row = _row(rows[0]) if rows else None
    if row and row.get("worktree_paths"):
        try:
            row["worktree_paths"] = json.loads(row["worktree_paths"])
        except (json.JSONDecodeError, TypeError):
            pass
    return row


def get_task(conn, task_id: str) -> dict | None:
    return _row(conn.execute("SELECT * FROM tasks WHERE id = %s", (task_id,)).fetchone())


def get_tasks_for_job(conn, job_id: str) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM tasks WHERE job_id = %s ORDER BY created_at", (job_id,)
        ).fetchall()
    ]


def log_event(
    conn,
    task_id: str,
    job_id: str,
    event_type: str,
    agent_id: str | None = None,
    details: dict | None = None,
) -> None:
    conn.execute(
        """INSERT INTO task_events (id, task_id, job_id, event_type, agent_id, timestamp, details)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (
            str(uuid.uuid4()),
            task_id,
            job_id,
            event_type,
            agent_id,
            _now(),
            json.dumps(details) if details is not None else None,
        ),
    )


def get_task_events(conn, task_id: str) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM task_events WHERE task_id = %s ORDER BY timestamp",
            (task_id,),
        ).fetchall()
    ]


def get_job_events(conn, job_id: str) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM task_events WHERE job_id = %s ORDER BY timestamp",
            (job_id,),
        ).fetchall()
    ]


def update_job_status(conn, job_id: str, status: str, output: str | None = None) -> dict | None:
    """Update a job's status. Returns the updated job, or None if not found."""
    job = get_job(conn, job_id)
    if not job:
        return None
    job_id = job["id"]
    if output is not None:
        conn.execute(
            "UPDATE jobs SET status = %s, output = %s WHERE id = %s", (status, output, job_id)
        )
    else:
        conn.execute("UPDATE jobs SET status = %s WHERE id = %s", (status, job_id))
    return get_job(conn, job_id)


def count_active_workers(conn, job_id: str) -> int:
    """Return the number of distinct agents currently working on this job."""
    row = conn.execute(
        "SELECT COUNT(DISTINCT owner_agent_id) AS cnt FROM tasks WHERE job_id = %s AND status = 'in_progress'",
        (job_id,),
    ).fetchone()
    return row["cnt"] if row else 0


def _get_newly_unblocked_jobs(conn, completed_job_id: str) -> list[dict]:
    """Return jobs that were blocked on completed_job_id and are now fully unblocked."""
    from naxe import resolver as _resolver

    dependents = conn.execute(
        "SELECT job_id FROM job_dependencies WHERE depends_on_job_id = %s",
        (completed_job_id,),
    ).fetchall()
    unblocked = []
    for row in dependents:
        jid = row["job_id"]
        job = get_job(conn, jid)
        if job and job.get("status") == "blocked" and _resolver.is_job_unblocked(conn, jid):
            unblocked.append(job)
    return unblocked


def _auto_transition_to_awaiting_approval(conn, task_id: str, job_id: str) -> None:
    """For any newly-unblocked tasks with human_task=1, auto-transition to awaiting_approval."""
    task = get_task(conn, task_id)
    if not task or task.get("human_task") != 1 or task.get("status") != TaskStatus.PENDING:
        return
    deps = conn.execute(
        "SELECT depends_on_task_id FROM dependencies WHERE task_id = %s",
        (task_id,),
    ).fetchall()
    if deps:
        dep_ids = [r["depends_on_task_id"] for r in deps]
        placeholders = ",".join(["%s"] * len(dep_ids))
        completed = conn.execute(
            f"SELECT COUNT(*) AS cnt FROM tasks WHERE id IN ({placeholders}) AND status = 'completed'",
            dep_ids,
        ).fetchone()["cnt"]
        if completed != len(dep_ids):
            return
    now = _now()
    conn.execute(
        "UPDATE tasks SET status = 'awaiting_approval', updated_at = %s WHERE id = %s AND status = 'pending'",
        (now, task_id),
    )
    log_event(conn, task_id, job_id, "awaiting_approval")
