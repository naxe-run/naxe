# Design Discussion: Requeue Traceability

## Problem

`requeue_task` currently resets a failed or cancelled task back to `pending` — clearing ownership, resetting `retry_count` to 0, and optionally replacing `input`. It logs a single `requeued` event to `task_events`, but no context is captured.

After a requeue, there's no answer to:
- Why was it requeued? (human decision? automated policy? what triggered it?)
- What failed the first time?
- What changed between attempts?
- Who requeued it?
- How many times has this happened?

For tasks that go through multiple requeue cycles (e.g. agent fails → human reviews → provides feedback → requeued with new instructions → agent tries again), the full reasoning chain is completely lost.

## What We Need

1. **Requeue reason**: a required or optional reason string captured at requeue time, stored durably (not just in the ephemeral event log detail field).

2. **Full retry/requeue history**: the ability to see every attempt a task has gone through — who worked it, what happened, why it was reset, what changed. This is a chain, not a single value.

3. **Feedback capture**: when a human requeues after reviewing failed output, their feedback should be attached so the next agent picking up the task can read it. Currently there's no mechanism for this.

## Open Questions

1. **Where does requeue history live?**
   - Could be in `task_events` with richer `details` JSON — but `task_events` is currently a flat log with minimal structure
   - Could be in the **comment/feedback thread** (`comment-feedback-thread.md`) — requeue events post a structured comment with reason + feedback
   - A dedicated `task_attempts` table is the most structured but also the heaviest lift

2. **Should `requeue_task` require a reason?**
   - Making it required ensures traceability but adds friction for simple cases
   - Optional with a warning if omitted might be the right balance

3. **How does the next agent access prior attempt context?**
   - Via `get_comments(task_id)` if the thread exists
   - Via a new `get_task_history(task_id)` tool
   - Inline in `claim_next_action` / `claim_task` response (shows prior attempt count + last reason)

4. **What happens to `retry_count` reset on requeue?**
   - Currently `requeue_task` resets `retry_count = 0`
   - Should we preserve total lifetime attempt count separately from the current retry window?

5. **Should auto-retries (via `max_retries`) also log a reason?**
   - Currently `retry_task` (called automatically on `fail_task`) just increments `retry_count` with no context
   - Could log "auto-retry N of M" as a structured event

## Relationship to Other Features

- Closely linked to **comment/feedback thread** (`comment-feedback-thread.md`) — the thread is likely the right place for feedback-on-requeue to live
- Independent of approval context, though both involve human feedback loops

## Current State in Naxe

- `requeue_task(task_id, input?)`: resets to pending, clears owner, resets `retry_count=0`, logs `requeued` event
- `retry_task(task_id)`: auto-retry on failure if `retry_count < max_retries`, increments counter, logs `retried` event
- `task_events`: immutable log with `event_type` and optional `details` JSON — currently no details written on requeue/retry
- No attempt history, no reason storage, no feedback mechanism
