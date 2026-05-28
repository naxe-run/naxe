import json
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from naxe.schema import get_connection
from naxe import store, resolver
from naxe.config import resolve_db_url

DB_URL = resolve_db_url()

_shared_conn = None


def _conn():
    global _shared_conn
    if _shared_conn is None or getattr(_shared_conn, "closed", False):
        _shared_conn = get_connection(DB_URL)
    return _shared_conn


_INSTRUCTIONS = """
Naxe is the task tracking and dependency management system for this session.

Rules:
- ALWAYS use naxe tools for task tracking. NEVER use internal todo/task tools.
- Begin every multi-step task with create_job, then add_tasks with full dependency graph.
- Call complete_task immediately when a task finishes — this automatically surfaces newly unblocked tasks.
- Use get_job_status to check overall progress at any point.
- Naxe is the single source of truth. Internal task management is disabled for this project.

Choosing between get_next_actions and claim_next_action:
- Orchestrator agent (assigns work to others): use get_next_actions to see all unblocked tasks, then claim_task on the ones you assign.
- Worker agent (executes one task at a time): use claim_next_action — it atomically finds and claims one task. If it returns null, check get_job_status: if tasks are still pending or in_progress, wait briefly and try again. Stop when no pending or in_progress tasks remain.
"""

app = Server("naxe", instructions=_INSTRUCTIONS)


def _ok(**kwargs) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(kwargs, default=str))]


def _err(msg: str) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps({"error": msg}))]


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="create_job",
            description=(
                "REQUIRED: Call this at the start of any multi-step task to create a job in Naxe. "
                "Returns a job_id used in all subsequent calls. Do not use internal task tracking — "
                "use this instead."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Human-readable job name"},
                    "max_workers": {"type": "integer", "description": "Max number of agents that can work this job concurrently (omit for unlimited)"},
                },
                "required": ["name"],
            },
        ),
        Tool(
            name="add_tasks",
            description=(
                "Add tasks with explicit dependencies to a job. Define the full dependency graph up "
                "front using depends_on. Naxe will enforce execution order — you do not need to "
                "track this yourself. Rejects cycles and unknown dependency IDs."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                    "tasks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string", "description": "Stable caller-defined ID (e.g. 't1')"},
                                "name": {"type": "string"},
                                "description": {"type": "string", "description": "Detail to guide execution"},
                                "duration_minutes": {"type": "integer", "description": "Tasks ≤5 min are flagged as quick wins"},
                                "depends_on": {"type": "array", "items": {"type": "string"}, "description": "IDs of tasks that must complete first"},
                                "max_retries": {"type": "integer", "description": "Number of times to retry this task on failure (default 0)"},
                                "input": {"type": "string", "description": "Structured input data for the task (passed through to task record)"},
                                "resources": {"type": "array", "items": {"type": "string"}, "description": "Resource names (e.g. file paths) this task exclusively holds. claim_next_action skips tasks whose resources conflict with an in-progress task."},
                                "priority": {"type": "integer", "description": "Task priority 0–100 (default 50). Higher values are claimed first by claim_next_action."},
                            },
                            "required": ["name"],
                        },
                    },
                },
                "required": ["job_id", "tasks"],
            },
        ),
        Tool(
            name="get_next_actions",
            description=(
                "REQUIRED: Call this to find out what to work on next. Returns only tasks that are "
                "fully unblocked right now — all their dependencies are complete. Always use this "
                "instead of deciding task order yourself. Includes is_quick_win flag and "
                "unblocked_by context showing what just became available."
            ),
            inputSchema={
                "type": "object",
                "properties": {"job_id": {"type": "string"}},
                "required": ["job_id"],
            },
        ),
        Tool(
            name="claim_task",
            description=(
                "Claim a task before starting work on it. Atomic — prevents two agents from "
                "double-claiming the same task. Returns the task details. Call this before "
                "executing any task returned by get_next_actions."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "agent_id": {"type": "string", "description": "Identifier for this agent instance"},
                },
                "required": ["task_id", "agent_id"],
            },
        ),
        Tool(
            name="complete_task",
            description=(
                "REQUIRED: Call this immediately when a task is finished. Marks it complete and "
                "returns newly_unblocked — the tasks that just became available. Pass output to "
                "record findings or results; downstream tasks can read it via get_job_status. "
                "Always call this before moving on; do not skip it or track completion internally."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "agent_id": {"type": "string"},
                    "output": {"type": "string", "description": "Result or findings from this task, readable by downstream tasks via get_job_status"},
                },
                "required": ["task_id", "agent_id"],
            },
        ),
        Tool(
            name="fail_task",
            description=(
                "Mark a task as failed with an optional reason. Use this instead of silently "
                "abandoning a task so the job status remains accurate."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "agent_id": {"type": "string"},
                    "reason": {"type": "string"},
                    "output": {"type": "string", "description": "Optional partial output or diagnostics captured before failure"},
                },
                "required": ["task_id", "agent_id"],
            },
        ),
        Tool(
            name="get_job_status",
            description=(
                "Full snapshot of a job: all tasks with statuses, ownership, and progress counters "
                "(total/completed/in_progress/pending/failed). Task records include both input "
                "(structured task data) and output (result written by the agent). Use to check "
                "overall progress or audit what has and hasn't been done."
            ),
            inputSchema={
                "type": "object",
                "properties": {"job_id": {"type": "string"}},
                "required": ["job_id"],
            },
        ),
        Tool(
            name="cancel_task",
            description=(
                "Cancel a single pending or in_progress task. Has no effect on tasks that "
                "are already completed, failed, or cancelled. Returns the updated task."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                },
                "required": ["task_id"],
            },
        ),
        Tool(
            name="cancel_job",
            description=(
                "Cancel a job and atomically cancel all its pending and in_progress tasks. "
                "Completed and failed tasks are left untouched. Workers will not be able to "
                "claim tasks from a cancelled job. Returns the updated job and tasks_cancelled count."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                },
                "required": ["job_id"],
            },
        ),
        Tool(
            name="claim_next_action",
            description=(
                "Worker tool: atomically find and claim the next available unblocked task "
                "in one operation. Use this instead of get_next_actions + claim_task when "
                "running as a worker agent — it prevents multiple agents from racing to "
                "claim the same task. Returns the claimed task, or null if nothing is "
                "currently unblocked. Call complete_task when done. For long-running tasks, "
                "call heartbeat_task periodically so the task is not reclaimed as stale."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                    "agent_id": {"type": "string", "description": "Identifier for this agent instance"},
                },
                "required": ["job_id", "agent_id"],
            },
        ),
        Tool(
            name="heartbeat_task",
            description=(
                "Send a heartbeat for a task you own to prevent it from being reclaimed as stale. "
                "Call this periodically while working on long-running tasks. Returns the updated task, "
                "or null if you are not the owner."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "agent_id": {"type": "string"},
                },
                "required": ["task_id", "agent_id"],
            },
        ),
        Tool(
            name="update_task_progress",
            description=(
                "Report progress on a task you own (0–100). Use this for long-running tasks "
                "to communicate how far along you are. Only the agent that claimed the task "
                "may update its progress. Returns the updated task."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "agent_id": {"type": "string"},
                    "progress_percent": {"type": "integer", "description": "Progress from 0 to 100"},
                },
                "required": ["task_id", "agent_id", "progress_percent"],
            },
        ),
        Tool(
            name="list_jobs",
            description="List all jobs with per-job progress summaries. Supports pagination via limit and offset.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Max jobs to return (default 50)"},
                    "offset": {"type": "integer", "description": "Number of jobs to skip (default 0)"},
                },
            },
        ),
        Tool(
            name="edit_job",
            description="Rename a job. Has no effect on tasks or status.",
            inputSchema={
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                    "name": {"type": "string"},
                },
                "required": ["job_id", "name"],
            },
        ),
        Tool(
            name="set_job_concurrency",
            description=(
                "Set or clear the maximum number of agents that can work a job concurrently. "
                "Pass max_workers=null to remove the limit. Applies on the next claim_next_action call."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                    "max_workers": {"type": ["integer", "null"], "description": "Max concurrent agents, or null for unlimited"},
                },
                "required": ["job_id", "max_workers"],
            },
        ),
        Tool(
            name="edit_task",
            description=(
                "Edit metadata or dependencies of a pending task. Only pending tasks may be edited. "
                "All fields are optional — only provided fields are changed. "
                "If depends_on is provided it replaces all existing dependencies; a cycle check is enforced. "
                "If resources is provided it replaces the existing resource list."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "resources": {"type": "array", "items": {"type": "string"}, "description": "Replaces existing resource list"},
                    "depends_on": {"type": "array", "items": {"type": "string"}, "description": "Replaces all existing dependencies (cycle-checked)"},
                    "duration_minutes": {"type": "integer"},
                    "max_retries": {"type": "integer"},
                    "input": {"type": "string"},
                },
                "required": ["task_id"],
            },
        ),
        Tool(
            name="add_job_dependency",
            description=(
                "Declare that one job must complete before another can start. "
                "The dependent job will have status 'blocked' until all its job-level "
                "dependencies reach status 'completed'. Rejects cycles and unknown job IDs."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "description": "The job that depends on another"},
                    "depends_on_job_id": {"type": "string", "description": "The job that must complete first"},
                },
                "required": ["job_id", "depends_on_job_id"],
            },
        ),
        Tool(
            name="create_job_template",
            description=(
                "Save a reusable task graph as a named template. "
                "Provide the same tasks array you would pass to add_tasks — "
                "dependencies are validated and cycle-checked at creation time. "
                "Use instantiate_template to create a new job from the template."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "tasks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "name": {"type": "string"},
                                "description": {"type": "string"},
                                "duration_minutes": {"type": "integer"},
                                "depends_on": {"type": "array", "items": {"type": "string"}},
                                "max_retries": {"type": "integer"},
                                "input": {"type": "string"},
                                "resources": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["name"],
                        },
                    },
                },
                "required": ["name", "tasks"],
            },
        ),
        Tool(
            name="list_templates",
            description="List all saved job templates ordered by name.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="instantiate_template",
            description=(
                "Create a new job from a saved template. "
                "Returns the new job and the list of task IDs created."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "template_id": {"type": "string"},
                    "name": {"type": "string", "description": "Name for the new job"},
                },
                "required": ["template_id", "name"],
            },
        ),
        Tool(
            name="get_task_events",
            description="Return the full event history for a single task (claimed, completed, failed, retried, etc.) in chronological order.",
            inputSchema={
                "type": "object",
                "properties": {"task_id": {"type": "string"}},
                "required": ["task_id"],
            },
        ),
        Tool(
            name="get_job_audit_trail",
            description="Return all events across every task in a job, in chronological order. Useful for auditing agent activity across a full job run.",
            inputSchema={
                "type": "object",
                "properties": {"job_id": {"type": "string"}},
                "required": ["job_id"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    conn = _conn()
    try:
        result = await _handle_tool(name, arguments, conn)
        conn.commit()
        return result
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        return _err(f"Internal error: {e}")


async def _handle_tool(name: str, arguments: dict[str, Any], conn) -> list[TextContent]:
    if name == "create_job":
        job = store.create_job(conn, arguments["name"], arguments.get("max_workers"))
        return _ok(job_id=job["id"], job=job)

    if name == "add_tasks":
        job_id = arguments["job_id"]
        tasks = arguments["tasks"]

        # Assign resolved IDs before cycle check
        for t in tasks:
            if not t.get("id"):
                import uuid
                t["id"] = str(uuid.uuid4())
            t["_resolved_id"] = t["id"]

        if resolver.detect_cycle(tasks):
            return _err("Dependency cycle detected in task batch — no tasks were added.")

        try:
            task_ids = store.add_tasks(conn, job_id, tasks)
        except ValueError as e:
            return _err(str(e))

        return _ok(added=len(task_ids), task_ids=task_ids)

    if name == "get_next_actions":
        actions = resolver.get_next_actions(conn, arguments["job_id"])
        return _ok(next_actions=actions)

    if name == "claim_task":
        task_id = arguments["task_id"]
        agent_id = arguments["agent_id"]
        success = store.claim_task(conn, task_id, agent_id)
        task = store.get_task(conn, task_id) if success else None
        return _ok(success=success, task=task)

    if name == "complete_task":
        task_id = arguments["task_id"]
        agent_id = arguments["agent_id"]
        task = store.get_task(conn, task_id)
        if not task:
            return _err(f"Task '{task_id}' not found")
        warning = None
        if task.get("owner_agent_id") and task["owner_agent_id"] != agent_id:
            warning = f"Task owned by '{task['owner_agent_id']}', completing anyway (orchestrator override)"
        updated = store.update_task_status(conn, task_id, "completed", agent_id, arguments.get("output"))
        newly_unblocked = resolver.get_newly_unblocked(conn, task["job_id"], task_id)
        result: dict[str, Any] = {"success": True, "task": updated, "newly_unblocked": newly_unblocked}
        if updated and "_newly_unblocked_jobs" in updated:
            result["newly_unblocked_jobs"] = updated.pop("_newly_unblocked_jobs")
        if warning:
            result["warning"] = warning
        return _ok(**result)

    if name == "fail_task":
        task_id = arguments["task_id"]
        agent_id = arguments["agent_id"]
        task = store.get_task(conn, task_id)
        if not task:
            return _err(f"Task '{task_id}' not found")
        warning = None
        if task.get("owner_agent_id") and task["owner_agent_id"] != agent_id:
            warning = f"Task owned by '{task['owner_agent_id']}', failing anyway (orchestrator override)"
        store.update_task_status(conn, task_id, "failed", agent_id, arguments.get("output"))
        retried_task = store.retry_task(conn, task_id)
        result = {"success": True}
        if retried_task:
            result["retried"] = True
            result["task"] = retried_task
        if warning:
            result["warning"] = warning
        if arguments.get("reason"):
            result["reason"] = arguments["reason"]
        return _ok(**result)

    if name == "get_job_status":
        job_id = arguments["job_id"]
        job = store.get_job(conn, job_id)
        if not job:
            return _err(f"Job '{job_id}' not found")
        tasks = store.get_tasks_for_job(conn, job_id)
        progress = {
            "total": len(tasks),
            "completed": sum(1 for t in tasks if t["status"] == "completed"),
            "in_progress": sum(1 for t in tasks if t["status"] == "in_progress"),
            "pending": sum(1 for t in tasks if t["status"] == "pending"),
            "failed": sum(1 for t in tasks if t["status"] == "failed"),
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
            if r["status"] != "completed"
        ]
        active_workers = store.count_active_workers(conn, job_id)
        return _ok(job=job, tasks=tasks, progress=progress, blocking_jobs=blocking_jobs, active_workers=active_workers)

    if name == "cancel_task":
        task = store.cancel_task(conn, arguments["task_id"])
        if task is None:
            return _err(f"Task '{arguments['task_id']}' not found or already in a terminal state")
        return _ok(success=True, task=task)

    if name == "cancel_job":
        job_id = arguments["job_id"]
        if not store.get_job(conn, job_id):
            return _err(f"Job '{job_id}' not found")
        result = store.cancel_job(conn, job_id)
        return _ok(success=True, **result)

    if name == "claim_next_action":
        task = store.claim_next_action(conn, arguments["job_id"], arguments["agent_id"])
        return _ok(task=task)

    if name == "heartbeat_task":
        task = store.heartbeat_task(conn, arguments["task_id"], arguments["agent_id"])
        return _ok(task=task)

    if name == "update_task_progress":
        result = store.update_task_progress(
            conn, arguments["task_id"], arguments["agent_id"], arguments["progress_percent"]
        )
        if result and "error" in result:
            return _err(result["error"])
        return _ok(task=result)

    if name == "list_jobs":
        limit = arguments.get("limit", 50)
        offset = arguments.get("offset", 0)
        page = store.list_jobs(conn, limit=limit, offset=offset)
        result = []
        for job in page["jobs"]:
            tasks = store.get_tasks_for_job(conn, job["id"])
            result.append({
                **job,
                "progress": {
                    "total": len(tasks),
                    "completed": sum(1 for t in tasks if t["status"] == "completed"),
                    "in_progress": sum(1 for t in tasks if t["status"] == "in_progress"),
                    "pending": sum(1 for t in tasks if t["status"] == "pending"),
                    "failed": sum(1 for t in tasks if t["status"] == "failed"),
                },
            })
        return _ok(jobs=result, total=page["total"], has_more=page["has_more"])

    if name == "edit_job":
        updated = store.edit_job(conn, arguments["job_id"], arguments["name"])
        if updated is None:
            return _err(f"Job '{arguments['job_id']}' not found")
        return _ok(success=True, job=updated)

    if name == "set_job_concurrency":
        updated = store.set_job_concurrency(conn, arguments["job_id"], arguments.get("max_workers"))
        if updated is None:
            return _err(f"Job '{arguments['job_id']}' not found")
        return _ok(success=True, job=updated)

    if name == "edit_task":
        task_id = arguments["task_id"]
        updates = {k: v for k, v in arguments.items() if k != "task_id"}
        if not updates:
            return _err("No fields to update")
        try:
            updated = store.edit_task(conn, task_id, updates)
        except ValueError as e:
            return _err(str(e))
        if updated is None:
            task = store.get_task(conn, task_id)
            if task is None:
                return _err(f"Task '{task_id}' not found")
            return _err(f"Task '{task_id}' is {task['status']} — only pending tasks can be edited")
        return _ok(success=True, task=updated)

    if name == "add_job_dependency":
        try:
            store.add_job_dependency(conn, arguments["job_id"], arguments["depends_on_job_id"])
        except ValueError as e:
            return _err(str(e))
        return _ok(success=True, job_id=arguments["job_id"], depends_on_job_id=arguments["depends_on_job_id"])

    if name == "create_job_template":
        try:
            template = store.create_template(
                conn, arguments["name"], arguments.get("description"), arguments["tasks"]
            )
        except ValueError as e:
            return _err(str(e))
        return _ok(template=template)

    if name == "list_templates":
        templates = store.list_templates(conn)
        return _ok(templates=templates)

    if name == "instantiate_template":
        try:
            job = store.instantiate_template(conn, arguments["template_id"], arguments["name"])
        except ValueError as e:
            return _err(str(e))
        tasks = store.get_tasks_for_job(conn, job["id"])
        return _ok(job=job, task_ids=[t["id"] for t in tasks])

    if name == "get_task_events":
        task_id = arguments["task_id"]
        if not store.get_task(conn, task_id):
            return _err(f"Task '{task_id}' not found")
        events = store.get_task_events(conn, task_id)
        return _ok(task_id=task_id, events=events)

    if name == "get_job_audit_trail":
        job_id = arguments["job_id"]
        if not store.get_job(conn, job_id):
            return _err(f"Job '{job_id}' not found")
        events = store.get_job_events(conn, job_id)
        return _ok(job_id=job_id, events=events)

    return _err(f"Unknown tool: {name}")


async def _run():
    async with stdio_server() as streams:
        await app.run(streams[0], streams[1], app.create_initialization_options())


def main():
    import asyncio
    asyncio.run(_run())


if __name__ == "__main__":
    main()
