from __future__ import annotations

import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .contracts import ContractError, validate_categories_result
from .runner import BuildLock, RunHeartbeat, _append_transcript, _write_json
from .scaffold import ensure_scaffold, is_scaffold
from .sop_runtime import read_sop_section, sop_index, update_sop_section
from .workspace_agent import AgentExecutionError, OpenAIWorkspaceAgent
from .workspace_runtime import (
    WorkspaceError,
    delete_workspace,
    fetch_public_url,
    list_workspace,
    query_artifacts,
    read_workspace,
    run_workspace,
    workspace_capabilities,
    write_workspace,
)

CAPABILITY = "category_scope"


def discovery_summary(categories: list[dict[str, Any]]) -> dict[str, Any]:
    """Resumen acotado para la IA y las métricas: nunca el árbol completo."""
    by_id = {item["id"]: item for item in categories}

    def depth(item: dict[str, Any]) -> int:
        level = 0
        while item.get("parentId") is not None:
            item = by_id[item["parentId"]]
            level += 1
        return level

    parents = {item.get("parentId") for item in categories}
    return {
        "categories": len(categories),
        "roots": sum(1 for item in categories if item.get("parentId") is None),
        "leaves": sum(1 for item in categories if item["id"] not in parents),
        "max_depth": max((depth(item) for item in categories), default=0),
    }


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _enable_capability(workspace_root: Path) -> None:
    """The runtime, not the agent, declares the capability after a valid tree is produced."""
    import json

    manifest_path = workspace_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    capabilities = manifest.get("capabilities") if isinstance(manifest.get("capabilities"), list) else []
    if CAPABILITY not in capabilities:
        manifest["capabilities"] = [*capabilities, CAPABILITY]
        _write_json(manifest_path, manifest)


def run_discovery(payload: dict[str, Any]) -> dict[str, Any]:
    """Expone el árbol de categorías del sitio sin descargar productos.

    El descubrimiento es parte del crawler del sitio (`crawl.py --mode discover`), generado y
    mantenido por el agente según el SOP del sitio. El runtime solo valida el contrato.
    """
    run_id = str(payload.get("run_id") or uuid.uuid4())
    site_url = str(payload.get("site_url", ""))
    site_key = str(payload.get("site_key", "site"))
    build_root = Path(str(payload.get("build_root", "build"))).expanduser().resolve()
    workspace_root = build_root / "workspace"
    site_sop_path = workspace_root / "site-sop.md"
    transcript = build_root / "logs" / "agent-transcript.jsonl"
    source = {"site_key": payload.get("site_key"), "site_url": site_url}
    result: dict[str, Any] = {
        "schema_version": 1, "run_id": run_id, "status": "EXECUTION_FAILED",
        "source": source, "categories": [], "metrics": {}, "error": None,
    }
    heartbeat: RunHeartbeat | None = None
    started_at = time.time()
    state: dict[str, Any] = {"latest": None, "iterations": 0}

    def append(role: str, content: str, metadata: dict[str, Any] | None = None) -> None:
        _append_transcript(transcript, role, content, metadata)

    def finish(final: dict[str, Any]) -> dict[str, Any]:
        if heartbeat is not None:
            heartbeat.stop()
        _write_json(build_root / "run-state.json", {
            "run_id": run_id, "status": final["status"], "finished_at": time.time(),
            "technical_iterations": state["iterations"], "mode": "discover",
        })
        return final

    def completed() -> dict[str, Any]:
        categories = state["latest"]["categories"]
        _enable_capability(workspace_root)
        return finish({
            **result,
            "status": "COMPLETED",
            "categories": categories,
            "metrics": {**(state["latest"].get("metrics") or {}), **discovery_summary(categories)},
        })

    try:
        if os.getenv("AI_MODE", "real").lower() == "fake":
            raise ValueError("El descubrimiento de categorías requiere AI_MODE=real")
        with BuildLock(build_root, run_id):
            for child in build_root.iterdir():
                if child.name not in {".lock", "workspace"}:
                    shutil.rmtree(child) if child.is_dir() else child.unlink()
            workspace_root.mkdir(parents=True, exist_ok=True)
            scaffold_created = ensure_scaffold(workspace_root, site_url)
            for folder in ("logs", "results", "evidence"):
                (build_root / folder).mkdir(parents=True, exist_ok=True)
            heartbeat = RunHeartbeat(build_root / "run-state.json", run_id, started_at)
            heartbeat.start()
            append("system", "Category discovery started", source)

            def execute_discovery(_arguments: dict[str, Any]) -> dict[str, Any]:
                if state["iterations"] >= 10:
                    return {
                        "ok": False, "status": "EXECUTION_FAILED", "stop_agent": True,
                        "error": {"code": "TECHNICAL_ITERATIONS_EXHAUSTED", "message": "Technical iterations exhausted"},
                    }
                state["iterations"] += 1
                heartbeat.set_counts(0, state["iterations"])
                heartbeat.set_stage("discovering_categories")
                try:
                    raw = run_workspace(workspace_root, build_root, site_url, run_id, mode="discover")
                    validate_categories_result(raw)
                except (WorkspaceError, ContractError, ValueError) as error:
                    append("tool", "discover failed technically", {"error": str(error), "technical_iterations": state["iterations"]})
                    return {
                        "ok": False, "status": "EXECUTION_FAILED", "technical_iterations": state["iterations"],
                        "error": {"code": "TECHNICAL_EXECUTION_ERROR", "message": str(error), "retryable": True},
                        "stop_agent": state["iterations"] >= 10,
                    }
                state["latest"] = raw
                summary = discovery_summary(raw["categories"])
                sop_pending = "Pendiente de descubrir por el agente." in _read_text(site_sop_path)
                append("tool", "discover produced a valid tree", summary)
                return {
                    "ok": True, "status": "COMPLETED", "summary": summary,
                    "technical_iterations": state["iterations"],
                    "sop_review_required": sop_pending, "stop_agent": not sop_pending,
                }

            already_capable = CAPABILITY in workspace_capabilities(workspace_root)
            if already_capable and not scaffold_created and not is_scaffold(workspace_root):
                initial_state = execute_discovery({})
                if initial_state.get("ok") and not initial_state.get("sop_review_required"):
                    return completed()
                reason = "discovery_failed"
            else:
                initial_state = {"status": "NOT_RUN"}
                reason = "category_discovery_required"
            initial_state = {**initial_state, "invocation_reason": reason, "task": "discover_categories"}
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
                "run_workspace": execute_discovery,
            }
            heartbeat.set_stage("agent_editing")
            OpenAIWorkspaceAgent(
                model=str(payload.get("model") or os.getenv("AI_CHAT_MODEL", "gpt-6-luna")),
                site_url=site_url, site_key=site_key, sop=str(payload.get("sop", "")),
                workspace_files=list_workspace(workspace_root), initial_state=initial_state,
                site_sop_index=sop_index(site_sop_path), handlers=handlers, transcript=append,
            ).run()
            if state["latest"] is None:
                raise WorkspaceError("La IA terminó sin producir un descubrimiento válido")
            return completed()
    except (ContractError, AgentExecutionError, RuntimeError, ValueError, WorkspaceError) as error:
        if state["latest"] is not None:
            # The category tree is already valid and persisted. A later optional SOP
            # conversation must not discard the result or leave the UI polling forever.
            append("system", "A valid discovery was preserved despite a later agent failure", {"error": str(error)})
            return completed()
        result["error"] = {"code": "DISCOVERY_ERROR", "message": str(error), "retryable": True}
        if transcript.parent.exists():
            append("system", "Discovery failed", {"error": str(error)})
        return finish(result) if heartbeat is not None else result
