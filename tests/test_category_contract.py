import json
import sys
from pathlib import Path

import pytest

from stackline_crawling.contracts import (
    ContractError,
    validate_categories_result,
    validate_scope,
    validate_scope_conformance,
)
from stackline_crawling.discovery import discovery_summary, run_discovery
from stackline_crawling.workspace_runtime import run_workspace


def tree(*items):
    return {"schema_version": 1, "categories": [
        {"id": i, "name": i.upper(), "url": f"https://x.test/{i}", "parentId": p} for i, p in items
    ]}


def test_valid_tree():
    validate_categories_result(tree(("a", None), ("b", "a"), ("c", "b")))


@pytest.mark.parametrize("items", [
    [("a", None), ("a", None)],          # id duplicado
    [("a", "zz")],                        # padre inexistente
    [("a", "b"), ("b", "a")],             # ciclo
    [("a", "a")],                         # autopadre
])
def test_invalid_trees(items):
    with pytest.raises(ContractError):
        validate_categories_result(tree(*items))


def test_discovery_rejects_products():
    raw = tree(("a", None))
    raw["products"] = []
    with pytest.raises(ContractError):
        validate_categories_result(raw)


def test_scope_conformance_uses_path_prefix():
    scope = validate_scope([{"id": "1", "url": "https://x.test/1", "path": ["A", "B"]}])
    validate_scope_conformance([{"name": "p", "categoryPath": ["A", "B"]}], scope)
    validate_scope_conformance([{"name": "p", "categoryPath": ["A", "B", "C"]}], scope)
    with pytest.raises(ContractError):
        validate_scope_conformance([{"name": "p", "categoryPath": ["A"]}], scope)


def test_summary():
    summary = discovery_summary(tree(("a", None), ("b", "a"), ("c", "b"))["categories"])
    assert summary == {"categories": 3, "roots": 1, "leaves": 1, "max_depth": 2}


def test_run_workspace_discover_and_scope(tmp_path: Path):
    workspace, build = tmp_path / "w", tmp_path / "b"
    workspace.mkdir()
    (workspace / "manifest.json").write_text(json.dumps({"entrypoint": "crawl.py"}))
    (workspace / "crawl.py").write_text(
        "import argparse, json\n"
        "p = argparse.ArgumentParser()\n"
        "for a in ('--site-url', '--output', '--run-id', '--mode', '--scope'): p.add_argument(a)\n"
        "a = p.parse_args()\n"
        "out = {'mode': a.mode, 'scope': a.scope}\n"
        "open(a.output, 'w').write(json.dumps(out))\n"
    )
    assert run_workspace(workspace, build, "https://x.test", "r", mode="discover")["mode"] == "discover"
    assert (build / "results" / "categories-result.json").is_file()
    scope = tmp_path / "scope.json"
    scope.write_text("[]")
    crawl = run_workspace(workspace, build, "https://x.test", "r", scope_path=scope)
    assert crawl["scope"] == str(scope) and crawl["mode"] is None


def test_discovery_requires_real_ai_mode(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AI_MODE", "fake")
    result = run_discovery({"site_url": "https://x.test", "build_root": str(tmp_path / "b")})
    assert result["status"] == "EXECUTION_FAILED"
    assert result["error"]["code"] == "DISCOVERY_ERROR"


def test_agent_payload_does_not_include_empty_tool_calls():
    from stackline_crawling.workspace_agent import OpenAIWorkspaceAgent

    # The construction rule is exercised through the source contract: an assistant
    # text turn must omit tool_calls, while a function turn includes it.
    source = __import__("inspect").getsource(OpenAIWorkspaceAgent.run)
    assert 'if tool_calls:' in source
    assert 'assistant_message["tool_calls"] = tool_calls' in source
