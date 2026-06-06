from __future__ import annotations

from rich.markup import escape
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.reactive import reactive
from textual.widgets import DataTable, Footer, Static, Tab, Tabs
from textual import on

from naxe import store
from naxe.schema import get_connection, TaskStatus, JobStatus
from naxe.config import resolve_db_url, resolve_theme
from naxe.tui.theme import (
    _NAXE_THEME, _NAXE_BOLD_THEME,
    _apply_theme_palette, _rebuild_status_style,
    _C_PENDING, _C_RUNNING, _C_FAILED, _C_HUMAN, _C_APPROVAL,
    _C_JOB_ACTIVE, _C_JOB_DONE, _C_JOB_DEAD,
)
from naxe.tui.widgets import (
    AddTaskModal, ConfirmModal, CreateJobModal, EditJobModal, PromptModal,
)
from naxe.tui.screens import (
    HumanActionsScreen, JobDetailScreen, _render_job_summary, _relative_time,
)

DB_URL = resolve_db_url()

_JOB_FILTERS: list[tuple[str, str]] = [
    ("open", "Open"),
    ("all", "All"),
    ("completed", "Completed"),
    ("cancelled", "Cancelled"),
]
_JOB_FILTER_KEYS = [k for k, _ in _JOB_FILTERS]


def _batch_task_counts(conn, job_ids: list[str]) -> dict[str, dict[str, int]]:
    """Single query: returns {job_id: {status: count, 'human_waiting': count}}."""
    if not job_ids:
        return {}
    placeholders = ",".join(["%s"] * len(job_ids))
    rows = conn.execute(
        f"SELECT job_id, status, COUNT(*) as cnt FROM tasks "
        f"WHERE job_id IN ({placeholders}) GROUP BY job_id, status",
        job_ids,
    ).fetchall()
    result: dict[str, dict[str, int]] = {}
    for row in rows:
        result.setdefault(row["job_id"], {})[row["status"]] = row["cnt"]

    human_rows = conn.execute(
        f"SELECT job_id, COUNT(*) as cnt FROM tasks "
        f"WHERE job_id IN ({placeholders}) AND status = 'awaiting_approval' AND human_task = 1 "
        f"GROUP BY job_id",
        job_ids,
    ).fetchall()
    for row in human_rows:
        result.setdefault(row["job_id"], {})["human_waiting"] = row["cnt"]

    return result


def _fetch_jobs(conn, status_filter: str) -> list[dict]:
    if status_filter == "open":
        return [
            dict(r) for r in conn.execute(
                "SELECT * FROM jobs WHERE status NOT IN ('completed', 'cancelled') ORDER BY created_at DESC"
            ).fetchall()
        ]
    if status_filter == "completed":
        return [
            dict(r) for r in conn.execute(
                "SELECT * FROM jobs WHERE status = 'completed' ORDER BY created_at DESC LIMIT 200"
            ).fetchall()
        ]
    if status_filter == "cancelled":
        return [
            dict(r) for r in conn.execute(
                "SELECT * FROM jobs WHERE status = 'cancelled' ORDER BY created_at DESC LIMIT 200"
            ).fetchall()
        ]
    return [
        dict(r) for r in conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT 500"
        ).fetchall()
    ]


def _job_status_cell(job: dict) -> Text:
    status = job.get("status", "active")
    if status == TaskStatus.COMPLETED:
        sym, sty = "●", _C_JOB_DONE
    elif status == TaskStatus.CANCELLED:
        sym, sty = "⊘", _C_JOB_DEAD
    elif job.get("paused"):
        sym, sty = "⏸", _C_RUNNING
    elif status == "blocked":
        sym, sty = "⊟", _C_PENDING
    else:
        sym, sty = "◉", _C_JOB_ACTIVE
    t = Text(no_wrap=True)
    t.append(sym + " ", style=sty)
    return t


def _job_tasks_cell(counts: dict[str, int]) -> Text:
    status_keys = {"completed", "in_progress", "pending", "failed", "cancelled", "awaiting_approval"}
    total = sum(v for k, v in counts.items() if k in status_keys)
    if total == 0:
        return Text("—", style="dim")
    done = counts.get("completed", 0)
    running = counts.get("in_progress", 0)
    failed = counts.get("failed", 0)
    human_waiting = counts.get("human_waiting", 0)
    approval_waiting = counts.get("awaiting_approval", 0) - human_waiting

    t = Text()
    if done == total:
        t.append(f"{done}/{total}", style=_C_JOB_DONE)
        return t
    t.append(f"{done}/{total}", style="dim")
    if running:
        t.append(f"  {running}◉ ", style=_C_RUNNING)
    if human_waiting:
        t.append(f"  {human_waiting}☻ ", style=_C_HUMAN)
    if approval_waiting:
        t.append(f"  {approval_waiting}⧗ ", style=_C_APPROVAL)
    if failed:
        t.append(f"  {failed}✗ ", style=_C_FAILED)
    return t


class NaxeUI(App):
    """Interactive TUI for browsing Naxe jobs and tasks."""

    TITLE = "NAXE"

    CSS = """
    Screen {
        background: $surface;
    }

    Tabs {
        height: 3;
        background: $panel-darken-1;
        border-bottom: solid $panel;
    }

    #list-header {
        height: 3;
        background: $panel;
        padding: 0 2;
        border-bottom: solid $primary-darken-2;
        content-align: left middle;
        color: $text;
    }

    #list-main {
        height: 1fr;
        layout: horizontal;
    }

    #jobs-panel {
        width: 3fr;
        border-right: solid $panel-lighten-1;
        layout: vertical;
    }

    #jobs-table {
        height: 1fr;
        background: $surface;
    }

    #summary-panel {
        width: 2fr;
        background: $panel-darken-2;
        layout: vertical;
    }

    #summary-scroll {
        height: 1fr;
        padding: 1 2;
    }

    #job-summary {
        height: auto;
    }
    """

    BINDINGS = [
        Binding("enter", "open_job", "Open Job"),
        Binding("a", "actions", "Actions"),
        Binding("n", "new_job", "New Job"),
        Binding("t", "quick_task", "Quick Task"),
        Binding("e", "edit_job", "Edit Job"),
        Binding("x", "cancel_job", "Cancel Job"),
        Binding("p", "pause_resume_job", "Pause/Resume"),
        Binding("r", "refresh", "Refresh"),
        Binding("f", "cycle_filter", "Cycle Filter"),
        Binding("q", "quit", "Quit"),
    ]

    job_filter: reactive[str] = reactive("open")

    def compose(self) -> ComposeResult:
        yield Static("", id="list-header")
        yield Tabs(
            *[Tab(label, id=f"jf-{key}") for key, label in _JOB_FILTERS],
            id="job-filter-tabs",
        )
        yield Horizontal(
            Vertical(
                DataTable(id="jobs-table", cursor_type="row", zebra_stripes=True),
                id="jobs-panel",
            ),
            Vertical(
                ScrollableContainer(
                    Static("[dim]Select a job to view its summary.[/dim]", id="job-summary"),
                    id="summary-scroll",
                ),
                id="summary-panel",
            ),
            id="list-main",
        )
        yield Footer()

    def on_mount(self) -> None:
        self.register_theme(_NAXE_THEME)
        self.register_theme(_NAXE_BOLD_THEME)
        initial_theme = resolve_theme()
        self.theme = initial_theme
        theme_obj = self.get_theme(initial_theme)
        if theme_obj:
            _apply_theme_palette(theme_obj)
            _rebuild_status_style()

        self._conn = get_connection(DB_URL, readonly=False)
        self._selected_job_id: str | None = None

        table = self.query_one("#jobs-table", DataTable)
        table.add_column("", width=2, key="status")
        table.add_column("Name", key="name")
        table.add_column("Tasks", width=22, key="tasks")
        table.add_column("Age", width=10, key="age")

        self._load()
        self.set_interval(3.0, self._refresh)
        table.focus()

    def _load(self) -> None:
        self._conn.rollback()
        jobs = _fetch_jobs(self._conn, self.job_filter)
        counts_by_job = _batch_task_counts(self._conn, [j["id"] for j in jobs])

        table = self.query_one("#jobs-table", DataTable)
        anchor_id = self._selected_job_id
        table.clear()

        for job in jobs:
            counts = counts_by_job.get(job["id"], {})
            table.add_row(
                _job_status_cell(job),
                escape(job["name"]),
                _job_tasks_cell(counts),
                _relative_time(job.get("created_at")),
                key=job["id"],
            )

        if anchor_id:
            row_keys = list(table.rows.keys())
            for i, rk in enumerate(row_keys):
                if str(rk.value) == anchor_id:
                    table.move_cursor(row=i)
                    break

        total = len(jobs)
        label = self.job_filter if self.job_filter != "all" else "total"
        self.query_one("#list-header", Static).update(
            f"[bold]Jobs[/bold]  [dim]·  {total} {label}[/dim]"
            f"  [dim]↑↓ navigate  Enter open[/dim]"
        )
        self._conn.rollback()

    def _refresh(self) -> None:
        self._load()
        if self._selected_job_id:
            job = store.get_job(self._conn, self._selected_job_id)
            self.query_one("#job-summary", Static).update(_render_job_summary(self._conn, job))
        self._conn.rollback()

    @on(DataTable.RowHighlighted)
    def on_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        job_id = str(event.row_key.value)
        self._selected_job_id = job_id
        job = store.get_job(self._conn, job_id)
        self.query_one("#job-summary", Static).update(_render_job_summary(self._conn, job))

    @on(DataTable.RowSelected)
    def on_row_selected(self, event: DataTable.RowSelected) -> None:
        job_id = str(event.row_key.value)
        self.push_screen(JobDetailScreen(job_id))

    @on(Tabs.TabActivated)
    def on_tab_activated(self, event: Tabs.TabActivated) -> None:
        if event.tab is None:
            return
        tab_id = event.tab.id or ""
        if not tab_id.startswith("jf-"):
            return
        key = tab_id[len("jf-"):]
        if key in _JOB_FILTER_KEYS and key != self.job_filter:
            self.job_filter = key
            self._selected_job_id = None
            self.query_one("#job-summary", Static).update("[dim]Select a job to view its summary.[/dim]")
            self._load()

    def action_open_job(self) -> None:
        if self._selected_job_id:
            self.push_screen(JobDetailScreen(self._selected_job_id))

    def watch_theme(self, theme_name: str) -> None:
        theme = self.get_theme(theme_name)
        if theme is not None:
            _apply_theme_palette(theme)
            _rebuild_status_style()

    def action_actions(self) -> None:
        if not isinstance(self.screen, HumanActionsScreen):
            self.push_screen(HumanActionsScreen())

    def action_quick_task(self) -> None:
        self.push_screen(AddTaskModal(), self._on_quick_task_created)

    def _on_quick_task_created(self, task: dict | None) -> None:
        if task is None:
            return
        try:
            result = store.add_tasks(self._conn, tasks=[task])
            self._conn.commit()
            self._load()
            self.push_screen(JobDetailScreen(result["job_id"]))
        except Exception as e:
            self.notify(str(e), title="Error creating quick task", severity="error")

    def action_new_job(self) -> None:
        self.push_screen(CreateJobModal(), self._on_job_created)

    def _on_job_created(self, result: dict | None) -> None:
        if result is None:
            return
        try:
            store.create_job(self._conn, **result)
            self._conn.commit()
            self._load()
        except Exception as e:
            self.notify(str(e), title="Error creating job", severity="error")

    def action_edit_job(self) -> None:
        if not self._selected_job_id:
            self.notify("No job selected", severity="warning")
            return
        job = store.get_job(self._conn, self._selected_job_id)
        if job is None:
            return
        self.push_screen(EditJobModal(job), self._on_job_edited)

    def _on_job_edited(self, result: str | None) -> None:
        if result is None:
            return
        try:
            store.edit_job(self._conn, self._selected_job_id, result)
            self._conn.commit()
            self._load()
            job = store.get_job(self._conn, self._selected_job_id)
            self.query_one("#job-summary", Static).update(_render_job_summary(self._conn, job))
        except Exception as e:
            self.notify(str(e), title="Error editing job", severity="error")

    def action_cancel_job(self) -> None:
        if not self._selected_job_id:
            self.notify("No job selected", severity="warning")
            return
        job = store.get_job(self._conn, self._selected_job_id)
        if job is None:
            return
        if job["status"] == JobStatus.CANCELLED:
            self.notify("Job is already cancelled", severity="warning")
            return
        self.push_screen(
            ConfirmModal(f"Cancel job '{job['name']}' and all its tasks?"),
            self._on_cancel_job_confirmed,
        )

    def _on_cancel_job_confirmed(self, confirmed: bool) -> None:
        if not confirmed:
            return
        try:
            result = store.cancel_job(self._conn, self._selected_job_id)
            self._conn.commit()
            n = result["tasks_cancelled"]
            self.notify(f"Job cancelled ({n} task{'s' if n != 1 else ''} cancelled)", severity="warning")
            self._load()
        except Exception as e:
            self.notify(str(e), title="Error cancelling job", severity="error")

    def action_refresh(self) -> None:
        self._load()

    def action_pause_resume_job(self) -> None:
        if not self._selected_job_id:
            self.notify("No job selected", severity="warning")
            return
        job = store.get_job(self._conn, self._selected_job_id)
        if job is None:
            return
        if job.get("paused"):
            store.resume_job(self._conn, self._selected_job_id)
            self._conn.commit()
            self._load()
            self.notify("Job resumed")
        else:
            self.push_screen(
                PromptModal("Pause reason (optional)"),
                lambda reason: self._on_pause_confirmed(reason),
            )

    def _on_pause_confirmed(self, reason: str | None) -> None:
        if reason is None:
            return
        store.pause_job(self._conn, self._selected_job_id, reason=reason or None)
        self._conn.commit()
        self._load()
        job = store.get_job(self._conn, self._selected_job_id)
        self.query_one("#job-summary", Static).update(_render_job_summary(self._conn, job))
        self.notify("Job paused")

    def action_cycle_filter(self) -> None:
        idx = _JOB_FILTER_KEYS.index(self.job_filter)
        next_key = _JOB_FILTER_KEYS[(idx + 1) % len(_JOB_FILTER_KEYS)]
        tabs = self.query_one("#job-filter-tabs", Tabs)
        tabs.active = f"jf-{next_key}"


def main() -> None:
    NaxeUI().run()


if __name__ == "__main__":
    main()
