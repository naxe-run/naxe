import sqlite3


DDL = """
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status      TEXT NOT NULL DEFAULT 'active',
    output      TEXT,
    paused      INTEGER NOT NULL DEFAULT 0,
    pause_reason TEXT DEFAULT NULL,
    worktree    INTEGER NOT NULL DEFAULT 0,
    worktree_paths  TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
    id               TEXT PRIMARY KEY,
    job_id           TEXT NOT NULL REFERENCES jobs(id),
    name             TEXT NOT NULL,
    description      TEXT,
    status           TEXT NOT NULL DEFAULT 'pending',
    owner_agent_id   TEXT,
    duration_minutes INTEGER,
    output           TEXT,
    max_retries      INTEGER NOT NULL DEFAULT 0,
    retry_count      INTEGER NOT NULL DEFAULT 0,
    input            TEXT,
    resources        TEXT,
    repo             TEXT,
    progress         INTEGER NOT NULL DEFAULT 0,
    priority         INTEGER NOT NULL DEFAULT 50,
    approved_by      TEXT,
    approval_notes   TEXT,
    requires_approval INTEGER NOT NULL DEFAULT 0,
    human_task       INTEGER NOT NULL DEFAULT 0,
    created_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    start_date       TIMESTAMP DEFAULT NULL,
    due_date         TIMESTAMP DEFAULT NULL,
    recurrence_interval_days INTEGER DEFAULT NULL,
    critical         INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS dependencies (
    task_id          TEXT NOT NULL REFERENCES tasks(id),
    depends_on_task_id TEXT NOT NULL REFERENCES tasks(id),
    PRIMARY KEY (task_id, depends_on_task_id)
);

CREATE TABLE IF NOT EXISTS job_dependencies (
    job_id             TEXT NOT NULL REFERENCES jobs(id),
    depends_on_job_id  TEXT NOT NULL REFERENCES jobs(id),
    PRIMARY KEY (job_id, depends_on_job_id)
);

CREATE TABLE IF NOT EXISTS job_templates (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT,
    tasks_json  TEXT NOT NULL,
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS task_events (
    id          TEXT PRIMARY KEY,
    task_id     TEXT NOT NULL REFERENCES tasks(id),
    job_id      TEXT NOT NULL REFERENCES jobs(id),
    event_type  TEXT NOT NULL,
    agent_id    TEXT,
    timestamp   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    details     TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_job_status ON tasks(job_id, status);
CREATE INDEX IF NOT EXISTS idx_task_events_task_id ON task_events(task_id);
CREATE INDEX IF NOT EXISTS idx_task_events_job_id ON task_events(job_id);
"""


class _SQLiteCursor:
    """Wraps a sqlite3.Cursor to expose rowcount and fetch methods."""

    def __init__(self, cursor: sqlite3.Cursor):
        self._cur = cursor

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount

    def fetchone(self):
        row = self._cur.fetchone()
        return dict(row) if row else None

    def fetchall(self):
        return [dict(r) for r in self._cur.fetchall()]


class _SQLiteConnection:
    """
    Thin wrapper around sqlite3.Connection that accepts %s-style placeholders
    (converting them to ?) and returns rows as plain dicts.
    """

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def execute(self, sql: str, params=()) -> _SQLiteCursor:
        normalized = sql.replace("%s", "?")
        return _SQLiteCursor(self._conn.execute(normalized, params))

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    def transaction(self):
        return self

    def __enter__(self):
        self._conn.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return self._conn.__exit__(exc_type, exc_val, exc_tb)


_migrations_run = False

_MIGRATIONS = [
    "ALTER TABLE tasks ADD COLUMN output TEXT",
    "ALTER TABLE tasks ADD COLUMN last_heartbeat_at TIMESTAMP",
    "ALTER TABLE jobs ADD COLUMN heartbeat_timeout_seconds INTEGER DEFAULT 300",
    "ALTER TABLE tasks ADD COLUMN max_retries INTEGER DEFAULT 0",
    "ALTER TABLE tasks ADD COLUMN retry_count INTEGER DEFAULT 0",
    "ALTER TABLE tasks ADD COLUMN input TEXT",
    "ALTER TABLE tasks ADD COLUMN resources TEXT",
    "ALTER TABLE tasks ADD COLUMN progress INTEGER DEFAULT 0",
    "ALTER TABLE jobs ADD COLUMN max_workers INTEGER DEFAULT NULL",
    "ALTER TABLE tasks ADD COLUMN priority INTEGER DEFAULT 50",
    "ALTER TABLE tasks ADD COLUMN repo TEXT",
    "ALTER TABLE jobs ADD COLUMN output TEXT",
    "ALTER TABLE tasks ADD COLUMN approved_by TEXT",
    "ALTER TABLE tasks ADD COLUMN approval_notes TEXT",
    "ALTER TABLE jobs ADD COLUMN paused INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE jobs ADD COLUMN worktree INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE tasks ADD COLUMN requires_approval INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE jobs ADD COLUMN worktree_paths TEXT",
    "ALTER TABLE tasks ADD COLUMN human_task INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE tasks ADD COLUMN start_date TIMESTAMP DEFAULT NULL",
    "ALTER TABLE tasks ADD COLUMN due_date TIMESTAMP DEFAULT NULL",
    "ALTER TABLE tasks ADD COLUMN recurrence_interval_days INTEGER DEFAULT NULL",
    "ALTER TABLE jobs ADD COLUMN pause_reason TEXT DEFAULT NULL",
    "ALTER TABLE tasks ADD COLUMN critical INTEGER NOT NULL DEFAULT 0",
]


def _init_schema(conn, is_postgres: bool = False) -> None:
    global _migrations_run
    # Always run CREATE TABLE / INDEX — safe, idempotent, needed for new tables
    for statement in DDL.strip().split(";"):
        s = statement.strip()
        if s:
            conn.execute(s)
    conn.commit()
    # ALTER TABLE migrations only run once per process to avoid DDL lock contention
    if not _migrations_run:
        if is_postgres:
            # Short lock timeout so DDL never hangs waiting for idle TUI transactions
            try:
                conn.execute("SET lock_timeout = '3s'")
                conn.commit()
            except Exception:
                conn.rollback()
        for migration in _MIGRATIONS:
            try:
                if is_postgres:
                    migration = migration.replace("ADD COLUMN ", "ADD COLUMN IF NOT EXISTS ")
                conn.execute(migration)
                conn.commit()
            except Exception:
                conn.rollback()
        if is_postgres:
            try:
                conn.execute("RESET lock_timeout")
                conn.commit()
            except Exception:
                conn.rollback()
        _migrations_run = True


def get_connection(url: str, readonly: bool = False):
    """
    Return a database connection for the given URL.

    - postgresql:// or postgres:// → psycopg (PostgreSQL)
    - anything else                → SQLite file path (or :memory: for tests)

    Pass readonly=True to skip schema initialisation (for read-only consumers
    like the watch command that must not compete for DDL write locks).
    """
    if url.startswith("postgresql://") or url.startswith("postgres://"):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError:
            raise ImportError(
                "psycopg is required for PostgreSQL support. "
                "Install it with: pip install 'naxe[postgres]'"
            )
        conn = psycopg.connect(url, row_factory=dict_row, autocommit=False)
        if not readonly:
            _init_schema(conn, is_postgres=True)
        return conn

    # SQLite path
    raw = sqlite3.connect(url, check_same_thread=False)
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA journal_mode=WAL")
    raw.execute("PRAGMA busy_timeout=5000")
    raw.execute("PRAGMA synchronous=NORMAL")
    raw.execute("PRAGMA foreign_keys=ON")
    conn = _SQLiteConnection(raw)
    if not readonly:
        _init_schema(conn)
    return conn
