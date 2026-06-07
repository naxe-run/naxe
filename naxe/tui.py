"""naxe ui — interactive TUI for browsing Naxe jobs and tasks."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from rich.markup import escape
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.reactive import reactive
from textual.screen import ModalScreen, Screen
from textual.theme import Theme
from textual.widgets import Button, Checkbox, DataTable, Footer, Input, Label, Static, Tab, Tabs, TextArea, Tree
from textual.widgets.tree import TreeNode
from textual import on

from naxe import store
from naxe.config import resolve_db_url, resolve_theme
from naxe.schema import get_connection

DB_URL = resolve_db_url()

# ── Theme ─────────────────────────────────────────────────────────────────────

_NAXE_THEME = Theme(
    name="naxe",
    dark=True,
    # Naxe: warm dark backgrounds, muted blood reds, earthy golds
    background="#1c1c1c",
    surface="#252525",
    panel="#2e2a27",
    boost="#3c3836",
    primary="#b5351a",
    secondary="#b07a00",
    accent="#7c6f64",
    warning="#c47f17",
    error="#8b0000",
    success="#5a7a3a",
    foreground="#d5c4a1",
)

_NAXE_BOLD_THEME = Theme(
    name="naxe-bold",
    dark=True,
    # Naxe Bold: DOOM marine suit + logo on fire — dark military green, zero mercy
    background="#0a0a0a",
    surface="#141414",
    panel="#1e1a18",
    boost="#2e2820",
    primary="#ff3300",          # DOOM fire red — screaming
    secondary="#ff9900",        # molten orange — ammo burning
    accent="#ff6600",           # raw fire
    warning="#ffcc00",          # nuclear yellow
    error="#ff0000",            # pure red — no subtlety
    success="#00ff66",          # neon green — 100% health
    foreground="#fff5cc",       # hot white with a warm tint
)

MODAL_CSS = """
CreateJobModal, EditJobModal, AddTaskModal, EditTaskModal, ConfirmModal, PromptModal {
    align: center middle;
    background: $background 60%;
}

TextEditorModal {
    align: center middle;
    background: $background 60%;
}

.modal-dialog {
    background: $panel;
    border: solid $primary;
    padding: 1 2;
    width: 70;
    height: 20;
}

ConfirmModal .modal-dialog {
    height: 10;
}

EditJobModal .modal-dialog {
    height: 14;
}

.modal-dialog-tall {
    background: $panel;
    border: solid $primary;
    padding: 1 2;
    width: 70;
    height: 32;
}

.editor-dialog {
    background: $panel;
    border: solid $primary;
    padding: 1 2;
    width: 90;
    height: 36;
}

.modal-title {
    text-style: bold;
    margin-bottom: 1;
    color: $text;
}

.modal-field-label {
    color: $text-muted;
    margin-top: 1;
}

.modal-buttons {
    layout: horizontal;
    height: 3;
    margin-top: 1;
    align-horizontal: right;
}

.modal-buttons Button {
    margin-left: 1;
}

.modal-hint {
    color: $text-muted;
    margin-bottom: 1;
    height: auto;
}

.text-field-btn {
    height: 3;
    width: 1fr;
    background: $surface;
    border: tall $primary-darken-2;
    content-align: left middle;
    text-align: left;
}

AddTaskModal ScrollableContainer, EditTaskModal ScrollableContainer {
    height: 1fr;
}

AddTaskModal Input, EditTaskModal Input {
    height: 3;
}

TextEditorModal TextArea {
    height: 1fr;
}
"""


# ── Color palette — built from the active theme at runtime ───────────────────

_C_PENDING    = "dim"
_C_RUNNING    = "bold yellow"
_C_COMPLETE   = "green"
_C_FAILED     = "bold red"
_C_CANCELLED  = "dim red"
_C_HUMAN      = "bold magenta"
_C_APPROVAL   = "dim"
_C_JOB_ACTIVE = "bold cyan"
_C_JOB_DONE   = "green"
_C_JOB_DEAD   = "dim red"


def _apply_theme_palette(theme: "Theme") -> None:
    """Recompute module-level color constants from the active Textual theme."""
    global _C_PENDING, _C_RUNNING, _C_COMPLETE, _C_FAILED, _C_CANCELLED
    global _C_HUMAN, _C_APPROVAL, _C_JOB_ACTIVE, _C_JOB_DONE, _C_JOB_DEAD

    p   = theme.primary  or "#b5351a"
    w   = theme.warning  or "#c47f17"
    e   = theme.error    or "#8b0000"
    s   = theme.success  or "#5a7a3a"
    ac  = theme.accent   or "#7c6f64"

    _C_PENDING    = f"dim {ac}"
    _C_RUNNING    = f"bold {w}"
    _C_COMPLETE   = s
    _C_FAILED     = f"bold {e}"
    _C_CANCELLED  = f"dim {e}"
    _C_HUMAN      = f"bold {p}"
    _C_APPROVAL   = f"{ac} italic"
    _C_JOB_ACTIVE = f"bold {w}"
    _C_JOB_DONE   = s
    _C_JOB_DEAD   = f"dim {e}"


# ── Status styling ────────────────────────────────────────────────────────────

_STATUS_STYLE: dict[str, str] = {
    "pending":           _C_PENDING,
    "in_progress":       _C_RUNNING,
    "completed":         _C_COMPLETE,
    "failed":            _C_FAILED,
    "cancelled":         _C_CANCELLED,
    "awaiting_approval": _C_HUMAN,
    "next_action":       "dim",
    "waiting_on":        _C_PENDING,
    "scheduled":         "dim",
}


def _rebuild_status_style() -> None:
    _STATUS_STYLE["pending"]           = _C_PENDING
    _STATUS_STYLE["in_progress"]       = _C_RUNNING
    _STATUS_STYLE["completed"]         = _C_COMPLETE
    _STATUS_STYLE["failed"]            = _C_FAILED
    _STATUS_STYLE["cancelled"]         = _C_CANCELLED
    _STATUS_STYLE["awaiting_approval"] = _C_HUMAN
    _STATUS_STYLE["next_action"]       = "dim"
    _STATUS_STYLE["waiting_on"]        = _C_PENDING
    _STATUS_STYLE["scheduled"]         = "dim"

_STATUS_SYMBOL: dict[str, str] = {
    "pending": "○",
    "in_progress": "◉",
    "completed": "●",
    "failed": "✗",
    "cancelled": "⊘",
    "awaiting_approval": "⧗",
    "next_action": "▶",
    "waiting_on":  "⊟",
    "scheduled":   "⏱",
}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _fmt_ts(ts: str | None) -> str:
    if not ts:
        return "—"
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ts)


def _relative_time(ts: str | None) -> str:
    if not ts:
        return "—"
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - dt.astimezone(timezone.utc)
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
    """Returns (formatted date string, is_overdue). Returns ('', False) when null."""
    if not ts:
        return "", False
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        is_overdue = dt.astimezone(timezone.utc) < datetime.now(timezone.utc)
        label = dt.strftime("%b ") + str(dt.day)
        return label, is_overdue
    except Exception:
        return str(ts), False


def _compute_display_status(task: dict, blocked_ids: set, now_iso: str) -> str:
    if task["id"] in blocked_ids:
        return "waiting_on"
    if task.get("start_date") and task["start_date"] > now_iso:
        return "scheduled"
    if task["status"] == "pending":
        return "next_action"
    return task["status"]


def _progress_bar(pct: int, width: int = 16) -> str:
    filled = round(pct / 100 * width)
    return "━" * filled + "╌" * (width - filled)


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


def _task_label(task: dict) -> Text:
    status = task["status"]
    display_status = task.get("display_status", status)
    is_human = bool(task.get("human_task"))
    req_approval = bool(task.get("requires_approval"))

    if is_human and status == "pending":
        symbol, style = "☻", _C_PENDING
    elif is_human and status == "awaiting_approval":
        symbol, style = "☻", _C_HUMAN
    elif req_approval and status == "pending":
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
    if status == "in_progress" and task.get("progress"):
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

    if is_human and status == "awaiting_approval":
        sym, sty, status_label = "☻", _C_HUMAN, "HUMAN TASK"
    elif is_human and status == "pending":
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
    if not is_human and task.get("requires_approval"):
        badge += f"  [{_C_APPROVAL}]⧗ approval required[/{_C_APPROVAL}]"
    lines.append(badge)

    if status == "in_progress" and task.get("progress"):
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

    if events:
        lines.append("")
        lines.append("[bold underline]Audit Trail[/bold underline]")
        _ev_styles: dict[str, str] = {
            "completed":         _C_COMPLETE,
            "failed":            _C_FAILED,
            "rejected":          _C_FAILED,
            "claimed":           _C_RUNNING,
            "created":           "dim",
            "approved":          _C_COMPLETE,
            "approval_requested": _C_APPROVAL,
            "awaiting_approval": _C_HUMAN,
            "cancelled":         _C_CANCELLED,
            "reclaimed":         _C_PENDING,
            "requeued":          _C_APPROVAL,
            "retried": "yellow",
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


def _render_job_summary(conn, job: dict | None) -> str:
    if job is None:
        return "[dim]Select a job to view its summary.[/dim]"

    status = job.get("status", "active")
    if status == "completed":
        sym, sty = "●", _C_JOB_DONE
    elif status == "cancelled":
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

    tasks = store.get_tasks_for_job(conn, job["id"])
    total = len(tasks)
    if total == 0:
        lines.append("[dim]No tasks.[/dim]")
    else:
        counts: dict[str, int] = {}
        for t in tasks:
            counts[t["status"]] = counts.get(t["status"], 0) + 1

        done = counts.get("completed", 0)
        running = counts.get("in_progress", 0)
        pending = counts.get("pending", 0)
        failed = counts.get("failed", 0)
        cancelled = counts.get("cancelled", 0)
        human_waiting = sum(1 for t in tasks if t["status"] == "awaiting_approval" and t.get("human_task"))
        approval_waiting = sum(1 for t in tasks if t["status"] == "awaiting_approval" and not t.get("human_task"))

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


# ── Modal helpers ────────────────────────────────────────────────────────────

def _format_task_reference_list(tasks: list[dict]) -> str:
    if not tasks:
        return "[dim]No existing tasks.[/dim]"
    lines = ["[dim]Existing tasks (for depends_on):[/dim]"]
    for t in tasks:
        status = t.get("status", "")
        sym = _STATUS_SYMBOL.get(status, "○")
        lines.append(f"[dim]  {t['id'][:8]}  {sym} {escape(t['name'])}[/dim]")
    return "\n".join(lines)


def _parse_int_field(value: str, field_name: str) -> tuple[int | None, str | None]:
    """Returns (parsed_int, error_message). error_message is None on success."""
    v = value.strip()
    if not v:
        return None, None
    try:
        return int(v), None
    except ValueError:
        return None, f"{field_name} must be a whole number"


def _parse_list_field(value: str) -> list[str]:
    return [x.strip() for x in value.split(",") if x.strip()]


# ── Modals ────────────────────────────────────────────────────────────────────

class ConfirmModal(ModalScreen):
    CSS = MODAL_CSS

    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-dialog"):
            yield Static(self._message, classes="modal-title")
            with Horizontal(classes="modal-buttons"):
                yield Button("No", id="btn-no", variant="default")
                yield Button("Yes", id="btn-yes", variant="error")

    def on_mount(self) -> None:
        self.query_one("#btn-no", Button).focus()

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "btn-yes")

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(False)


class PromptModal(ModalScreen):
    """Single-input prompt modal. Dismisses with the string value or None on cancel."""

    CSS = MODAL_CSS

    def __init__(self, title: str, placeholder: str = "") -> None:
        super().__init__()
        self._title = title
        self._placeholder = placeholder

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-dialog"):
            yield Static(self._title, classes="modal-title")
            yield Input(placeholder=self._placeholder, id="prompt-input")
            with Horizontal(classes="modal-buttons"):
                yield Button("Cancel", id="btn-cancel", variant="default")
                yield Button("OK", id="btn-ok", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#prompt-input", Input).focus()

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-cancel":
            self.dismiss(None)
        else:
            self.dismiss(self.query_one("#prompt-input", Input).value.strip())

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(None)


class TextEditorModal(ModalScreen):
    """Full-screen pop-out for editing a multi-line text field."""

    CSS = MODAL_CSS

    def __init__(self, title: str, initial_text: str = "") -> None:
        super().__init__()
        self._title = title
        self._initial_text = initial_text

    def compose(self) -> ComposeResult:
        with Vertical(classes="editor-dialog"):
            yield Static(self._title, classes="modal-title")
            yield TextArea(self._initial_text, id="editor-area")
            with Horizontal(classes="modal-buttons"):
                yield Button("Cancel", id="btn-cancel", variant="default")
                yield Button("Save", id="btn-save", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#editor-area", TextArea).focus()

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-cancel":
            self.dismiss(None)
        else:
            self.dismiss(self.query_one("#editor-area", TextArea).text)

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(None)

class CreateJobModal(ModalScreen):
    CSS = MODAL_CSS

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-dialog"):
            yield Static("Create Job", classes="modal-title")
            yield Label("Name *", classes="modal-field-label")
            yield Input(placeholder="Job name", id="job-name")
            yield Label("Max Workers (optional — leave blank for unlimited)", classes="modal-field-label")
            yield Input(placeholder="e.g. 4", id="job-max-workers")
            yield Checkbox("Use worktree isolation", id="job-worktree")
            with Horizontal(classes="modal-buttons"):
                yield Button("Cancel", id="btn-cancel", variant="default")
                yield Button("Create", id="btn-submit", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#job-name", Input).focus()

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-cancel":
            self.dismiss(None)
            return
        name = self.query_one("#job-name", Input).value.strip()
        if not name:
            self.notify("Name is required", severity="error")
            return
        max_workers_raw = self.query_one("#job-max-workers", Input).value
        max_workers, err = _parse_int_field(max_workers_raw, "Max Workers")
        if err:
            self.notify(err, severity="error")
            return
        worktree = self.query_one("#job-worktree", Checkbox).value
        self.dismiss({"name": name, "max_workers": max_workers, "worktree": worktree})

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(None)


class EditJobModal(ModalScreen):
    CSS = MODAL_CSS

    def __init__(self, job: dict) -> None:
        super().__init__()
        self._job = job

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-dialog"):
            yield Static("Edit Job", classes="modal-title")
            yield Label("Name *", classes="modal-field-label")
            yield Input(value=self._job["name"], id="job-name")
            with Horizontal(classes="modal-buttons"):
                yield Button("Cancel", id="btn-cancel", variant="default")
                yield Button("Save", id="btn-submit", variant="primary")

    def on_mount(self) -> None:
        inp = self.query_one("#job-name", Input)
        inp.focus()
        inp.cursor_position = len(inp.value)

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-cancel":
            self.dismiss(None)
            return
        name = self.query_one("#job-name", Input).value.strip()
        if not name:
            self.notify("Name is required", severity="error")
            return
        self.dismiss(name)

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(None)


class AddTaskModal(ModalScreen):
    CSS = MODAL_CSS

    def __init__(self, job_id: str | None = None, existing_tasks: list[dict] | None = None) -> None:
        super().__init__()
        self._job_id = job_id
        self._existing_tasks = existing_tasks or []
        self._description: str = ""
        self._input_text: str = ""

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-dialog-tall"):
            yield Static("Add Task", classes="modal-title")
            yield Label("Name *", classes="modal-field-label")
            yield Input(placeholder="Task name", id="t-name")
            if self._existing_tasks:
                yield Static(
                    _format_task_reference_list(self._existing_tasks),
                    classes="modal-hint",
                )
            with ScrollableContainer(can_focus=False):
                yield Label("Description", classes="modal-field-label")
                yield Button("", id="btn-edit-desc", classes="text-field-btn")
                yield Checkbox("Human task", id="t-human-task")
                yield Checkbox("Requires approval", id="t-requires-approval")
                yield Checkbox("Critical", id="t-critical")
                yield Label("Priority (0–100, default 50)", classes="modal-field-label")
                yield Input(value="50", id="t-priority")
                yield Label("Duration (minutes, optional)", classes="modal-field-label")
                yield Input(placeholder="e.g. 30", id="t-duration")
                yield Label("Resources (comma-separated, optional)", classes="modal-field-label")
                yield Input(placeholder="e.g. gpu, db-write", id="t-resources")
                yield Label("Repo (optional)", classes="modal-field-label")
                yield Input(placeholder="e.g. my-repo", id="t-repo")
                yield Label("Max Retries (default 0)", classes="modal-field-label")
                yield Input(value="0", id="t-max-retries")
                yield Label("Input (optional)", classes="modal-field-label")
                yield Button("", id="btn-edit-input", classes="text-field-btn")
                yield Label("Depends On (task IDs, comma-separated)", classes="modal-field-label")
                yield Input(placeholder="e.g. abc12345, def67890", id="t-depends-on")
                yield Label("Start Date (optional, ISO 8601)", classes="modal-field-label")
                yield Input(placeholder="e.g. 2026-06-10T08:00:00Z", id="t-start-date")
                yield Label("Due Date (optional, YYYY-MM-DD)", classes="modal-field-label")
                yield Input(placeholder="e.g. 2026-06-30", id="t-due-date")
                yield Label("Recurrence (days, optional)", classes="modal-field-label")
                yield Input(placeholder="e.g. 7", id="t-recurrence")
            yield Static("[dim]Ctrl+Enter to submit[/dim]", classes="modal-hint")
            with Horizontal(classes="modal-buttons"):
                yield Button("Cancel", id="btn-cancel", variant="default")
                yield Button("Add Task", id="btn-submit", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#t-name", Input).focus()

    def _update_preview(self, widget_id: str, text: str) -> None:
        if not text.strip():
            content = "[dim](empty — click Edit to add)[/dim]"
        else:
            first_line = text.split("\n")[0][:57]
            suffix = "…" if len(text) > 57 or "\n" in text else ""
            content = escape(first_line) + suffix
        self.query_one(f"#{widget_id}", Button).label = content

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-cancel":
            self.dismiss(None)
            return
        if event.button.id == "btn-edit-desc":
            self.app.push_screen(
                TextEditorModal("Edit Description", self._description),
                lambda text: self._on_desc_edited(text),
            )
            return
        if event.button.id == "btn-edit-input":
            self.app.push_screen(
                TextEditorModal("Edit Input", self._input_text),
                lambda text: self._on_input_edited(text),
            )
            return

        name = self.query_one("#t-name", Input).value.strip()
        if not name:
            self.notify("Name is required", severity="error")
            return

        priority_raw = self.query_one("#t-priority", Input).value
        priority, err = _parse_int_field(priority_raw, "Priority")
        if err:
            self.notify(err, severity="error")
            return
        if priority is None:
            priority = 50
        if not (0 <= priority <= 100):
            self.notify("Priority must be between 0 and 100", severity="error")
            return

        duration, err = _parse_int_field(self.query_one("#t-duration", Input).value, "Duration")
        if err:
            self.notify(err, severity="error")
            return

        max_retries, err = _parse_int_field(self.query_one("#t-max-retries", Input).value, "Max Retries")
        if err:
            self.notify(err, severity="error")
            return
        if max_retries is None:
            max_retries = 0

        task: dict = {"name": name, "priority": priority, "max_retries": max_retries}

        if self._description.strip():
            task["description"] = self._description.strip()

        resources = _parse_list_field(self.query_one("#t-resources", Input).value)
        if resources:
            task["resources"] = resources

        repo = self.query_one("#t-repo", Input).value.strip()
        if repo:
            task["repo"] = repo

        if duration is not None:
            task["duration_minutes"] = duration

        if self._input_text.strip():
            task["input"] = self._input_text.strip()

        depends_on = _parse_list_field(self.query_one("#t-depends-on", Input).value)
        if depends_on:
            task["depends_on"] = depends_on

        if self.query_one("#t-human-task", Checkbox).value:
            task["human_task"] = True

        if self.query_one("#t-requires-approval", Checkbox).value:
            task["requires_approval"] = True

        if self.query_one("#t-critical", Checkbox).value:
            task["critical"] = True

        start_date = self.query_one("#t-start-date", Input).value.strip()
        if start_date:
            task["start_date"] = start_date

        due_date = self.query_one("#t-due-date", Input).value.strip()
        if due_date:
            task["due_date"] = due_date

        recurrence, err = _parse_int_field(self.query_one("#t-recurrence", Input).value, "Recurrence")
        if err:
            self.notify(err, severity="error")
            return
        if recurrence is not None:
            task["recurrence_interval_days"] = recurrence

        self.dismiss(task)

    def _on_desc_edited(self, text: str | None) -> None:
        if text is not None:
            self._description = text
            self._update_preview("btn-edit-desc", text)

    def _on_input_edited(self, text: str | None) -> None:
        if text is not None:
            self._input_text = text
            self._update_preview("btn-edit-input", text)

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(None)
        elif event.key == "ctrl+enter":
            self.query_one("#btn-submit", Button).press()


class EditTaskModal(ModalScreen):
    CSS = MODAL_CSS

    def __init__(self, task: dict, existing_tasks: list[dict]) -> None:
        super().__init__()
        self._naxe_task = task
        self._existing_tasks = [t for t in existing_tasks if t["id"] != task["id"]]
        self._description: str = task.get("description") or ""
        self._input_text: str = str(task.get("input") or "")

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-dialog-tall"):
            yield Static(f"Edit Task  [dim]{escape(self._naxe_task['name'])}[/dim]", classes="modal-title")
            yield Label("Name *", classes="modal-field-label")
            yield Input(value=self._naxe_task.get("name", ""), id="t-name")
            if self._existing_tasks:
                yield Static(
                    _format_task_reference_list(self._existing_tasks),
                    classes="modal-hint",
                )
            with ScrollableContainer(can_focus=False):
                yield Label("Description", classes="modal-field-label")
                yield Button(self._preview(self._description), id="btn-edit-desc", classes="text-field-btn")
                yield Label("Duration (minutes, optional)", classes="modal-field-label")
                yield Input(
                    value=str(self._naxe_task["duration_minutes"]) if self._naxe_task.get("duration_minutes") else "",
                    id="t-duration",
                )
                yield Label("Resources (comma-separated, optional)", classes="modal-field-label")
                yield Input(value=self._initial_resources(), id="t-resources")
                yield Label("Max Retries", classes="modal-field-label")
                yield Input(value=str(self._naxe_task.get("max_retries") or 0), id="t-max-retries")
                yield Label("Input (optional)", classes="modal-field-label")
                yield Button(self._preview(self._input_text), id="btn-edit-input", classes="text-field-btn")
                yield Label("Depends On (task IDs, comma-separated)", classes="modal-field-label")
                yield Input(id="t-depends-on")
                yield Label("Start Date (optional, ISO 8601)", classes="modal-field-label")
                yield Input(
                    value=self._naxe_task.get("start_date") or "",
                    placeholder="e.g. 2026-06-10T08:00:00Z",
                    id="t-start-date",
                )
                yield Label("Due Date (optional, YYYY-MM-DD)", classes="modal-field-label")
                yield Input(
                    value=self._naxe_task.get("due_date") or "",
                    placeholder="e.g. 2026-06-30",
                    id="t-due-date",
                )
                yield Label("Recurrence (days, optional)", classes="modal-field-label")
                yield Input(
                    value=str(self._naxe_task["recurrence_interval_days"]) if self._naxe_task.get("recurrence_interval_days") else "",
                    placeholder="e.g. 7",
                    id="t-recurrence",
                )
                yield Checkbox("Critical", id="t-critical", value=bool(self._naxe_task.get("critical")))
            with Horizontal(classes="modal-buttons"):
                yield Button("Cancel", id="btn-cancel", variant="default")
                yield Button("Save", id="btn-submit", variant="primary")

    @staticmethod
    def _preview(text: str) -> str:
        if not text.strip():
            return "[dim](empty — click Edit to add)[/dim]"
        first_line = text.split("\n")[0][:60]
        suffix = "…" if len(text) > 60 or "\n" in text else ""
        return escape(first_line) + suffix

    def _initial_resources(self) -> str:
        resources = self._naxe_task.get("resources")
        if not resources:
            return ""
        try:
            res_list = json.loads(resources) if isinstance(resources, str) else resources
            return ", ".join(res_list)
        except Exception:
            return str(resources)

    def on_mount(self) -> None:
        self.query_one("#t-name", Input).focus()

    def _update_preview(self, widget_id: str, text: str) -> None:
        self.query_one(f"#{widget_id}", Button).label = self._preview(text)

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-cancel":
            self.dismiss(None)
            return
        if event.button.id == "btn-edit-desc":
            self.app.push_screen(
                TextEditorModal("Edit Description", self._description),
                lambda text: self._on_desc_edited(text),
            )
            return
        if event.button.id == "btn-edit-input":
            self.app.push_screen(
                TextEditorModal("Edit Input", self._input_text),
                lambda text: self._on_input_edited(text),
            )
            return

        name = self.query_one("#t-name", Input).value.strip()
        if not name:
            self.notify("Name is required", severity="error")
            return

        duration, err = _parse_int_field(self.query_one("#t-duration", Input).value, "Duration")
        if err:
            self.notify(err, severity="error")
            return

        max_retries, err = _parse_int_field(self.query_one("#t-max-retries", Input).value, "Max Retries")
        if err:
            self.notify(err, severity="error")
            return

        updates: dict = {"name": name}
        updates["description"] = self._description.strip() or None
        updates["input"] = self._input_text.strip() or None

        resources = _parse_list_field(self.query_one("#t-resources", Input).value)
        updates["resources"] = resources or None

        depends_on = _parse_list_field(self.query_one("#t-depends-on", Input).value)
        if depends_on:
            updates["depends_on"] = depends_on

        if duration is not None:
            updates["duration_minutes"] = duration

        if max_retries is not None:
            updates["max_retries"] = max_retries

        start_date = self.query_one("#t-start-date", Input).value.strip()
        updates["start_date"] = start_date or None

        due_date = self.query_one("#t-due-date", Input).value.strip()
        updates["due_date"] = due_date or None

        recurrence, err = _parse_int_field(self.query_one("#t-recurrence", Input).value, "Recurrence")
        if err:
            self.notify(err, severity="error")
            return
        updates["recurrence_interval_days"] = recurrence  # None clears it

        updates["critical"] = bool(self.query_one("#t-critical", Checkbox).value)

        self.dismiss(updates)

    def _on_desc_edited(self, text: str | None) -> None:
        if text is not None:
            self._description = text
            self._update_preview("btn-edit-desc", text)

    def _on_input_edited(self, text: str | None) -> None:
        if text is not None:
            self._input_text = text
            self._update_preview("btn-edit-input", text)

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(None)


# ── Human Actions Screen ─────────────────────────────────────────────────────

def _fetch_human_actions(conn) -> list[dict]:
    """All awaiting_approval tasks across active jobs, ordered by priority then age."""
    rows = conn.execute(
        """SELECT t.*, j.name AS job_name
           FROM tasks t
           JOIN jobs j ON t.job_id = j.id
           WHERE t.status = 'awaiting_approval'
             AND j.status NOT IN ('cancelled', 'completed')
           ORDER BY t.priority DESC, t.created_at ASC"""
    ).fetchall()
    return [dict(r) for r in rows]


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
        Binding("f", "noop", show=False),
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
                    Static(
                        "[dim]Select a task to view details.[/dim]",
                        id="actions-detail",
                    ),
                    id="actions-detail-scroll",
                ),
                id="actions-detail-panel",
            ),
            id="actions-main",
        )
        yield Footer()

    def on_mount(self) -> None:
        self._conn = get_connection(DB_URL, readonly=False)
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
        self._conn.rollback()
        tasks = _fetch_human_actions(self._conn)

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
            task = store.get_task(self._conn, self._selected_task_id)
            if task:
                events = store.get_task_events(self._conn, self._selected_task_id)
                self.query_one("#actions-detail", Static).update(
                    _render_task_detail(task, events)
                )
        self._conn.rollback()

    @on(DataTable.RowHighlighted)
    def on_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        task_id = str(event.row_key.value)
        self._selected_task_id = task_id
        task = store.get_task(self._conn, task_id)
        if task:
            self._selected_job_id = task["job_id"]
            events = store.get_task_events(self._conn, task_id)
            self.query_one("#actions-detail", Static).update(
                _render_task_detail(task, events)
            )

    @on(DataTable.RowSelected)
    def on_row_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()

    def _selected_task(self) -> dict | None:
        if not self._selected_task_id:
            return None
        return store.get_task(self._conn, self._selected_task_id)

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
            result = store.approve_task(self._conn, task_id, approver_id="human")
            if result is None:
                self.notify("Could not approve task", severity="warning")
                return
            self._conn.commit()
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
            result = store.reject_task(self._conn, task_id, approver_id="human", reason=reason)
            if result is None:
                self.notify("Could not reject task", severity="warning")
                return
            self._conn.commit()
            self._selected_task_id = None
            self._load()
            self.notify("Task rejected", severity="warning")
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
            Vertical(
                Tree("Tasks", id="task-tree"),
                id="tree-panel",
            ),
            Vertical(
                ScrollableContainer(
                    Static(
                        "[dim]Navigate to a task to view its details.[/dim]",
                        id="task-detail",
                    ),
                    id="task-scroll",
                ),
                id="task-panel",
            ),
            id="detail-main",
        )
        yield Footer()

    def on_mount(self) -> None:
        self._conn = get_connection(DB_URL, readonly=False)
        tree = self.query_one("#task-tree", Tree)
        tree.show_root = False
        self._load()
        self.set_interval(2.0, self._refresh)
        tree.focus()

    # ── Data ──────────────────────────────────────────────────────────────────

    def _load(self) -> None:
        self._conn.rollback()
        self._current_job = store.get_job(self._conn, self._job_id)
        self._update_header()
        self._rebuild_tree()
        self._refresh_detail()
        self._conn.rollback()

    def _refresh(self) -> None:
        self._load()

    def _update_header(self) -> None:
        header = self.query_one("#detail-header", Static)
        job = self._current_job
        if job is None:
            header.update("[dim]Job not found.[/dim]")
            return

        status = job.get("status", "active")
        if status == "completed":
            sym, sty = "●", _C_JOB_DONE
        elif status == "cancelled":
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

        tasks = store.get_tasks_for_job(self._conn, job["id"])
        total = len(tasks)
        counts: dict[str, int] = {}
        for t in tasks:
            counts[t["status"]] = counts.get(t["status"], 0) + 1

        human_waiting = sum(1 for t in tasks if t["status"] == "awaiting_approval" and t.get("human_task"))
        approval_waiting = sum(1 for t in tasks if t["status"] == "awaiting_approval" and not t.get("human_task"))

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

    # ── Tree ──────────────────────────────────────────────────────────────────

    def _rebuild_tree(self) -> None:
        tree = self.query_one("#task-tree", Tree)

        collapsed: set[str] = set()
        self._collect_collapsed(tree.root, collapsed)

        tree.clear()
        job = self._current_job
        if not job:
            return

        tasks = store.get_tasks_for_job(self._conn, job["id"])
        now_iso = datetime.now(timezone.utc).isoformat()
        if tasks:
            task_ids = [t["id"] for t in tasks]
            placeholders = ",".join(["%s"] * len(task_ids))
            dep_rows = self._conn.execute(
                f"SELECT d.task_id FROM dependencies d "
                f"JOIN tasks dep ON dep.id = d.depends_on_task_id "
                f"WHERE d.task_id IN ({placeholders}) AND dep.status != 'completed'",
                task_ids,
            ).fetchall()
            blocked_ids = {r["task_id"] for r in dep_rows}
            for t in tasks:
                t["display_status"] = _compute_display_status(t, blocked_ids, now_iso)
        if not tasks:
            tree.root.expand()
            tree.root.add_leaf(Text("No tasks yet.", style="dim"))
            return

        if self.task_filter == "all":
            self._add_tree_nodes(tree.root, tasks, collapsed)
        else:
            self._add_flat_nodes(tree.root, tasks, self.task_filter)

        tree.root.expand()

    def _add_tree_nodes(
        self, root: TreeNode, tasks: list[dict], collapsed: set[str]
    ) -> None:
        task_map = {t["id"]: t for t in tasks}
        task_ids = list(task_map.keys())
        placeholders = ",".join(["%s"] * len(task_ids))
        rows = self._conn.execute(
            f"SELECT task_id, depends_on_task_id FROM dependencies "
            f"WHERE task_id IN ({placeholders})",
            task_ids,
        ).fetchall()

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

    def _add_flat_nodes(
        self, root: TreeNode, tasks: list[dict], status_filter: str
    ) -> None:
        matching = [t for t in tasks if t["status"] == status_filter]
        if not matching:
            label = status_filter.replace("_", " ")
            root.add_leaf(Text(f"No {label} tasks.", style="dim"))
            return
        for task in matching:
            root.add_leaf(_task_label(task), data=task)

    def _collect_collapsed(self, node: TreeNode, result: set[str]) -> None:
        if isinstance(node.data, dict) and not node.is_expanded:
            result.add(node.data["id"])
        for child in node.children:
            self._collect_collapsed(child, result)

    # ── Detail panel ─────────────────────────────────────────────────────────

    def _refresh_detail(self) -> None:
        if not self._selected_task_id:
            return
        task = store.get_task(self._conn, self._selected_task_id)
        if task:
            events = store.get_task_events(self._conn, self._selected_task_id)
            self.query_one("#task-detail", Static).update(
                _render_task_detail(task, events)
            )

    def _show_task(self, task: dict) -> None:
        self._selected_task_id = task["id"]
        events = store.get_task_events(self._conn, task["id"])
        self.query_one("#task-detail", Static).update(
            _render_task_detail(task, events)
        )

    # ── Events ────────────────────────────────────────────────────────────────

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

    # ── Actions ───────────────────────────────────────────────────────────────

    def action_new_task(self) -> None:
        tasks = store.get_tasks_for_job(self._conn, self._job_id)
        self.app.push_screen(AddTaskModal(self._job_id, tasks), self._on_task_added)

    def _on_task_added(self, result: dict | None) -> None:
        if result is None:
            return
        try:
            store.add_tasks(self._conn, self._job_id, [result])
            self._conn.commit()
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
        task = store.get_task(self._conn, task_id)
        if task is None:
            return
        editable = task["status"] == "pending" or (
            task["status"] == "awaiting_approval" and bool(task.get("human_task"))
        )
        if not editable:
            self.notify(
                f"Cannot edit a {task['status'].replace('_', ' ')} task",
                severity="warning",
            )
            return
        other_tasks = store.get_tasks_for_job(self._conn, self._job_id)
        self.app.push_screen(EditTaskModal(task, other_tasks), self._on_task_edited)

    def _on_task_edited(self, result: dict | None) -> None:
        if result is None:
            return
        try:
            store.edit_task(self._conn, self._selected_task_id, result)
            self._conn.commit()
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
        task = store.get_task(self._conn, task_id)
        if task is None:
            return
        cancellable = task["status"] in ("pending", "in_progress") or (
            task["status"] == "awaiting_approval" and bool(task.get("human_task"))
        )
        if not cancellable:
            self.notify(
                f"Cannot cancel a {task['status'].replace('_', ' ')} task",
                severity="warning",
            )
            return
        self.app.push_screen(
            ConfirmModal(f"Cancel task '{task['name']}'?"),
            lambda confirmed: self._on_cancel_confirmed(confirmed, task_id),
        )

    def _on_cancel_confirmed(self, confirmed: bool, task_id: str) -> None:
        if not confirmed:
            return
        try:
            result = store.cancel_task(self._conn, task_id)
            if result is None:
                self.notify("Task could not be cancelled", severity="warning")
                return
            self._conn.commit()
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
            store.resume_job(self._conn, job["id"])
            self._conn.commit()
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
        store.pause_job(self._conn, job["id"], reason=reason or None)
        self._conn.commit()
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


# ── Job List (main App) ───────────────────────────────────────────────────────

_JOB_FILTERS: list[tuple[str, str]] = [
    ("open", "Open"),
    ("all", "All"),
    ("completed", "Completed"),
    ("cancelled", "Cancelled"),
]
_JOB_FILTER_KEYS = [k for k, _ in _JOB_FILTERS]


def _fetch_jobs(conn, status_filter: str) -> list[dict]:
    if status_filter == "open":
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM jobs WHERE status NOT IN ('completed', 'cancelled') "
                "ORDER BY created_at DESC"
            ).fetchall()
        ]
    if status_filter == "completed":
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM jobs WHERE status = 'completed' "
                "ORDER BY created_at DESC LIMIT 200"
            ).fetchall()
        ]
    if status_filter == "cancelled":
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM jobs WHERE status = 'cancelled' "
                "ORDER BY created_at DESC LIMIT 200"
            ).fetchall()
        ]
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT 500"
        ).fetchall()
    ]


def _job_status_cell(job: dict) -> Text:
    status = job.get("status", "active")
    if status == "completed":
        sym, sty = "●", _C_JOB_DONE
    elif status == "cancelled":
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
                    Static(
                        "[dim]Select a job to view its summary.[/dim]",
                        id="job-summary",
                    ),
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

    # ── Data ──────────────────────────────────────────────────────────────────

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

        # Restore cursor position by job ID
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
            self.query_one("#job-summary", Static).update(
                _render_job_summary(self._conn, job)
            )
        self._conn.rollback()

    # ── Events ────────────────────────────────────────────────────────────────

    @on(DataTable.RowHighlighted)
    def on_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        job_id = str(event.row_key.value)
        self._selected_job_id = job_id
        job = store.get_job(self._conn, job_id)
        self.query_one("#job-summary", Static).update(
            _render_job_summary(self._conn, job)
        )

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
            self.query_one("#job-summary", Static).update(
                "[dim]Select a job to view its summary.[/dim]"
            )
            self._load()

    # ── Actions ───────────────────────────────────────────────────────────────

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
            self.query_one("#job-summary", Static).update(
                _render_job_summary(self._conn, job)
            )
        except Exception as e:
            self.notify(str(e), title="Error editing job", severity="error")

    def action_cancel_job(self) -> None:
        if not self._selected_job_id:
            self.notify("No job selected", severity="warning")
            return
        job = store.get_job(self._conn, self._selected_job_id)
        if job is None:
            return
        if job["status"] == "cancelled":
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
        self.query_one("#job-summary", Static).update(
            _render_job_summary(self._conn, job)
        )
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
