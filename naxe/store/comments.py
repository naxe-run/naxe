import uuid

from naxe.store.core import _now, get_task, log_event


def get_recent_comments_for_task(conn, task_id: str, rounds: int = 1) -> list[dict]:
    """Return comments from the most recent `rounds` approval rounds for a task."""
    row = conn.execute(
        """SELECT MAX(approval_round) AS max_round FROM task_comments WHERE task_id = %s""",
        (task_id,),
    ).fetchone()
    if not row or row["max_round"] is None:
        return []
    min_round = row["max_round"] - rounds + 1
    return [
        dict(r)
        for r in conn.execute(
            """SELECT * FROM task_comments
               WHERE task_id = %s AND approval_round >= %s
               ORDER BY created_at ASC""",
            (task_id, min_round),
        ).fetchall()
    ]


def get_task_comments(conn, task_id: str, approval_round: int | None = None) -> list[dict]:
    """Return all comments for a task, optionally filtered by approval_round."""
    if approval_round is not None:
        return [
            dict(r)
            for r in conn.execute(
                """SELECT * FROM task_comments
                   WHERE task_id = %s AND approval_round = %s
                   ORDER BY created_at ASC""",
                (task_id, approval_round),
            ).fetchall()
        ]
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM task_comments WHERE task_id = %s ORDER BY created_at ASC",
            (task_id,),
        ).fetchall()
    ]


def add_task_comment(
    conn, task_id: str, author_id: str, author_type: str, content: str
) -> dict | None:
    """Add a comment to a task. author_type must be 'agent' or 'human'.

    Returns the new comment dict, or None if the task does not exist or author_type is invalid.
    """
    if author_type not in ("agent", "human"):
        return None
    task = get_task(conn, task_id)
    if not task:
        return None
    comment_id = str(uuid.uuid4())
    now = _now()
    current_round = task.get("approval_round") or 0
    conn.execute(
        """INSERT INTO task_comments (id, task_id, job_id, author_id, author_type, content, approval_round, created_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (comment_id, task_id, task["job_id"], author_id, author_type, content, current_round, now),
    )
    log_event(
        conn,
        task_id,
        task["job_id"],
        "comment_added",
        author_id if author_type == "agent" else None,
        details={"author_type": author_type, "approval_round": current_round},
    )
    return dict(
        conn.execute(
            "SELECT * FROM task_comments WHERE id = %s", (comment_id,)
        ).fetchone()
    )
