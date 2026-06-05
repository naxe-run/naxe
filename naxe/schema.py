import sqlite3
from enum import StrEnum


class TaskStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    AWAITING_APPROVAL = "awaiting_approval"


class JobStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


DDL = """
CREATE TABLE IF NOT EXISTS jobs (
    id                        TEXT PRIMARY KEY,
    name                      TEXT NOT NULL,
    created_at                TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status                    TEXT NOT NULL DEFAULT 'active',
    output                    TEXT,
    paused                    INTEGER NOT NULL DEFAULT 0,
    pause_reason              TEXT DEFAULT NULL,
    worktree                  INTEGER NOT NULL DEFAULT 0,
    worktree_paths            TEXT,
    max_workers               INTEGER DEFAULT NULL,
    heartbeat_timeout_seconds INTEGER NOT NULL DEFAULT 300
);

CREATE TABLE IF NOT EXISTS tasks (
    id                       TEXT PRIMARY KEY,
    job_id                   TEXT NOT NULL REFERENCES jobs(id),
    name                     TEXT NOT NULL,
    description              TEXT,
    status                   TEXT NOT NULL DEFAULT 'pending',
    owner_agent_id           TEXT,
    duration_minutes         INTEGER,
    output                   TEXT,
    max_retries              INTEGER NOT NULL DEFAULT 0,
    retry_count              INTEGER NOT NULL DEFAULT 0,
    input                    TEXT,
    resources                TEXT,
    repo                     TEXT,
    progress                 INTEGER NOT NULL DEFAULT 0,
    priority                 INTEGER NOT NULL DEFAULT 50,
    approved_by              TEXT,
    approval_notes           TEXT,
    approval_round           INTEGER NOT NULL DEFAULT 0,
    requires_approval        INTEGER NOT NULL DEFAULT 0,
    human_task               INTEGER NOT NULL DEFAULT 0,
    last_heartbeat_at        TIMESTAMP,
    created_at               TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at               TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    start_date               TIMESTAMP DEFAULT NULL,
    due_date                 TIMESTAMP DEFAULT NULL,
    recurrence_interval_days INTEGER DEFAULT NULL,
    critical                 INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS dependencies (
    task_id            TEXT NOT NULL REFERENCES tasks(id),
    depends_on_task_id TEXT NOT NULL REFERENCES tasks(id),
    PRIMARY KEY (task_id, depends_on_task_id)
);

CREATE TABLE IF NOT EXISTS job_dependencies (
    job_id            TEXT NOT NULL REFERENCES jobs(id),
    depends_on_job_id TEXT NOT NULL REFERENCES jobs(id),
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
    id         TEXT PRIMARY KEY,
    task_id    TEXT NOT NULL REFERENCES tasks(id),
    job_id     TEXT NOT NULL REFERENCES jobs(id),
    event_type TEXT NOT NULL,
    agent_id   TEXT,
    timestamp  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    details    TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_job_status ON tasks(job_id, status);
CREATE INDEX IF NOT EXISTS idx_task_events_task_id ON task_events(task_id);
CREATE INDEX IF NOT EXISTS idx_task_events_job_id ON task_events(job_id);

CREATE TABLE IF NOT EXISTS task_comments (
    id             TEXT PRIMARY KEY,
    task_id        TEXT NOT NULL REFERENCES tasks(id),
    job_id         TEXT NOT NULL REFERENCES jobs(id),
    author_id      TEXT NOT NULL,
    author_type    TEXT NOT NULL,
    content        TEXT NOT NULL,
    approval_round INTEGER NOT NULL DEFAULT 0,
    created_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_task_comments_task_id ON task_comments(task_id);
CREATE INDEX IF NOT EXISTS idx_task_comments_task_round ON task_comments(task_id, approval_round);

CREATE TABLE IF NOT EXISTS agents (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    key_hash   TEXT NOT NULL UNIQUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    active     INTEGER NOT NULL DEFAULT 1
);
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


def _init_schema(conn, is_postgres: bool = False) -> None:
    for statement in DDL.strip().split(";"):
        s = statement.strip()
        if s:
            conn.execute(s)
    conn.commit()


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
