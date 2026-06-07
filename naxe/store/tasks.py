import json
import uuid
from datetime import datetime, timedelta, timezone

from naxe.schema import TaskStatus, JobStatus
from naxe.store.core import (
    _now, _row, get_task, get_tasks_for_job, get_job,
    log_event, update_job_status, count_active_workers,
    _get_newly_unblocked_jobs, _auto_transition_to_awaiting_approval,
)
from naxe.store.comments import get_recent_comments_for_task
from naxe.store.jobs import create_job


def add_tasks(conn, job_id: str | None = None, tasks: list[dict] = None) -> dict:
    """
    Batch insert tasks + their dependencies atomically.
    Raises ValueError if any dep ID is unknown or the job doesn't exist.

    If job_id is None, a new job is auto-created using the first task's name.
    Returns {'job_id': str, 'task_ids': list[str], 'auto_created_job': bool}.
    """
    if tasks is None:
        tasks = []
    auto_created = False
    if job_id is None:
        if not tasks:
            raise ValueError("tasks must be non-empty when job_id is not provided")
        job = create_job(conn, tasks[0]["name"])
        job_id = job["id"]
        auto_created = True
    else:
        job = get_job(conn, job_id)
        if not job:
            raise ValueError(f"Job '{job_id}' not found")
        job_id = job["id"]

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
                resources, priority, repo, requires_approval, human_task, start_date, due_date,
                recurrence_interval_days, critical, created_at, updated_at)
               VALUES (%s, %s, %s, %s, 'pending', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                tid, job_id, t["name"], t.get("description"),
                t.get("duration_minutes"), t.get("max_retries", 0), t.get("input"),
                json.dumps(resources) if resources is not None else None,
                t.get("priority", 50), t.get("repo"),
                1 if t.get("requires_approval") else 0,
                1 if t.get("human_task") else 0,
                t.get("start_date"),
                t.get("due_date"),
                t.get("recurrence_interval_days"),
                1 if t.get("critical") else 0,
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

    return {"job_id": job_id, "task_ids": task_ids, "auto_created_job": auto_created}


def claim_task(conn, task_id: str, agent_id: str) -> bool:
    """Atomic claim — returns True only if this call actually claimed the task."""
    task = get_task(conn, task_id)
    if task and task.get("human_task") == 1:
        return False
    if task and task.get("start_date") and task["start_date"] > _now():
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
            task["recent_comments"] = get_recent_comments_for_task(conn, task_id)
            return task
    return claimed


def claim_next_action(conn, job_id: str, agent_id: str, repo: str | None = None) -> dict | None:
    """Atomically find and claim the next unblocked pending task for a worker agent.

    Returns the claimed task dict, or None if no unblocked tasks are available.
    Uses optimistic concurrency: if another agent claims the same task between
    the SELECT and UPDATE, returns None (caller should retry).
    """
    # Reset timed-out tasks before selecting so they don't block the queue.
    reclaim_stale_tasks(conn, job_id)
    job = get_job(conn, job_id)
    if not job or job.get("paused") == 1:
        return None
    job_id = job["id"]  # resolve prefix to full UUID

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
        AND (t.start_date IS NULL OR t.start_date <= %s)
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
               ORDER BY critical DESC, priority DESC, created_at ASC""",
            (job_id, _now(), repo),
        ).fetchall()
    else:
        candidates = conn.execute(
            f"""SELECT * FROM tasks t
               WHERE {base_where}
               ORDER BY critical DESC, priority DESC, created_at ASC""",
            (job_id, _now()),
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
    result["status"] = TaskStatus.IN_PROGRESS
    result["owner_agent_id"] = agent_id
    result["updated_at"] = now
    log_event(conn, result["id"], job_id, "claimed", agent_id)
    result["recent_comments"] = get_recent_comments_for_task(conn, result["id"])
    return result


def reclaim_stale_tasks(conn, job_id: str) -> list[dict]:
    """Reset in_progress tasks whose heartbeat has expired back to pending.

    Only reclaims tasks where last_heartbeat_at is set and older than the job's
    heartbeat_timeout_seconds. Returns list of reclaimed task dicts.
    """
    job = get_job(conn, job_id)
    if not job:
        return []
    job_id = job["id"]
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


def cancel_task(conn, task_id: str) -> dict | None:
    """Cancel a single task. Works on pending, in_progress, and awaiting_approval (human tasks) tasks."""
    task = get_task(conn, task_id)
    if not task:
        return None
    cancellable = task["status"] in (TaskStatus.PENDING, TaskStatus.IN_PROGRESS) or (
        task["status"] == TaskStatus.AWAITING_APPROVAL and bool(task.get("human_task"))
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
            update_job_status(conn, job_id, JobStatus.COMPLETED)
    return task


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
    if task and status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
        if status == TaskStatus.COMPLETED:
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
                update_job_status(conn, job_id, JobStatus.COMPLETED, job_output)
                task["_newly_unblocked_jobs"] = _get_newly_unblocked_jobs(conn, job_id)
        log_event(conn, task_id, task["job_id"], status, agent_id)
    return task


def complete_task(conn, task_id: str, agent_id: str, output: str | None = None) -> dict:
    """Complete a task, enforcing human_task and requires_approval guards."""
    task = get_task(conn, task_id)
    if not task:
        return {"error": f"Task '{task_id}' not found"}
    if task.get("human_task") == 1:
        return {"error": "Human tasks cannot be completed by agents — use approve_task or reject_task."}
    if task.get("requires_approval") == 1 and not task.get("approved_by"):
        now = _now()
        conn.execute(
            "UPDATE tasks SET status = 'awaiting_approval', updated_at = %s WHERE id = %s",
            (now, task_id),
        )
        log_event(conn, task_id, task["job_id"], "approval_requested", agent_id)
        return {"task": get_task(conn, task_id), "routed_to_approval": True}
    # Spawn recurrence BEFORE marking complete so the job doesn't close prematurely
    recurrence_spawned = None
    recurrence_interval_days = task.get("recurrence_interval_days")
    if recurrence_interval_days:
        new_start = (datetime.now(timezone.utc) + timedelta(days=recurrence_interval_days)).isoformat()
        copy_fields = ("name", "description", "duration_minutes", "max_retries", "input",
                       "resources", "priority", "repo", "requires_approval", "human_task",
                       "recurrence_interval_days", "due_date", "critical")
        new_task = {f: task[f] for f in copy_fields if task.get(f) is not None}
        new_task["start_date"] = new_start
        result = add_tasks(conn, task["job_id"], [new_task])
        recurrence_spawned = {"job_id": task["job_id"], "task_id": result["task_ids"][0]}

    updated = update_task_status(conn, task_id, TaskStatus.COMPLETED, agent_id, output)
    from naxe import resolver as _resolver
    newly_unblocked = _resolver.get_newly_unblocked(conn, task["job_id"], task_id)
    for t in newly_unblocked:
        _auto_transition_to_awaiting_approval(conn, t["id"], t["job_id"])
    ret = {"success": True, "task": updated}
    if recurrence_spawned:
        ret["recurrence_spawned"] = recurrence_spawned
    return ret


def retry_task(conn, task_id: str) -> dict | None:
    """If retry_count < max_retries, reset task to pending and increment retry_count."""
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
    """Reset a failed or cancelled task back to pending, clearing ownership and retry count."""
    task = get_task(conn, task_id)
    if not task or task.get("status") not in (TaskStatus.FAILED, TaskStatus.CANCELLED):
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


def edit_task(conn, task_id: str, updates: dict) -> dict | None:
    """Edit metadata and/or dependencies of a pending task.

    Editable when pending (any task) or awaiting_approval (human tasks only).
    If `depends_on` is present it replaces all existing dependencies; a cycle check is
    run against the full job graph before committing.
    """
    from naxe import resolver as _resolver

    task = get_task(conn, task_id)
    if not task:
        return None
    editable = task["status"] == TaskStatus.PENDING or (
        task["status"] == TaskStatus.AWAITING_APPROVAL and bool(task.get("human_task"))
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

    editable_fields = ("name", "description", "resources", "duration_minutes", "input",
                       "max_retries", "start_date", "due_date", "recurrence_interval_days", "critical")
    set_parts = []
    params = []
    for field in editable_fields:
        if field in updates:
            val = updates[field]
            if field == "resources":
                val = json.dumps(val) if val is not None else None
            if field == "critical":
                val = 1 if val else 0
            set_parts.append(f"{field} = %s")
            params.append(val)

    if set_parts:
        now = _now()
        set_parts.append("updated_at = %s")
        params.append(now)
        params.append(task_id)
        conn.execute(f"UPDATE tasks SET {', '.join(set_parts)} WHERE id = %s", params)

    return get_task(conn, task_id)
