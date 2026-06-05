from naxe.schema import TaskStatus, JobStatus
from naxe.store.core import (
    _now, get_task, log_event, update_job_status,
    _get_newly_unblocked_jobs, _auto_transition_to_awaiting_approval,
)
from naxe.store.tasks import retry_task
from naxe.store.comments import add_task_comment


def startup_scan_awaiting_approval(conn) -> None:
    """On server start, transition any pending human_task tasks that are fully unblocked."""
    candidates = conn.execute(
        """SELECT * FROM tasks
           WHERE status = 'pending' AND human_task = 1
             AND id NOT IN (
                 SELECT d.task_id FROM dependencies d
                 JOIN tasks dep ON dep.id = d.depends_on_task_id
                 WHERE dep.status != 'completed'
             )"""
    ).fetchall()
    for task in candidates:
        _auto_transition_to_awaiting_approval(conn, task["id"], task["job_id"])


def request_approval(conn, task_id: str, agent_id: str, notes: str | None = None) -> dict | None:
    """Transition an in_progress task to awaiting_approval.

    Only the owning agent may request approval. Returns the updated task or None.
    """
    task = get_task(conn, task_id)
    if not task or task.get("status") != TaskStatus.IN_PROGRESS or task.get("owner_agent_id") != agent_id:
        return None
    now = _now()
    conn.execute(
        "UPDATE tasks SET status = 'awaiting_approval', approval_notes = %s, updated_at = %s WHERE id = %s",
        (notes, now, task_id),
    )
    log_event(conn, task_id, task["job_id"], "approval_requested", agent_id)
    return get_task(conn, task_id)


def approve_task(conn, task_id: str, approver_id: str, notes: str | None = None) -> dict | None:
    """Approve an awaiting_approval task, marking it completed.

    Returns a dict with the updated task and newly_unblocked tasks, or None if not found/eligible.
    """
    task = get_task(conn, task_id)
    if not task or task.get("status") != TaskStatus.AWAITING_APPROVAL:
        return None
    now = _now()
    cur = conn.execute(
        """UPDATE tasks SET status = 'completed', approved_by = %s, approval_notes = %s,
           updated_at = %s WHERE id = %s AND status = 'awaiting_approval'""",
        (approver_id, notes, now, task_id),
    )
    if cur.rowcount == 0:
        return None
    task = get_task(conn, task_id)
    job_id = task["job_id"]
    log_event(conn, task_id, job_id, "approved", approver_id)
    remaining = conn.execute(
        "SELECT COUNT(*) AS cnt FROM tasks WHERE job_id = %s AND status NOT IN ('completed', 'cancelled')",
        (job_id,),
    ).fetchone()["cnt"]
    if remaining == 0:
        update_job_status(conn, job_id, JobStatus.COMPLETED)
        task["_newly_unblocked_jobs"] = _get_newly_unblocked_jobs(conn, job_id)
    from naxe import resolver as _resolver
    newly_unblocked = _resolver.get_newly_unblocked(conn, job_id, task_id)
    for t in newly_unblocked:
        _auto_transition_to_awaiting_approval(conn, t["id"], t["job_id"])
    return {"task": task, "newly_unblocked": newly_unblocked}


def reject_task(conn, task_id: str, approver_id: str, reason: str) -> dict | None:
    """Hard-fail an awaiting_approval task.

    Marks the task as failed, stores reason in approval_notes, and auto-retries
    via retry_task() if max_retries allows.
    """
    task = get_task(conn, task_id)
    if not task or task.get("status") != TaskStatus.AWAITING_APPROVAL:
        return None
    now = _now()
    job_id = task["job_id"]
    log_event(conn, task_id, job_id, "rejected", approver_id, details={"reason": reason})
    conn.execute(
        """UPDATE tasks SET status = 'failed', approved_by = %s, approval_notes = %s,
           updated_at = %s WHERE id = %s AND status = 'awaiting_approval'""",
        (approver_id, reason, now, task_id),
    )
    retry_task(conn, task_id)
    return get_task(conn, task_id)


def return_task(conn, task_id: str, approver_id: str, feedback: str) -> dict | None:
    """Return an awaiting_approval task to pending with feedback for the agent.

    Stores feedback as a human comment, resets status to pending, increments
    approval_round, and clears owner_agent_id so the task can be re-claimed.
    """
    task = get_task(conn, task_id)
    if not task or task.get("status") != TaskStatus.AWAITING_APPROVAL:
        return None
    now = _now()
    job_id = task["job_id"]
    current_round = task.get("approval_round") or 0
    new_round = current_round + 1
    add_task_comment(conn, task_id, approver_id, "human", feedback)
    conn.execute(
        """UPDATE tasks SET status = 'pending', owner_agent_id = NULL,
           approval_round = %s, updated_at = %s
           WHERE id = %s AND status = 'awaiting_approval'""",
        (new_round, now, task_id),
    )
    log_event(
        conn, task_id, job_id, "feedback_loop", approver_id,
        details={"approval_round": new_round, "approver_id": approver_id},
    )
    return get_task(conn, task_id)
