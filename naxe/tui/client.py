from __future__ import annotations

from abc import ABC, abstractmethod

from naxe import store


class NaxeClient(ABC):

    # ── Read ──────────────────────────────────────────────────────────────────

    @abstractmethod
    def fetch_jobs(self, status_filter: str) -> list[dict]: ...

    @abstractmethod
    def batch_task_counts(self, job_ids: list[str]) -> dict[str, dict[str, int]]: ...

    @abstractmethod
    def get_job(self, job_id: str) -> dict | None: ...

    @abstractmethod
    def get_task(self, task_id: str) -> dict | None: ...

    @abstractmethod
    def get_tasks_for_job(self, job_id: str) -> list[dict]: ...

    @abstractmethod
    def get_task_events(self, task_id: str) -> list[dict]: ...

    @abstractmethod
    def get_blocked_task_ids(self, task_ids: list[str]) -> set[str]: ...

    @abstractmethod
    def get_dependency_edges(self, task_ids: list[str]) -> list[dict]: ...

    @abstractmethod
    def fetch_human_actions(self) -> list[dict]: ...

    # ── Write ─────────────────────────────────────────────────────────────────

    @abstractmethod
    def create_job(self, name: str, max_workers: int | None = None) -> dict: ...

    @abstractmethod
    def edit_job(self, job_id: str, name: str) -> dict | None: ...

    @abstractmethod
    def cancel_job(self, job_id: str) -> dict: ...

    @abstractmethod
    def pause_job(self, job_id: str, reason: str | None = None) -> None: ...

    @abstractmethod
    def resume_job(self, job_id: str) -> None: ...

    @abstractmethod
    def add_tasks(self, tasks: list[dict], job_id: str | None = None) -> dict: ...

    @abstractmethod
    def cancel_task(self, task_id: str) -> None: ...

    @abstractmethod
    def edit_task(self, task_id: str, updates: dict) -> None: ...

    @abstractmethod
    def approve_task(self, task_id: str) -> None: ...

    @abstractmethod
    def reject_task(self, task_id: str, reason: str) -> None: ...

    @abstractmethod
    def return_task(self, task_id: str, feedback: str) -> None: ...

    @abstractmethod
    def add_task_comment(self, task_id: str, content: str) -> None: ...

    @abstractmethod
    def rollback(self) -> None: ...


class LocalNaxeClient(NaxeClient):
    """Direct SQLite-backed client; conn is not readonly."""

    def __init__(self, conn) -> None:
        self._conn = conn

    # ── Read ──────────────────────────────────────────────────────────────────

    def fetch_jobs(self, status_filter: str) -> list[dict]:
        if status_filter == "open":
            return [
                dict(r) for r in self._conn.execute(
                    "SELECT * FROM jobs WHERE status NOT IN ('completed', 'cancelled') ORDER BY created_at DESC"
                ).fetchall()
            ]
        if status_filter == "completed":
            return [
                dict(r) for r in self._conn.execute(
                    "SELECT * FROM jobs WHERE status = 'completed' ORDER BY created_at DESC LIMIT 200"
                ).fetchall()
            ]
        if status_filter == "cancelled":
            return [
                dict(r) for r in self._conn.execute(
                    "SELECT * FROM jobs WHERE status = 'cancelled' ORDER BY created_at DESC LIMIT 200"
                ).fetchall()
            ]
        return [
            dict(r) for r in self._conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT 500"
            ).fetchall()
        ]

    def batch_task_counts(self, job_ids: list[str]) -> dict[str, dict[str, int]]:
        if not job_ids:
            return {}
        placeholders = ",".join(["%s"] * len(job_ids))
        rows = self._conn.execute(
            f"SELECT job_id, status, COUNT(*) as cnt FROM tasks "
            f"WHERE job_id IN ({placeholders}) GROUP BY job_id, status",
            job_ids,
        ).fetchall()
        result: dict[str, dict[str, int]] = {}
        for row in rows:
            result.setdefault(row["job_id"], {})[row["status"]] = row["cnt"]

        human_rows = self._conn.execute(
            f"SELECT job_id, COUNT(*) as cnt FROM tasks "
            f"WHERE job_id IN ({placeholders}) AND status = 'awaiting_approval' AND human_task = 1 "
            f"GROUP BY job_id",
            job_ids,
        ).fetchall()
        for row in human_rows:
            result.setdefault(row["job_id"], {})["human_waiting"] = row["cnt"]

        return result

    def get_job(self, job_id: str) -> dict | None:
        return store.get_job(self._conn, job_id)

    def get_task(self, task_id: str) -> dict | None:
        return store.get_task(self._conn, task_id)

    def get_tasks_for_job(self, job_id: str) -> list[dict]:
        return store.get_tasks_for_job(self._conn, job_id)

    def get_task_events(self, task_id: str) -> list[dict]:
        return store.get_task_events(self._conn, task_id)

    def get_blocked_task_ids(self, task_ids: list[str]) -> set[str]:
        if not task_ids:
            return set()
        placeholders = ",".join(["%s"] * len(task_ids))
        rows = self._conn.execute(
            f"SELECT d.task_id FROM dependencies d "
            f"JOIN tasks dep ON dep.id = d.depends_on_task_id "
            f"WHERE d.task_id IN ({placeholders}) AND dep.status != 'completed'",
            task_ids,
        ).fetchall()
        return {r["task_id"] for r in rows}

    def get_dependency_edges(self, task_ids: list[str]) -> list[dict]:
        if not task_ids:
            return []
        placeholders = ",".join(["%s"] * len(task_ids))
        rows = self._conn.execute(
            f"SELECT task_id, depends_on_task_id FROM dependencies WHERE task_id IN ({placeholders})",
            task_ids,
        ).fetchall()
        return [dict(r) for r in rows]

    def fetch_human_actions(self) -> list[dict]:
        rows = self._conn.execute(
            """SELECT t.*, j.name AS job_name
               FROM tasks t
               JOIN jobs j ON t.job_id = j.id
               WHERE t.status = 'awaiting_approval'
                 AND j.status NOT IN ('cancelled', 'completed')
               ORDER BY t.priority DESC, t.created_at ASC"""
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Write ─────────────────────────────────────────────────────────────────

    def create_job(self, name: str, max_workers: int | None = None) -> dict:
        result = store.create_job(self._conn, name, max_workers)
        self._conn.commit()
        return result

    def edit_job(self, job_id: str, name: str) -> dict | None:
        result = store.edit_job(self._conn, job_id, name)
        self._conn.commit()
        return result

    def cancel_job(self, job_id: str) -> dict:
        result = store.cancel_job(self._conn, job_id)
        self._conn.commit()
        return result

    def pause_job(self, job_id: str, reason: str | None = None) -> None:
        store.pause_job(self._conn, job_id, reason=reason)
        self._conn.commit()

    def resume_job(self, job_id: str) -> None:
        store.resume_job(self._conn, job_id)
        self._conn.commit()

    def add_tasks(self, tasks: list[dict], job_id: str | None = None) -> dict:
        result = store.add_tasks(self._conn, job_id, tasks)
        self._conn.commit()
        return result

    def cancel_task(self, task_id: str) -> None:
        store.cancel_task(self._conn, task_id)
        self._conn.commit()

    def edit_task(self, task_id: str, updates: dict) -> None:
        store.edit_task(self._conn, task_id, updates)
        self._conn.commit()

    def approve_task(self, task_id: str) -> None:
        store.approve_task(self._conn, task_id, approver_id="human")
        self._conn.commit()

    def reject_task(self, task_id: str, reason: str) -> None:
        store.reject_task(self._conn, task_id, approver_id="human", reason=reason)
        self._conn.commit()

    def return_task(self, task_id: str, feedback: str) -> None:
        store.return_task(self._conn, task_id, approver_id="human", feedback=feedback)
        self._conn.commit()

    def add_task_comment(self, task_id: str, content: str) -> None:
        store.add_task_comment(self._conn, task_id, "human", "human", content)
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()


class RemoteNaxeClient(NaxeClient):
    """HTTP client talking to a naxe server's REST API.

    Reads from GET /api/v1/jobs and POST /api/v1/call/{tool_name}.
    Writes all go through POST /api/v1/call/{tool_name}.
    """

    def __init__(self, base_url: str, api_key: str | None = None) -> None:
        import httpx
        headers: dict[str, str] = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._http = httpx.Client(base_url=base_url, headers=headers)
        self._task_counts_cache: dict[str, dict[str, int]] = {}
        self._tasks_cache: dict[str, list[dict]] = {}  # job_id -> tasks
        self._deps_cache: dict[str, dict] = {}  # job_id -> {edges, blocked_ids}

    def _call(self, tool_name: str, **kwargs) -> dict:
        resp = self._http.post(f"/api/v1/call/{tool_name}", json=kwargs)
        resp.raise_for_status()
        return resp.json()

    def _get_jobs(self, status_filter: str) -> dict:
        resp = self._http.get("/api/v1/jobs", params={"filter": status_filter})
        resp.raise_for_status()
        return resp.json()

    # ── Read ──────────────────────────────────────────────────────────────────

    def fetch_jobs(self, status_filter: str) -> list[dict]:
        data = self._get_jobs(status_filter)
        self._task_counts_cache = data.get("task_counts", {})
        return data.get("jobs", [])

    def batch_task_counts(self, job_ids: list[str]) -> dict[str, dict[str, int]]:
        return {jid: self._task_counts_cache.get(jid, {}) for jid in job_ids}

    def get_job(self, job_id: str) -> dict | None:
        data = self._call("get_job_status", job_id=job_id)
        self._tasks_cache[job_id] = data.get("tasks", [])
        return data.get("job")

    def get_task(self, task_id: str) -> dict | None:
        for tasks in self._tasks_cache.values():
            for t in tasks:
                if t["id"] == task_id:
                    return t
        return None

    def get_tasks_for_job(self, job_id: str) -> list[dict]:
        if job_id in self._tasks_cache:
            return self._tasks_cache[job_id]
        data = self._call("get_job_status", job_id=job_id)
        self._tasks_cache[job_id] = data.get("tasks", [])
        return self._tasks_cache[job_id]

    def get_task_events(self, task_id: str) -> list[dict]:
        data = self._call("get_task_events", task_id=task_id)
        return data.get("events", [])

    def _get_deps(self, task_ids: list[str]) -> dict:
        """Fetch and cache dep edges + blocked_ids for the job containing task_ids."""
        task_id_set = set(task_ids)
        job_id = None
        for jid, tasks in self._tasks_cache.items():
            if any(t["id"] in task_id_set for t in tasks):
                job_id = jid
                break
        if not job_id:
            return {"edges": [], "blocked_ids": []}
        if job_id not in self._deps_cache:
            resp = self._http.get(f"/api/v1/jobs/{job_id}/deps")
            resp.raise_for_status()
            self._deps_cache[job_id] = resp.json()
        return self._deps_cache[job_id]

    def get_blocked_task_ids(self, task_ids: list[str]) -> set[str]:
        task_id_set = set(task_ids)
        data = self._get_deps(task_ids)
        return {tid for tid in data.get("blocked_ids", []) if tid in task_id_set}

    def get_dependency_edges(self, task_ids: list[str]) -> list[dict]:
        task_id_set = set(task_ids)
        data = self._get_deps(task_ids)
        return [e for e in data.get("edges", []) if e["task_id"] in task_id_set]

    def fetch_human_actions(self) -> list[dict]:
        data = self._get_jobs("open")
        jobs = data.get("jobs", [])
        task_counts = data.get("task_counts", {})
        self._task_counts_cache = task_counts

        result: list[dict] = []
        for job in jobs:
            counts = task_counts.get(job["id"], {})
            if not counts.get("awaiting_approval", 0):
                continue
            job_data = self._call("get_job_status", job_id=job["id"])
            self._tasks_cache[job["id"]] = job_data.get("tasks", [])
            for t in job_data.get("tasks", []):
                if t.get("status") == "awaiting_approval":
                    result.append({**t, "job_name": job["name"]})

        result.sort(key=lambda t: (-int(t.get("priority") or 0), t.get("created_at") or ""))
        return result

    # ── Write ─────────────────────────────────────────────────────────────────

    def create_job(self, name: str, max_workers: int | None = None) -> dict:
        kwargs: dict = {"name": name}
        if max_workers is not None:
            kwargs["max_workers"] = max_workers
        return self._call("create_job", **kwargs).get("job", {})

    def edit_job(self, job_id: str, name: str) -> dict | None:
        return self._call("edit_job", job_id=job_id, name=name).get("job")

    def cancel_job(self, job_id: str) -> dict:
        return self._call("cancel_job", job_id=job_id)

    def pause_job(self, job_id: str, reason: str | None = None) -> None:
        kwargs: dict = {"job_id": job_id}
        if reason:
            kwargs["reason"] = reason
        self._call("pause_job", **kwargs)

    def resume_job(self, job_id: str) -> None:
        self._call("resume_job", job_id=job_id)

    def add_tasks(self, tasks: list[dict], job_id: str | None = None) -> dict:
        kwargs: dict = {"tasks": tasks}
        if job_id is not None:
            kwargs["job_id"] = job_id
        return self._call("add_tasks", **kwargs)

    def cancel_task(self, task_id: str) -> None:
        self._call("cancel_task", task_id=task_id)

    def edit_task(self, task_id: str, updates: dict) -> None:
        self._call("edit_task", task_id=task_id, **updates)

    def approve_task(self, task_id: str) -> None:
        self._call("approve_task", task_id=task_id, approver_id="human")

    def reject_task(self, task_id: str, reason: str) -> None:
        self._call("reject_task", task_id=task_id, approver_id="human", reason=reason)

    def return_task(self, task_id: str, feedback: str) -> None:
        self._call("return_task", task_id=task_id, approver_id="human", feedback=feedback)

    def add_task_comment(self, task_id: str, content: str) -> None:
        self._call("add_task_comment", task_id=task_id, author_id="human", author_type="human", content=content)

    def rollback(self) -> None:
        self._tasks_cache.clear()
        self._deps_cache.clear()
