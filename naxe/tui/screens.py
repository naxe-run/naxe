from __future__ import annotations

import json
from datetime import datetime, timezone

from rich.markup import escape
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Static, Tab, Tabs, Tree
from textual.widgets.tree import TreeNode
from textual import on

from naxe.schema import TaskStatus
from naxe.tui.client import NaxeClient
from naxe.tui.theme import (
    _C_PENDING, _C_RUNNING, _C_COMPLETE, _C_FAILED, _C_CANCELLED,
    _C_HUMAN, _C_APPROVAL, _C_JOB_DONE, _C_JOB_DEAD, _C_JOB_ACTIVE,
    _STATUS_STYLE, _STATUS_SYMBOL,
)
from naxe.tui.widgets import (
    ConfirmModal, PromptModal, TextEditorModal, AddTaskModal, EditTaskModal,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fmt_ts(ts: str | None) -> str:
    if not ts:
        return "—"
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ts)


def _relative_time(ts: str | None) -> str:
    if not ts:
        return "—"
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        s = int(delta.total_seconds())
        if s < 60:
            return f"{s}s ago"
        if s < 3600:
            return f"{s // 60}m ago"
        if s < 86400:
            return f"{s // 3600}h ago"
        return f"{s // 86400}d ago"
    except Exception:
        return str(ts)


def _fmt_due_date(ts: str | None) -> tuple[str, bool]:
    if not ts:
        return "", False
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        is_overdue = dt < datetime.now(timezone.utc)
        label = dt.strftime("%b ") + str(dt.day)
        return label, is_overdue
    except Exception:
        return str(ts), False


def _compute_display_status(task: dict, blocked_ids: set, now_iso: str) -> str:
    if task["id"] in blocked_ids:
        return "waiting_on"
    if task.get("start_date") and task["start_date"] > now_iso:
        return "scheduled"
    if task["status"] == TaskStatus.PENDING:
        return "next_action"
    return task["status"]


def _progress_bar(pct: int, width: int = 16) -> str:
    filled = round(pct / 100 * width)
    return "━" * filled + "╌" * (width - filled)


def _task_label(task: dict) -> Text:
    status = task["status"]
    display_status = task.get("display_status", status)
    is_human = bool(task.get("human_task"))
    req_approval = bool(task.get("approval_gate"))

    if is_human and status == TaskStatus.PENDING:
        symbol, style = "☻", _C_PENDING
    elif is_human and status == TaskStatus.AWAITING_APPROVAL:
        symbol, style = "☻", _C_HUMAN
    elif req_approval and status == TaskStatus.PENDING:
        symbol, style = "⧗", _C_PENDING
    else:
        symbol = _STATUS_SYMBOL.get(display_status, "●")
        style = _STATUS_STYLE.get(display_status, "")

    t = Text(overflow="ellipsis", no_wrap=True)
    if task.get("critical"):
        t.append("! ", style=_C_FAILED)
    t.append(f"{symbol} ", style=style)
    t.append(task["name"], style=style)
    t.append(f"  [{task['id'][:8]}]", style="dim")
    if task.get("owner_agent_id"):
        t.append(f"  @{task['owner_agent_id']}", style="dim")
    if status == TaskStatus.IN_PROGRESS and task.get("progress"):
        p = int(task["progress"])
        t.append(f"  [{_progress_bar(p, 8)}] {p}%", style=_C_RUNNING)
    due_label, overdue = _fmt_due_date(task.get("due_date"))
    if due_label:
        t.append(f"  {due_label}", style=_C_FAILED if overdue else "dim")
    n = task.get("recurrence_interval_days")
    if n:
        t.append(f"  ↻{n}d", style="dim")
    return t


def _render_task_detail(task: dict | None, events: list[dict]) -> str:
    if task is None:
        return "[dim]Navigate to a task to view its details.[/dim]"

    status = task["status"]
    is_human = bool(task.get("human_task"))

    if is_human and status == TaskStatus.AWAITING_APPROVAL:
        sym, sty, status_label = "☻", _C_HUMAN, "HUMAN TASK"
    elif is_human and status == TaskStatus.PENDING:
        sym, sty, status_label = "☻", _C_PENDING, "PENDING"
    else:
        sym = _STATUS_SYMBOL.get(status, "●")
        sty = _STATUS_STYLE.get(status, "")
        status_label = status.replace("_", " ").upper()

    lines: list[str] = []
    lines.append(f"[bold]{escape(task['name'])}[/bold]")
    lines.append(f"[dim]{task['id']}[/dim]")
    lines.append("")

    badge = f"[{sty}]{sym} {status_label}[/{sty}]"
    if not is_human and task.get("approval_gate"):
        badge += f"  [{_C_APPROVAL}]⧗ approval required[/{_C_APPROVAL}]"
    lines.append(badge)

    if status == TaskStatus.IN_PROGRESS and task.get("progress"):
        p = int(task["progress"])
        lines.append(f"[{_C_RUNNING}] [{_progress_bar(p, 20)}] {p}%[/{_C_RUNNING}]")

    lines.append("")

    if task.get("description"):
        lines.append("[bold underline]Description[/bold underline]")
        lines.append(escape(task["description"]))
        lines.append("")

    def row(key: str, val: str) -> str:
        return f"[dim]{key}:[/dim]  {val}"

    display_status = task.get("display_status")
    if display_status and display_status != task["status"]:
        lines.append(row("State", display_status.replace("_", " ")))

    if task.get("owner_agent_id"):
        lines.append(row("Agent", f"[{_C_RUNNING}]{escape(task['owner_agent_id'])}[/{_C_RUNNING}]"))
    if task.get("priority") is not None:
        p_val = int(task["priority"])
        p_color = _C_COMPLETE if p_val > 50 else _C_FAILED if p_val < 50 else "dim"
        lines.append(row("Priority", f"[{p_color}]{p_val}[/{p_color}]"))
    if task.get("critical"):
        lines.append(row("Critical", f"[{_C_FAILED}]yes[/{_C_FAILED}]"))
    if task.get("repo"):
        lines.append(row("Repo", escape(str(task["repo"]))))
    if task.get("duration_minutes"):
        lines.append(row("Est. duration", f"{task['duration_minutes']} min"))
    if task.get("resources"):
        try:
            res = (
                json.loads(task["resources"])
                if isinstance(task["resources"], str)
                else task["resources"]
            )
            lines.append(row("Resources", escape(", ".join(res))))
        except Exception:
            lines.append(row("Resources", escape(str(task["resources"]))))
    rc = int(task.get("retry_count") or 0)
    mr = int(task.get("max_retries") or 0)
    if rc or mr:
        lines.append(row("Retries", f"{rc}/{mr}"))
    if task.get("approved_by"):
        lines.append(row("Approved by", escape(str(task["approved_by"]))))
    lines.append(row("Created", _fmt_ts(task.get("created_at"))))
    lines.append(row("Updated", _fmt_ts(task.get("updated_at"))))
    due_label, overdue = _fmt_due_date(task.get("due_date"))
    if due_label:
        due_str = f"[{_C_FAILED}]{due_label} (overdue)[/{_C_FAILED}]" if overdue else due_label
        lines.append(row("Due", due_str))
    n = task.get("recurrence_interval_days")
    if n:
        lines.append(row("Recurrence", f"every {n} days"))

    if task.get("approval_notes"):
        lines.append("")
        lines.append("[bold underline]Notes[/bold underline]")
        lines.append(escape(task["approval_notes"]))

    if task.get("input"):
        lines.append("")
        lines.append("[bold underline]Input[/bold underline]")
        inp = str(task["input"])
        if len(inp) > 800:
            inp = inp[:800] + "…"
        lines.append(f"[dim]{escape(inp)}[/dim]")

    if task.get("output"):
        lines.append("")
        lines.append("[bold underline]Output[/bold underline]")
        out = str(task["output"])
        if len(out) > 800:
            out = out[:800] + "…"
        lines.append(f"[dim]{escape(out)}[/dim]")

    if task.get("recent_comments"):
        lines.append("")
        lines.append("[bold underline]Feedback[/bold underline]")
        for c in task["recent_comments"]:
            ts = _fmt_ts(c.get("created_at"))
            author = c.get("author_id", "")
            lines.append(f"[dim]{ts}  @{escape(author)}[/dim]")
            lines.append(escape(c.get("content", "")))

    if events:
        lines.append("")
        lines.append("[bold underline]Audit Trail[/bold underline]")
        _ev_styles: dict[str, str] = {
            "completed":          _C_COMPLETE,
            "failed":             _C_FAILED,
            "rejected":           _C_FAILED,
            "claimed":            _C_RUNNING,
            "created":            "dim",
            "approved":           _C_COMPLETE,
            "approval_requested": _C_APPROVAL,
            "awaiting_approval":  _C_HUMAN,
            "cancelled":          _C_CANCELLED,
            "reclaimed":          _C_PENDING,
            "requeued":           _C_APPROVAL,
            "retried":            "yellow",
        }
        for ev in events[-12:]:
            ts = _fmt_ts(ev.get("timestamp"))
            etype = ev.get("event_type", "")
            agent = ev.get("agent_id") or ""
            ev_sty = _ev_styles.get(etype, "dim")
            line = f"[{ev_sty}]{escape(etype)}[/{ev_sty}]  [dim]{ts}"
            if agent:
                line += f"  @{escape(agent)}"
            line += "[/dim]"
            lines.append(line)

    return "\n".join(lines)


def _render_job_summary(job: dict | None, counts: dict[str, int]) -> str:
    if job is None:
        return "[dim]Select a job to view its summary.[/dim]"

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

    lines: list[str] = []
    lines.append(f"[bold]{escape(job['name'])}[/bold]")
    lines.append(f"[dim]{job['id']}[/dim]")
    lines.append("")

    badge = f"[{sty}]{sym} {status.upper()}[/{sty}]"
    if job.get("paused"):
        badge += f"  [{_C_RUNNING}]⏸ PAUSED[/{_C_RUNNING}]"
    if job.get("worktree"):
        badge += "  [dim]⎇ worktree[/dim]"
    lines.append(badge)
    lines.append("")

    total = sum(v for k, v in counts.items() if k != "human_waiting")
    if total == 0:
        lines.append("[dim]No tasks.[/dim]")
    else:
        done = counts.get("completed", 0)
        running = counts.get("in_progress", 0)
        pending = counts.get("pending", 0)
        failed = counts.get("failed", 0)
        cancelled = counts.get("cancelled", 0)
        human_waiting = counts.get("human_waiting", 0)
        approval_waiting = counts.get("awaiting_approval", 0) - human_waiting

        lines.append(f"[bold]{done}/{total}[/bold] [dim]tasks completed[/dim]")
        if running:
            lines.append(f"  [{_C_RUNNING}]◉ {running} running[/{_C_RUNNING}]")
        if human_waiting:
            lines.append(f"  [{_C_HUMAN}]☻ {human_waiting} human task{'s' if human_waiting != 1 else ''} waiting[/{_C_HUMAN}]")
        if approval_waiting:
            lines.append(f"  [{_C_APPROVAL}]⧗ {approval_waiting} awaiting approval[/{_C_APPROVAL}]")
        if pending:
            lines.append(f"  [{_C_PENDING}]○ {pending} pending[/{_C_PENDING}]")
        if failed:
            lines.append(f"  [{_C_FAILED}]✗ {failed} failed[/{_C_FAILED}]")
        if cancelled:
            lines.append(f"  [{_C_CANCELLED}]⊘ {cancelled} cancelled[/{_C_CANCELLED}]")

    lines.append("")

    def row(key: str, val: str) -> str:
        return f"[dim]{key}:[/dim]  {val}"

    if job.get("paused") and job.get("pause_reason"):
        lines.append(row("Pause reason", escape(str(job["pause_reason"]))))
    if job.get("max_workers"):
        lines.append(row("Max workers", str(job["max_workers"])))
    lines.append(row("Created", _fmt_ts(job.get("created_at"))))

    if job.get("output"):
        lines.append("")
        lines.append("[bold underline]Output[/bold underline]")
        out = str(job["output"])
        if len(out) > 600:
            out = out[:600] + "…"
        lines.append(f"[dim]{escape(out)}[/dim]")

    return "\n".join(lines)


# ── Human Actions Screen ──────────────────────────────────────────────────────

class HumanActionsScreen(Screen):
    CSS = """
    #actions-header {
        height: 3;
        background: $panel;
        padding: 0 2;
        border-bottom: solid $primary-darken-2;
        content-align: left middle;
        color: $text;
    }

    #actions-main {
        height: 1fr;
        layout: horizontal;
    }

    #actions-list-panel {
        width: 3fr;
        border-right: solid $panel-lighten-1;
        layout: vertical;
    }

    #actions-table {
        height: 1fr;
        background: $surface;
    }

    #actions-detail-panel {
        width: 2fr;
        background: $panel-darken-2;
        layout: vertical;
    }

    #actions-detail-scroll {
        height: 1fr;
        padding: 1 2;
    }

    #actions-detail {
        height: auto;
    }
    """

    BINDINGS = [
        Binding("escape", "go_back", "Back"),
        Binding("a", "approve_task", "Approve"),
        Binding("d", "deny_task", "Deny"),
        Binding("r", "manual_refresh", "Refresh"),
        Binding("q", "app.quit", "Quit"),
        Binding("n", "noop", show=False),
        Binding("e", "noop", show=False),
        Binding("x", "noop", show=False),
        Binding("f", "return_with_feedback", "Return"),
        Binding("enter", "noop", show=False),
    ]

    def action_noop(self) -> None:
        pass

    def compose(self) -> ComposeResult:
        yield Static("", id="actions-header")
        yield Horizontal(
            Vertical(
                DataTable(id="actions-table", cursor_type="row", zebra_stripes=True),
                id="actions-list-panel",
            ),
            Vertical(
                ScrollableContainer(
                    Static("[dim]Select a task to view details.[/dim]", id="actions-detail"),
                    id="actions-detail-scroll",
                ),
                id="actions-detail-panel",
            ),
            id="actions-main",
        )
        yield Footer()

    def on_mount(self) -> None:
        self._selected_task_id: str | None = None
        self._selected_job_id: str | None = None

        table = self.query_one("#actions-table", DataTable)
        table.add_column("", width=2, key="status")
        table.add_column("Task", key="name")
        table.add_column("Job", width=24, key="job")
        table.add_column("Type", width=10, key="type")
        table.add_column("Age", width=10, key="age")

        self._load()
        self.set_interval(3.0, self._load)
        table.focus()

    def _load(self) -> None:
        self.app.client.rollback()
        tasks = self.app.client.fetch_human_actions()

        table = self.query_one("#actions-table", DataTable)
        anchor_id = self._selected_task_id
        table.clear()

        for task in tasks:
            is_human = bool(task.get("human_task"))
            sym = "☻" if is_human else "⧗"
            status_cell = Text(sym + " ", style=_C_HUMAN if is_human else _C_APPROVAL)
            type_cell = Text("human" if is_human else "approval", style="dim")
            table.add_row(
                status_cell,
                escape(task["name"]),
                escape(task["job_name"]),
                type_cell,
                _relative_time(task.get("created_at")),
                key=task["id"],
            )

        if anchor_id:
            for i, rk in enumerate(table.rows.keys()):
                if str(rk.value) == anchor_id:
                    table.move_cursor(row=i)
                    break

        total = len(tasks)
        self.query_one("#actions-header", Static).update(
            f"[bold]Actions[/bold]  [dim]·  {total} task{'s' if total != 1 else ''} awaiting human attention[/dim]"
        )

        if self._selected_task_id:
            task = self.app.client.get_task(self._selected_task_id)
            if task:
                events = self.app.client.get_task_events(self._selected_task_id)
                self.query_one("#actions-detail", Static).update(_render_task_detail(task, events))
        self.app.client.rollback()

    @on(DataTable.RowHighlighted)
    def on_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        task_id = str(event.row_key.value)
        self._selected_task_id = task_id
        task = self.app.client.get_task(task_id)
        if task:
            self._selected_job_id = task["job_id"]
            events = self.app.client.get_task_events(task_id)
            self.query_one("#actions-detail", Static).update(_render_task_detail(task, events))

    @on(DataTable.RowSelected)
    def on_row_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()

    def _selected_task(self) -> dict | None:
        if not self._selected_task_id:
            return None
        return self.app.client.get_task(self._selected_task_id)

    def action_approve_task(self) -> None:
        task = self._selected_task()
        if task is None:
            self.notify("No task selected", severity="warning")
            return
        label = "Complete" if bool(task.get("human_task")) else "Approve"
        self.app.push_screen(
            ConfirmModal(f"{label} '{task['name']}'?"),
            lambda confirmed: self._on_approve_confirmed(confirmed, task["id"]),
        )

    def _on_approve_confirmed(self, confirmed: bool, task_id: str) -> None:
        if not confirmed:
            return
        try:
            self.app.client.approve_task(task_id)
            self._selected_task_id = None
            self._load()
            self.notify("Task approved", severity="information")
        except Exception as e:
            self.notify(str(e), title="Error", severity="error")

    def action_deny_task(self) -> None:
        task = self._selected_task()
        if task is None:
            self.notify("No task selected", severity="warning")
            return
        self.app.push_screen(
            PromptModal(f"Reject '{task['name']}'?", placeholder="Reason (required)"),
            lambda reason: self._on_deny_confirmed(reason, task["id"]),
        )

    def _on_deny_confirmed(self, reason: str | None, task_id: str) -> None:
        if reason is None:
            return
        if not reason:
            self.notify("A reason is required to reject a task", severity="error")
            return
        try:
            self.app.client.reject_task(task_id, reason)
            self._selected_task_id = None
            self._load()
            self.notify("Task rejected", severity="warning")
        except Exception as e:
            self.notify(str(e), title="Error", severity="error")

    def action_return_with_feedback(self) -> None:
        task = self._selected_task()
        if task is None:
            self.notify("No task selected", severity="warning")
            return
        task_id = task["id"]
        self.app.push_screen(
            TextEditorModal("Return with Feedback — describe what needs to change", ""),
            lambda text: self._on_return_confirmed(text, task_id),
        )

    def _on_return_confirmed(self, feedback: str | None, task_id: str) -> None:
        if feedback is None or feedback == "":
            return
        if not feedback.strip():
            self.notify("Feedback is required", severity="error")
            return
        try:
            self.app.client.return_task(task_id, feedback)
            self._selected_task_id = None
            self._load()
            self.notify("Task returned for revision")
        except Exception as e:
            self.notify(str(e), title="Error", severity="error")

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def action_manual_refresh(self) -> None:
        self._load()


# ── Job Detail Screen ─────────────────────────────────────────────────────────

_TASK_FILTERS: list[tuple[str, str]] = [
    ("all", "All"),
    ("pending", "Pending"),
    ("in_progress", "In Progress"),
    ("completed", "Completed"),
    ("failed", "Failed"),
    ("awaiting_approval", "Awaiting"),
    ("cancelled", "Cancelled"),
]
_TASK_FILTER_KEYS = [k for k, _ in _TASK_FILTERS]


class JobDetailScreen(Screen):
    CSS = """
    Tabs {
        height: 3;
        background: $panel-darken-1;
        border-bottom: solid $panel;
    }

    #detail-header {
        height: 3;
        background: $panel;
        padding: 0 2;
        border-bottom: solid $primary-darken-2;
        content-align: left middle;
        color: $text;
    }

    #detail-main {
        height: 1fr;
        layout: horizontal;
    }

    #tree-panel {
        width: 3fr;
        border-right: solid $panel-lighten-1;
        layout: vertical;
    }

    #task-tree {
        height: 1fr;
        padding: 0 1;
        background: $surface;
    }

    #task-panel {
        width: 2fr;
        background: $panel-darken-2;
        layout: vertical;
    }

    #task-scroll {
        height: 1fr;
        padding: 1 2;
    }

    #task-detail {
        height: auto;
    }
    """

    BINDINGS = [
        Binding("escape", "go_back", "Back"),
        Binding("r", "manual_refresh", "Refresh"),
        Binding("f", "cycle_filter", "Cycle Filter"),
        Binding("n", "new_task", "New Task"),
        Binding("e", "edit_task_modal", "Edit Task"),
        Binding("x", "cancel_task", "Cancel Task"),
        Binding("p", "pause_resume_job", "Pause/Resume"),
        Binding("E", "expand_all", "Expand All"),
        Binding("c", "collapse_all", "Collapse All"),
        Binding("q", "app.quit", "Quit"),
    ]

    task_filter: reactive[str] = reactive("all")

    def __init__(self, job_id: str) -> None:
        super().__init__()
        self._job_id = job_id
        self._current_job: dict | None = None
        self._selected_task_id: str | None = None

    def compose(self) -> ComposeResult:
        yield Static("", id="detail-header")
        yield Tabs(
            *[Tab(label, id=f"tf-{key}") for key, label in _TASK_FILTERS],
            id="task-filter-tabs",
        )
        yield Horizontal(
            Vertical(Tree("Tasks", id="task-tree"), id="tree-panel"),
            Vertical(
                ScrollableContainer(
                    Static("[dim]Navigate to a task to view its details.[/dim]", id="task-detail"),
                    id="task-scroll",
                ),
                id="task-panel",
            ),
            id="detail-main",
        )
        yield Footer()

    def on_mount(self) -> None:
        tree = self.query_one("#task-tree", Tree)
        tree.show_root = False
        self._load()
        self.set_interval(2.0, self._refresh)
        tree.focus()

    def _load(self) -> None:
        self.app.client.rollback()
        self._current_job = self.app.client.get_job(self._job_id)
        self._update_header()
        self._rebuild_tree()
        self._refresh_detail()
        if not self._selected_task_id:
            tree = self.query_one("#task-tree", Tree)
            node = self._first_task_node(tree.root)
            if node is not None and isinstance(node.data, dict):
                self._show_task(node.data)
        self.app.client.rollback()

    def _refresh(self) -> None:
        self._load()

    def _update_header(self) -> None:
        header = self.query_one("#detail-header", Static)
        job = self._current_job
        if job is None:
            header.update("[dim]Job not found.[/dim]")
            return

        status = job.get("status", "active")
        if status == TaskStatus.COMPLETED:
            sym, sty = "●", _C_JOB_DONE
        elif status == TaskStatus.CANCELLED:
            sym, sty = "⊘", _C_JOB_DEAD
        elif job.get("paused"):
            sym, sty = "⏸", _C_RUNNING
        else:
            sym, sty = "◉", _C_JOB_ACTIVE

        tags = ""
        if job.get("paused"):
            tags += f"  [{_C_RUNNING}]⏸ PAUSED[/{_C_RUNNING}]"
        if job.get("paused") and job.get("pause_reason"):
            tags += f"  [dim]— {escape(str(job['pause_reason']))}[/dim]"
        if job.get("worktree"):
            tags += "  [dim]⎇ worktree[/dim]"

        tasks = self.app.client.get_tasks_for_job(job["id"])
        total = len(tasks)
        counts: dict[str, int] = {}
        for t in tasks:
            counts[t["status"]] = counts.get(t["status"], 0) + 1

        human_waiting = sum(1 for t in tasks if t["status"] == TaskStatus.AWAITING_APPROVAL and t.get("human_task"))
        approval_waiting = sum(1 for t in tasks if t["status"] == TaskStatus.AWAITING_APPROVAL and not t.get("human_task"))

        parts: list[str] = []
        if counts.get("in_progress"):
            parts.append(f"[{_C_RUNNING}]{counts['in_progress']} running[/{_C_RUNNING}]")
        if human_waiting:
            parts.append(f"[{_C_HUMAN}]☻ {human_waiting} human waiting[/{_C_HUMAN}]")
        if approval_waiting:
            parts.append(f"[{_C_APPROVAL}]⧗ {approval_waiting} awaiting[/{_C_APPROVAL}]")
        if counts.get("failed"):
            parts.append(f"[{_C_FAILED}]{counts['failed']} failed[/{_C_FAILED}]")
        done = counts.get("completed", 0)
        parts.append(f"[dim]{done}/{total} done[/dim]")
        summary = "  ·  ".join(parts)

        header.update(
            f"[dim]← Esc[/dim]  "
            f"[{sty}]{sym}[/{sty}]  "
            f"[bold]{escape(job['name'])}[/bold]  "
            f"[dim]{job['id'][:8]}[/dim]"
            f"{tags}"
            f"  │  {summary}"
        )

    def _rebuild_tree(self) -> None:
        tree = self.query_one("#task-tree", Tree)
        collapsed: set[str] = set()
        self._collect_collapsed(tree.root, collapsed)
        tree.clear()
        job = self._current_job
        if not job:
            return

        tasks = self.app.client.get_tasks_for_job(job["id"])
        now_iso = datetime.now(timezone.utc).isoformat()
        if tasks:
            task_ids = [t["id"] for t in tasks]
            blocked_ids = self.app.client.get_blocked_task_ids(task_ids)
            for t in tasks:
                t["display_status"] = _compute_display_status(t, blocked_ids, now_iso)
        if not tasks:
            tree.root.expand()
            from rich.text import Text as RText
            tree.root.add_leaf(RText("No tasks yet.", style="dim"))
            return

        if self.task_filter == "all":
            self._add_tree_nodes(tree.root, tasks, collapsed)
        else:
            self._add_flat_nodes(tree.root, tasks, self.task_filter)
        tree.root.expand()

    def _add_tree_nodes(self, root: TreeNode, tasks: list[dict], collapsed: set[str]) -> None:
        task_map = {t["id"]: t for t in tasks}
        task_ids = list(task_map.keys())
        rows = self.app.client.get_dependency_edges(task_ids)

        children: dict[str, list[str]] = {tid: [] for tid in task_ids}
        has_parent: set[str] = set()
        for row in rows:
            parent_id = row["depends_on_task_id"]
            child_id = row["task_id"]
            children.setdefault(parent_id, []).append(child_id)
            has_parent.add(child_id)

        roots = [tid for tid in task_ids if tid not in has_parent]

        def add_node(parent: TreeNode, task_id: str) -> None:
            task = task_map.get(task_id)
            if not task:
                return
            kids = children.get(task_id, [])
            label = _task_label(task)
            if kids:
                node = parent.add(label, data=task)
                if task_id not in collapsed:
                    node.expand()
                for child_id in kids:
                    add_node(node, child_id)
            else:
                parent.add_leaf(label, data=task)

        for root_id in roots:
            add_node(root, root_id)

    def _add_flat_nodes(self, root: TreeNode, tasks: list[dict], status_filter: str) -> None:
        matching = [t for t in tasks if t["status"] == status_filter]
        if not matching:
            label = status_filter.replace("_", " ")
            from rich.text import Text as RText
            root.add_leaf(RText(f"No {label} tasks.", style="dim"))
            return
        for task in matching:
            root.add_leaf(_task_label(task), data=task)

    def _collect_collapsed(self, node: TreeNode, result: set[str]) -> None:
        if isinstance(node.data, dict) and not node.is_expanded:
            result.add(node.data["id"])
        for child in node.children:
            self._collect_collapsed(child, result)

    def _find_node_by_task_id(self, node: TreeNode, task_id: str) -> TreeNode | None:
        if isinstance(node.data, dict) and node.data.get("id") == task_id:
            return node
        for child in node.children:
            found = self._find_node_by_task_id(child, task_id)
            if found:
                return found
        return None

    def _first_task_node(self, node: TreeNode) -> TreeNode | None:
        for child in node.children:
            if isinstance(child.data, dict):
                return child
            found = self._first_task_node(child)
            if found:
                return found
        return None

    def _refresh_detail(self) -> None:
        if not self._selected_task_id:
            return
        task = self.app.client.get_task(self._selected_task_id)
        if task:
            events = self.app.client.get_task_events(self._selected_task_id)
            self.query_one("#task-detail", Static).update(_render_task_detail(task, events))

    def _show_task(self, task: dict) -> None:
        self._selected_task_id = task["id"]
        events = self.app.client.get_task_events(task["id"])
        self.query_one("#task-detail", Static).update(_render_task_detail(task, events))

    @on(Tree.NodeHighlighted)
    def on_node_highlighted(self, event: Tree.NodeHighlighted) -> None:
        task = event.node.data
        if isinstance(task, dict):
            self._show_task(task)
        else:
            self._selected_task_id = None
            self.query_one("#task-detail", Static).update(
                "[dim]Navigate to a task to view its details.[/dim]"
            )

    @on(Tabs.TabActivated)
    def on_tab_activated(self, event: Tabs.TabActivated) -> None:
        if event.tab is None:
            return
        tab_id = event.tab.id or ""
        if not tab_id.startswith("tf-"):
            return
        key = tab_id[len("tf-"):]
        if key in _TASK_FILTER_KEYS and key != self.task_filter:
            self.task_filter = key
            self._rebuild_tree()

    def action_new_task(self) -> None:
        tasks = self.app.client.get_tasks_for_job(self._job_id)
        self.app.push_screen(AddTaskModal(self._job_id, tasks), self._on_task_added)

    def _on_task_added(self, result: dict | None) -> None:
        if result is None:
            return
        try:
            self.app.client.add_tasks([result], self._job_id)
            self._load()
        except Exception as e:
            self.notify(str(e), title="Error adding task", severity="error")

    def action_edit_task_modal(self) -> None:
        task_id = self._selected_task_id
        if not task_id:
            tree = self.query_one("#task-tree", Tree)
            if tree.cursor_node and isinstance(tree.cursor_node.data, dict):
                task_id = tree.cursor_node.data["id"]
        if not task_id:
            self.notify("No task selected", severity="warning")
            return
        task = self.app.client.get_task(task_id)
        if task is None:
            return
        editable = task["status"] == TaskStatus.PENDING or (
            task["status"] == TaskStatus.AWAITING_APPROVAL and bool(task.get("human_task"))
        )
        if not editable:
            self.notify(f"Cannot edit a {task['status'].replace('_', ' ')} task", severity="warning")
            return
        other_tasks = self.app.client.get_tasks_for_job(self._job_id)
        self.app.push_screen(EditTaskModal(task, other_tasks), self._on_task_edited)

    def _on_task_edited(self, result: dict | None) -> None:
        if result is None:
            return
        try:
            self.app.client.edit_task(self._selected_task_id, result)
            self._load()
        except Exception as e:
            self.notify(str(e), title="Error editing task", severity="error")

    def action_cancel_task(self) -> None:
        task_id = self._selected_task_id
        if not task_id:
            tree = self.query_one("#task-tree", Tree)
            if tree.cursor_node and isinstance(tree.cursor_node.data, dict):
                task_id = tree.cursor_node.data["id"]
        if not task_id:
            self.notify("No task selected", severity="warning")
            return
        task = self.app.client.get_task(task_id)
        if task is None:
            return
        cancellable = task["status"] in (TaskStatus.PENDING, TaskStatus.IN_PROGRESS) or (
            task["status"] == TaskStatus.AWAITING_APPROVAL and bool(task.get("human_task"))
        )
        if not cancellable:
            self.notify(f"Cannot cancel a {task['status'].replace('_', ' ')} task", severity="warning")
            return
        self.app.push_screen(
            ConfirmModal(f"Cancel task '{task['name']}'?"),
            lambda confirmed: self._on_cancel_confirmed(confirmed, task_id),
        )

    def _on_cancel_confirmed(self, confirmed: bool, task_id: str) -> None:
        if not confirmed:
            return
        try:
            self.app.client.cancel_task(task_id)
            self._selected_task_id = None
            self._load()
        except Exception as e:
            self.notify(str(e), title="Error cancelling task", severity="error")

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def action_manual_refresh(self) -> None:
        self._load()

    def action_pause_resume_job(self) -> None:
        job = self._current_job
        if job is None:
            return
        if job.get("paused"):
            self.app.client.resume_job(job["id"])
            self._load()
            self.notify("Job resumed")
        else:
            self.app.push_screen(
                PromptModal("Pause reason (optional)"),
                lambda reason: self._on_pause_confirmed(reason),
            )

    def _on_pause_confirmed(self, reason: str | None) -> None:
        if reason is None:
            return
        job = self._current_job
        if job is None:
            return
        self.app.client.pause_job(job["id"], reason=reason or None)
        self._load()
        self.notify("Job paused")

    def action_cycle_filter(self) -> None:
        idx = _TASK_FILTER_KEYS.index(self.task_filter)
        next_key = _TASK_FILTER_KEYS[(idx + 1) % len(_TASK_FILTER_KEYS)]
        tabs = self.query_one("#task-filter-tabs", Tabs)
        tabs.active = f"tf-{next_key}"

    def action_expand_all(self) -> None:
        tree = self.query_one("#task-tree", Tree)
        self._set_all_expanded(tree.root, expand=True)

    def action_collapse_all(self) -> None:
        tree = self.query_one("#task-tree", Tree)
        for child in tree.root.children:
            self._set_all_expanded(child, expand=False)

    def _set_all_expanded(self, node: TreeNode, *, expand: bool) -> None:
        if expand:
            node.expand()
        else:
            node.collapse()
        for child in node.children:
            self._set_all_expanded(child, expand=expand)
