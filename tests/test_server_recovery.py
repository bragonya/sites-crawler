import json

from stackline_crawling.server import CrawlingHandler


def test_recover_running_job_as_terminal_after_restart(tmp_path):
    storage = tmp_path / "crawling"
    build = storage / "clients" / "demo" / "sites" / "site-1" / "build"
    build.mkdir(parents=True)
    run_id = "orphan-run"
    (build / "run-state.json").write_text(json.dumps({
        "run_id": run_id,
        "status": "RUNNING",
        "stage": "executing_crawler",
    }))

    previous_storage = CrawlingHandler.storage_root
    previous_registry = CrawlingHandler.registry_root
    CrawlingHandler.storage_root = storage
    CrawlingHandler.registry_root = storage / ".jobs"
    try:
        recovered = CrawlingHandler._find_job(run_id)
        assert recovered["status"] == "EXECUTION_FAILED"
        assert recovered["error"]["code"] == "CRAWLER_RESTARTED"
        state = json.loads((build / "run-state.json").read_text())
        assert state["status"] == "EXECUTION_FAILED"
        assert (storage / ".jobs" / f"{run_id}.json").is_file()
    finally:
        CrawlingHandler.storage_root = previous_storage
        CrawlingHandler.registry_root = previous_registry


def test_registry_running_job_is_not_reported_forever(tmp_path):
    storage = tmp_path / "crawling"
    build = storage / "build"
    build.mkdir(parents=True)
    run_id = "persisted-run"
    registry = storage / ".jobs"
    registry.mkdir()
    (registry / f"{run_id}.json").write_text(json.dumps({
        "run_id": run_id,
        "status": "RUNNING",
        "build_root": str(build),
        "mode": "discover",
    }))

    previous_storage = CrawlingHandler.storage_root
    previous_registry = CrawlingHandler.registry_root
    CrawlingHandler.storage_root = storage
    CrawlingHandler.registry_root = registry
    try:
        recovered = CrawlingHandler._find_job(run_id)
        assert recovered["status"] == "EXECUTION_FAILED"
        assert recovered["error"]["code"] == "CRAWLER_RESTARTED"
    finally:
        CrawlingHandler.storage_root = previous_storage
        CrawlingHandler.registry_root = previous_registry
