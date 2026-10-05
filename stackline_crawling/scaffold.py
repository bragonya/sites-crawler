from __future__ import annotations

import json
from pathlib import Path


SCAFFOLD_MANIFEST = {
    "schema_version": 1,
    "status": "SCAFFOLD",
    "entrypoint": "crawl.py",
    "contract": "crawl-result.schema.v1",
}

SCAFFOLD_SITE_SOP = """# Site-specific crawler notes

## Platform

Pendiente de descubrir por el agente.

## Catalog

Pendiente de descubrir por el agente.

## Pagination

Pendiente de descubrir por el agente.

## Price Format

Pendiente de descubrir por el agente.

## Known Issues

Pendiente de descubrir por el agente.
"""

SCAFFOLD_CRAWL = '''"""Site-specific crawler entrypoint.

The agent must implement the site functions below. The runner invokes this file
with --site-url, --output and --run-id and expects a complete crawl-result.json.
"""

from __future__ import annotations

import argparse


# TODO(agent): implement site discovery, extraction and pagination.
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site-url", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--run-id", required=True)
    parser.parse_args()
    raise SystemExit(
        "CRAWLER_NOT_IMPLEMENTED: implement crawl.py before running the workspace"
    )


if __name__ == "__main__":
    main()
'''

SCAFFOLD_CONFIG = {
    "schema_version": 1,
    "site_url": "",
    "notes": "Runtime-generated scaffold. Site-specific values belong in the crawler and site-sop.md.",
}

SCAFFOLD_HTTP = '''from __future__ import annotations

import httpx


def client() -> httpx.Client:
    return httpx.Client(
        timeout=20,
        follow_redirects=True,
        headers={"User-Agent": "StacklineCrawler/1.0"},
    )
'''

SCAFFOLD_OUTPUT = '''from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_result(path: str, result: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(destination)
'''

SCAFFOLD_METRICS = '''from __future__ import annotations

import hashlib
from collections.abc import Iterable


def html_fingerprint(urls: Iterable[str]) -> str:
    value = "|".join(sorted(urls)).encode("utf-8")
    return "sha256:" + hashlib.sha256(value).hexdigest()
'''


def _write_if_missing(path: Path, content: str) -> None:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def ensure_scaffold(workspace_root: Path, site_url: str) -> bool:
    """Create only missing scaffold files. Return True when it was new."""
    workspace_root.mkdir(parents=True, exist_ok=True)
    manifest_path = workspace_root / "manifest.json"
    created = not manifest_path.exists()
    if created:
        manifest = {**SCAFFOLD_MANIFEST}
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_if_missing(workspace_root / "crawl.py", SCAFFOLD_CRAWL)
    _write_if_missing(workspace_root / "config.json", json.dumps({**SCAFFOLD_CONFIG, "site_url": site_url}, ensure_ascii=False, indent=2) + "\n")
    _write_if_missing(workspace_root / "site-sop.md", SCAFFOLD_SITE_SOP)
    _write_if_missing(workspace_root / "lib" / "http.py", SCAFFOLD_HTTP)
    _write_if_missing(workspace_root / "lib" / "output.py", SCAFFOLD_OUTPUT)
    _write_if_missing(workspace_root / "lib" / "metrics.py", SCAFFOLD_METRICS)
    return created


def is_scaffold(workspace_root: Path) -> bool:
    manifest_path = workspace_root / "manifest.json"
    if not manifest_path.is_file():
        return True
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return manifest.get("status") == "SCAFFOLD"


def mark_ready(workspace_root: Path) -> None:
    manifest_path = workspace_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["status"] = "READY"
    temporary = manifest_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(manifest_path)
