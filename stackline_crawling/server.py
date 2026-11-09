from __future__ import annotations

import json
import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .discovery import run_discovery
from .runner import run_crawl


_TERMINAL_STATUSES = {"COMPLETED", "QUALITY_FAILED", "EXECUTION_FAILED"}


def _default_storage_root() -> Path:
    configured = os.getenv("CRAWL_STORAGE_ROOT") or os.getenv("CRAWLING_STORAGE_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    # Desarrollo local: stack-line-crawling y .local son hermanos bajo stackline.
    sibling = Path.cwd().parent / ".local" / "crawling"
    return sibling.resolve() if sibling.is_dir() else (Path.cwd() / ".crawler-jobs").resolve()


def _safe_run_id(run_id: str) -> str:
    return "".join(char for char in run_id if char.isalnum() or char in {"-", "_"}) or "unknown"


class CrawlingHandler(BaseHTTPRequestHandler):
    jobs: dict[str, dict] = {}
    lock = threading.Lock()
    storage_root = _default_storage_root()
    registry_root = storage_root / ".jobs"

    @classmethod
    def _registry_path(cls, run_id: str) -> Path:
        return cls.registry_root / f"{_safe_run_id(run_id)}.json"

    @classmethod
    def _persist_job(cls, job: dict) -> None:
        cls.registry_root.mkdir(parents=True, exist_ok=True)
        path = cls._registry_path(str(job["run_id"]))
        temporary = path.with_suffix(".json.tmp")
        persisted = {
            key: job.get(key)
            for key in ("run_id", "status", "build_root", "mode", "error")
            if key in job
        }
        temporary.write_text(json.dumps(persisted, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    @classmethod
    def _load_registry_job(cls, run_id: str) -> dict | None:
        path = cls._registry_path(run_id)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    @staticmethod
    def _result_path(job: dict) -> Path:
        name = "categories-result.json" if job.get("mode") == "discover" else "crawl-result.json"
        return Path(str(job.get("build_root", ""))) / "results" / name

    @classmethod
    def _load_result(cls, job: dict) -> dict | None:
        try:
            value = json.loads(cls._result_path(job).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    @classmethod
    def _read_progress(cls, job: dict) -> dict | None:
        try:
            value = json.loads((Path(str(job.get("build_root", ""))) / "run-state.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    @classmethod
    def _mark_restarted(cls, job: dict) -> dict:
        """Convert an in-memory orphan into an explicit terminal failure."""
        if job.get("status") != "RUNNING":
            return job
        result = cls._load_result(job)
        if result and result.get("status") in _TERMINAL_STATUSES:
            job["status"] = result["status"]
            cls._persist_job(job)
            return job
        error = {
            "code": "CRAWLER_RESTARTED",
            "message": "The crawler process restarted during this run; start a new run.",
            "retryable": True,
        }
        job["status"] = "EXECUTION_FAILED"
        job["error"] = error
        state_path = Path(str(job.get("build_root", ""))) / "run-state.json"
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state.update({"status": "EXECUTION_FAILED", "finished_at": time.time(), "error": error})
            temporary = state_path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(state_path)
        except (OSError, json.JSONDecodeError):
            pass
        cls._persist_job(job)
        return job

    @classmethod
    def _recover_from_storage(cls, run_id: str) -> dict | None:
        """Recover a job created by a previous crawler process.

        A process restart cannot resume a Python thread. If its persisted state is still
        RUNNING, expose a terminal restart error instead of making the API poll forever.
        """
        if not cls.storage_root.is_dir():
            return None
        for state_path in cls.storage_root.rglob("run-state.json"):
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if state.get("run_id") != run_id:
                continue
            build_root = state_path.parent
            mode = "discover" if (build_root / "results" / "categories-result.json").exists() else "crawl"
            job = {"run_id": run_id, "status": state.get("status", "RUNNING"), "build_root": str(build_root), "mode": mode}
            job = cls._mark_restarted(job)
            return job
        return None

    @classmethod
    def _find_job(cls, run_id: str) -> dict | None:
        with cls.lock:
            job = cls.jobs.get(run_id)
        if job:
            return job
        job = cls._load_registry_job(run_id)
        if job and job.get("status") == "RUNNING":
            job = cls._mark_restarted(job)
        if job is None:
            job = cls._recover_from_storage(run_id)
        if job:
            with cls.lock:
                cls.jobs[run_id] = job
        return job

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/healthz":
            self._json(200, {"status": "ok", "service": "stackline-crawling"})
            return
        if path.startswith("/v1/crawl/"):
            run_id = path.rsplit("/", 1)[-1]
            job = self._find_job(run_id)
            if not job:
                self._json(404, {"error": "run_id no encontrado"})
                return
            response = dict(job)
            result = job.get("result") if isinstance(job.get("result"), dict) else None
            if result is None and response.get("status") in _TERMINAL_STATUSES:
                result = self._load_result(job)
            if result is not None:
                response["result"] = result
            progress = self._read_progress(job)
            if progress is not None:
                response["progress"] = progress
                if response.get("status") == "RUNNING":
                    response.update(progress)
            self._json(200, response)
            return
        self._json(404, {"error": "ruta no encontrada"})

    def do_POST(self) -> None:  # noqa: N802
        route = urlparse(self.path).path
        if route not in {"/v1/crawl", "/v1/discover"}:
            self._json(404, {"error": "ruta no encontrada"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        run_id = str(payload.get("run_id") or uuid.uuid4())
        payload["run_id"] = run_id
        build_root = Path(str(payload.get("build_root", "build"))).expanduser().resolve()
        mode = "discover" if route == "/v1/discover" else "crawl"
        job = {"run_id": run_id, "status": "RUNNING", "build_root": str(build_root), "mode": mode}
        with self.lock:
            active_job = next((item for item in self.jobs.values()
                               if item.get("status") == "RUNNING" and item.get("build_root") == str(build_root)), None)
            if active_job:
                self._json(409, {
                    "code": "CRAWL_ALREADY_RUNNING",
                    "build_root": str(build_root),
                    "active_run_id": active_job.get("run_id"),
                })
                return
            self.jobs[run_id] = job
            self._persist_job(job)
        print(f"[crawler] {mode} started run_id={run_id} build_root={build_root}", flush=True)
        runner = self._discover if mode == "discover" else self._run
        threading.Thread(target=runner, args=(run_id, payload), daemon=True).start()
        self._json(202, {"run_id": run_id, "status": "RUNNING", "build_root": str(build_root)})

    def _finish(self, run_id: str, payload: dict, result: dict) -> None:
        job = {
            "run_id": run_id,
            "status": result.get("status", "EXECUTION_FAILED"),
            "build_root": str(payload["build_root"]),
            "mode": "discover" if payload.get("_mode") == "discover" else "crawl",
            "result": result,
        }
        with self.lock:
            self.jobs[run_id] = job
            self._persist_job(job)
        print(f"[crawler] {job['mode']} finished run_id={run_id} status={job['status']}", flush=True)

    def _run(self, run_id: str, payload: dict) -> None:
        try:
            result = run_crawl(payload)
        except Exception as error:  # noqa: BLE001
            result = {"status": "EXECUTION_FAILED", "error": {"code": "CRAWL_WORKER_ERROR", "message": str(error), "retryable": True}}
        self._finish(run_id, {**payload, "_mode": "crawl"}, result)

    def _discover(self, run_id: str, payload: dict) -> None:
        try:
            result = run_discovery(payload)
        except Exception as error:  # noqa: BLE001
            result = {"status": "EXECUTION_FAILED", "error": {"code": "DISCOVERY_WORKER_ERROR", "message": str(error), "retryable": True}}
        self._finish(run_id, {**payload, "_mode": "discover"}, result)

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    host = os.getenv("CRAWL_BIND_HOST", "0.0.0.0")
    port = int(os.getenv("CRAWL_PORT", "8090"))
    CrawlingHandler.storage_root = _default_storage_root()
    CrawlingHandler.registry_root = CrawlingHandler.storage_root / ".jobs"
    print(f"[crawler] listening on {host}:{port} storage_root={CrawlingHandler.storage_root}", flush=True)
    ThreadingHTTPServer((host, port), CrawlingHandler).serve_forever()


if __name__ == "__main__":
    main()
