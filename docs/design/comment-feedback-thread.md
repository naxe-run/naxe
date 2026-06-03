# Design Discussion: Comment / Feedback Thread

## Problem

Naxe has no way to attach freeform commentary to a task. The `task_events` table is an immutable audit log of state transitions (claimed, completed, failed, etc.) — it records *what happened* but not *why*, *what was discussed*, or *what feedback was exchanged* during review cycles.

This gap surfaces most clearly in two scenarios:
- An agent submits work for human review. The human needs to leave feedback. The agent needs to read that feedback, act on it, and resubmit. There's nowhere to store this back-and-forth.
- A task is requeued after failure. There's no record of why it failed, what was tried, or what changed — just a reset status.

## What We Need

A sequential, append-only thread of comments attached to a task. Each comment should capture:
- Who wrote it (agent ID or human identifier)
- When it was written
- The content (freeform text)
- Optionally, what triggered it (e.g. "rejection feedback", "requeue reason", "approval request notes")

## Open Questions

1. **Is this a new table or an extension of `task_events`?**
   - New `task_comments` table keeps the audit log clean and purpose-built
   - Extending `task_events` with a `comment` field avoids a new table but muddies the event semantics
   - Leaning toward a separate table

2. **Who can post a comment?**
   - Any agent or human, or only the task owner / approver?
   - Should there be a `role` field (e.g. "agent", "reviewer", "system")?

3. **What tools are needed?**
   - `add_comment(task_id, author_id, body, context?)` — append a comment
   - `get_comments(task_id)` — retrieve the thread
   - Should comments be included inline in `get_job_status` task records, or only via a dedicated call?

4. **How does this relate to approval notes?**
   - Currently `approval_notes` is a single text field on the task record, overwritten each time
   - Should approval notes migrate into the comment thread, or stay separate?

## Relationship to Other Features

This is likely the foundation for:
- **Approval context** — structured notes when requesting/granting/rejecting approval should land in the thread
- **Requeue traceability** — requeue reasons and retry history should land in the thread

Solving the thread first probably makes the other two straightforward.

## Current State in Naxe

- `task_events` table: immutable audit log, `event_type` + optional `details` JSON
- `approval_notes` column on tasks: single overwritable string
- `request_approval(notes)`, `approve_task(notes)`, `reject_task(reason)`: all write to `approval_notes`
- No append-only comment concept exists anywhere
