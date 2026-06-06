import os
import sys

from naxe.config import (
    resolve_db_url, resolve_db_url_with_source, write_config_url, _CONFIG_FILE,
    resolve_theme_with_source, write_theme, _THEME_FILE,
)


def main():
    args = sys.argv[1:]

    if not args:
        print("Usage: naxe-config <command> [args]")
        print()
        print("Commands:")
        print("  status                 Show DB connection, auth mode, and job summary")
        print("  set-url <url>          Save a DB URL to ~/.config/naxe/config")
        print("  get-url                Print the currently resolved DB URL and its source")
        print("  set-theme <name>       Save a default theme to ~/.config/naxe/theme")
        print("  get-theme              Print the currently resolved theme and its source")
        print()
        print("Agent commands:")
        print("  register-agent <name>  Register a new agent and print its API key (shown once)")
        print("  revoke-agent <name>    Revoke an agent's API key")
        print("  list-agents            List all registered agents")
        sys.exit(1)

    command = args[0]

    if command == "status":
        _status()

    elif command == "set-url":
        if len(args) < 2:
            print("Usage: naxe-config set-url <url>", file=sys.stderr)
            sys.exit(1)
        write_config_url(args[1])
        print(f"Saved to {_CONFIG_FILE}")

    elif command == "get-url":
        url, source = resolve_db_url_with_source()
        print(f"{url}  ({source})")

    elif command == "set-theme":
        if len(args) < 2:
            print("Usage: naxe-config set-theme <name>", file=sys.stderr)
            print("Built-in naxe themes: naxe, naxe-bold")
            sys.exit(1)
        write_theme(args[1])
        print(f"Saved to {_THEME_FILE}")

    elif command == "get-theme":
        theme, source = resolve_theme_with_source()
        print(f"{theme}  ({source})")

    elif command == "register-agent":
        if len(args) < 2:
            print("Usage: naxe-config register-agent <name>", file=sys.stderr)
            sys.exit(1)
        _register_agent(args[1])

    elif command == "revoke-agent":
        if len(args) < 2:
            print("Usage: naxe-config revoke-agent <name>", file=sys.stderr)
            sys.exit(1)
        _revoke_agent(args[1])

    elif command == "list-agents":
        _list_agents()

    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        sys.exit(1)


def _status() -> None:
    from naxe.schema import get_connection
    from naxe import store
    from naxe.config import resolve_theme_with_source

    url, url_source = resolve_db_url_with_source()
    theme, theme_source = resolve_theme_with_source()

    print(f"Database:  {url}")
    print(f"           ({url_source})")

    try:
        conn = get_connection(url, readonly=True)
    except Exception as e:
        print(f"Status:    ✗ Cannot connect — {e}")
        sys.exit(1)

    print(f"Status:    ✓ Connected")

    # Auth mode
    try:
        total_agents = conn.execute("SELECT COUNT(*) AS cnt FROM agents").fetchone()["cnt"]
        active_agents = store.count_active_agents(conn)
        if total_agents == 0:
            print(f"Auth:      Open mode (no agents registered)")
        else:
            print(f"Auth:      Locked — {active_agents} active agent{'s' if active_agents != 1 else ''}, {total_agents - active_agents} revoked")
    except Exception:
        print(f"Auth:      Unknown (agents table not found — run CREATE TABLE manually)")

    # Job summary
    try:
        total_jobs = conn.execute("SELECT COUNT(*) AS cnt FROM jobs").fetchone()["cnt"]
        active_jobs = conn.execute(
            "SELECT COUNT(*) AS cnt FROM jobs WHERE status NOT IN ('completed', 'cancelled')"
        ).fetchone()["cnt"]
        in_progress_tasks = conn.execute(
            "SELECT COUNT(*) AS cnt FROM tasks WHERE status = 'in_progress'"
        ).fetchone()["cnt"]
        print(f"Jobs:      {active_jobs} active, {total_jobs} total")
        if in_progress_tasks:
            print(f"Tasks:     {in_progress_tasks} currently in progress")
    except Exception:
        print(f"Jobs:      Unknown (schema not initialised)")

    print(f"Theme:     {theme}  ({theme_source})")


def _register_agent(name: str) -> None:
    from naxe.schema import get_connection
    from naxe import store, auth
    from naxe.auth import validate_key_format

    conn = get_connection(resolve_db_url())

    # If agents are already registered, caller must present a valid key
    # (only an existing agent can register new ones on a locked DB)
    n = store.count_active_agents(conn)
    if n > 0:
        raw_key = os.environ.get("NAXE_API_KEY", "")
        if not raw_key:
            print(
                "naxe-config: NAXE_API_KEY is required to register a new agent when agents are already registered.",
                file=sys.stderr,
            )
            sys.exit(1)
        if not validate_key_format(raw_key):
            print("naxe-config: NAXE_API_KEY has invalid format.", file=sys.stderr)
            sys.exit(1)
        if store.get_agent_by_key_hash(conn, auth.hash_key(raw_key)) is None:
            print("naxe-config: Invalid or revoked API key.", file=sys.stderr)
            sys.exit(1)

    raw_key = auth.generate_key()
    try:
        store.register_agent(conn, name, auth.hash_key(raw_key))
        conn.commit()
    except ValueError as e:
        print(f"naxe-config: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Agent '{name}' registered.")
    print(f"Key: {raw_key}")
    print("Store this securely — it will not be shown again.")


def _revoke_agent(name: str) -> None:
    from naxe.schema import get_connection
    from naxe import store

    conn = get_connection(resolve_db_url())
    revoked = store.revoke_agent(conn, name)
    if revoked:
        conn.commit()
        print(f"Agent '{name}' revoked.")
    else:
        print(f"naxe-config: Agent '{name}' not found or already revoked.", file=sys.stderr)
        sys.exit(1)


def _list_agents() -> None:
    from naxe.schema import get_connection
    from naxe import store

    conn = get_connection(resolve_db_url())
    agents = store.list_agents(conn)

    if not agents:
        print("No agents registered. (Open mode — any caller is accepted.)")
        return

    print(f"{'Name':<24}  {'Created':<20}  Status")
    print("-" * 56)
    for a in agents:
        status = "active" if a["active"] else "revoked"
        created = str(a["created_at"])[:19]
        print(f"{a['name']:<24}  {created:<20}  {status}")
