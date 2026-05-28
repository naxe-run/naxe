# Task Management

This project uses **Naxe** for all task tracking and dependency management via MCP.

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
