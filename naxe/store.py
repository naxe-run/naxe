import json
import uuid
from datetime import datetime, timedelta, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row(row) -> dict | None:
    return dict(row) if row else None


def create_job(conn, name: str, max_workers: int | None = None, worktree: bool = False) -> dict:
    job_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO jobs (id, name, created_at, status, max_workers, worktree) VALUES (%s, %s, %s, 'active', %s, %s)",
        (job_id, name, _now(), max_workers, int(worktree)),
    )
    return _row(conn.execute("SELECT * FROM jobs WHERE id = %s", (job_id,)).fetchone())


def set_job_concurrency(conn, job_id: str, max_workers: int | None) -> dict | None:
    """Set or clear the max_workers limit on a job. Returns the updated job, or None if not found."""
    if not get_job(conn, job_id):
        return None
    conn.execute("UPDATE jobs SET max_workers = %s WHERE id = %s", (max_workers, job_id))
    return get_job(conn, job_id)


def set_worktree_paths(conn, job_id: str, paths: dict) -> dict | None:
    if not get_job(conn, job_id):
        return None
    conn.execute(
        "UPDATE jobs SET worktree_paths = %s WHERE id = %s",
        (json.dumps(paths) if paths else None, job_id),
    )
    return get_job(conn, job_id)


def get_job(conn, job_id: str) -> dict | None:
    row = _row(conn.execute("SELECT * FROM jobs WHERE id = %s", (job_id,)).fetchone())
    if row and row.get("worktree_paths"):
        try:
            row["worktree_paths"] = json.loads(row["worktree_paths"])
        except (json.JSONDecodeError, TypeError):
            pass
    return row


def list_jobs(conn, limit: int = 50, offset: int = 0) -> dict:
    total = conn.execute("SELECT COUNT(*) as n FROM jobs").fetchone()["n"]
    jobs = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT %s OFFSET %s", (limit, offset)
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


def add_tasks(conn, job_id: str, tasks: list[dict]) -> list[str]:
    """
    Batch insert tasks + their dependencies atomically.
    Raises ValueError if any dep ID is unknown or the job doesn't exist.
    Caller is responsible for cycle detection before calling this.
    """
    if not get_job(conn, job_id):
        raise ValueError(f"Job '{job_id}' not found")

    # Build set of all known task IDs (existing in DB + new batch)
    existing_ids = {
        r["id"]
        for r in conn.execute("SELECT id FROM tasks WHERE job_id = %s", (job_id,)).fetchall()
    }
    batch_ids = set()
    for t in tasks:
        tid = t.get("id") or str(uuid.uuid4())
        t["_resolved_id"] = tid
        batch_ids.add(tid)

    all_known = existing_ids | batch_ids

    # Validate all dep IDs are known
    for t in tasks:
        for dep in t.get("depends_on", []) or []:
            if dep not in all_known:
                raise ValueError(f"Unknown dependency '{dep}' for task '{t.get('name')}'")

    now = _now()
    task_ids = []
    for t in tasks:
        tid = t["_resolved_id"]
        resources = t.get("resources")
        conn.execute(
            """INSERT INTO tasks
               (id, job_id, name, description, status, duration_minutes, max_retries, input,
                resources, priority, repo, requires_approval, human_task, created_at, updated_at)
               VALUES (%s, %s, %s, %s, 'pending', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                tid, job_id, t["name"], t.get("description"),
                t.get("duration_minutes"), t.get("max_retries", 0), t.get("input"),
                json.dumps(resources) if resources is not None else None,
                t.get("priority", 50), t.get("repo"),
                1 if t.get("requires_approval") else 0,
                1 if t.get("human_task") else 0,
                now, now,
            ),
        )
        for dep in t.get("depends_on", []) or []:
            conn.execute(
                "INSERT INTO dependencies (task_id, depends_on_task_id) VALUES (%s, %s)",
                (tid, dep),
            )
        task_ids.append(tid)

    for tid in task_ids:
        log_event(conn, tid, job_id, "created")

    # Auto-transition human_task tasks that are already unblocked
    for tid in task_ids:
        _auto_transition_to_awaiting_approval(conn, tid, job_id)

    return task_ids


def _auto_transition_to_awaiting_approval(conn, task_id: str, job_id: str) -> None:
    """For any newly-unblocked tasks with human_task=1, auto-transition to awaiting_approval."""
    task = get_task(conn, task_id)
    if not task or task.get("human_task") != 1 or task.get("status") != "pending":
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


def get_task(conn, task_id: str) -> dict | None:
    return _row(conn.execute("SELECT * FROM tasks WHERE id = %s", (task_id,)).fetchone())


def get_tasks_for_job(conn, job_id: str) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM tasks WHERE job_id = %s ORDER BY created_at", (job_id,)
        ).fetchall()
    ]


def claim_task(conn, task_id: str, agent_id: str) -> bool:
    """Atomic claim — returns True only if this call actually claimed the task."""
    task = get_task(conn, task_id)
    if task and task.get("human_task") == 1:
        return False
    now = _now()
    cur = conn.execute(
        """UPDATE tasks SET status = 'in_progress', owner_agent_id = %s, updated_at = %s
           WHERE id = %s AND status = 'pending' AND owner_agent_id IS NULL""",
        (agent_id, now, task_id),
    )
    claimed = cur.rowcount == 1
    if claimed:
        task = get_task(conn, task_id)
        if task:
            log_event(conn, task_id, task["job_id"], "claimed", agent_id)
    return claimed


def claim_next_action(conn, job_id: str, agent_id: str, repo: str | None = None) -> dict | None:
    """Atomically find and claim the next unblocked pending task for a worker agent.

    Returns the claimed task dict, or None if no unblocked tasks are available.
    Uses optimistic concurrency: if another agent claims the same task between
    the SELECT and UPDATE, returns None (caller should retry).
    """
    reclaim_stale_tasks(conn, job_id)
    job = get_job(conn, job_id)
    if not job or job.get("paused") == 1:
        return None

    now = _now()

    # Collect resources held by in-progress tasks, scoped by worktree flag
    held_resources: set[str] = set()
    if job.get("worktree"):
        # Worktree job: only check resources within this job
        in_progress = conn.execute(
            "SELECT resources FROM tasks WHERE job_id = %s AND status = 'in_progress'",
            (job_id,),
        ).fetchall()
    else:
        # Non-worktree job: check resources across all non-worktree jobs
        in_progress = conn.execute(
            """SELECT t.resources FROM tasks t
               JOIN jobs j ON j.id = t.job_id
               WHERE t.status = 'in_progress' AND j.worktree = 0""",
        ).fetchall()
    for row in in_progress:
        raw = row["resources"]
        if raw:
            held_resources.update(json.loads(raw))

    # Find the first unblocked pending non-human task whose resources don't conflict
    base_where = """
        t.job_id = %s
        AND t.status = 'pending'
        AND t.owner_agent_id IS NULL
        AND (t.human_task = 0 OR t.human_task IS NULL)
        AND NOT EXISTS (
            SELECT 1 FROM dependencies d
            JOIN tasks dep ON dep.id = d.depends_on_task_id
            WHERE d.task_id = t.id AND dep.status NOT IN ('completed')
        )
    """

    if repo is not None:
        candidates = conn.execute(
            f"""SELECT * FROM tasks t
               WHERE {base_where}
                 AND (t.repo = %s OR t.repo IS NULL)
               ORDER BY priority DESC, created_at ASC""",
            (job_id, repo),
        ).fetchall()
    else:
        candidates = conn.execute(
            f"""SELECT * FROM tasks t
               WHERE {base_where}
               ORDER BY priority DESC, created_at ASC""",
            (job_id,),
        ).fetchall()

    task = None
    for candidate in candidates:
        raw = candidate["resources"]
        candidate_resources = set(json.loads(raw)) if raw else set()
        if not candidate_resources & held_resources:
            task = candidate
            break

    if task is None:
        return None

    # Enforce max_workers concurrency limit if set
    max_workers = job.get("max_workers") if job else None
    if max_workers is not None and count_active_workers(conn, job_id) >= max_workers:
        return None

    cur = conn.execute(
        """UPDATE tasks SET status = 'in_progress', owner_agent_id = %s, updated_at = %s
           WHERE id = %s AND status = 'pending' AND owner_agent_id IS NULL""",
        (agent_id, now, task["id"]),
    )

    if cur.rowcount == 0:
        return None

    result = dict(task)
    result["status"] = "in_progress"
    result["owner_agent_id"] = agent_id
    result["updated_at"] = now
    dm = result.get("duration_minutes")
    result["is_quick_win"] = dm is not None and dm <= 5
    log_event(conn, result["id"], job_id, "claimed", agent_id)
    return result


def cancel_task(conn, task_id: str) -> dict | None:
    """Cancel a single task. Works on pending, in_progress, and awaiting_approval (human tasks) tasks.

    Returns the updated task, or None if the task doesn't exist or is already terminal.
    """
    task = get_task(conn, task_id)
    if not task:
        return None
    cancellable = task["status"] in ("pending", "in_progress") or (
        task["status"] == "awaiting_approval" and bool(task.get("human_task"))
    )
    if not cancellable:
        return None
    now = _now()
    cur = conn.execute(
        "UPDATE tasks SET status = 'cancelled', updated_at = %s WHERE id = %s",
        (now, task_id),
    )
    if cur.rowcount == 0:
        return None
    task = get_task(conn, task_id)
    if task:
        log_event(conn, task_id, task["job_id"], "cancelled")
        job_id = task["job_id"]
        remaining = conn.execute(
            "SELECT COUNT(*) AS cnt FROM tasks WHERE job_id = %s AND status NOT IN ('completed', 'cancelled')",
            (job_id,),
        ).fetchone()["cnt"]
        if remaining == 0:
            update_job_status(conn, job_id, "completed")
    return task


def cancel_job(conn, job_id: str) -> dict:
    """Cancel a job and all its non-terminal tasks atomically.

    Returns a dict with the updated job and count of tasks cancelled.
    """
    now = _now()
    non_terminal = ('pending', 'in_progress', 'awaiting_approval')
    to_cancel = [
        r["id"]
        for r in conn.execute(
            f"SELECT id FROM tasks WHERE job_id = %s AND status IN ({','.join(['%s']*len(non_terminal))})",
            (job_id, *non_terminal),
        ).fetchall()
    ]
    conn.execute(
        "UPDATE jobs SET status = 'cancelled' WHERE id = %s",
        (job_id,),
    )
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


def pause_job(conn, job_id: str) -> dict | None:
    if not get_job(conn, job_id):
        return None
    conn.execute("UPDATE jobs SET paused = 1 WHERE id = %s", (job_id,))
    return get_job(conn, job_id)


def resume_job(conn, job_id: str) -> dict | None:
    if not get_job(conn, job_id):
        return None
    conn.execute("UPDATE jobs SET paused = 0 WHERE id = %s", (job_id,))
    return get_job(conn, job_id)


def reclaim_stale_tasks(conn, job_id: str) -> list[dict]:
    """Reset in_progress tasks whose heartbeat has expired back to pending.

    Only reclaims tasks where last_heartbeat_at is set and older than the job's
    heartbeat_timeout_seconds. Returns list of reclaimed task dicts.
    """
    job = get_job(conn, job_id)
    if not job:
        return []
    timeout = job.get("heartbeat_timeout_seconds") or 300
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=timeout)).isoformat()
    stale = conn.execute(
        """SELECT id FROM tasks
           WHERE job_id = %s AND status = 'in_progress'
             AND last_heartbeat_at IS NOT NULL AND last_heartbeat_at < %s""",
        (job_id, cutoff),
    ).fetchall()
    if not stale:
        return []
    now = _now()
    reclaimed = []
    for row in stale:
        task_id = row["id"]
        conn.execute(
            """UPDATE tasks SET status = 'pending', owner_agent_id = NULL, updated_at = %s
               WHERE id = %s""",
            (now, task_id),
        )
        reclaimed.append(get_task(conn, task_id))
    result = [t for t in reclaimed if t]
    for t in result:
        log_event(conn, t["id"], job_id, "reclaimed")
    return result


def heartbeat_task(conn, task_id: str, agent_id: str) -> dict | None:
    """Update last_heartbeat_at only if this agent owns the task. Returns updated task or None."""
    now = _now()
    cur = conn.execute(
        "UPDATE tasks SET last_heartbeat_at = %s, updated_at = %s WHERE id = %s AND owner_agent_id = %s",
        (now, now, task_id, agent_id),
    )
    if cur.rowcount == 0:
        return None
    return get_task(conn, task_id)


def update_task_progress(conn, task_id: str, agent_id: str, progress: int) -> dict | None:
    """Update progress (0-100) on a task owned by this agent. Returns updated task or error dict."""
    if not (0 <= progress <= 100):
        return {"error": "progress must be between 0 and 100"}
    task = get_task(conn, task_id)
    if not task:
        return {"error": f"Task '{task_id}' not found"}
    if task.get("owner_agent_id") != agent_id:
        return {"error": "agent_id does not match task owner"}
    now = _now()
    conn.execute(
        "UPDATE tasks SET progress = %s, updated_at = %s WHERE id = %s",
        (progress, now, task_id),
    )
    return get_task(conn, task_id)


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


def retry_task(conn, task_id: str) -> dict | None:
    """If retry_count < max_retries, reset task to pending and increment retry_count.

    Returns the updated task dict, or None if retries are exhausted.
    """
    task = get_task(conn, task_id)
    if not task:
        return None
    retry_count = task.get("retry_count") or 0
    max_retries = task.get("max_retries") or 0
    if retry_count >= max_retries:
        return None
    now = _now()
    conn.execute(
        """UPDATE tasks SET status = 'pending', owner_agent_id = NULL,
           retry_count = retry_count + 1, updated_at = %s
           WHERE id = %s""",
        (now, task_id),
    )
    task = get_task(conn, task_id)
    if task:
        log_event(conn, task_id, task["job_id"], "retried")
    return task


def requeue_task(conn, task_id: str, input=None) -> dict | None:
    """Reset a failed or cancelled task back to pending, clearing ownership and retry count.

    If input is provided, also replaces the task's existing input.
    Returns the updated task, or None if not found or in a non-requeue-able state.
    """
    task = get_task(conn, task_id)
    if not task or task.get("status") not in ("failed", "cancelled"):
        return None
    now = _now()
    if input is not None:
        conn.execute(
            """UPDATE tasks SET status = 'pending', owner_agent_id = NULL, retry_count = 0,
               input = %s, updated_at = %s
               WHERE id = %s""",
            (input, now, task_id),
        )
    else:
        conn.execute(
            """UPDATE tasks SET status = 'pending', owner_agent_id = NULL, retry_count = 0,
               updated_at = %s
               WHERE id = %s""",
            (now, task_id),
        )
    task = get_task(conn, task_id)
    if task:
        log_event(conn, task_id, task["job_id"], "requeued")
    return task


def request_approval(conn, task_id: str, agent_id: str, notes: str | None = None) -> dict | None:
    """Transition an in_progress task to awaiting_approval.

    Only the owning agent may request approval. Returns the updated task or None.
    """
    task = get_task(conn, task_id)
    if not task or task.get("status") != "in_progress" or task.get("owner_agent_id") != agent_id:
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
    if not task or task.get("status") != "awaiting_approval":
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
        update_job_status(conn, job_id, "completed")
        task["_newly_unblocked_jobs"] = _get_newly_unblocked_jobs(conn, job_id)
    from naxe import resolver as _resolver
    newly_unblocked = _resolver.get_newly_unblocked(conn, job_id, task_id)
    for t in newly_unblocked:
        _auto_transition_to_awaiting_approval(conn, t["id"], t["job_id"])
    return {"task": task, "newly_unblocked": newly_unblocked}


def reject_task(conn, task_id: str, approver_id: str, reason: str) -> dict | None:
    """Reject an awaiting_approval task, marking it failed (and auto-retrying if configured).

    Returns the updated task or None if not found/eligible.
    """
    task = get_task(conn, task_id)
    if not task or task.get("status") != "awaiting_approval":
        return None
    now = _now()
    conn.execute(
        """UPDATE tasks SET status = 'failed', approved_by = %s, approval_notes = %s,
           updated_at = %s WHERE id = %s AND status = 'awaiting_approval'""",
        (approver_id, reason, now, task_id),
    )
    task = get_task(conn, task_id)
    log_event(conn, task_id, task["job_id"], "rejected", approver_id)
    retry_task(conn, task_id)
    return get_task(conn, task_id)


def complete_task(conn, task_id: str, agent_id: str, output: str | None = None) -> dict:
    """Complete a task, enforcing human_task and requires_approval guards.

    Returns a dict with success/task, or an error dict.
    """
    task = get_task(conn, task_id)
    if not task:
        return {"error": f"Task '{task_id}' not found"}
    if task.get("human_task") == 1:
        return {"error": "Human tasks cannot be completed by agents — use approve_task or reject_task."}
    if task.get("requires_approval") == 1 and not task.get("approved_by"):
        return {"error": "Task requires approval before it can be completed. Call request_approval first."}
    updated = update_task_status(conn, task_id, "completed", agent_id, output)
    from naxe import resolver as _resolver
    newly_unblocked = _resolver.get_newly_unblocked(conn, task["job_id"], task_id)
    for t in newly_unblocked:
        _auto_transition_to_awaiting_approval(conn, t["id"], t["job_id"])
    return {"success": True, "task": updated}


def update_task_status(
    conn,
    task_id: str,
    status: str,
    agent_id: str | None = None,
    output: str | None = None,
) -> dict | None:
    now = _now()
    if output is not None:
        conn.execute(
            "UPDATE tasks SET status = %s, output = %s, updated_at = %s WHERE id = %s",
            (status, output, now, task_id),
        )
    else:
        conn.execute(
            "UPDATE tasks SET status = %s, updated_at = %s WHERE id = %s",
            (status, now, task_id),
        )
    task = get_task(conn, task_id)
    if task and status in ("completed", "failed"):
        if status == "completed":
            job_id = task["job_id"]
            remaining = conn.execute(
                "SELECT COUNT(*) AS cnt FROM tasks WHERE job_id = %s AND status NOT IN ('completed', 'cancelled')",
                (job_id,),
            ).fetchone()["cnt"]
            if remaining == 0:
                task_outputs = conn.execute(
                    """SELECT name, output FROM tasks
                       WHERE job_id = %s AND output IS NOT NULL AND output != ''
                       ORDER BY created_at""",
                    (job_id,),
                ).fetchall()
                job_output = "\n\n".join(
                    f"## {r['name']}\n{r['output']}" for r in task_outputs if r["output"]
                ) or None
                update_job_status(conn, job_id, "completed", job_output)
                task["_newly_unblocked_jobs"] = _get_newly_unblocked_jobs(conn, job_id)
        log_event(conn, task_id, task["job_id"], status, agent_id)
    return task


def count_active_workers(conn, job_id: str) -> int:
    """Return the number of distinct agents currently working on this job."""
    row = conn.execute(
        "SELECT COUNT(DISTINCT owner_agent_id) AS cnt FROM tasks WHERE job_id = %s AND status = 'in_progress'",
        (job_id,),
    ).fetchone()
    return row["cnt"] if row else 0


def add_job_dependency(conn, job_id: str, depends_on_job_id: str) -> None:
    """Add a dependency edge between jobs, raising ValueError on cycle or missing jobs."""
    from naxe import resolver as _resolver

    if not get_job(conn, job_id):
        raise ValueError(f"Job '{job_id}' not found")
    if not get_job(conn, depends_on_job_id):
        raise ValueError(f"Job '{depends_on_job_id}' not found")
    if _resolver.detect_job_cycle(conn, job_id, depends_on_job_id):
        raise ValueError("Dependency would create a cycle in job dependencies")
    conn.execute(
        "INSERT INTO job_dependencies (job_id, depends_on_job_id) VALUES (%s, %s)",
        (job_id, depends_on_job_id),
    )


def update_job_status(conn, job_id: str, status: str, output: str | None = None) -> dict | None:
    """Update a job's status. Returns the updated job, or None if not found."""
    if not get_job(conn, job_id):
        return None
    if output is not None:
        conn.execute(
            "UPDATE jobs SET status = %s, output = %s WHERE id = %s", (status, output, job_id)
        )
    else:
        conn.execute("UPDATE jobs SET status = %s WHERE id = %s", (status, job_id))
    return get_job(conn, job_id)


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


def edit_job(conn, job_id: str, name: str) -> dict | None:
    """Rename a job. Returns the updated job, or None if not found."""
    if not get_job(conn, job_id):
        return None
    conn.execute("UPDATE jobs SET name = %s WHERE id = %s", (name, job_id))
    return get_job(conn, job_id)


def edit_task(conn, task_id: str, updates: dict) -> dict | None:
    """Edit metadata and/or dependencies of a task.

    Editable when pending (any task) or awaiting_approval (human tasks only).
    Fields absent from `updates` are left unchanged.
    If `depends_on` is present it replaces all existing dependencies; a cycle check is
    run against the full job graph before committing.

    Returns the updated task, or None if the task is not found or not pending.
    Raises ValueError on unknown dep IDs or a cycle.
    """
    from naxe import resolver as _resolver

    task = get_task(conn, task_id)
    if not task:
        return None
    editable = task["status"] == "pending" or (
        task["status"] == "awaiting_approval" and bool(task.get("human_task"))
    )
    if not editable:
        return None

    job_id = task["job_id"]

    if "depends_on" in updates:
        new_deps = list(updates["depends_on"] or [])
        all_ids = {
            r["id"]
            for r in conn.execute("SELECT id FROM tasks WHERE job_id = %s", (job_id,)).fetchall()
        }
        for dep in new_deps:
            if dep not in all_ids:
                raise ValueError(f"Unknown dependency '{dep}'")

        # Build full job graph with proposed deps for this task, check for cycles
        existing_deps = conn.execute(
            "SELECT task_id, depends_on_task_id FROM dependencies "
            "WHERE task_id IN (SELECT id FROM tasks WHERE job_id = %s)",
            (job_id,),
        ).fetchall()
        graph: dict[str, list[str]] = {tid: [] for tid in all_ids}
        for d in existing_deps:
            if d["task_id"] != task_id:
                graph[d["task_id"]].append(d["depends_on_task_id"])
        graph[task_id] = new_deps
        batch = [{"_resolved_id": tid, "depends_on": deps} for tid, deps in graph.items()]
        if _resolver.detect_cycle(batch):
            raise ValueError("Dependency change would create a cycle")

        conn.execute("DELETE FROM dependencies WHERE task_id = %s", (task_id,))
        for dep in new_deps:
            conn.execute(
                "INSERT INTO dependencies (task_id, depends_on_task_id) VALUES (%s, %s)",
                (task_id, dep),
            )

    editable = ("name", "description", "resources", "duration_minutes", "input", "max_retries")
    set_parts = []
    params = []
    for field in editable:
        if field in updates:
            val = updates[field]
            if field == "resources":
                val = json.dumps(val) if val is not None else None
            set_parts.append(f"{field} = %s")
            params.append(val)

    if set_parts:
        now = _now()
        set_parts.append("updated_at = %s")
        params.append(now)
        params.append(task_id)
        conn.execute(f"UPDATE tasks SET {', '.join(set_parts)} WHERE id = %s", params)

    return get_task(conn, task_id)


def create_template(conn, name: str, description: str | None, tasks: list[dict]) -> dict:
    from naxe import resolver as _resolver

    for t in tasks:
        if not t.get("id"):
            t["id"] = str(uuid.uuid4())
        t["_resolved_id"] = t["id"]
    if _resolver.detect_cycle(tasks):
        raise ValueError("Dependency cycle detected in template tasks")

    template_id = str(uuid.uuid4())
    now = _now()
    conn.execute(
        "INSERT INTO job_templates (id, name, description, tasks_json, created_at) VALUES (%s, %s, %s, %s, %s)",
        (template_id, name, description, json.dumps(tasks), now),
    )
    return _row(conn.execute("SELECT * FROM job_templates WHERE id = %s", (template_id,)).fetchone())


def list_templates(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM job_templates ORDER BY name").fetchall()]


def get_template(conn, template_id: str) -> dict | None:
    return _row(conn.execute("SELECT * FROM job_templates WHERE id = %s", (template_id,)).fetchone())


def instantiate_template(conn, template_id: str, job_name: str) -> dict:
    template = get_template(conn, template_id)
    if not template:
        raise ValueError(f"Template '{template_id}' not found")
    tasks = json.loads(template["tasks_json"])
    # Remap all task IDs to fresh UUIDs so each instantiation is independent
    id_map = {t["id"]: str(uuid.uuid4()) for t in tasks if t.get("id")}
    remapped = []
    for t in tasks:
        new_t = {k: v for k, v in t.items() if k not in ("id", "_resolved_id", "depends_on")}
        new_t["id"] = id_map.get(t.get("id", ""), str(uuid.uuid4()))
        new_t["depends_on"] = [id_map.get(d, d) for d in (t.get("depends_on") or [])]
        remapped.append(new_t)
    job = create_job(conn, job_name)
    add_tasks(conn, job["id"], remapped)
    return job


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
