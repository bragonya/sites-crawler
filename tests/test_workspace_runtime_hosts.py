from pathlib import Path

import pytest

from stackline_crawling import workspace_runtime
from stackline_crawling.workspace_runtime import WorkspaceError, fetch_public_url


class _Response:
    status_code = 200
    content = b"<html>ok</html>"
    encoding = "utf-8"
    headers = {"content-type": "text/html"}

    def __init__(self, url):
        self.url = url


class _Client:
    def __init__(self, response_url):
        self.response_url = response_url

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def get(self, _url):
        return _Response(self.response_url)


def test_www_variant_is_allowed_for_a_canonical_redirect(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        workspace_runtime.httpx,
        "Client",
        lambda **_kwargs: _Client("https://www.example.com/catalog"),
    )

    result = fetch_public_url("https://example.com", "example.com", tmp_path)

    assert result["final_url"] == "https://www.example.com/catalog"
    assert (tmp_path / "evidence/agent").is_dir()


def test_external_redirect_is_rejected(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        workspace_runtime.httpx,
        "Client",
        lambda **_kwargs: _Client("https://other.example/catalog"),
    )

    with pytest.raises(WorkspaceError, match="host del sitio"):
        fetch_public_url("https://example.com", "example.com", tmp_path)


def test_www_allowed_host_also_accepts_bare_variant():
    assert workspace_runtime._allowed_host_variants("www.example.com") == {
        "example.com", "www.example.com",
    }
