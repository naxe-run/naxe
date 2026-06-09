"""Tests for the _request_agent_id ContextVar seam in server.py."""
import asyncio
import pytest
from naxe.server import _request_agent_id


def test_request_agent_id_importable():
    assert _request_agent_id is not None


def test_default_is_none():
    assert _request_agent_id.get() is None


def test_set_and_get():
    token = _request_agent_id.set("alice")
    try:
        assert _request_agent_id.get() == "alice"
    finally:
        _request_agent_id.reset(token)
    assert _request_agent_id.get() is None


@pytest.mark.asyncio
async def test_per_task_isolation():
    """Two concurrent async tasks must see their own values, not each other's."""
    results = {}

    async def task_a():
        _request_agent_id.set("agent-a")
        await asyncio.sleep(0)  # yield so task_b runs
        results["a"] = _request_agent_id.get()

    async def task_b():
        _request_agent_id.set("agent-b")
        await asyncio.sleep(0)
        results["b"] = _request_agent_id.get()

    await asyncio.gather(
        asyncio.create_task(task_a()),
        asyncio.create_task(task_b()),
    )

    assert results["a"] == "agent-a"
    assert results["b"] == "agent-b"


@pytest.mark.asyncio
async def test_call_tool_injects_agent_id(monkeypatch):
    """call_tool must inject agent_id when ContextVar is set (locked mode)."""
    from unittest.mock import MagicMock
    from naxe.handlers._common import _err
    import naxe.server as server_mod

    captured = {}

    def fake_handler(conn, arguments):
        captured["arguments"] = dict(arguments)
        return [MagicMock()]

    monkeypatch.setattr(server_mod, "DISPATCH", {"list_jobs": fake_handler})
    monkeypatch.setattr(server_mod, "_conn_with_retry", lambda: MagicMock())

    token = _request_agent_id.set("alice")
    try:
        await server_mod.call_tool("list_jobs", {})
    finally:
        _request_agent_id.reset(token)

    assert captured["arguments"].get("agent_id") == "alice"


@pytest.mark.asyncio
async def test_call_tool_no_agent_id_in_open_mode(monkeypatch):
    """call_tool must NOT inject agent_id when ContextVar is None (open mode)."""
    from unittest.mock import MagicMock
    import naxe.server as server_mod

    captured = {}

    def fake_handler(conn, arguments):
        captured["arguments"] = dict(arguments)
        return [MagicMock()]

    monkeypatch.setattr(server_mod, "DISPATCH", {"list_jobs": fake_handler})
    monkeypatch.setattr(server_mod, "_conn_with_retry", lambda: MagicMock())

    # Ensure ContextVar is None (open mode)
    token = _request_agent_id.set(None)
    try:
        await server_mod.call_tool("list_jobs", {})
    finally:
        _request_agent_id.reset(token)

    assert "agent_id" not in captured["arguments"]
