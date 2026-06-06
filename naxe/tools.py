from mcp.types import Tool


def list_all_tools() -> list[Tool]:
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
                    "worktree": {"type": "boolean", "description": "Set to true if this job runs in an isolated git worktree. When true, resource conflict checks are scoped to this job only. When false (default), resource conflicts are checked across all non-worktree jobs."},
                },
                "required": ["name"],
            },
        ),
        Tool(
            name="add_tasks",
            description=(
                "Add tasks with explicit dependencies to a job. Define the full dependency graph up "
                "front using depends_on. Naxe will enforce execution order — you do not need to "
                "track this yourself. Rejects cycles and unknown dependency IDs. "
                "job_id is optional — if omitted, a new job is auto-created using the first task's name."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "description": "Full job UUID or a unique ID prefix (e.g. first 8 chars). Optional — if omitted, a new job is created automatically."},
                    "tasks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string", "description": "Stable caller-defined ID (e.g. 't1')"},
                                "name": {"type": "string"},
                                "description": {"type": "string", "description": "Detail to guide execution"},
                                "duration_minutes": {"type": "integer"},
                                "depends_on": {"type": "array", "items": {"type": "string"}, "description": "IDs of tasks that must complete first"},
                                "max_retries": {"type": "integer", "description": "Number of times to retry this task on failure (default 0)"},
                                "input": {"type": "string", "description": "Structured input data for the task (passed through to task record)"},
                                "resources": {"type": "array", "items": {"type": "string"}, "description": "Resource names (e.g. file paths) this task exclusively holds. claim_next_action skips tasks whose resources conflict with an in-progress task."},
                                "repo": {"type": "string", "description": "Repository identifier this task is scoped to (e.g. 'org/repo'). Tasks without a repo can be claimed by any agent. Tasks with a repo can only be claimed by agents that specify that repo (or agents without a repo filter)."},
                                "priority": {"type": "integer", "description": "Task priority 0–100 (default 50). Higher values are claimed first by claim_next_action."},
                                "requires_approval": {"type": "boolean", "description": "Set to true if this task must go through the approval flow (request_approval → approve_task) before it can be completed. An agent that tries to call complete_task directly on a requires_approval task will receive an error and must call request_approval first. Use this for tasks involving irreversible actions, external communication, financial operations, or anything requiring human sign-off."},
                                "human_task": {"type": "boolean", "description": "Set to true for tasks performed entirely by a human, not an agent. Human tasks are never claimable by agents. When all dependencies complete, the task auto-transitions to awaiting_approval. A human confirms completion via approve_task or rejects via reject_task. Use requires_approval for agent-executed tasks that need human sign-off; use human_task for tasks the agent will not perform at all."},
                                "start_date": {"type": "string", "description": "ISO 8601 datetime before which this task is invisible to agents — not surfaced by get_next_actions or claim_next_action."},
                                "due_date": {"type": "string", "description": "ISO 8601 deadline for this task. Stored and surfaced in task records; no automatic enforcement."},
                                "recurrence_interval_days": {"type": "integer", "description": "If set, completing this task automatically spawns a new job containing a copy of this task, with start_date offset by this many days into the future."},
                                "critical": {"type": "boolean", "description": "If true, this task sorts above all non-critical tasks regardless of priority. Sort order: critical DESC, priority DESC, created_at ASC."},
                            },
                            "required": ["name"],
                        },
                    },
                },
                "required": ["tasks"],
            },
        ),
        Tool(
            name="get_next_actions",
            description=(
                "REQUIRED: Call this to find out what to work on next. Returns only tasks that are "
                "fully unblocked right now — all their dependencies are complete. Always use this "
                "instead of deciding task order yourself. Includes "
                "unblocked_by context showing what just became available."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                    "repo": {"type": "string", "description": "If provided, only return tasks scoped to this repo or tasks with no repo set."},
                },
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
                "Always call this before moving on; do not skip it or track completion internally. "
                "NOTE: Tasks with human_task=true can never be completed by agents — they must be "
                "resolved via approve_task or reject_task."
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
                "overall progress or audit what has and hasn't been done. "
                "job_id accepts a full UUID or a unique prefix (e.g. the first 8 chars of the ID)."
            ),
            inputSchema={
                "type": "object",
                "properties": {"job_id": {"type": "string", "description": "Full job UUID or a unique ID prefix (e.g. first 8 chars)"}},
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
                "properties": {"task_id": {"type": "string"}},
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
                "properties": {"job_id": {"type": "string"}},
                "required": ["job_id"],
            },
        ),
        Tool(
            name="pause_job",
            description=(
                "Pause a job so that no new tasks can be claimed by workers. In-progress tasks "
                "continue until they complete or fail. Use resume_job to allow claiming again. "
                "Returns the updated job."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                    "reason": {"type": "string", "description": "Optional reason for pausing this job. Stored on the job and shown in the TUI."},
                },
                "required": ["job_id"],
            },
        ),
        Tool(
            name="resume_job",
            description="Resume a previously paused job, allowing workers to claim tasks again. Returns the updated job.",
            inputSchema={
                "type": "object",
                "properties": {"job_id": {"type": "string"}},
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
                    "repo": {"type": "string", "description": "If provided, only claim tasks scoped to this repo or tasks with no repo set."},
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
            description="List all jobs with per-job progress summaries. Supports pagination and prefix filtering. Pass id_prefix (e.g. the first 8 chars of a job ID) to narrow results to matching jobs.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Max jobs to return (default 50)"},
                    "offset": {"type": "integer", "description": "Number of jobs to skip (default 0)"},
                    "id_prefix": {"type": "string", "description": "Filter jobs whose ID starts with this prefix (e.g. first 8 chars of a short ID)"},
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
            name="set_worktree_paths",
            description=(
                "Store the filesystem paths of git worktrees created for this job, keyed by repo "
                "identifier. Call after running `git worktree add` for each repo involved in the job. "
                "Subagents read paths via get_job_status to know which directory to work in. "
                "Pass an empty object to clear all paths."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                    "paths": {
                        "type": "object",
                        "description": 'Map of repo identifier to absolute worktree path. Example: {"org/frontend": "/worktrees/job-abc/frontend"}',
                    },
                },
                "required": ["job_id", "paths"],
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
                    "start_date": {"type": "string", "description": "ISO 8601 datetime before which this task is invisible to agents."},
                    "due_date": {"type": "string", "description": "ISO 8601 deadline for this task."},
                    "recurrence_interval_days": {"type": "integer", "description": "If set, completing this task automatically spawns a recurring copy."},
                    "critical": {"type": "boolean", "description": "If true, this task sorts above all non-critical tasks regardless of priority."},
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
            description="Create a new job from a saved template. Returns the new job and the list of task IDs created.",
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
        Tool(
            name="get_blocked_tasks",
            description=(
                "Return all pending tasks in a job that are blocked by incomplete dependencies. "
                "Each entry includes the task id and name, plus a blocked_by list showing which "
                "dependencies are not yet completed (with their id, name, and current status). "
                "Tasks with no dependencies that are simply unstarted are NOT included."
            ),
            inputSchema={
                "type": "object",
                "properties": {"job_id": {"type": "string"}},
                "required": ["job_id"],
            },
        ),
        Tool(
            name="requeue_task",
            description=(
                "Reset a failed or cancelled task back to pending so it can be claimed and retried. "
                "Optionally provide new input data to override the task's existing input. "
                "Has no effect on tasks that are not in a failed or cancelled state."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "input": {"type": "string", "description": "Optional new input data to replace the task's existing input"},
                },
                "required": ["task_id"],
            },
        ),
        Tool(
            name="request_approval",
            description=(
                "Transition a task you own from in_progress to awaiting_approval. "
                "Downstream tasks that depend on this task will remain blocked until it is "
                "approved or rejected. Only the agent that claimed the task may request approval."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "agent_id": {"type": "string", "description": "Must match the agent that claimed the task"},
                    "notes": {"type": "string", "description": "Optional notes explaining what needs approval"},
                },
                "required": ["task_id", "agent_id"],
            },
        ),
        Tool(
            name="approve_task",
            description=(
                "Approve a task that is awaiting approval, marking it completed and unblocking "
                "downstream tasks. Returns the updated task and newly unblocked tasks."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "approver_id": {"type": "string", "description": "Identifier of the approver"},
                    "notes": {"type": "string", "description": "Optional approval notes"},
                },
                "required": ["task_id", "approver_id"],
            },
        ),
        Tool(
            name="reject_task",
            description=(
                "Hard-fail a task that is awaiting approval. "
                "Marks the task as failed, stores reason in approval_notes, and "
                "auto-retries via retry_task() if max_retries allows. "
                "Use return_task instead if you want to send the task back to the agent with feedback. "
                "Returns the updated task."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "approver_id": {"type": "string"},
                    "reason": {"type": "string", "description": "Reason for rejection (stored in approval_notes)"},
                },
                "required": ["task_id", "approver_id", "reason"],
            },
        ),
        Tool(
            name="return_task",
            description=(
                "Return a task that is awaiting approval back to pending with feedback for the agent. "
                "Stores feedback as a human comment, resets status to pending, increments approval_round, "
                "and clears owner_agent_id so the task can be re-claimed. "
                "Use reject_task instead if you want to hard-fail the task. "
                "Returns the updated task."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "approver_id": {"type": "string"},
                    "feedback": {"type": "string", "description": "Actionable feedback for the agent (stored as a comment)"},
                },
                "required": ["task_id", "approver_id", "feedback"],
            },
        ),
        Tool(
            name="add_task_comment",
            description=(
                "Add a comment to a task. Can be called by humans or agents at any point "
                "in the task lifecycle. Comments are threaded by approval_round and are "
                "automatically surfaced to the agent when it re-claims the task."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "author_id": {"type": "string", "description": "ID of the person or agent posting the comment"},
                    "author_type": {
                        "type": "string",
                        "enum": ["agent", "human"],
                        "description": "Whether the comment author is an agent or a human",
                    },
                    "content": {"type": "string", "description": "Comment text"},
                },
                "required": ["task_id", "author_id", "author_type", "content"],
            },
        ),
        Tool(
            name="get_task_comments",
            description=(
                "Retrieve the comment history for a task, ordered by creation time. "
                "Optionally filter to a specific approval_round. "
                "Comments include author_id, author_type, content, approval_round, and created_at."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "approval_round": {
                        "type": "integer",
                        "description": "If provided, only return comments from this approval round.",
                    },
                },
                "required": ["task_id"],
            },
        ),
    ]
