from naxe.handlers._common import _ok, _err
from naxe import store


def handle_create_job_template(conn, arguments: dict) -> list:
    try:
        template = store.create_template(
            conn, arguments["name"], arguments.get("description"), arguments["tasks"]
        )
    except ValueError as e:
        return _err(str(e))
    return _ok(template=template)


def handle_list_templates(conn, arguments: dict) -> list:
    templates = store.list_templates(conn)
    return _ok(templates=templates)


def handle_instantiate_template(conn, arguments: dict) -> list:
    try:
        job = store.instantiate_template(conn, arguments["template_id"], arguments["name"])
    except ValueError as e:
        return _err(str(e))
    tasks = store.get_tasks_for_job(conn, job["id"])
    return _ok(job=job, task_ids=[t["id"] for t in tasks])
