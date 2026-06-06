"""naxe init — interactive first-run setup wizard."""

import sys


def _print(msg: str = "") -> None:
    print(msg)


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        value = input(f"{prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)
    return value or default


def _confirm(prompt: str, default: bool = True) -> bool:
    suffix = " [Y/n]" if default else " [y/N]"
    try:
        value = input(f"{prompt}{suffix}: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)
    if not value:
        return default
    return value.startswith("y")


def _hr() -> None:
    print("─" * 56)


def main() -> None:
    from naxe.config import (
        resolve_db_url_with_source, write_config_url,
        resolve_theme_with_source,
    )
    from naxe import store
    from naxe.schema import get_connection

    _print()
    _print("  ███╗  ██╗ █████╗ ██╗  ██╗███████╗")
    _print("  ████╗ ██║██╔══██╗╚██╗██╔╝██╔════╝")
    _print("  ██╔██╗██║███████║ ╚███╔╝ █████╗  ")
    _print("  ██║╚████║██║  ██║ ██╔██╗ ██╔══╝  ")
    _print("  ██║ ╚███║██║  ██║██╔╝ ██╗███████╗")
    _print()
    _print("  Dependency-aware task graph engine for AI agents.")
    _print()

    # ── Step 1: Database ──────────────────────────────────────────────────────

    _hr()
    _print("Step 1 of 3 — Database")
    _hr()
    _print()

    current_url, current_source = resolve_db_url_with_source()
    is_default = current_source == "default"

    if not is_default:
        _print(f"  Current DB: {current_url}")
        _print(f"  Source:     {current_source}")
        _print()
        if not _confirm("  Reconfigure database?", default=False):
            _print("  Keeping existing database configuration.")
            db_url = current_url
        else:
            db_url = _configure_db()
    else:
        _print("  No database configured yet.")
        _print()
        db_url = _configure_db()

    # ── Step 2: Test connection ───────────────────────────────────────────────

    _print()
    _print("  Testing connection...")
    try:
        conn = get_connection(db_url)
        _print("  ✓ Connected successfully.")
    except Exception as e:
        _print(f"  ✗ Cannot connect: {e}")
        _print()
        _print("  Fix the connection issue and run `naxe init` again.")
        sys.exit(1)

    # ── Step 3: Auth ──────────────────────────────────────────────────────────

    _print()
    _hr()
    _print("Step 2 of 3 — Authentication")
    _hr()
    _print()

    try:
        total_agents = conn.execute("SELECT COUNT(*) AS cnt FROM agents").fetchone()["cnt"]
        active_agents = store.count_active_agents(conn)
    except Exception:
        total_agents = 0
        active_agents = 0

    agent_key = None

    if total_agents == 0:
        _print("  Auth is currently in open mode — any caller is accepted.")
        _print()
        _print("  You can register an agent to enforce identity on all connections.")
        _print("  Recommended for multi-agent or shared PostgreSQL deployments.")
        _print("  For personal local use, open mode is fine.")
        _print()
        if _confirm("  Register an agent now?", default=False):
            agent_key = _register_first_agent(conn)
    else:
        _print(f"  Auth is locked — {active_agents} active agent(s), {total_agents - active_agents} revoked.")
        _print("  Skipping auth setup.")

    # ── Step 4: MCP config ────────────────────────────────────────────────────

    _print()
    _hr()
    _print("Step 3 of 3 — MCP Configuration")
    _hr()
    _print()
    _print("  Install naxe globally and add it to Claude Code:")
    _print()
    _print("    uv tool install naxe")
    _print()

    env_flags = f'--env NAXE_DB_URL="{db_url}"'
    if agent_key:
        env_flags += f" \\\n      --env NAXE_API_KEY={agent_key}"

    _print(f"    claude mcp add --scope user --transport stdio naxe \\")
    _print(f"      {env_flags} \\")
    _print(f"      -- naxe")
    _print()
    _print("  Then restart Claude Code and run /mcp to confirm the connection.")

    # ── Summary ───────────────────────────────────────────────────────────────

    _print()
    _hr()
    _print("Status")
    _hr()
    _print()

    theme, theme_source = resolve_theme_with_source()
    try:
        total_jobs = conn.execute("SELECT COUNT(*) AS cnt FROM jobs").fetchone()["cnt"]
        active_jobs = conn.execute(
            "SELECT COUNT(*) AS cnt FROM jobs WHERE status NOT IN ('completed', 'cancelled')"
        ).fetchone()["cnt"]
        jobs_str = f"{active_jobs} active, {total_jobs} total"
    except Exception:
        jobs_str = "—"

    _print(f"  Database:  {db_url}")
    _print(f"  Status:    ✓ Connected")
    if total_agents == 0:
        _print(f"  Auth:      Open mode")
    else:
        _print(f"  Auth:      Locked — {active_agents} active agent(s)")
    _print(f"  Jobs:      {jobs_str}")
    _print(f"  Theme:     {theme}")
    _print()
    _print("  Setup complete. Run `naxe-config status` anytime to check.")
    _print()


def _configure_db() -> str:
    from naxe.config import write_config_url

    print("  Choose a database backend:")
    print()
    print("    1) SQLite  — zero setup, stores data in a local file (default)")
    print("    2) PostgreSQL — recommended for multi-agent or shared use")
    print()

    choice = _ask("  Enter 1 or 2", default="1")

    if choice == "2":
        try:
            import psycopg  # noqa: F401
        except ImportError:
            import subprocess
            print()
            print("  Installing psycopg (PostgreSQL driver)...")
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "psycopg[binary]>=3.1"],
                check=True,
            )
            print("  ✓ psycopg installed.")
        print()
        print("  Enter your PostgreSQL connection URL.")
        print("  Format: postgresql://user:pass@host/dbname")
        print()
        url = _ask("  PostgreSQL URL")
        if not url:
            print("  No URL entered. Falling back to SQLite.")
            url = _ask("  SQLite file path", default="./naxe.db")
    else:
        print()
        url = _ask("  SQLite file path", default="./naxe.db")

    write_config_url(url)
    print(f"  Saved.")
    return url


def _register_first_agent(conn) -> str | None:
    from naxe import store, auth

    print()
    name = _ask("  Agent name", default="claude-code")
    if not name:
        return None

    raw_key = auth.generate_key()
    try:
        store.register_agent(conn, name, auth.hash_key(raw_key))
        conn.commit()
    except ValueError as e:
        print(f"  ✗ {e}")
        return None

    print()
    print(f"  Agent '{name}' registered.")
    print(f"  Key: {raw_key}")
    print(f"  Store this securely — it will not be shown again.")
    return raw_key
