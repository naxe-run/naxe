import time
from datetime import datetime, timezone

from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.text import Text
from rich.tree import Tree

from naxe.schema import get_connection
from naxe import store
from naxe.config import resolve_db_url

_HEADER = (
    "███╗  ██╗ █████╗ ██╗  ██╗███████╗\n"
    "████╗ ██║██╔══██╗╚██╗██╔╝██╔════╝\n"
    "██╔██╗██║███████║ ╚███╔╝ █████╗\n"
    "██║╚████║██║  ██║ ██╔██╗ ██╔══╝\n"
    "██║ ╚███║██║  ██║██╔╝ ██╗███████╗"
)

DB_URL = resolve_db_url()

_STATUS_STYLE = {
    "pending": "dim",
    "in_progress": "bold yellow",
    "completed": "green",
    "failed": "bold red",
    "cancelled": "dim red",
    "awaiting_approval": "bold blue",
}

_STATUS_SYMBOL = {
    "pending": "○",
    "in_progress": "◉",
    "completed": "●",
    "failed": "✗",
    "cancelled": "⊘",
    "awaiting_approval": "⧗",
}


def _progress_bar(progress: int, width: int = 8) -> str:
    filled = round(progress / 100 * width)
    return "[" + "━" * filled + "╌" * (width - filled) + f"] {progress}%"


def _task_label(task: dict) -> Text:
    status = task["status"]
    is_human = bool(task.get("human_task"))
    if is_human and status == "pending":
        symbol, style = "☻", "dim"
    elif is_human and status == "awaiting_approval":
        symbol, style = "☻", "bold magenta"
    elif bool(task.get("approval_gate")) and status == "pending":
        symbol, style = "⧗", "dim"
    else:
        style = _STATUS_STYLE.get(status, "")
        symbol = _STATUS_SYMBOL.get(status, "●")
    short_id = task["id"][:8]
    label = Text()
    label.append(f"{symbol} {task['name']}", style=style)
    label.append(f"  [{short_id}]", style="dim")
    if task.get("owner_agent_id"):
        label.append(f"  {task['owner_agent_id']}", style="dim")
    if status == "in_progress" and task.get("progress"):
        label.append(f"  {_progress_bar(task['progress'])}", style="bold yellow")
    return label


def _build_job_panel(conn, job: dict):
    tasks = store.get_tasks_for_job(conn, job["id"])
    if not tasks:
        return Panel(Text("No tasks yet.", style="dim"), title=job["name"])

    total = len(tasks)
    completed = sum(1 for t in tasks if t["status"] == "completed")
    failed = sum(1 for t in tasks if t["status"] == "failed")
    cancelled = sum(1 for t in tasks if t["status"] == "cancelled")

    if completed + failed + cancelled == total:
        summary = Text()
        if cancelled == total:
            symbol, style = "⊘", "dim red"
        elif failed:
            symbol, style = "✗", "bold red"
        else:
            symbol, style = "●", "bold green"
        summary.append(f"{symbol} ", style=style)
        summary.append(job["name"], style="bold")
        if cancelled == total:
            summary.append(f"  {cancelled}/{total} tasks cancelled", style="dim red")
        else:
            summary.append(f"  {completed}/{total} tasks", style="green" if not failed else "dim")
            if failed:
                summary.append(f"  {failed} failed", style="bold red")
            if cancelled:
                summary.append(f"  {cancelled} cancelled", style="dim red")
        return Panel(summary, title=f"[dim]{job['id'][:8]}[/dim]", title_align="left")

    task_map = {t["id"]: t for t in tasks}
    task_ids = list(task_map.keys())

    placeholders = ",".join(["%s"] * len(task_ids))
    rows = conn.execute(
        f"SELECT task_id, depends_on_task_id FROM dependencies WHERE task_id IN ({placeholders})",
        task_ids,
    ).fetchall()

    children: dict[str, list[str]] = {tid: [] for tid in task_ids}
    has_parent: set[str] = set()
    for row in rows:
        children[row["depends_on_task_id"]].append(row["task_id"])
        has_parent.add(row["task_id"])

    roots = [tid for tid in task_ids if tid not in has_parent]

    in_progress = sum(1 for t in tasks if t["status"] == "in_progress")
    pending = total - completed - in_progress - failed - cancelled

    summary = Text()
    summary.append(f"{completed}/{total} tasks", style="bold")
    parts = []
    if in_progress:
        parts.append(Text(f"{in_progress} in progress", style="bold yellow"))
    if pending:
        parts.append(Text(f"{pending} pending", style="dim"))
    if failed:
        parts.append(Text(f"{failed} failed", style="bold red"))
    if cancelled:
        parts.append(Text(f"{cancelled} cancelled", style="dim red"))
    if parts:
        summary.append("  —  ")
        for i, part in enumerate(parts):
            summary.append_text(part)
            if i < len(parts) - 1:
                summary.append(", ")

    tree = Tree(summary)

    def add_node(parent_tree, task_id: str):
        task = task_map.get(task_id)
        if not task:
            return
        branch = parent_tree.add(_task_label(task))
        for child_id in children.get(task_id, []):
            add_node(branch, child_id)

    for root_id in roots:
        add_node(tree, root_id)

    return Panel(tree, title=f"[bold]{job['name']}[/bold]  [dim]{job['id'][:8]}[/dim]", title_align="left")


def _header() -> Text:
    t = Text(_HEADER, style="bold cyan")
    return t


def build_renderable(conn, session_start: str):
    jobs = store.list_watch_jobs(conn, session_start)

    if not jobs:
        return Group(
            _header(),
            Text(""),
            Text("No jobs yet.", style="dim"),
            Text("Refreshing every 2s — Ctrl+C to exit", style="dim"),
        )

    panels = [_build_job_panel(conn, job) for job in jobs]
    panels.append(Text("Refreshing every 2s — Ctrl+C to exit", style="dim"))
    return Group(_header(), Text(""), *panels)


def main():
    session_start = datetime.now(timezone.utc).isoformat()
    conn = get_connection(DB_URL, readonly=True)
    try:
        with Live(build_renderable(conn, session_start), refresh_per_second=2, screen=True) as live:
            conn.rollback()
            while True:
                time.sleep(2)
                live.update(build_renderable(conn, session_start))
                conn.rollback()
    except KeyboardInterrupt:
        pass