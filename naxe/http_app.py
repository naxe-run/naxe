from __future__ import annotations

import json

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route


def _json(data, status_code: int = 200) -> Response:
    return Response(
        json.dumps(data, default=str),
        status_code=status_code,
        media_type="application/json",
    )

from mcp.server.sse import SseServerTransport

from naxe.schema import get_connection
from naxe import store
from naxe.auth import hash_key, validate_key_format
from naxe.handlers import DISPATCH
from naxe.server import app as _mcp_server, _request_agent_id
from naxe.tui.client import LocalNaxeClient


def _resolve_bearer(conn, request: Request) -> str | None:
    """Validate Authorization: Bearer header against agents table.

    Returns None in open mode (no agents registered).
    Raises PermissionError on bad/missing key in locked mode.
    """
    row = conn.execute("SELECT COUNT(*) AS cnt FROM agents").fetchone()
    if row["cnt"] == 0:
        return None  # open mode

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise PermissionError("Missing or invalid Authorization header")

    raw_key = auth[len("Bearer "):]
    if not validate_key_format(raw_key):
        raise PermissionError("Invalid API key format")

    agent = store.get_agent_by_key_hash(conn, hash_key(raw_key))
    if agent is None:
        raise PermissionError("Invalid or revoked API key")

    return agent["name"]


def build_app(db_url: str, context: str | None = None) -> Starlette:
    sse = SseServerTransport("/messages/")

    # ── SSE MCP endpoint ──────────────────────────────────────────────────────

    async def handle_sse(request: Request) -> Response:
        conn = get_connection(db_url)
        try:
            agent_name = _resolve_bearer(conn, request)
        except PermissionError as e:
            conn.close()
            return Response(str(e), status_code=401)
        finally:
            conn.close()

        _request_agent_id.set(agent_name)
        async with sse.connect_sse(request.scope, request._receive, request._send) as streams:
            await _mcp_server.run(
                streams[0],
                streams[1],
                _mcp_server.create_initialization_options(),
            )
        return Response()

    async def handle_messages(request: Request) -> Response:
        await sse.handle_post_message(request.scope, request._receive, request._send)
        return Response()

    # ── REST: tool call ───────────────────────────────────────────────────────

    async def handle_call(request: Request) -> Response:
        tool_name = request.path_params["tool_name"]
        conn = get_connection(db_url)
        try:
            agent_name = _resolve_bearer(conn, request)
        except PermissionError as e:
            conn.close()
            return _json({"error": str(e)}, status_code=401)

        try:
            body = await request.json()
        except Exception:
            body = {}

        try:
            arguments = dict(body)
            if agent_name is not None:
                arguments["agent_id"] = agent_name
            if context is not None:
                arguments["_context"] = context

            handler = DISPATCH.get(tool_name)
            if handler is None:
                return _json({"error": f"Unknown tool: {tool_name}"}, status_code=404)

            result = handler(conn, arguments)
            conn.commit()
            # Unwrap TextContent list → plain JSON dict
            payload = json.loads(result[0].text) if result else {}
            return _json(payload)
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            return _json({"error": str(e)}, status_code=500)
        finally:
            conn.close()

    # ── REST: jobs list with task counts ─────────────────────────────────────

    async def handle_jobs(request: Request) -> Response:
        conn = get_connection(db_url)
        try:
            _resolve_bearer(conn, request)
        except PermissionError as e:
            conn.close()
            return _json({"error": str(e)}, status_code=401)

        try:
            status_filter = request.query_params.get("filter", "open")
            client = LocalNaxeClient(conn)
            jobs = client.fetch_jobs(status_filter)
            task_counts = client.batch_task_counts([j["id"] for j in jobs])
            conn.rollback()
            return _json({"jobs": jobs, "task_counts": task_counts})
        except Exception as e:
            return _json({"error": str(e)}, status_code=500)
        finally:
            conn.close()

    # ── REST: dependency edges for a job ─────────────────────────────────────

    async def handle_deps(request: Request) -> Response:
        job_id = request.path_params["job_id"]
        conn = get_connection(db_url)
        try:
            _resolve_bearer(conn, request)
        except PermissionError as e:
            conn.close()
            return _json({"error": str(e)}, status_code=401)

        try:
            client = LocalNaxeClient(conn)
            tasks = client.get_tasks_for_job(job_id)
            task_ids = [t["id"] for t in tasks]
            edges = client.get_dependency_edges(task_ids)
            blocked_ids = list(client.get_blocked_task_ids(task_ids))
            conn.rollback()
            return _json({"edges": edges, "blocked_ids": blocked_ids})
        except Exception as e:
            return _json({"error": str(e)}, status_code=500)
        finally:
            conn.close()

    routes = [
        Route("/sse", endpoint=handle_sse),
        Route("/messages/", endpoint=handle_messages, methods=["POST"]),
        Route("/api/v1/call/{tool_name}", endpoint=handle_call, methods=["POST"]),
        Route("/api/v1/jobs", endpoint=handle_jobs, methods=["GET"]),
        Route("/api/v1/jobs/{job_id}/deps", endpoint=handle_deps, methods=["GET"]),
    ]

    return Starlette(routes=routes)
