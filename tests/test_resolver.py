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


def test_quick_win_flag(conn):
    job = store.create_job(conn, "test")
    store.add_tasks(conn, job["id"], [
        {"id": "t1", "name": "Quick", "duration_minutes": 5},
        {"id": "t2", "name": "Long", "duration_minutes": 60},
        {"id": "t3", "name": "No duration"},
    ])
    actions = resolver.get_next_actions(conn, job["id"])
    by_id = {a["id"]: a for a in actions}
    assert by_id["t1"]["is_quick_win"] is True
    assert by_id["t2"]["is_quick_win"] is False
    assert by_id["t3"]["is_quick_win"] is False


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
