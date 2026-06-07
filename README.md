# Naxe

A local-first MCP server that acts as a dependency-aware task graph engine for AI agents. Agents write tasks with dependencies; the engine surfaces only the tasks that are actionable right now.

**Core insight:** agents shouldn't have to reason about what they *can* do — only what they *should* do.

## Install

```bash
uv tool install naxe
# or: pipx install naxe
# or: pip install naxe
```

This makes the `naxe` command available globally.

For development (editable install):

```bash
git clone https://github.com/naxe-run/naxe
uv tool install --editable ./naxe
```

## Quick Start

### 1. Run the setup wizard

```bash
naxe init
```

This walks you through database selection (SQLite or PostgreSQL), tests the connection, optionally registers an agent, and prints the exact `claude mcp add` command to paste into your shell.

**Or configure manually** (after `uv tool install`):

```bash
claude mcp add --scope user --transport stdio naxe \
  --env NAXE_DB_URL=postgresql://user:pass@localhost/naxe \
  --env NAXE_API_KEY=naxe_sk_... \
  -- naxe
```

Omit `--env NAXE_API_KEY` if running in open mode (no registered agents). `--scope user` makes the MCP available in all your projects.

### 2. Start using it

In a Claude Code session, ask Claude to use Naxe for multi-step tasks:

> "Use Naxe to track this refactor. Create a job with tasks for each step."

Claude will call `create_job` and `add_tasks` to plan the work, then work through tasks using `claim_task` and `complete_task`.

## Database

Naxe supports two backends:

### SQLite (default — zero setup)

A `naxe.db` file is created in the working directory automatically. Good for personal use and ephemeral agent sessions.

```bash
# Override the path
export NAXE_DB_PATH=/path/to/my.db
# Or use a full URL
export NAXE_DB_URL=./naxe.db
```

### PostgreSQL (recommended for multi-agent or shared use)

Install the PostgreSQL extra:

Then point naxe at your database:

```bash
export NAXE_DB_URL=postgresql://user:pass@localhost/naxe
```

Or store it permanently:

```bash
naxe config set-url postgresql://user:pass@localhost/naxe
```

PostgreSQL is the right choice when:
- Multiple agents run concurrently against the same job graph
- You want the task history to persist across machine restarts
- You're running a team setup where multiple people share one naxe instance

> **Note:** SQLite is single-writer by design. For multiple concurrent agents writing tasks, use PostgreSQL.

## Authentication

By default, naxe runs in **open mode** — any caller is accepted and `agent_id` is self-reported. This is fine for personal local use.

To enforce identity and access control, register agents with API keys:

```bash
# Register an agent (first registration requires no key)
naxe config register-agent my-agent
# → Key: naxe_sk_...   Store this — it will not be shown again.

# Add the key to your MCP config
claude mcp remove naxe
claude mcp add --scope user --transport stdio naxe \
  --env NAXE_API_KEY=naxe_sk_... \
  -- naxe
```

Once any agent is registered, naxe enforces `NAXE_API_KEY` on every connection. The key carries the agent's identity — all tool calls are attributed to the registered name regardless of what the caller passes as `agent_id`.

### Managing agents

```bash
naxe config list-agents           # Show all registered agents and their status
naxe config revoke-agent <name>   # Revoke an agent's key
naxe config register-agent <name> # Register a new agent (requires NAXE_API_KEY if others exist)
```

### When to use auth

| Use case | Recommendation |
|---|---|
| Personal local use | Open mode (no keys needed) |
| Ephemeral agent sessions | Open mode (each agent owns its own naxe instance) |
| Multi-agent, shared PostgreSQL | Register each agent with a key |
| Team / shared deployment | Register each user/agent with a key |

## Configuration

```bash
naxe config get-url          # Show current DB URL and where it came from
naxe config set-url <url>    # Save a DB URL to ~/.config/naxe/config
naxe config get-theme        # Show current TUI theme
naxe config set-theme <name> # Save theme (built-in: naxe, naxe-bold)
naxe config get-context      # Show the active context
naxe config set-context <name> # Set the active context
```

Environment variables take precedence over config files:

| Variable | Purpose |
|---|---|
| `NAXE_DB_URL` | Full database URL (postgresql:// or file path) |
| `NAXE_DB_PATH` | SQLite file path shorthand |
| `NAXE_API_KEY` | Agent API key for authenticated deployments |
| `NAXE_THEME` | TUI theme name |
| `NAXE_CONTEXT` | Active context (workspace) |

## Context

Contexts let you partition jobs into isolated workspaces — for example, separating `home` and `work` tasks on a shared database. Context is a server-level setting; agents never see or set it directly.

```bash
# Set a context
naxe config set-context work

# Or via environment variable
NAXE_CONTEXT=work naxe
```

When a context is active, only jobs in that context are visible. Jobs created without a context are only visible when no context is set. To use multiple contexts, configure separate MCP server entries with different `NAXE_CONTEXT` values:

```bash
claude mcp add --scope user --transport stdio naxe-work \
  --env NAXE_DB_URL="postgresql://..." \
  --env NAXE_CONTEXT=work \
  -- naxe

claude mcp add --scope user --transport stdio naxe-home \
  --env NAXE_DB_URL="postgresql://..." \
  --env NAXE_CONTEXT=home \
  -- naxe
```

## Tools

Naxe exposes 33 MCP tools grouped by function:

### Job lifecycle
| Tool | Description |
|---|---|
| `create_job` | Create a new task graph job |
| `get_job_status` | Full snapshot: all tasks, progress counters, blocking jobs |
| `list_jobs` | List jobs with per-job progress summaries |
| `edit_job` | Rename a job |
| `cancel_job` | Cancel a job and all pending tasks atomically |
| `pause_job` | Pause a job (no new claims until resumed) |
| `resume_job` | Resume a paused job |
| `add_job_dependency` | Declare that one job must complete before another starts |
| `set_job_concurrency` | Set or clear a max concurrent agent limit |
| `set_worktree_paths` | Store git worktree paths keyed by repo |

### Task execution
| Tool | Description |
|---|---|
| `add_tasks` | Add tasks with dependencies. `job_id` is optional — omit it to auto-create a job. |
| `get_next_actions` | Return all currently unblocked tasks (orchestrator use) |
| `claim_task` | Atomically claim a specific task |
| `claim_next_action` | Atomically find and claim the next unblocked task (worker use) |
| `complete_task` | Mark done; returns newly unblocked tasks |
| `fail_task` | Mark failed with optional reason and output |
| `heartbeat_task` | Prevent a long-running task from being reclaimed as stale |
| `update_task_progress` | Report 0–100 progress on a task you own |
| `cancel_task` | Cancel a single pending or in-progress task |
| `edit_task` | Edit metadata or dependencies of a pending task |
| `requeue_task` | Reset a failed or cancelled task back to pending |

### Approval workflow
| Tool | Description |
|---|---|
| `request_approval` | Transition an in-progress task to awaiting_approval |
| `approve_task` | Approve a task, marking it complete and unblocking dependents |
| `reject_task` | Hard-fail a task awaiting approval |
| `return_task` | Return a task to pending with feedback for the agent |

### Comments & audit
| Tool | Description |
|---|---|
| `add_task_comment` | Add a comment to a task (agent or human) |
| `get_task_comments` | Retrieve comment history, optionally by approval round |
| `get_task_events` | Full event history for a single task |
| `get_job_audit_trail` | All events across every task in a job |
| `get_blocked_tasks` | List pending tasks blocked by incomplete dependencies |

### Templates
| Tool | Description |
|---|---|
| `create_job_template` | Save a reusable task graph as a named template |
| `list_templates` | List all saved templates |
| `instantiate_template` | Create a new job from a template |

## TUI

Naxe includes an interactive terminal UI for browsing jobs and tasks:

```bash
naxe ui
```

Keyboard shortcuts: `Enter` open job, `A` approval queue, `N` new job, `E` edit, `X` cancel, `P` pause/resume, `F` cycle filter, `Q` quit.

## Design notes

- **SQLite is single-writer.** Concurrent agents reading is fine; concurrent writes will serialize. Use PostgreSQL for true multi-agent parallelism.
- **Authentication is optional.** Open mode (no registered agents) is the zero-config default. Once any agent is registered, all connections require a valid key.
- **`agent_id` is server-enforced when auth is enabled.** With a registered key, the server ignores whatever `agent_id` the caller passes and uses the registered name instead. The audit trail is trustworthy.
- **Pre-1.0:** breaking schema changes will require dropping and recreating the database. A proper migration system will be added before 1.0.

## Run Tests

```bash
uv run pytest tests/ -v
```
