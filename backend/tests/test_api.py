"""The HTTP surface, exercised as the UI drives it."""

from __future__ import annotations

from pathlib import Path

SPLIT_SHOW = "tv shows/Cowboy Bebop"


def scan(client, root: str = "/mnt/disk1/data/media") -> dict:
    response = client.post("/api/scan", json={"root": root})
    assert response.status_code == 200, response.text
    return response.json()


def test_health_reports_the_disks_it_can_see(client) -> None:
    body = client.get("/api/health").json()
    assert body["ok"] is True
    assert body["backend"] == "local"
    assert [d["name"] for d in body["disks"]] == ["disk1", "disk2", "disk3", "disk4"]


def test_scan_normalises_a_pasted_disk_path(client) -> None:
    summary = scan(client)
    assert summary["root"] == "data/media"
    assert summary["total_files"] > 0
    assert summary["dup_files"] > 0

    active = client.get("/api/scan").json()
    assert active["scanning"] is False
    assert active["scan"]["id"] == summary["id"]


def test_scan_views_are_unavailable_until_something_is_indexed(client) -> None:
    for path in ("/api/tree", "/api/duplicates", "/api/fragmented", "/api/queue/preview"):
        response = (
            client.post(path, json={"source_relpath": "x", "target_disk": "disk1"})
            if path.endswith("preview")
            else client.get(path)
        )
        assert response.status_code == 409, path
        assert "run a scan" in response.json()["detail"]


def test_tree_returns_the_node_its_children_and_breadcrumbs(client) -> None:
    scan(client)
    body = client.get("/api/tree", params={"path": SPLIT_SHOW}).json()

    assert body["node"]["relpath"] == SPLIT_SHOW
    assert body["node"]["disk_count"] > 1
    assert [c["name"] for c in body["breadcrumbs"]] == ["tv shows", "Cowboy Bebop"]
    assert all(child["is_dir"] for child in body["children"])


def test_an_unknown_tree_path_is_a_404(client) -> None:
    scan(client)
    assert client.get("/api/tree", params={"path": "nope/nothing"}).status_code == 404


def test_duplicates_are_sorted_by_reclaimable_bytes(client) -> None:
    scan(client)
    body = client.get("/api/duplicates").json()

    wasted = [g["wasted_bytes"] for g in body["groups"]]
    assert wasted == sorted(wasted, reverse=True)
    assert body["total_wasted_bytes"] == sum(wasted)
    assert {g["kind"] for g in body["groups"]} <= {"identical", "variant"}


def test_duplicates_reject_an_unknown_kind(client) -> None:
    scan(client)
    response = client.get("/api/duplicates", params={"kind": "sideways"})
    assert response.status_code == 400


def test_preview_does_not_touch_the_queue(client) -> None:
    scan(client)
    body = client.post(
        "/api/queue/preview", json={"source_relpath": SPLIT_SHOW, "target_disk": "disk1"}
    ).json()

    assert body["conflicts"]
    assert "decision" in body["blocked_reason"]
    assert body["disks_after"]["disk1"]["delta"] > 0
    assert client.get("/api/queue").json()["jobs"] == []


def test_preview_rejects_an_unknown_disk_or_path(client) -> None:
    scan(client)
    assert (
        client.post(
            "/api/queue/preview", json={"source_relpath": SPLIT_SHOW, "target_disk": "disk99"}
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/queue/preview", json={"source_relpath": "nope", "target_disk": "disk1"}
        ).status_code
        == 404
    )


def test_the_full_plan_and_dry_run_flow(client) -> None:
    scan(client)

    plan = client.post(
        "/api/queue", json={"source_relpath": SPLIT_SHOW, "target_disk": "disk1"}
    ).json()
    job = plan["jobs"][0]["job"]
    assert job["status"] == "draft"
    assert job["unresolved_conflicts"] == len(job["conflicts"]) > 0
    assert plan["ready"] is False

    # Running is refused while decisions are outstanding.
    refused = client.post("/api/execute/start", json={"dry_run": True}).json()
    assert refused["started"] is False

    plan = client.post(
        f"/api/queue/{job['id']}/resolve-all", json={"mode": "keep_larger"}
    ).json()
    job = plan["jobs"][0]["job"]
    assert job["status"] == "ready"
    assert job["unresolved_conflicts"] == 0
    assert plan["ready"] is True
    assert plan["total_move_bytes"] > 0

    operations = client.get(f"/api/queue/{job['id']}/operations").json()
    assert {op["kind"] for op in operations} == {"move", "delete", "prune"}

    started = client.post("/api/execute/start", json={"dry_run": True}).json()
    assert started["started"] is True

    state = client.get("/api/execute/state").json()
    assert state["dry_run"] is True
    assert state["ops_done"] == state["ops_total"] == len(operations)

    log = client.get("/api/execute/log").json()
    assert log[0]["type"] == "queue_start"
    assert log[-1]["type"] == "queue_done"
    assert all(event["dry_run"] for event in log)

    leftover = client.get("/api/queue").json()["jobs"]
    assert leftover[0]["job"]["id"] == job["id"]
    assert leftover[0]["job"]["status"] == "ready"


def test_a_live_run_requires_the_confirmation_phrase(client) -> None:
    scan(client)
    plan = client.post(
        "/api/queue", json={"source_relpath": "music", "target_disk": "disk1"}
    ).json()
    assert plan["jobs"][0]["job"]["status"] == "ready"

    assert client.post("/api/execute/start", json={"dry_run": False}).status_code == 400
    assert (
        client.post("/api/execute/start", json={"dry_run": False, "confirm": "yes"}).status_code
        == 400
    )


def test_a_confirmed_live_run_actually_moves_data(client, mount: Path) -> None:
    scan(client)
    client.post("/api/queue", json={"source_relpath": "music", "target_disk": "disk1"})

    started = client.post(
        "/api/execute/start", json={"dry_run": False, "confirm": "MOVE MY FILES"}
    ).json()
    assert started["started"] is True

    # The executor runs on a worker thread; the log ends when it is done.
    from app.service import service

    thread = service.executor._thread
    assert thread is not None
    thread.join(120)

    assert not (mount / "disk4/data/media/music").exists()
    assert (mount / "disk1/data/media/music").is_dir()
    assert client.get("/api/queue").json()["jobs"] == []


def test_resolving_a_single_conflict_updates_only_that_row(client) -> None:
    scan(client)
    plan = client.post(
        "/api/queue", json={"source_relpath": SPLIT_SHOW, "target_disk": "disk1"}
    ).json()
    job = plan["jobs"][0]["job"]
    target = job["conflicts"][0]

    plan = client.post(
        f"/api/queue/{job['id']}/resolve",
        json={"relpath": target["relpath"], "mode": "keep_disk", "keep_disk": "disk3"},
    ).json()
    conflicts = {c["relpath"]: c for c in plan["jobs"][0]["job"]["conflicts"]}

    assert conflicts[target["relpath"]]["survivor"] == "disk3"
    assert conflicts[target["relpath"]]["resolved"] is True
    assert plan["jobs"][0]["job"]["unresolved_conflicts"] == len(conflicts) - 1


def test_keep_disk_without_a_disk_is_rejected(client) -> None:
    scan(client)
    plan = client.post(
        "/api/queue", json={"source_relpath": SPLIT_SHOW, "target_disk": "disk1"}
    ).json()
    job_id = plan["jobs"][0]["job"]["id"]
    relpath = plan["jobs"][0]["job"]["conflicts"][0]["relpath"]

    response = client.post(
        f"/api/queue/{job_id}/resolve", json={"relpath": relpath, "mode": "keep_disk"}
    )
    assert response.status_code == 400
    assert "keep_disk is required" in response.json()["detail"]


def test_reordering_reverses_the_projection_order(client) -> None:
    scan(client)
    client.post("/api/queue", json={"source_relpath": "music", "target_disk": "disk1"})
    client.post("/api/queue", json={"source_relpath": "movies", "target_disk": "disk2"})
    plan = client.get("/api/queue").json()
    ids = [jp["job"]["id"] for jp in plan["jobs"]]

    reordered = client.post("/api/queue/reorder", json={"order": list(reversed(ids))}).json()
    assert [jp["job"]["id"] for jp in reordered["jobs"]] == list(reversed(ids))
    assert [jp["job"]["position"] for jp in reordered["jobs"]] == [0, 1]


def test_removing_and_clearing_the_queue(client) -> None:
    scan(client)
    plan = client.post("/api/queue", json={"source_relpath": "music", "target_disk": "disk1"}).json()
    job_id = plan["jobs"][0]["job"]["id"]

    assert client.delete(f"/api/queue/{job_id}").json()["jobs"] == []
    assert client.delete(f"/api/queue/{job_id}").status_code == 404

    client.post("/api/queue", json={"source_relpath": "music", "target_disk": "disk1"})
    assert client.delete("/api/queue").json()["jobs"] == []


def test_queueing_something_outside_the_scan_is_rejected(client) -> None:
    scan(client)
    response = client.post(
        "/api/queue", json={"source_relpath": "not/here", "target_disk": "disk1"}
    )
    assert response.status_code == 400
    assert "not part of the current scan" in response.json()["detail"]


def test_switching_to_ssh_drops_the_fixture_mount_root(client) -> None:
    updated = client.put(
        "/api/connection",
        json={
            "storage_backend": "ssh",
            "ssh_host": "tower.local",
            "mount_root": "/tmp/shuffler-fixtures/mnt",
        },
    ).json()
    assert updated["mount_root"] == "/mnt"


def test_connection_settings_round_trip_without_leaking_the_password(client) -> None:
    updated = client.put(
        "/api/connection",
        json={"ssh_host": "tower.local", "ssh_user": "root", "ssh_password": "hunter2"},
    ).json()

    assert updated["ssh_host"] == "tower.local"
    assert updated["has_password"] is True
    assert updated["ssh_password"] is None

    fetched = client.get("/api/connection").json()
    assert fetched["ssh_password"] is None
    assert fetched["has_password"] is True

    cleared = client.put("/api/connection", json={"ssh_password": ""}).json()
    assert cleared["has_password"] is False


def test_the_reserve_setting_changes_what_is_runnable(client) -> None:
    scan(client)
    client.post("/api/queue", json={"source_relpath": SPLIT_SHOW, "target_disk": "disk1"})
    plan = client.get("/api/queue").json()
    job_id = plan["jobs"][0]["job"]["id"]
    client.post(f"/api/queue/{job_id}/resolve-all", json={"mode": "keep_larger"})
    assert client.get("/api/queue").json()["ready"] is True

    client.put("/api/connection", json={"reserve_bytes": 55 * 1024**3})
    blocked = client.get("/api/queue").json()
    assert blocked["ready"] is False
    assert any("reserve" in reason for reason in blocked["blocked_reasons"])


def test_scan_history_lists_and_reactivates(client) -> None:
    first = scan(client, "data/media/movies")
    second = scan(client, "data/media")

    history = client.get("/api/scans").json()
    assert [entry["id"] for entry in history][0] == second["id"]
    assert first["id"] in {entry["id"] for entry in history}

    reactivated = client.post(f"/api/scans/{first['id']}/activate").json()
    assert reactivated["root"] == "data/media/movies"
    assert client.get("/api/scan").json()["scan"]["id"] == first["id"]
    assert client.post("/api/scans/nope/activate").status_code == 404


def test_normalise_root_is_exposed_for_the_ui(client) -> None:
    body = client.get("/api/normalise-root", params={"path": "/mnt/disk7/data/media/"}).json()
    assert body["root"] == "data/media"


def test_version_reports_the_running_configuration(client) -> None:
    body = client.get("/api/version").json()
    assert body["version"]
    assert "dry_run" in body


def test_a_job_interrupted_by_a_restart_is_not_left_running(client, tmp_path) -> None:
    """Nothing survives a restart, so a persisted RUNNING job was interrupted."""
    from app import db
    from app.models import Job, JobStatus
    from app.service import Service

    scan(client)
    plan = client.post("/api/queue", json={"source_relpath": "music", "target_disk": "disk1"}).json()
    job = Job(**plan["jobs"][0]["job"])
    job.status = JobStatus.RUNNING
    db.save_job(job.id, job.position, job.created_at, job.model_dump(mode="json"))

    revived = Service()
    restored = next(j for j in revived.planner.jobs if j.id == job.id)
    assert restored.status is JobStatus.FAILED
    assert "restart" in (restored.error or "")


def test_a_failed_job_can_be_retried(client) -> None:
    from app.models import JobStatus
    from app.service import service

    scan(client)
    plan = client.post("/api/queue", json={"source_relpath": "music", "target_disk": "disk1"}).json()
    job_id = plan["jobs"][0]["job"]["id"]

    job = service.planner.get(job_id)
    assert job is not None
    job.status = JobStatus.FAILED
    job.error = "remote command failed: TimeoutError"
    job.bytes_done = 123

    retried = client.post(f"/api/queue/{job_id}/retry", json={}).json()
    revived = retried["jobs"][0]["job"]
    assert revived["status"] == "ready"
    assert revived["error"] is None
    assert revived["bytes_done"] == 0
    assert retried["ready"] is True


def test_retrying_a_job_that_did_not_fail_is_refused(client) -> None:
    scan(client)
    plan = client.post("/api/queue", json={"source_relpath": "music", "target_disk": "disk1"}).json()
    job_id = plan["jobs"][0]["job"]["id"]

    response = client.post(f"/api/queue/{job_id}/retry", json={})
    assert response.status_code == 409
    assert "only a failed job" in response.json()["detail"]
    assert client.post("/api/queue/nope/retry", json={}).status_code == 404


def test_a_retry_keeps_the_conflict_decisions(client) -> None:
    from app.models import JobStatus
    from app.service import service

    scan(client)
    plan = client.post(
        "/api/queue", json={"source_relpath": SPLIT_SHOW, "target_disk": "disk1"}
    ).json()
    job_id = plan["jobs"][0]["job"]["id"]
    client.post(f"/api/queue/{job_id}/resolve-all", json={"mode": "keep_larger"})

    job = service.planner.get(job_id)
    assert job is not None
    job.status = JobStatus.FAILED

    revived = client.post(f"/api/queue/{job_id}/retry", json={}).json()["jobs"][0]["job"]
    assert revived["status"] == "ready"
    assert revived["unresolved_conflicts"] == 0


def test_execution_state_exposes_the_per_file_figures(client) -> None:
    """The UI polls this endpoint, so it must carry what the event stream does."""
    scan(client)
    client.post("/api/queue", json={"source_relpath": "music", "target_disk": "disk1"})
    client.post("/api/execute/start", json={"dry_run": True})

    state = client.get("/api/execute/state").json()
    for field in (
        "current_file",
        "file_bytes_done",
        "file_bytes_total",
        "bytes_per_second",
        "eta_seconds",
    ):
        assert field in state, field


def test_a_dry_run_claims_no_transfer_speed(client) -> None:
    """Nothing is copied, so any rate would be a fiction."""
    scan(client)
    client.post("/api/queue", json={"source_relpath": "music", "target_disk": "disk1"})
    client.post("/api/execute/start", json={"dry_run": True})

    state = client.get("/api/execute/state").json()
    assert state["bytes_per_second"] == 0
    assert state["eta_seconds"] is None


def test_a_finished_job_disappears_from_the_queue(client) -> None:
    """A completed row is history, not work — fetching the queue drops it."""
    from app.models import JobStatus
    from app.service import service

    scan(client)
    plan = client.post("/api/queue", json={"source_relpath": "music", "target_disk": "disk1"}).json()
    job = service.planner.get(plan["jobs"][0]["job"]["id"])
    assert job is not None
    job.status = JobStatus.DONE

    assert client.get("/api/queue").json()["jobs"] == []
