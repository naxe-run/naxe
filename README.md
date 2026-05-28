# Naxe

A local-first MCP server that acts as a dependency-aware task graph engine for AI agents. Agents write tasks with dependencies; the engine surfaces only the tasks that are actionable right now.

**Core insight:** agents shouldn't have to reason about what they *can* do — only what they *should* do.

## Install

```bash
uv sync
```

## Claude Code MCP Configuration

Add to your Claude Code MCP config (e.g. `~/.claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "naxe": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/naxe", "naxe"],
      "env": {
        "NAXE_DB_PATH": "/path/to/naxe.db"
      }
    }
  }
}
```

Or with plain Python after install:

```json
{
  "mcpServers": {
    "naxe": {
      "command": "python",
      "args": ["-m", "naxe.server"],
      "env": {
        "NAXE_DB_PATH": "./naxe.db"
      }
    }
  }
}
```

## Tools

| Tool | Description |
|---|---|
| `create_job` | Create a new task graph job |
| `add_tasks` | Add tasks with dependencies (validates cycles + unknown deps) |
| `get_next_actions` | Return all currently unblocked tasks |
| `claim_task` | Atomically claim a task for an agent |
| `complete_task` | Mark done; returns newly unblocked tasks |
| `fail_task` | Mark failed with optional reason |
| `get_job_status` | Full job snapshot with progress counters |
| `list_jobs` | List all jobs |

## Run Tests

```bash
uv run pytest tests/ -v
```
