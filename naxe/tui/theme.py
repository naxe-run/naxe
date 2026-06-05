from textual.theme import Theme

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
    background="#0a0a0a",
    surface="#141414",
    panel="#1e1a18",
    boost="#2e2820",
    primary="#ff3300",
    secondary="#ff9900",
    accent="#ff6600",
    warning="#ffcc00",
    error="#ff0000",
    success="#00ff66",
    foreground="#fff5cc",
)

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


def _apply_theme_palette(theme: Theme) -> None:
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
    "pending":           "○",
    "in_progress":       "◉",
    "completed":         "●",
    "failed":            "✗",
    "cancelled":         "⊘",
    "awaiting_approval": "⧗",
    "next_action":       "▶",
    "waiting_on":        "⊟",
    "scheduled":         "⏱",
}
