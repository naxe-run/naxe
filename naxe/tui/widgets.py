from __future__ import annotations

import json

from rich.markup import escape
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.screen import ModalScreen
from textual.app import ComposeResult
from textual.widgets import Button, Checkbox, Input, Label, Static, TextArea
from textual import on

from naxe.tui.theme import MODAL_CSS, _STATUS_SYMBOL


# ── Modal helpers ─────────────────────────────────────────────────────────────

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
        updates["recurrence_interval_days"] = recurrence

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
