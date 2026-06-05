from naxe.handlers._common import _ok, _err
from naxe import store


def handle_add_job_dependency(conn, arguments: dict) -> list:
    try:
        store.add_job_dependency(conn, arguments["job_id"], arguments["depends_on_job_id"])
    except ValueError as e:
        return _err(str(e))
    return _ok(success=True, job_id=arguments["job_id"], depends_on_job_id=arguments["depends_on_job_id"])


def handle_set_job_concurrency(conn, arguments: dict) -> list:
    updated = store.set_job_concurrency(conn, arguments["job_id"], arguments.get("max_workers"))
    if updated is None:
        return _err(f"Job '{arguments['job_id']}' not found")
    return _ok(success=True, job=updated)


def handle_set_worktree_paths(conn, arguments: dict) -> list:
    updated = store.set_worktree_paths(conn, arguments["job_id"], arguments.get("paths", {}))
    if updated is None:
        return _err(f"Job '{arguments['job_id']}' not found")
    return _ok(success=True, job=updated)
