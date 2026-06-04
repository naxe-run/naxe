import pytest
import sqlite3
from naxe import schema as naxe_schema
from naxe.schema import get_connection
from naxe import store, resolver


@pytest.fixture
def conn():
    naxe_schema._migrations_run = False
    c = get_connection(":memory:")
    yield c
    c.close()


def test_no_deps_returns_all_pending(conn):
    job = store.create_job(conn, "test")
    store.add_tasks(conn, job["id"], [
        {"id": "t1", "name": "Task 1"},
        {"id": "t2", "name": "Task 2"},
    ])
    actions = resolver.get_next_actions(conn, job["id"])
    assert {a["id"] for a in actions} == {"t1", "t2"}


def test_blocked_task_not_returned(conn):
    job = store.create_job(conn, "test")
    store.add_tasks(conn, job["id"], [
        {"id": "t1", "name": "Task 1"},
        {"id": "t2", "name": "Task 2", "depends_on": ["t1"]},
    ])
    actions = resolver.get_next_actions(conn, job["id"])
    assert [a["id"] for a in actions] == ["t1"]


def test_completing_dep_unblocks_task(conn):
    job = store.create_job(conn, "test")
    store.add_tasks(conn, job["id"], [
        {"id": "t1", "name": "Task 1"},
        {"id": "t2", "name": "Task 2", "depends_on": ["t1"]},
    ])
    store.claim_task(conn, "t1", "agent-1")
    store.update_task_status(conn, "t1", "completed")
    actions = resolver.get_next_actions(conn, job["id"])
    assert [a["id"] for a in actions] == ["t2"]


def test_get_next_actions_returns_display_status(conn):
    job = store.create_job(conn, "test")
    store.add_tasks(conn, job["id"], [
        {"id": "t1", "name": "Quick", "duration_minutes": 5},
        {"id": "t2", "name": "Long", "duration_minutes": 60},
        {"id": "t3", "name": "No duration"},
    ])
    actions = resolver.get_next_actions(conn, job["id"])
    for a in actions:
        assert a.get("display_status") == "next_action"
        assert "is_quick_win" not in a


def test_detect_cycle_direct(conn):
    tasks = [
        {"id": "a", "_resolved_id": "a", "name": "A", "depends_on": ["b"]},
        {"id": "b", "_resolved_id": "b", "name": "B", "depends_on": ["a"]},
    ]
    assert resolver.detect_cycle(tasks) is True


def test_detect_cycle_indirect(conn):
    tasks = [
        {"id": "a", "_resolved_id": "a", "name": "A", "depends_on": ["c"]},
        {"id": "b", "_resolved_id": "b", "name": "B", "depends_on": ["a"]},
        {"id": "c", "_resolved_id": "c", "name": "C", "depends_on": ["b"]},
    ]
    assert resolver.detect_cycle(tasks) is True


def test_detect_cycle_none_linear(conn):
    tasks = [
        {"id": "a", "_resolved_id": "a", "name": "A", "depends_on": []},
        {"id": "b", "_resolved_id": "b", "name": "B", "depends_on": ["a"]},
        {"id": "c", "_resolved_id": "c", "name": "C", "depends_on": ["b"]},
    ]
    assert resolver.detect_cycle(tasks) is False


def test_detect_cycle_none_branching(conn):
    tasks = [
        {"id": "a", "_resolved_id": "a", "name": "A", "depends_on": []},
        {"id": "b", "_resolved_id": "b", "name": "B", "depends_on": ["a"]},
        {"id": "c", "_resolved_id": "c", "name": "C", "depends_on": ["a"]},
        {"id": "d", "_resolved_id": "d", "name": "D", "depends_on": ["b", "c"]},
    ]
    assert resolver.detect_cycle(tasks) is False


def test_get_newly_unblocked(conn):
    job = store.create_job(conn, "test")
    store.add_tasks(conn, job["id"], [
        {"id": "t1", "name": "Task 1"},
        {"id": "t2", "name": "Task 2", "depends_on": ["t1"]},
        {"id": "t3", "name": "Task 3", "depends_on": ["t1"]},
        {"id": "t4", "name": "Task 4", "depends_on": ["t2", "t3"]},
    ])
    store.update_task_status(conn, "t1", "completed")
    unblocked = resolver.get_newly_unblocked(conn, job["id"], "t1")
    assert {u["id"] for u in unblocked} == {"t2", "t3"}


def test_complex_10_task_graph(conn):
    """10+ task branching graph resolves correctly end-to-end."""
    job = store.create_job(conn, "complex")
    store.add_tasks(conn, job["id"], [
        {"id": "t1",  "name": "Start"},
        {"id": "t2",  "name": "A",  "depends_on": ["t1"]},
        {"id": "t3",  "name": "B",  "depends_on": ["t1"]},
        {"id": "t4",  "name": "C",  "depends_on": ["t1"]},
        {"id": "t5",  "name": "D",  "depends_on": ["t2"]},
        {"id": "t6",  "name": "E",  "depends_on": ["t2", "t3"]},
        {"id": "t7",  "name": "F",  "depends_on": ["t3", "t4"]},
        {"id": "t8",  "name": "G",  "depends_on": ["t5"]},
        {"id": "t9",  "name": "H",  "depends_on": ["t6", "t7"]},
        {"id": "t10", "name": "End", "depends_on": ["t8", "t9"]},
        {"id": "t11", "name": "Cleanup", "depends_on": ["t10"]},
    ])

    def next_ids():
        return {a["id"] for a in resolver.get_next_actions(conn, job["id"])}

    assert next_ids() == {"t1"}

    store.update_task_status(conn, "t1", "completed")
    assert next_ids() == {"t2", "t3", "t4"}

    store.update_task_status(conn, "t2", "completed")
    store.update_task_status(conn, "t3", "completed")
    # t4 still pending, t5 unblocked (t2 done), t6 unblocked (t2+t3 done), t7 needs t4
    assert next_ids() == {"t4", "t5", "t6"}

    store.update_task_status(conn, "t4", "completed")
    assert next_ids() == {"t5", "t6", "t7"}

    store.update_task_status(conn, "t5", "completed")
    store.update_task_status(conn, "t6", "completed")
    store.update_task_status(conn, "t7", "completed")
    assert next_ids() == {"t8", "t9"}

    store.update_task_status(conn, "t8", "completed")
    store.update_task_status(conn, "t9", "completed")
    assert next_ids() == {"t10"}

    store.update_task_status(conn, "t10", "completed")
    assert next_ids() == {"t11"}

    store.update_task_status(conn, "t11", "completed")
    assert next_ids() == set()


def test_get_next_actions_critical_first(conn):
    job = store.create_job(conn, "critical-resolver")
    store.add_tasks(conn, job["id"], [
        {"id": "t-normal", "name": "Normal", "priority": 100},
        {"id": "t-critical", "name": "Critical", "priority": 50, "critical": True},
    ])
    actions = resolver.get_next_actions(conn, job["id"])
    assert len(actions) >= 2
    assert actions[0]["id"] == "t-critical"
    assert "is_quick_win" not in actions[0]


def test_display_status_next_action_on_unblocked_pending(conn):
    from naxe import resolver
    job = store.create_job(conn, "ds-next-action")
    store.add_tasks(conn, job["id"], [{"id": "t1", "name": "Task"}])
    actions = resolver.get_next_actions(conn, job["id"])
    assert len(actions) == 1
    assert actions[0]["display_status"] == "next_action"


def test_display_status_not_on_blocked_task(conn):
    from naxe import resolver
    job = store.create_job(conn, "ds-blocked")
    store.add_tasks(conn, job["id"], [
        {"id": "t1", "name": "Gate"},
        {"id": "t2", "name": "Blocked", "depends_on": ["t1"]},
    ])
    actions = resolver.get_next_actions(conn, job["id"])
    ids = [a["id"] for a in actions]
    assert "t1" in ids
    assert "t2" not in ids  # blocked, not returned
    for a in actions:
        assert a["display_status"] == "next_action"
