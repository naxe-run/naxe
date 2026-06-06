import json
import uuid

from naxe.store.core import _now, _row
from naxe.store.jobs import create_job
from naxe.store.tasks import add_tasks


def create_template(conn, name: str, description: str | None, tasks: list[dict]) -> dict:
    from naxe import resolver as _resolver

    for t in tasks:
        if not t.get("id"):
            t["id"] = str(uuid.uuid4())
        t["_resolved_id"] = t["id"]
    if _resolver.detect_cycle(tasks):
        raise ValueError("Dependency cycle detected in template tasks")

    template_id = str(uuid.uuid4())
    now = _now()
    conn.execute(
        "INSERT INTO job_templates (id, name, description, tasks_json, created_at) VALUES (%s, %s, %s, %s, %s)",
        (template_id, name, description, json.dumps(tasks), now),
    )
    return _row(conn.execute("SELECT * FROM job_templates WHERE id = %s", (template_id,)).fetchone())


def list_templates(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM job_templates ORDER BY name").fetchall()]


def get_template(conn, template_id: str) -> dict | None:
    return _row(conn.execute("SELECT * FROM job_templates WHERE id = %s", (template_id,)).fetchone())


def instantiate_template(conn, template_id: str, job_name: str) -> dict:
    template = get_template(conn, template_id)
    if not template:
        raise ValueError(f"Template '{template_id}' not found")
    tasks = json.loads(template["tasks_json"])
    # Remap all task IDs to fresh UUIDs so each instantiation is independent
    id_map = {t["id"]: str(uuid.uuid4()) for t in tasks if t.get("id")}
    remapped = []
    for t in tasks:
        new_t = {k: v for k, v in t.items() if k not in ("id", "_resolved_id", "depends_on")}
        new_t["id"] = id_map.get(t.get("id", ""), str(uuid.uuid4()))
        new_t["depends_on"] = [id_map.get(d, d) for d in (t.get("depends_on") or [])]
        remapped.append(new_t)
    job = create_job(conn, job_name)
    add_tasks(conn, job["id"], remapped)
    return job
