from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .contracts import ContractError, Thresholds, validate_result
from .crawler import PublicSiteCrawler
from .health import evaluate_health
from .sop_runtime import read_sop_section, sop_index, update_sop_section
from .scaffold import ensure_scaffold, is_scaffold, mark_ready
from .workspace_agent import AgentExecutionError, OpenAIWorkspaceAgent
from .workspace_runtime import (
    WorkspaceError,
    delete_workspace,
    fetch_public_url,
    list_workspace,
    query_artifacts,
    read_workspace,
    run_workspace,
    write_workspace,
)


class BuildLock:
    def __init__(self, build_root: Path, run_id: str) -> None:
        self.root = build_root
        self.run_id = run_id
        self.path = build_root / ".lock"
        self.handle = None

    def __enter__(self) -> "BuildLock":
        self.root.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+")
        try:
            import fcntl
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as error:
            self.handle.close()
            raise RuntimeError("Ya hay un crawling ejecutándose para este build") from error
        self.handle.seek(0)
        self.handle.truncate()
        self.handle.write(json.dumps({"run_id": self.run_id, "started_at": time.time()}))
        self.handle.flush()
        return self

    def __exit__(self, *_: object) -> None:
        if self.handle is not None:
            try:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            finally:
                self.handle.close()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _append_transcript(path: Path, role: str, content: str, metadata: dict[str, Any] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"timestamp": time.time(), "role": role, "content": content, "metadata": metadata or {}}
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _base_result(run_id: str, source: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "run_id": run_id,
        "status": "EXECUTION_FAILED",
        "source": source,
        "data": {"categories": [], "products": []},
        "metrics": {
            "pages_discovered": 0, "pages_visited": 0, "pages_failed": 0,
            "products_extracted": 0, "products_with_price": 0, "categories_extracted": 0,
            "product_links_found": 0, "pagination_links_found": 0, "duplicate_products": 0,
            "blocked_or_challenge_detected": False, "response_size_bytes": 0,
            "html_fingerprint": "not-available", "duration_ms": 0,
        },
        "quality": {"state": "failed", "score": 0.0, "threshold": 0.80, "reasons": [], "anomalies": []},
        "evidence": [],
        "error": None,
    }


def _agent_summary(result: dict[str, Any], quality_attempts: int, technical_iterations: int) -> dict[str, Any]:
    metrics = result.get("metrics", {})
    visible_metrics = {
        key: metrics.get(key)
        for key in (
            "pages_discovered", "pages_visited", "pages_failed", "products_extracted",
            "products_with_price", "categories_extracted", "product_links_found",
            "pagination_links_found", "duplicate_products", "blocked_or_challenge_detected",
            "response_size_bytes", "duration_ms", "html_fingerprint",
        )
        if key in metrics
    }
    return {
        "ok": result.get("status") == "COMPLETED",
        "status": result.get("status"),
        "health": result.get("quality"),
        "metrics": visible_metrics,
        "quality_attempts": quality_attempts,
        "technical_iterations": technical_iterations,
        "error": result.get("error"),
    }


def _finalize_generated_result(
    raw: dict[str, Any],
    run_id: str,
    source: dict[str, Any],
    baseline_products: int | None,
    thresholds: Thresholds,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise WorkspaceError("El crawler debe producir un objeto JSON")
    data = raw.get("data")
    metrics = raw.get("metrics")
    if not isinstance(data, dict) or not isinstance(data.get("products"), list) or not isinstance(data.get("categories"), list):
        raise WorkspaceError("El crawler debe producir data.products y data.categories como listas")
    if not isinstance(metrics, dict):
        raise WorkspaceError("El crawler debe producir metrics como objeto")
    products = data["products"]
    categories = data["categories"]
    if metrics.get("products_extracted") != len(products):
        raise WorkspaceError("metrics.products_extracted no coincide con data.products")
    if metrics.get("categories_extracted") != len(categories):
        raise WorkspaceError("metrics.categories_extracted no coincide con data.categories")
    result = dict(raw)
    result.update({"schema_version": 1, "run_id": run_id, "source": source, "data": data, "metrics": metrics})
    health = evaluate_health(metrics, baseline_products, thresholds)
    result["quality"] = health
    result["status"] = "COMPLETED" if health["state"] in {"healthy", "first_run"} else "QUALITY_FAILED"
    result["error"] = None if result["status"] == "COMPLETED" else {
        "code": "QUALITY_THRESHOLD_FAILED",
        "message": "El resultado no superó los umbrales",
        "retryable": True,
    }
    validate_result(result)
    return result


def run_crawl(payload: dict[str, Any]) -> dict[str, Any]:
    run_id = str(payload.get("run_id") or uuid.uuid4())
    site_url = str(payload.get("site_url", ""))
    site_key = str(payload.get("site_key", "site"))
    build_root = Path(str(payload.get("build_root", "build"))).expanduser().resolve()
    workspace_root = build_root / "workspace"
    site_sop_path = workspace_root / "site-sop.md"
    source = {"site_key": payload.get("site_key"), "site_url": site_url}
    result = _base_result(run_id, source)
    transcript = build_root / "logs" / "agent-transcript.jsonl"
    latest_result: dict[str, Any] | None = None
    quality_attempts = 0
    technical_iterations = 0
    thresholds = Thresholds.from_payload(payload.get("thresholds"))

    def append(role: str, content: str, metadata: dict[str, Any] | None = None) -> None:
        _append_transcript(transcript, role, content, metadata)

    try:
        with BuildLock(build_root, run_id):
            for child in build_root.iterdir():
                if child.name not in {".lock", "workspace"}:
                    shutil.rmtree(child) if child.is_dir() else child.unlink()
            workspace_root.mkdir(parents=True, exist_ok=True)
            scaffold_created = ensure_scaffold(workspace_root, site_url)
            (build_root / "logs").mkdir(parents=True, exist_ok=True)
            (build_root / "results").mkdir(parents=True, exist_ok=True)
            (build_root / "evidence").mkdir(parents=True, exist_ok=True)
            _write_json(build_root / "run-state.json", {
                "run_id": run_id,
                "status": "RUNNING",
                "started_at": time.time(),
                "quality_attempts": quality_attempts,
                "technical_iterations": technical_iterations,
            })
            append("system", "Crawling iniciado", source)

            if os.getenv("AI_MODE", "real").lower() == "fake":
                observation = PublicSiteCrawler(build_root).crawl(site_url, payload.get("max_pages"))
                raw = {
                    "schema_version": 1,
                    "run_id": run_id,
                    "source": source,
                    "data": {"categories": observation["categories"], "products": observation["products"]},
                    "metrics": observation["metrics"],
                    "evidence": [],
                    "error": None,
                }
                latest_result = _finalize_generated_result(raw, run_id, source, payload.get("baseline_products"), thresholds)
                _write_json(build_root / "results/crawl-result.json", latest_result)
                _write_json(build_root / "results/health-result.json", latest_result["quality"])
                append("assistant", "Modo fake: el crawler determinista produjo el JSON contractual", latest_result["quality"])
            else:
                def execute_current_workspace(_arguments: dict[str, Any]) -> dict[str, Any]:
                    nonlocal latest_result, quality_attempts, technical_iterations
                    if quality_attempts >= 3:
                        return {"ok": False, "status": "QUALITY_FAILED", "error": {"code": "QUALITY_ATTEMPTS_EXHAUSTED", "message": "Se agotaron los 3 intentos de quality"}, "stop_agent": True}
                    if technical_iterations >= 10:
                        return {"ok": False, "status": "EXECUTION_FAILED", "error": {"code": "TECHNICAL_ITERATIONS_EXHAUSTED", "message": "Se agotaron las iteraciones técnicas"}, "stop_agent": True}
                    technical_iterations += 1
                    try:
                        raw = run_workspace(workspace_root, build_root, site_url, run_id)
                        candidate = _finalize_generated_result(raw, run_id, source, payload.get("baseline_products"), thresholds)
                    except (WorkspaceError, ContractError, ValueError) as error:
                        append("tool", "run_workspace falló técnicamente", {"error": str(error), "technical_iterations": technical_iterations})
                        _write_json(build_root / "run-state.json", {
                            "run_id": run_id, "status": "RUNNING", "started_at": time.time(),
                            "quality_attempts": quality_attempts, "technical_iterations": technical_iterations,
                            "last_error": str(error),
                        })
                        return {
                            "ok": False,
                            "status": "EXECUTION_FAILED",
                            "quality_attempts": quality_attempts,
                            "technical_iterations": technical_iterations,
                            "error": {"code": "TECHNICAL_EXECUTION_ERROR", "message": str(error), "retryable": True},
                            "stop_agent": technical_iterations >= 10,
                        }
                    quality_attempts += 1
                    mark_ready(workspace_root)
                    latest_result = candidate
                    _write_json(build_root / "results/crawl-result.json", candidate)
                    _write_json(build_root / "results/health-result.json", candidate["quality"])
                    _write_json(build_root / "run-state.json", {
                        "run_id": run_id, "status": candidate["status"], "finished_at": time.time(),
                        "quality_attempts": quality_attempts, "technical_iterations": technical_iterations,
                        "quality": candidate["quality"],
                    })
                    append("tool", "run_workspace produjo resultado evaluable", _agent_summary(candidate, quality_attempts, technical_iterations))
                    summary = _agent_summary(candidate, quality_attempts, technical_iterations)
                    sop_pending = site_sop_path.is_file() and "Pendiente de descubrir por el agente." in site_sop_path.read_text(encoding="utf-8")
                    summary["sop_review_required"] = sop_pending
                    summary["stop_agent"] = quality_attempts >= 3 or (candidate["status"] == "COMPLETED" and not sop_pending)
                    return summary

                has_existing_crawler = not scaffold_created and not is_scaffold(workspace_root)
                user_feedback = str(payload.get("user_prompt") or "").strip()
                if has_existing_crawler:
                    initial_state = execute_current_workspace({})
                    if initial_state.get("status") == "COMPLETED" and not user_feedback:
                        result = latest_result
                        return result
                    reason = "user_feedback" if user_feedback else (
                        "health_below_threshold" if initial_state.get("status") == "QUALITY_FAILED" else "execution_error"
                    )
                else:
                    initial_state = {
                        "status": "NOT_CREATED",
                        "reason": "first_script",
                        "quality_attempts": 0,
                        "technical_iterations": 0,
                    }
                    reason = "first_script"

                initial_state = {**initial_state, "invocation_reason": reason, "user_feedback": user_feedback}
                host = urlparse(site_url).hostname or ""
                handlers = {
                    "workspace_list": lambda _args: {"files": list_workspace(workspace_root)},
                    "workspace_read": lambda args: read_workspace(workspace_root, str(args["path"]), int(args.get("offset", 0)), int(args.get("limit", 240))),
                    "workspace_write": lambda args: write_workspace(workspace_root, str(args["path"]), str(args["content"])),
                    "workspace_delete": lambda args: delete_workspace(workspace_root, str(args["path"])),
                    "sop_read": lambda args: read_sop_section(site_sop_path, str(args["section"])),
                    "sop_update": lambda args: update_sop_section(site_sop_path, str(args["section"]), str(args["content"])),
                    "web_fetch": lambda args: fetch_public_url(str(args["url"]), host, build_root),
                    "query_artifacts": lambda args: query_artifacts(build_root, str(args["command"])),
                    "run_workspace": execute_current_workspace,
                }
                agent = OpenAIWorkspaceAgent(
                    model=str(payload.get("model") or os.getenv("AI_CHAT_MODEL", "gpt-6-luna")),
                    site_url=site_url,
                    site_key=site_key,
                    sop=str(payload.get("sop", "")),
                    workspace_files=list_workspace(workspace_root),
                    initial_state=initial_state,
                    site_sop_index=sop_index(site_sop_path),
                    handlers=handlers,
                    transcript=append,
                )
                agent.run()
                if latest_result is None:
                    raise WorkspaceError("La IA terminó sin ejecutar un crawler válido")

            result = latest_result
            return result
    except (ContractError, AgentExecutionError, RuntimeError, ValueError, WorkspaceError) as error:
        result["error"] = {"code": "EXECUTION_ERROR", "message": str(error), "retryable": True}
        _write_json(build_root / "results/crawl-result.json", result)
        _write_json(build_root / "run-state.json", {
            "run_id": run_id, "status": "EXECUTION_FAILED", "finished_at": time.time(),
            "quality_attempts": quality_attempts, "technical_iterations": technical_iterations,
            "error": str(error),
        })
        append("system", "Fallo de ejecución", {"error": str(error)})
        return result
