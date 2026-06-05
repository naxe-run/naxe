import os
import pytest
from naxe.schema import get_connection
from naxe import store
from naxe.auth import generate_key, hash_key, validate_key_format
from naxe.server import _resolve_session_identity


@pytest.fixture
def conn():
    c = get_connection(":memory:")
    yield c
    c.close()


# ── Pure auth function tests ──────────────────────────────────────────────────

def test_generate_key_format():
    key = generate_key()
    assert validate_key_format(key)


def test_validate_key_format_rejects_bad_prefix():
    assert not validate_key_format("sk_abc123")
    assert not validate_key_format("naxe_abc123")


def test_validate_key_format_rejects_wrong_length():
    assert not validate_key_format("naxe_sk_tooshort")
    assert not validate_key_format("naxe_sk_" + "a" * 65)


def test_hash_deterministic():
    key = generate_key()
    assert hash_key(key) == hash_key(key)


def test_hash_different_keys():
    assert hash_key(generate_key()) != hash_key(generate_key())


# ── Store layer tests ─────────────────────────────────────────────────────────

def test_count_active_agents_empty(conn):
    assert store.count_active_agents(conn) == 0


def test_register_and_count(conn):
    key = generate_key()
    store.register_agent(conn, "alice", hash_key(key))
    assert store.count_active_agents(conn) == 1


def test_register_duplicate_name_raises(conn):
    key1, key2 = generate_key(), generate_key()
    store.register_agent(conn, "alice", hash_key(key1))
    with pytest.raises(ValueError, match="already registered"):
        store.register_agent(conn, "alice", hash_key(key2))


def test_get_agent_by_key_hash_found(conn):
    key = generate_key()
    store.register_agent(conn, "alice", hash_key(key))
    agent = store.get_agent_by_key_hash(conn, hash_key(key))
    assert agent is not None
    assert agent["name"] == "alice"


def test_get_agent_by_key_hash_not_found(conn):
    assert store.get_agent_by_key_hash(conn, hash_key(generate_key())) is None


def test_revoke_agent(conn):
    key = generate_key()
    store.register_agent(conn, "alice", hash_key(key))
    assert store.revoke_agent(conn, "alice") is True
    assert store.get_agent_by_key_hash(conn, hash_key(key)) is None
    assert store.count_active_agents(conn) == 0


def test_revoke_nonexistent_agent(conn):
    assert store.revoke_agent(conn, "nobody") is False


def test_list_agents_excludes_key_hash(conn):
    key = generate_key()
    store.register_agent(conn, "alice", hash_key(key))
    agents = store.list_agents(conn)
    assert len(agents) == 1
    assert "key_hash" not in agents[0]
    assert agents[0]["name"] == "alice"


def test_list_agents_includes_revoked(conn):
    key = generate_key()
    store.register_agent(conn, "alice", hash_key(key))
    store.revoke_agent(conn, "alice")
    agents = store.list_agents(conn)
    assert len(agents) == 1
    assert agents[0]["active"] == 0


# ── Session identity resolution tests ────────────────────────────────────────

def test_open_mode_returns_none(conn, monkeypatch):
    monkeypatch.delenv("NAXE_API_KEY", raising=False)
    assert _resolve_session_identity(conn) is None


def test_locked_mode_no_key_raises(conn, monkeypatch):
    key = generate_key()
    store.register_agent(conn, "alice", hash_key(key))
    monkeypatch.delenv("NAXE_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="NAXE_API_KEY is required"):
        _resolve_session_identity(conn)


def test_locked_mode_invalid_format_raises(conn, monkeypatch):
    key = generate_key()
    store.register_agent(conn, "alice", hash_key(key))
    monkeypatch.setenv("NAXE_API_KEY", "not-a-valid-key")
    with pytest.raises(SystemExit, match="invalid format"):
        _resolve_session_identity(conn)


def test_locked_mode_wrong_key_raises(conn, monkeypatch):
    key = generate_key()
    store.register_agent(conn, "alice", hash_key(key))
    monkeypatch.setenv("NAXE_API_KEY", generate_key())  # valid format, wrong key
    with pytest.raises(SystemExit, match="Invalid or revoked"):
        _resolve_session_identity(conn)


def test_locked_mode_valid_key_returns_name(conn, monkeypatch):
    key = generate_key()
    store.register_agent(conn, "alice", hash_key(key))
    monkeypatch.setenv("NAXE_API_KEY", key)
    assert _resolve_session_identity(conn) == "alice"


def test_revoked_key_raises(conn, monkeypatch):
    key = generate_key()
    store.register_agent(conn, "alice", hash_key(key))
    store.revoke_agent(conn, "alice")
    monkeypatch.setenv("NAXE_API_KEY", key)
    with pytest.raises(SystemExit, match="Invalid or revoked"):
        _resolve_session_identity(conn)
