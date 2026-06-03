# Design Discussion: Approval Context

## Problem

The current approval flow (`request_approval` → `approve_task` / `reject_task`) works mechanically but lacks context at two key moments:

**At task creation**: There's no way to declare *why* this task requires approval. A reviewer seeing a task in `awaiting_approval` has no structured signal about what kind of sign-off is needed (e.g. "this sends an external email", "this modifies production data", "this costs money").

**At approval time**: The agent calls `request_approval(notes)` with freeform text, but there's no structured way to surface *what the agent actually did* and *what specifically needs review*. The reviewer has to infer context from the task description and whatever the agent chose to write.

## What We Need

### 1. Approval reason at creation time
A structured field on the task (set in `add_tasks`) that explains why approval is required — visible to reviewers before any work starts. Different from the task `description` (which describes what to do) — this describes *why a human must sign off*.

Possible field: `approval_reason: string` on the task record.

### 2. Structured handoff when requesting approval
When an agent calls `request_approval`, it should be able to provide more than freeform notes:
- A summary of what was done
- Specific items that need review
- Any artifacts or outputs the reviewer should look at

This might be better served by the **comment/feedback thread** (see `comment-feedback-thread.md`) — the agent posts a structured comment at approval-request time rather than cramming everything into a single `notes` field.

## Open Questions

1. **Is `approval_reason` a free-text field or an enum?**
   - Free text is flexible but unstructured
   - An enum (e.g. `external_communication`, `financial`, `destructive`, `irreversible`) enables filtering and tooling
   - Could be both: a category enum + optional free-text detail

2. **Where does the "what the agent did" handoff live?**
   - As a structured field on `request_approval`?
   - As a comment in the feedback thread (preferred if thread exists)?
   - As part of the task `output` field (already exists)?

3. **Should `approval_reason` be required when `requires_approval=true`?**
   - Could enforce at `add_tasks` time: if `requires_approval=true`, `approval_reason` must be set
   - Or leave it optional and just surface it when present

## Relationship to Other Features

- Depends on or benefits greatly from **comment/feedback thread** (`comment-feedback-thread.md`) for the agent handoff
- Independent of requeue traceability

## Current State in Naxe

- `requires_approval` boolean on tasks: flags that a task needs the `request_approval` → approve/reject flow
- `approval_notes` on tasks: single overwritable string, last-write-wins
- `approved_by` on tasks: records who approved
- `request_approval(task_id, agent_id, notes)`: transitions to `awaiting_approval`, stores notes
- `approve_task(task_id, approver_id, notes)`: marks completed, overwrites `approval_notes`
- `reject_task(task_id, approver_id, reason)`: marks failed, overwrites `approval_notes`
