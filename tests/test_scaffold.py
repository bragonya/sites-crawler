import json
from pathlib import Path

from stackline_crawling.scaffold import ensure_scaffold, is_scaffold, mark_ready


def test_scaffold_creates_runner_shape_and_preserves_ready_workspace(tmp_path: Path):
    workspace = tmp_path / "workspace"
    assert ensure_scaffold(workspace, "https://example.com") is True
    assert is_scaffold(workspace)
    for relative in ("manifest.json", "crawl.py", "config.json", "site-sop.md", "lib/http.py", "lib/output.py", "lib/metrics.py"):
        assert (workspace / relative).is_file()

    original = (workspace / "crawl.py").read_text(encoding="utf-8")
    mark_ready(workspace)
    assert not is_scaffold(workspace)
    assert json.loads((workspace / "manifest.json").read_text(encoding="utf-8"))["status"] == "READY"
    assert ensure_scaffold(workspace, "https://other.example") is False
    assert (workspace / "crawl.py").read_text(encoding="utf-8") == original
