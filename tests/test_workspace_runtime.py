import json
from pathlib import Path

from stackline_crawling.workspace_runtime import delete_workspace, run_workspace, write_workspace


def _metrics():
    return {
        "pages_discovered": 1,
        "pages_visited": 1,
        "pages_failed": 0,
        "products_extracted": 1,
        "products_with_price": 1,
        "categories_extracted": 1,
        "product_links_found": 1,
        "pagination_links_found": 0,
        "duplicate_products": 0,
        "blocked_or_challenge_detected": False,
        "response_size_bytes": 10,
        "html_fingerprint": "sha256:test",
        "duration_ms": 1,
    }


def test_generated_workspace_writes_contract(tmp_path: Path):
    workspace = tmp_path / "workspace"
    build = tmp_path / "build"
    write_workspace(workspace, "manifest.json", '{"entrypoint":"crawl.py"}')
    script = """
import argparse, json
parser = argparse.ArgumentParser()
parser.add_argument('--site-url')
parser.add_argument('--output')
parser.add_argument('--run-id')
args = parser.parse_args()
result = {
    'schema_version': 1,
    'run_id': args.run_id,
    'data': {'categories': [{'name': 'Demo'}], 'products': [{'name': 'Producto'}]},
    'metrics': %s,
    'evidence': [],
    'error': None,
}
with open(args.output, 'w', encoding='utf-8') as stream:
    json.dump(result, stream)
""" % repr(_metrics())
    write_workspace(workspace, "crawl.py", script)

    result = run_workspace(workspace, build, "https://example.com", "run-1")

    assert result["run_id"] == "run-1"
    assert result["data"]["products"][0]["name"] == "Producto"


def test_temporary_analysis_file_can_be_removed(tmp_path: Path):
    workspace = tmp_path / "workspace"
    write_workspace(workspace, "analysis/check.py", "print('ok')")
    assert (workspace / "analysis/check.py").is_file()
    assert delete_workspace(workspace, "analysis/check.py")["deleted"] is True
    assert not (workspace / "analysis/check.py").exists()


def test_healthy_existing_workspace_skips_ai(tmp_path: Path, monkeypatch):
    from stackline_crawling.runner import run_crawl

    workspace = tmp_path / "build" / "workspace"
    write_workspace(workspace, "manifest.json", '{"entrypoint":"crawl.py"}')
    script = """
import argparse, json
parser = argparse.ArgumentParser()
parser.add_argument('--site-url')
parser.add_argument('--output')
parser.add_argument('--run-id')
args = parser.parse_args()
result = {
    'schema_version': 1,
    'run_id': args.run_id,
    'data': {'categories': [{'name': 'Demo'}], 'products': [{'name': 'Producto', 'unitCost': 10}]},
    'metrics': %s,
    'evidence': [],
    'error': None,
}
with open(args.output, 'w', encoding='utf-8') as stream:
    json.dump(result, stream)
""" % repr(_metrics())
    write_workspace(workspace, "crawl.py", script)
    monkeypatch.setenv("AI_MODE", "real")

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("La IA no debe invocarse con health saludable")

    monkeypatch.setattr("stackline_crawling.runner.OpenAIWorkspaceAgent", fail_if_called)
    result = run_crawl({
        "site_key": "demo",
        "site_url": "https://example.com",
        "build_root": str(tmp_path / "build"),
    })

    assert result["status"] == "COMPLETED"
