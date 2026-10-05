from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx


class WorkspaceError(RuntimeError):
    pass


_ALLOWED_QUERY_COMMANDS = {
    "awk", "basename", "cat", "cut", "dirname", "du", "file", "find", "grep", "head",
    "jq", "perl", "printf", "python", "python3", "readlink", "sed", "sort", "tail", "tr", "uniq", "wc",
}
_BLOCKED_UNQUOTED_MARKERS = (";", ">", "<", "`", "$(", "&&", "||")


def _has_unquoted_marker(command: str) -> bool:
    quote: str | None = None
    escaped = False
    index = 0
    while index < len(command):
        char = command[index]
        if escaped:
            escaped = False
            index += 1
            continue
        if char == "\\" and quote != "'":
            escaped = True
            index += 1
            continue
        if quote:
            if char == quote:
                quote = None
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
            index += 1
            continue
        if any(command.startswith(marker, index) for marker in _BLOCKED_UNQUOTED_MARKERS):
            return True
        index += 1
    return False


def _safe_path(root: Path, relative: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise WorkspaceError("La ruta debe ser relativa y no puede salir del workspace")
    resolved_root = root.resolve()
    resolved = (resolved_root / candidate).resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise WorkspaceError("La ruta queda fuera del workspace")
    return resolved


def list_workspace(root: Path, limit: int = 200) -> list[str]:
    entries: list[str] = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            entries.append(path.relative_to(root).as_posix())
            if len(entries) >= limit:
                break
    return entries


def read_workspace(root: Path, relative: str, offset: int = 0, limit: int = 240) -> dict[str, Any]:
    path = _safe_path(root, relative)
    if not path.is_file():
        raise WorkspaceError(f"No existe el archivo: {relative}")
    lines = path.read_text(encoding="utf-8").splitlines()
    end = min(len(lines), offset + limit)
    return {
        "path": relative,
        "offset": offset,
        "total_lines": len(lines),
        "lines": lines[offset:end],
        "has_more": end < len(lines),
    }


def write_workspace(root: Path, relative: str, content: str) -> dict[str, Any]:
    if len(content.encode("utf-8")) > 500_000:
        raise WorkspaceError("El archivo excede el límite de 500 KB")
    path = _safe_path(root, relative)
    if path.name in {".lock", "run-state.json", "site-sop.md"}:
        raise WorkspaceError("Usa sop_update para modificar el SOP del sitio")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)
    return {"path": relative, "bytes": len(content.encode("utf-8"))}


def query_artifacts(build_root: Path, command: str, timeout_seconds: int = 15, output_limit: int = 12_000) -> dict[str, Any]:
    if not command or len(command) > 2_000:
        raise WorkspaceError("La query está vacía o excede 2,000 caracteres")
    if _has_unquoted_marker(command) or re.search(r"(?:^|[\s/])\.\.(?:/|$)", command):
        raise WorkspaceError("La query contiene una operación no permitida")
    for segment in command.split("|"):
        tokens = shlex.split(segment.strip())
        if not tokens:
            raise WorkspaceError("La query contiene un segmento vacío")
        executable = Path(tokens[0]).name
        if executable not in _ALLOWED_QUERY_COMMANDS:
            raise WorkspaceError(f"Comando no permitido para consultas: {executable}")
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8"}
    try:
        completed = subprocess.run(
            ["/bin/sh", "-c", command],
            cwd=build_root,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise WorkspaceError("La query excedió el timeout") from error
    stdout = completed.stdout[:output_limit]
    if len(completed.stdout) > output_limit:
        stdout += "\n[output truncado]"
    return {
        "exit_code": completed.returncode,
        "stdout": stdout,
        "stderr": completed.stderr[-2_000:],
    }


def delete_workspace(root: Path, relative: str) -> dict[str, Any]:
    path = _safe_path(root, relative)
    if path.name in {".lock", "run-state.json", "site-sop.md"}:
        raise WorkspaceError("El agente no puede eliminar archivos de control o el SOP del sitio")
    if not path.exists():
        return {"path": relative, "deleted": False}
    if path.is_dir():
        raise WorkspaceError("Solo se pueden eliminar archivos, no directorios")
    path.unlink()
    return {"path": relative, "deleted": True}


def fetch_public_url(url: str, allowed_host: str, build_root: Path, max_bytes: int = 20_000) -> dict[str, Any]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise WorkspaceError("web_fetch requiere una URL HTTP(S) pública")
    if parsed.hostname != allowed_host:
        raise WorkspaceError("web_fetch solo permite el host del sitio en esta corrida")
    with httpx.Client(timeout=20, follow_redirects=True, headers={"User-Agent": "StacklineCrawlerAgent/1.0"}) as client:
        response = client.get(url)
    content = response.content[:max_bytes]
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    evidence_path = build_root / "evidence" / "agent" / f"{digest}.html"
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_bytes(content)
    return {
        "url": url,
        "final_url": str(response.url),
        "status_code": response.status_code,
        "content_type": response.headers.get("content-type", ""),
        "bytes_saved": len(content),
        "artifact": evidence_path.relative_to(build_root).as_posix(),
        "body_excerpt": content.decode(response.encoding or "utf-8", errors="replace")[:max_bytes],
    }


def run_workspace(workspace_root: Path, build_root: Path, site_url: str, run_id: str, timeout_seconds: int = 300) -> dict[str, Any]:
    manifest_path = workspace_root / "manifest.json"
    if not manifest_path.is_file():
        raise WorkspaceError("Falta workspace/manifest.json")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise WorkspaceError(f"manifest.json no es JSON válido: {error}") from error
    entrypoint = manifest.get("entrypoint", "crawl.py")
    if not isinstance(entrypoint, str) or not entrypoint.endswith(".py"):
        raise WorkspaceError("manifest.entrypoint debe ser un archivo Python")
    script = _safe_path(workspace_root, entrypoint)
    if not script.is_file():
        raise WorkspaceError(f"No existe el entrypoint: {entrypoint}")
    output_path = build_root / "results" / "crawl-result.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_path = build_root / "logs" / "runner.stdout.log"
    stderr_path = build_root / "logs" / "runner.stderr.log"
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, str(script), "--site-url", site_url, "--output", str(output_path), "--run-id", run_id]
    try:
        completed = subprocess.run(
            command,
            cwd=workspace_root,
            env={**os.environ, "STACKLINE_BUILD_ROOT": str(build_root)},
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise WorkspaceError("El crawler generado excedió el timeout") from error
    stdout_path.write_text(completed.stdout, encoding="utf-8")
    stderr_path.write_text(completed.stderr, encoding="utf-8")
    if completed.returncode != 0:
        raise WorkspaceError(f"El crawler terminó con exit code {completed.returncode}: {completed.stderr[-4_000:]}")
    if not output_path.is_file():
        raise WorkspaceError("El crawler terminó correctamente pero no produjo results/crawl-result.json")
    try:
        return json.loads(output_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise WorkspaceError(f"crawl-result.json no es JSON válido: {error}") from error
