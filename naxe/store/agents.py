import uuid

from naxe.store.core import _now


def count_active_agents(conn) -> int:
    """Return the number of currently active registered agents."""
    row = conn.execute("SELECT COUNT(*) AS cnt FROM agents WHERE active = 1").fetchone()
    return row["cnt"] if row else 0


def register_agent(conn, name: str, key_hash: str) -> dict:
    """Register a new agent. Raises ValueError if name already exists."""
    existing = conn.execute("SELECT id FROM agents WHERE name = %s", (name,)).fetchone()
    if existing:
        raise ValueError(f"Agent '{name}' is already registered")
    agent_id = str(uuid.uuid4())
    now = _now()
    conn.execute(
        "INSERT INTO agents (id, name, key_hash, created_at, active) VALUES (%s, %s, %s, %s, 1)",
        (agent_id, name, key_hash, now),
    )
    return {"id": agent_id, "name": name, "created_at": now, "active": 1}


def get_agent_by_key_hash(conn, key_hash: str) -> dict | None:
    """Return the active agent matching this key hash, or None if not found/inactive."""
    row = conn.execute(
        "SELECT id, name, created_at, active FROM agents WHERE key_hash = %s AND active = 1",
        (key_hash,),
    ).fetchone()
    return dict(row) if row else None


def revoke_agent(conn, name: str) -> bool:
    """Deactivate an agent by name. Returns True if found and revoked, False if not found."""
    cur = conn.execute(
        "UPDATE agents SET active = 0 WHERE name = %s AND active = 1",
        (name,),
    )
    return cur.rowcount > 0


def list_agents(conn) -> list[dict]:
    """List all agents. Never includes key_hash."""
    rows = conn.execute(
        "SELECT id, name, created_at, active FROM agents ORDER BY created_at ASC"
    ).fetchall()
    return [dict(r) for r in rows]
