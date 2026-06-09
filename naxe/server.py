import json
import os
from contextvars import ContextVar
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from naxe.schema import get_connection
from naxe import store
from naxe.config import resolve_db_url, resolve_context
from naxe.tools import list_all_tools
from naxe.handlers import DISPATCH
from naxe.handlers._common import _err

DB_URL = resolve_db_url()
CONTEXT = resolve_context()

_shared_conn = None
_request_agent_id: ContextVar[str | None] = ContextVar('_request_agent_id', default=None)


def _conn():
    global _shared_conn
    if _shared_conn is None or getattr(_shared_conn, "closed", False):
        _shared_conn = get_connection(DB_URL)
    return _shared_conn


def _conn_with_retry():
    global _shared_conn
    try:
        return _conn()
    except Exception:
        _shared_conn = None
        return _conn()


def _resolve_session_identity(conn) -> str | None:
    """Validate NAXE_API_KEY and return the registered agent name.

    Returns None in open mode (no agents registered).
    Raises SystemExit with a clear message if auth fails.
    """
    from naxe.auth import hash_key, validate_key_format

    # Use total agent count (including revoked) to determine lock mode.
    # Once any agent has been registered, auth stays enforced even if all are revoked.
    row = conn.execute("SELECT COUNT(*) AS cnt FROM agents").fetchone()
    if row["cnt"] == 0:
        return None  # open mode — no agents ever registered

    raw_key = os.environ.get("NAXE_API_KEY", "")
    if not raw_key:
        raise SystemExit(
            "naxe: NAXE_API_KEY is required — register an agent with: naxe config register-agent <name>"
        )
    if not validate_key_format(raw_key):
        raise SystemExit("naxe: NAXE_API_KEY has invalid format")

    agent = store.get_agent_by_key_hash(conn, hash_key(raw_key))
    if agent is None:
        raise SystemExit("naxe: Invalid or revoked API key")

    return agent["name"]


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


@app.list_tools()
async def list_tools() -> list[Tool]:
    return list_all_tools()


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    conn = _conn_with_retry()
    try:
        agent_id = _request_agent_id.get()
        if agent_id is not None:
            arguments["agent_id"] = agent_id
        arguments["_context"] = CONTEXT
        handler = DISPATCH.get(name)
        if handler is None:
            return _err(f"Unknown tool: {name}")
        result = handler(conn, arguments)
        conn.commit()
        return result
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        return _err(f"Internal error: {e}")


async def _run():
    conn = _conn()
    _request_agent_id.set(_resolve_session_identity(conn))
    store.startup_scan_awaiting_approval(conn)
    conn.commit()
    async with stdio_server() as streams:
        await app.run(streams[0], streams[1], app.create_initialization_options())


def main():
    import asyncio
    asyncio.run(_run())


if __name__ == "__main__":
    main()
