from __future__ import annotations

import json
import os
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .runner import run_crawl


class CrawlingHandler(BaseHTTPRequestHandler):
    jobs: dict[str, dict] = {}
    lock = threading.Lock()

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
            with self.lock:
                job = self.jobs.get(run_id)
            if not job:
                self._json(404, {"error": "run_id no encontrado en este proceso"})
                return
            self._json(200, job)
            return
        self._json(404, {"error": "ruta no encontrada"})

    def do_POST(self) -> None:  # noqa: N802
        if urlparse(self.path).path != "/v1/crawl":
            self._json(404, {"error": "ruta no encontrada"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        run_id = str(payload.get("run_id") or uuid.uuid4())
        payload["run_id"] = run_id
        build_root = Path(str(payload.get("build_root", "build"))).expanduser().resolve()
        with self.lock:
            if any(job.get("status") == "RUNNING" and job.get("build_root") == str(build_root) for job in self.jobs.values()):
                self._json(409, {"code": "CRAWL_ALREADY_RUNNING", "build_root": str(build_root)})
                return
            self.jobs[run_id] = {"run_id": run_id, "status": "RUNNING", "build_root": str(build_root)}
        threading.Thread(target=self._run, args=(run_id, payload), daemon=True).start()
        self._json(202, {"run_id": run_id, "status": "RUNNING", "build_root": str(build_root)})

    def _run(self, run_id: str, payload: dict) -> None:
        result = run_crawl(payload)
        with self.lock:
            self.jobs[run_id] = {"run_id": run_id, "status": result["status"], "build_root": payload["build_root"], "result": result}

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    host = os.getenv("CRAWL_BIND_HOST", "0.0.0.0")
    port = int(os.getenv("CRAWL_PORT", "8090"))
    ThreadingHTTPServer((host, port), CrawlingHandler).serve_forever()


if __name__ == "__main__":
    main()
