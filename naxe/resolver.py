def detect_cycle(tasks: list[dict]) -> bool:
    """
    Check proposed task batch for dependency cycles using DFS.
    tasks: list of dicts with 'id'/'_resolved_id' and 'depends_on' keys.
    Returns True if a cycle exists.
    """
    # Build adjacency using resolved IDs
    graph: dict[str, list[str]] = {}
    for t in tasks:
        tid = t.get("_resolved_id") or t.get("id", "")
        graph[tid] = list(t.get("depends_on", []) or [])

    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {nid: WHITE for nid in graph}

    def dfs(node: str) -> bool:
        color[node] = GRAY
        for dep in graph.get(node, []):
            if dep not in color:
                continue  # external dep already in DB — not part of cycle check
            if color[dep] == GRAY:
                return True
            if color[dep] == WHITE and dfs(dep):
                return True
        color[node] = BLACK
        return False

    return any(dfs(n) for n in list(graph) if color[n] == WHITE)


def get_next_actions(conn, job_id: str) -> list[dict]:
    """Return all pending tasks whose dependencies are all completed."""
    pending = conn.execute(
        "SELECT * FROM tasks WHERE job_id = %s AND status = 'pending'",
        (job_id,),
    ).fetchall()

    result = []
    for task in pending:
        task_id = task["id"]
        deps = conn.execute(
            """SELECT d.depends_on_task_id, t.name, t.status
               FROM dependencies d
               JOIN tasks t ON t.id = d.depends_on_task_id
               WHERE d.task_id = %s""",
            (task_id,),
        ).fetchall()

        if not deps:
            unblocked = True
        else:
            unblocked = all(r["status"] == "completed" for r in deps)

        if unblocked:
            t = dict(task)
            dm = t.get("duration_minutes")
            t["is_quick_win"] = dm is not None and dm <= 5
            # Include names of completed deps that unblocked this task
            t["unblocked_by"] = [
                {"id": r["depends_on_task_id"], "name": r["name"]}
                for r in deps
            ]
            result.append(t)

    return result


def get_newly_unblocked(
    conn, job_id: str, just_completed_task_id: str
) -> list[dict]:
    """
    After completing a task, find tasks that are now unblocked.
    Only considers tasks that depend on the just-completed task.
    """
    candidates = conn.execute(
        """SELECT t.* FROM tasks t
           JOIN dependencies d ON d.task_id = t.id
           WHERE d.depends_on_task_id = %s AND t.status = 'pending'""",
        (just_completed_task_id,),
    ).fetchall()

    result = []
    for task in candidates:
        task_id = task["id"]
        deps = conn.execute(
            "SELECT depends_on_task_id FROM dependencies WHERE task_id = %s",
            (task_id,),
        ).fetchall()
        dep_ids = [r["depends_on_task_id"] for r in deps]
        placeholders = ",".join(["%s"] * len(dep_ids))
        completed_count = conn.execute(
            f"SELECT COUNT(*) AS cnt FROM tasks WHERE id IN ({placeholders}) AND status = 'completed'",
            dep_ids,
        ).fetchone()["cnt"]
        if completed_count == len(dep_ids):
            t = dict(task)
            dm = t.get("duration_minutes")
            t["is_quick_win"] = dm is not None and dm <= 5
            result.append(t)

    return result


def is_job_unblocked(conn, job_id: str) -> bool:
    """Return True if all jobs this job depends on are completed."""
    deps = conn.execute(
        "SELECT depends_on_job_id FROM job_dependencies WHERE job_id = %s",
        (job_id,),
    ).fetchall()
    if not deps:
        return True
    dep_ids = [r["depends_on_job_id"] for r in deps]
    placeholders = ",".join(["%s"] * len(dep_ids))
    completed = conn.execute(
        f"SELECT COUNT(*) AS cnt FROM jobs WHERE id IN ({placeholders}) AND status = 'completed'",
        dep_ids,
    ).fetchone()["cnt"]
    return completed == len(dep_ids)


def detect_job_cycle(conn, job_id: str, depends_on_job_id: str) -> bool:
    """
    Return True if adding job_id -> depends_on_job_id would create a cycle
    in the job_dependencies graph. Uses iterative DFS from depends_on_job_id.
    """
    if job_id == depends_on_job_id:
        return True
    visited: set[str] = set()
    stack = [depends_on_job_id]
    while stack:
        current = stack.pop()
        if current == job_id:
            return True
        if current in visited:
            continue
        visited.add(current)
        rows = conn.execute(
            "SELECT depends_on_job_id FROM job_dependencies WHERE job_id = %s",
            (current,),
        ).fetchall()
        for r in rows:
            stack.append(r["depends_on_job_id"])
    return False
