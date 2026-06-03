import pytest
from datetime import datetime, timedelta, timezone
from naxe import schema as naxe_schema
from naxe.schema import get_connection
from naxe import store


@pytest.fixture
def conn():
    naxe_schema._migrations_run = False
    c = get_connection(":memory:")
    yield c
    c.close()


def test_create_and_get_job(conn):
    job = store.create_job(conn, "My Job")
    assert job["name"] == "My Job"
    assert job["status"] == "active"

    fetched = store.get_job(conn, job["id"])
    assert fetched["id"] == job["id"]


def test_list_jobs(conn):
    store.create_job(conn, "Job A")
    store.create_job(conn, "Job B")
    result = store.list_jobs(conn)
    assert len(result["jobs"]) == 2


def test_add_tasks_lifecycle(conn):
    job = store.create_job(conn, "lifecycle")
    task_ids = store.add_tasks(conn, job["id"], [
        {"id": "t1", "name": "First"},
        {"id": "t2", "name": "Second", "depends_on": ["t1"]},
    ])
    assert len(task_ids) == 2

    task = store.get_task(conn, "t1")
    assert task["status"] == "pending"
    assert task["job_id"] == job["id"]


def test_claim_task_succeeds(conn):
    job = store.create_job(conn, "claim-test")
    store.add_tasks(conn, job["id"], [{"id": "t1", "name": "Task"}])

    success = store.claim_task(conn, "t1", "agent-1")
    assert success is True

    task = store.get_task(conn, "t1")
    assert task["status"] == "in_progress"
    assert task["owner_agent_id"] == "agent-1"


def test_claim_task_double_claim_prevented(conn):
    job = store.create_job(conn, "double-claim")
    store.add_tasks(conn, job["id"], [{"id": "t1", "name": "Task"}])

    r1 = store.claim_task(conn, "t1", "agent-1")
    r2 = store.claim_task(conn, "t1", "agent-2")

    assert r1 is True
    assert r2 is False

    task = store.get_task(conn, "t1")
    assert task["owner_agent_id"] == "agent-1"


def test_add_tasks_rejects_unknown_dep(conn):
    job = store.create_job(conn, "bad-deps")
    with pytest.raises(ValueError, match="Unknown dependency"):
        store.add_tasks(conn, job["id"], [
            {"id": "t1", "name": "Task", "depends_on": ["nonexistent"]},
        ])


def test_add_tasks_rejects_unknown_job(conn):
    with pytest.raises(ValueError, match="not found"):
        store.add_tasks(conn, "fake-job-id", [{"id": "t1", "name": "Task"}])


def test_update_task_status(conn):
    job = store.create_job(conn, "status-test")
    store.add_tasks(conn, job["id"], [{"id": "t1", "name": "Task"}])
    store.claim_task(conn, "t1", "agent-1")
    updated = store.update_task_status(conn, "t1", "completed")
    assert updated["status"] == "completed"


def test_cancel_task_pending(conn):
    job = store.create_job(conn, "cancel-task-pending")
    store.add_tasks(conn, job["id"], [{"id": "t1", "name": "Task"}])

    result = store.cancel_task(conn, "t1")
    assert result is not None
    assert result["status"] == "cancelled"


def test_cancel_task_in_progress(conn):
    job = store.create_job(conn, "cancel-task-in-progress")
    store.add_tasks(conn, job["id"], [{"id": "t1", "name": "Task"}])
    store.claim_task(conn, "t1", "agent-1")

    result = store.cancel_task(conn, "t1")
    assert result is not None
    assert result["status"] == "cancelled"


def test_cancel_task_rejects_completed(conn):
    job = store.create_job(conn, "cancel-task-completed")
    store.add_tasks(conn, job["id"], [{"id": "t1", "name": "Task"}])
    store.claim_task(conn, "t1", "agent-1")
    store.update_task_status(conn, "t1", "completed")

    result = store.cancel_task(conn, "t1")
    assert result is None


def test_cancel_last_task_completes_job(conn):
    job = store.create_job(conn, "cancel-last-task")
    store.add_tasks(conn, job["id"], [
        {"id": "t1", "name": "Done"},
        {"id": "t2", "name": "Skipped"},
    ])
    store.claim_task(conn, "t1", "agent-1")
    store.update_task_status(conn, "t1", "completed")

    store.cancel_task(conn, "t2")

    job = store.get_job(conn, job["id"])
    assert job["status"] == "completed"


def test_cancel_job_cancels_all_non_terminal_tasks(conn):
    job = store.create_job(conn, "cancel-job")
    store.add_tasks(conn, job["id"], [
        {"id": "t1", "name": "Done"},
        {"id": "t2", "name": "Running"},
        {"id": "t3", "name": "Waiting"},
    ])
    store.claim_task(conn, "t1", "agent-1")
    store.update_task_status(conn, "t1", "completed")
    store.claim_task(conn, "t2", "agent-1")

    result = store.cancel_job(conn, job["id"])

    assert result["tasks_cancelled"] == 2  # t2 (in_progress) + t3 (pending)
    assert result["job"]["status"] == "cancelled"
    assert store.get_task(conn, "t1")["status"] == "completed"
    assert store.get_task(conn, "t2")["status"] == "cancelled"
    assert store.get_task(conn, "t3")["status"] == "cancelled"


def test_claim_next_action_skips_cancelled_job(conn):
    job = store.create_job(conn, "skip-cancelled")
    store.add_tasks(conn, job["id"], [{"id": "t1", "name": "Task"}])
    store.cancel_job(conn, job["id"])

    result = store.claim_next_action(conn, job["id"], "agent-1")
    assert result is None


def test_claim_next_action_happy_path(conn):
    job = store.create_job(conn, "cna-happy")
    store.add_tasks(conn, job["id"], [{"id": "t1", "name": "Task", "duration_minutes": 3}])

    task = store.claim_next_action(conn, job["id"], "agent-1")
    assert task is not None
    assert task["id"] == "t1"
    assert task["status"] == "in_progress"
    assert task["owner_agent_id"] == "agent-1"
    assert task["is_quick_win"] is True


def test_claim_next_action_two_workers_get_different_tasks(conn):
    job = store.create_job(conn, "cna-two-workers")
    store.add_tasks(conn, job["id"], [
        {"id": "t1", "name": "Task A"},
        {"id": "t2", "name": "Task B"},
    ])

    task_a = store.claim_next_action(conn, job["id"], "agent-1")
    task_b = store.claim_next_action(conn, job["id"], "agent-2")

    assert task_a is not None
    assert task_b is not None
    assert task_a["id"] != task_b["id"]


def test_claim_next_action_returns_none_when_nothing_unblocked(conn):
    job = store.create_job(conn, "cna-blocked")
    store.add_tasks(conn, job["id"], [
        {"id": "t1", "name": "Gate"},
        {"id": "t2", "name": "Blocked", "depends_on": ["t1"]},
    ])

    # Claim the only unblocked task
    store.claim_next_action(conn, job["id"], "agent-1")

    # t2 is still blocked by t1; nothing left to claim
    result = store.claim_next_action(conn, job["id"], "agent-2")
    assert result is None


def test_claim_next_action_respects_dependency_order(conn):
    job = store.create_job(conn, "cna-ordering")
    store.add_tasks(conn, job["id"], [
        {"id": "t1", "name": "First"},
        {"id": "t2", "name": "Second", "depends_on": ["t1"]},
    ])

    task = store.claim_next_action(conn, job["id"], "agent-1")
    assert task["id"] == "t1"

    store.update_task_status(conn, "t1", "completed")

    task2 = store.claim_next_action(conn, job["id"], "agent-1")
    assert task2["id"] == "t2"


def test_heartbeat_task_owner_succeeds(conn):
    job = store.create_job(conn, "heartbeat-owner")
    store.add_tasks(conn, job["id"], [{"id": "t1", "name": "Task"}])
    store.claim_task(conn, "t1", "agent-1")

    result = store.heartbeat_task(conn, "t1", "agent-1")
    assert result is not None
    assert result["last_heartbeat_at"] is not None


def test_heartbeat_task_non_owner_returns_none(conn):
    job = store.create_job(conn, "heartbeat-non-owner")
    store.add_tasks(conn, job["id"], [{"id": "t1", "name": "Task"}])
    store.claim_task(conn, "t1", "agent-1")

    result = store.heartbeat_task(conn, "t1", "agent-2")
    assert result is None


def test_reclaim_stale_tasks(conn):
    job = store.create_job(conn, "reclaim-stale")
    # Set a short timeout so we can easily expire the heartbeat
    conn.execute(
        "UPDATE jobs SET heartbeat_timeout_seconds = 1 WHERE id = %s", (job["id"],)
    )
    conn.commit()
    store.add_tasks(conn, job["id"], [{"id": "t1", "name": "Task"}])
    store.claim_task(conn, "t1", "agent-1")

    # Set last_heartbeat_at to 10 seconds ago so it's definitely stale
    stale_ts = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    conn.execute(
        "UPDATE tasks SET last_heartbeat_at = %s WHERE id = %s", (stale_ts, "t1")
    )
    conn.commit()

    reclaimed = store.reclaim_stale_tasks(conn, job["id"])
    assert len(reclaimed) == 1
    assert reclaimed[0]["id"] == "t1"
    assert reclaimed[0]["status"] == "pending"
    assert reclaimed[0]["owner_agent_id"] is None


def test_reclaim_stale_tasks_skips_fresh_heartbeat(conn):
    job = store.create_job(conn, "reclaim-fresh")
    conn.execute(
        "UPDATE jobs SET heartbeat_timeout_seconds = 1 WHERE id = %s", (job["id"],)
    )
    conn.commit()
    store.add_tasks(conn, job["id"], [{"id": "t1", "name": "Task"}])
    store.claim_task(conn, "t1", "agent-1")
    store.heartbeat_task(conn, "t1", "agent-1")  # fresh heartbeat

    reclaimed = store.reclaim_stale_tasks(conn, job["id"])
    assert reclaimed == []
    assert store.get_task(conn, "t1")["status"] == "in_progress"


def test_claim_next_action_reclaims_stale_task(conn):
    job = store.create_job(conn, "cna-reclaim-stale")
    conn.execute(
        "UPDATE jobs SET heartbeat_timeout_seconds = 1 WHERE id = %s", (job["id"],)
    )
    conn.commit()
    store.add_tasks(conn, job["id"], [{"id": "t1", "name": "Task"}])
    store.claim_task(conn, "t1", "agent-1")

    stale_ts = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    conn.execute(
        "UPDATE tasks SET last_heartbeat_at = %s WHERE id = %s", (stale_ts, "t1")
    )
    conn.commit()

    # agent-2 calls claim_next_action — should reclaim t1 and claim it
    task = store.claim_next_action(conn, job["id"], "agent-2")
    assert task is not None
    assert task["id"] == "t1"
    assert task["owner_agent_id"] == "agent-2"


def test_edit_job_renames(conn):
    job = store.create_job(conn, "original-name")
    updated = store.edit_job(conn, job["id"], "new-name")
    assert updated["name"] == "new-name"
    assert store.get_job(conn, job["id"])["name"] == "new-name"


def test_edit_job_not_found(conn):
    assert store.edit_job(conn, "no-such-id", "x") is None


def test_edit_task_metadata(conn):
    job = store.create_job(conn, "edit-meta")
    store.add_tasks(conn, job["id"], [{"id": "t1", "name": "Old Name", "max_retries": 0}])

    updated = store.edit_task(conn, "t1", {"name": "New Name", "description": "desc", "max_retries": 2})
    assert updated["name"] == "New Name"
    assert updated["description"] == "desc"
    assert updated["max_retries"] == 2


def test_edit_task_resources(conn):
    import json
    job = store.create_job(conn, "edit-resources")
    store.add_tasks(conn, job["id"], [{"id": "t1", "name": "Task", "resources": ["old.py"]}])

    updated = store.edit_task(conn, "t1", {"resources": ["new.py", "other.py"]})
    assert json.loads(updated["resources"]) == ["new.py", "other.py"]


def test_edit_task_dependencies_replace(conn):
    job = store.create_job(conn, "edit-deps")
    store.add_tasks(conn, job["id"], [
        {"id": "t1", "name": "A"},
        {"id": "t2", "name": "B"},
        {"id": "t3", "name": "C", "depends_on": ["t1"]},
    ])

    # Replace t3's deps: now depends on t2 instead of t1
    updated = store.edit_task(conn, "t3", {"depends_on": ["t2"]})
    assert updated is not None
    # t1 completion should not unblock t3; t2 completion should
    from naxe import resolver
    store.update_task_status(conn, "t1", "completed")
    assert not any(a["id"] == "t3" for a in resolver.get_next_actions(conn, job["id"]))
    store.update_task_status(conn, "t2", "completed")
    assert any(a["id"] == "t3" for a in resolver.get_next_actions(conn, job["id"]))


def test_edit_task_cycle_rejected(conn):
    job = store.create_job(conn, "edit-cycle")
    # t2 first so t1's FK ref to t2 is valid
    store.add_tasks(conn, job["id"], [
        {"id": "t2", "name": "B"},
        {"id": "t1", "name": "A", "depends_on": ["t2"]},
    ])
    with pytest.raises(ValueError, match="cycle"):
        store.edit_task(conn, "t2", {"depends_on": ["t1"]})


def test_edit_task_rejects_non_pending(conn):
    job = store.create_job(conn, "edit-non-pending")
    store.add_tasks(conn, job["id"], [{"id": "t1", "name": "Task"}])
    store.claim_task(conn, "t1", "agent-1")

    result = store.edit_task(conn, "t1", {"name": "New"})
    assert result is None


def test_edit_task_unknown_dep_rejected(conn):
    job = store.create_job(conn, "edit-bad-dep")
    store.add_tasks(conn, job["id"], [{"id": "t1", "name": "Task"}])
    with pytest.raises(ValueError, match="Unknown dependency"):
        store.edit_task(conn, "t1", {"depends_on": ["no-such-task"]})


def test_resources_stored_on_task(conn):
    job = store.create_job(conn, "resources-stored")
    store.add_tasks(conn, job["id"], [
        {"id": "t1", "name": "Task A", "resources": ["naxe/store.py"]},
        {"id": "t2", "name": "Task B"},
    ])
    import json
    t1 = store.get_task(conn, "t1")
    assert json.loads(t1["resources"]) == ["naxe/store.py"]
    t2 = store.get_task(conn, "t2")
    assert t2["resources"] is None


def test_claim_next_action_skips_resource_conflict(conn):
    job = store.create_job(conn, "resources-conflict")
    store.add_tasks(conn, job["id"], [
        {"id": "t1", "name": "Task A", "resources": ["naxe/store.py"]},
        {"id": "t2", "name": "Task B", "resources": ["naxe/store.py"]},
        {"id": "t3", "name": "Task C", "resources": ["naxe/other.py"]},
    ])

    # Agent 1 claims t1 (first in creation order)
    task = store.claim_next_action(conn, job["id"], "agent-1")
    assert task["id"] == "t1"

    # Agent 2 can't get t2 (same resource), but can get t3
    task = store.claim_next_action(conn, job["id"], "agent-2")
    assert task is not None
    assert task["id"] == "t3"


def test_claim_next_action_unblocks_after_resource_released(conn):
    job = store.create_job(conn, "resources-release")
    store.add_tasks(conn, job["id"], [
        {"id": "t1", "name": "Task A", "resources": ["naxe/store.py"]},
        {"id": "t2", "name": "Task B", "resources": ["naxe/store.py"]},
    ])

    task = store.claim_next_action(conn, job["id"], "agent-1")
    assert task["id"] == "t1"

    # t2 is blocked while t1 is in-progress
    assert store.claim_next_action(conn, job["id"], "agent-2") is None

    store.update_task_status(conn, "t1", "completed")

    # Now t2 is claimable
    task = store.claim_next_action(conn, job["id"], "agent-2")
    assert task is not None
    assert task["id"] == "t2"


def test_full_crud_lifecycle(conn):
    job = store.create_job(conn, "full-lifecycle")
    store.add_tasks(conn, job["id"], [
        {"id": "t1", "name": "Step 1"},
        {"id": "t2", "name": "Step 2", "depends_on": ["t1"]},
        {"id": "t3", "name": "Step 3", "depends_on": ["t2"]},
    ])

    # Claim and complete t1
    assert store.claim_task(conn, "t1", "agent-1")
    store.update_task_status(conn, "t1", "completed")

    # t2 should now be claimable (pending)
    assert store.get_task(conn, "t2")["status"] == "pending"
    assert store.claim_task(conn, "t2", "agent-2")
    store.update_task_status(conn, "t2", "completed")

    # t3 claimable
    assert store.get_task(conn, "t3")["status"] == "pending"
    assert store.claim_task(conn, "t3", "agent-1")
    store.update_task_status(conn, "t3", "completed")

    tasks = store.get_tasks_for_job(conn, job["id"])
    assert all(t["status"] == "completed" for t in tasks)


# human_task auto-transition

def test_human_task_auto_transitions_when_no_deps(conn):
    job = store.create_job(conn, "human-no-deps")
    store.add_tasks(conn, job["id"], [{"id": "t1", "name": "Human step", "human_task": True}])
    task = store.get_task(conn, "t1")
    assert task["status"] == "awaiting_approval"


def test_human_task_stays_pending_while_blocked(conn):
    job = store.create_job(conn, "human-blocked")
    store.add_tasks(conn, job["id"], [
        {"id": "t1", "name": "Agent step"},
        {"id": "t2", "name": "Human step", "human_task": True, "depends_on": ["t1"]},
    ])
    assert store.get_task(conn, "t2")["status"] == "pending"
    store.claim_task(conn, "t1", "agent-1")
    store.complete_task(conn, "t1", "agent-1")
    assert store.get_task(conn, "t2")["status"] == "awaiting_approval"


# Agent cannot touch human tasks

def test_claim_task_rejects_human_task(conn):
    job = store.create_job(conn, "human-claim-task")
    store.add_tasks(conn, job["id"], [
        {"id": "gate", "name": "Gate"},
        {"id": "h1", "name": "Human", "human_task": True, "depends_on": ["gate"]},
    ])
    # h1 is pending (blocked), not yet awaiting_approval
    assert store.get_task(conn, "h1")["status"] == "pending"
    result = store.claim_task(conn, "h1", "agent-1")
    assert result is False


def test_claim_next_action_skips_human_task(conn):
    job = store.create_job(conn, "human-skip-cna")
    store.add_tasks(conn, job["id"], [{"id": "h1", "name": "Human", "human_task": True}])
    # h1 is awaiting_approval, so claim_next_action should return None
    result = store.claim_next_action(conn, job["id"], "agent-1")
    assert result is None


def test_complete_task_rejects_human_task(conn):
    job = store.create_job(conn, "human-complete-reject")
    store.add_tasks(conn, job["id"], [{"id": "h1", "name": "Human", "human_task": True}])
    # Force status back to pending to test the guard
    conn.execute("UPDATE tasks SET status = 'pending' WHERE id = %s", ("h1",))
    conn.commit()
    result = store.complete_task(conn, "h1", "agent-1")
    assert "error" in result
    assert "human" in result["error"].lower()


# requires_approval unaffected

def test_requires_approval_does_not_auto_transition(conn):
    job = store.create_job(conn, "req-approval-no-auto")
    store.add_tasks(conn, job["id"], [{"id": "t1", "name": "Approval task", "requires_approval": True}])
    task = store.get_task(conn, "t1")
    assert task["status"] == "pending"


def test_requires_approval_agent_flow_intact(conn):
    job = store.create_job(conn, "req-approval-flow")
    store.add_tasks(conn, job["id"], [{"id": "t1", "name": "Approval task", "requires_approval": True}])
    store.claim_task(conn, "t1", "agent-1")
    task = store.request_approval(conn, "t1", "agent-1", notes="please review")
    assert task["status"] == "awaiting_approval"
    result = store.approve_task(conn, "t1", "approver-x")
    assert result is not None
    assert result["task"]["status"] == "completed"


# Startup scan

def test_startup_scan_only_processes_human_tasks(conn):
    job = store.create_job(conn, "startup-scan")
    store.add_tasks(conn, job["id"], [
        {"id": "ra", "name": "Requires approval", "requires_approval": True},
        {"id": "ht", "name": "Human task", "human_task": True},
    ])
    # Force both back to pending to simulate pre-scan state
    conn.execute("UPDATE tasks SET status = 'pending' WHERE job_id = %s", (job["id"],))
    conn.commit()
    store.startup_scan_awaiting_approval(conn)
    assert store.get_task(conn, "ra")["status"] == "pending"
    assert store.get_task(conn, "ht")["status"] == "awaiting_approval"
