# Task Management

> **Note for human contributors:** This file contains instructions for AI agents working in this repository. It is automatically read by Claude Code and similar tools. You can ignore it — see the README for setup and contribution guidance.

This project uses **Naxe** for all task tracking and dependency management via MCP.

## Context

Naxe supports server-level contexts (workspaces) to isolate jobs — e.g. `home` vs `work`. Context is a server configuration, not a tool parameter. You do not need to set or read it. Jobs you create will automatically be stamped with the active context, and you will only see jobs in that context.

## Rules — follow these exactly

- ALWAYS use `create_job` at the start of any multi-step task.
- ALWAYS use `add_tasks` to define the full dependency graph before starting work.
- ALWAYS use `get_next_actions` to determine what to work on — do not decide execution order yourself.
- ALWAYS call `claim_task` before starting a task.
- ALWAYS call `complete_task` immediately when a task finishes.
- NEVER use internal todo or task tools (TodoWrite, TodoRead, or equivalent). Naxe is the single source of truth.

## Why

Naxe enforces dependency ordering, prevents double-claiming in multi-agent scenarios, and keeps a persistent audit trail. Internal task tools don't provide any of this and will diverge from Naxe's state.

## Typical flow

```
create_job("name")
  → add_tasks(job_id, [...tasks with depends_on...])
  → get_next_actions(job_id)         # only returns unblocked tasks
  → claim_task(task_id, agent_id)
  → ... do the work ...
  → complete_task(task_id, agent_id) # returns newly_unblocked
  → get_next_actions(job_id)         # repeat
```

## Approvals and the feedback loop

Some tasks require human approval before they are considered complete. You do not need to handle this yourself — just call `complete_task` as normal. If the task has `requires_approval` set, it will automatically route to `awaiting_approval` instead of completing. The response will include `routed_to_approval: true` so you know what happened.

A human reviewer then takes one of three actions:

| Reviewer action | Tool | What happens |
|---|---|---|
| Approve | `approve_task` | Task moves to `completed`. |
| Hard reject | `reject_task` | Task is marked `failed`. Auto-retry triggers if `max_retries` is configured; otherwise the task is permanently failed. Use this when the work should **not** be retried through feedback. |
| Return for revision | `return_task` | Task loops back to `pending`. `approval_round` is incremented and the reviewer's feedback is stored as a comment. Use this when the agent should revise and resubmit. |

### Distinction between `reject_task` and `return_task`

- **`reject_task`** — hard fail. The task is marked `failed` and will not re-enter the approval queue through this mechanism. If `max_retries` is configured the task may be retried from scratch, but no structured feedback is passed to the agent.
- **`return_task`** — deny and retry. The task is sent back to `pending` so the agent can revise its work. `approval_round` is incremented each time, and the reviewer's feedback comment is attached to the task record.

### Agent guidance for re-claimed tasks

When a task re-enters `pending` after a `return_task`, the task record includes a `recent_comments` field populated with the human's feedback from the most recent approval round. Before starting work on any re-claimed task, agents must:

1. Check `recent_comments` on the claimed task record.
2. Incorporate the requested changes before calling `complete_task` again.
3. If the full history across multiple rounds is needed, call `get_task_comments(task_id)` to retrieve all comments in order.

### Approval flow

```
claim_task(task_id, agent_id)
  → ... do the work ...
  → complete_task(task_id, agent_id)   # auto-routes to awaiting_approval if required
  # human reviews and calls approve_task / reject_task / return_task
  # if return_task: task goes back to pending with feedback in recent_comments
  → claim_task(task_id, agent_id)      # re-claim after return
  → ... revise the work based on recent_comments ...
  → complete_task(task_id, agent_id)   # resubmit
```
