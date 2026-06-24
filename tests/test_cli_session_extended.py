"""Extended CLI session/client tests for coverage."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from aird.cli.session import (
    AirdAPIError,
    AirdAuthError,
    AirdClient,
    _collect_local_upload_files,
    _cookie_to_dict,
    _load_cookies,
    _run_path_jobs,
    _save_cookies,
)


def test_api_errors():
    err = AirdAPIError("fail", 403)
    assert err.status == 403
    assert "fail" in str(err)


def test_cookie_to_dict():
    cookie = requests.cookies.create_cookie("sid", "val", domain="x.com", path="/")
    data = _cookie_to_dict(cookie)
    assert data["name"] == "sid"
    assert data["value"] == "val"


def test_save_and_load_cookies(tmp_path, monkeypatch):
    monkeypatch.setenv("AIRD_CLI_CONFIG_DIR", str(tmp_path))
    session = requests.Session()
    session.cookies.set("sid", "abc")
    session.headers["Authorization"] = "Bearer tok"
    _save_cookies(session)

    fresh = requests.Session()
    assert _load_cookies(fresh) is True
    assert fresh.cookies.get("sid") == "abc"
    assert fresh.headers["Authorization"] == "Bearer tok"


def test_load_cookies_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("AIRD_CLI_CONFIG_DIR", str(tmp_path))
    assert _load_cookies(requests.Session()) is False


def test_collect_local_upload_files(tmp_path):
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.txt").write_text("b", encoding="utf-8")
    files = _collect_local_upload_files(tmp_path, "remote")
    rels = sorted(remote for _, remote in files)
    assert rels == ["remote", "remote/sub"]


def test_run_path_jobs():
    seen = []

    def job(path: str) -> str:
        seen.append(path)
        return path

    count = _run_path_jobs(["a", "b"], job, 2, on_progress=lambda p: seen.append(f"done:{p}"))
    assert count == 2
    assert "done:a" in seen


def test_client_requires_server(monkeypatch):
    monkeypatch.setenv("AIRD_CLI_CONFIG_DIR", "/tmp/unused-aird-cli")
    monkeypatch.delenv("AIRD_SERVER_URL", raising=False)
    with patch("aird.cli.session.get_server_url", return_value=None):
        with pytest.raises(AirdAuthError, match="Server URL not set"):
            AirdClient()


def test_client_set_bearer_and_clear(tmp_path, monkeypatch):
    monkeypatch.setenv("AIRD_CLI_CONFIG_DIR", str(tmp_path))
    client = AirdClient("https://example.com")
    client.set_bearer_token("abc")
    assert client.http.headers["Authorization"] == "Bearer abc"
    client.set_bearer_token("")
    assert "Authorization" not in client.http.headers
    client.http.cookies.set("sid", "x")
    client.clear_session()
    assert not client.http.cookies
    assert client._xsrf is None


def test_check_auth_and_list_dir(monkeypatch):
    monkeypatch.setenv("AIRD_CLI_CONFIG_DIR", "/tmp/unused-aird-cli")
    client = AirdClient("https://example.com")
    client.http = MagicMock()

    ok = MagicMock(status_code=200)
    ok.json.return_value = {"files": [{"name": "a.txt", "is_dir": False, "size": 1}]}
    client.http.get.return_value = ok
    assert client.check_auth() == ok.json.return_value

    client.http.get.return_value = MagicMock(status_code=401)
    with pytest.raises(AirdAuthError):
        client.check_auth()

    client.http.get.return_value = MagicMock(status_code=500)
    with pytest.raises(AirdAPIError):
        client.check_auth()

    with patch.object(client, "ensure_auth"):
        client.http.get.return_value = ok
        files = client.list_dir()
        assert len(files) == 1

        client.http.get.return_value = MagicMock(status_code=403)
        with pytest.raises(AirdAPIError):
            client.list_dir("secret")


def test_iter_tree(monkeypatch):
    monkeypatch.setenv("AIRD_CLI_CONFIG_DIR", "/tmp/unused-aird-cli")
    client = AirdClient("https://example.com")
    with patch.object(
        client,
        "list_dir",
        side_effect=[
            [
                {"name": "sub", "is_dir": True},
                {"name": "root.txt", "is_dir": False, "size_bytes": 3},
            ],
            [{"name": "nested.txt", "is_dir": False, "size": 5}],
        ],
    ):
        items = list(client.iter_tree())
    assert ("root.txt", 3) in items
    assert ("sub/nested.txt", 5) in items


def test_login_password(monkeypatch):
    monkeypatch.setenv("AIRD_CLI_CONFIG_DIR", "/tmp/unused-aird-cli")
    client = AirdClient("https://example.com")
    client.http = MagicMock()
    with patch.object(client, "refresh_xsrf", return_value="xsrf"), patch.object(
        client, "save"
    ):
        client.http.post.return_value = MagicMock(status_code=302)
        client.login_password("alice", "secret")
        client.http.post.assert_called_once()

        client.http.post.return_value = MagicMock(status_code=401)
        with pytest.raises(AirdAuthError):
            client.login_password("alice", "bad")


def test_upload_file(tmp_path, monkeypatch):
    monkeypatch.setenv("AIRD_CLI_CONFIG_DIR", "/tmp/unused-aird-cli")
    client = AirdClient("https://example.com")
    client.http = MagicMock()
    local = tmp_path / "up.txt"
    local.write_text("data", encoding="utf-8")
    with patch.object(client, "ensure_auth"), patch.object(
        client, "_xsrf_header", return_value={}
    ):
        client.http.post.return_value = MagicMock(status_code=200)
        client.upload_file(local, "docs")
        client.http.post.assert_called_once()
        with pytest.raises(FileNotFoundError):
            client.upload_file(tmp_path / "missing.txt")


def test_download_file(tmp_path, monkeypatch):
    monkeypatch.setenv("AIRD_CLI_CONFIG_DIR", "/tmp/unused-aird-cli")
    client = AirdClient("https://example.com")
    client.http = MagicMock()
    response = MagicMock(status_code=200)
    response.iter_content.return_value = [b"abc"]
    response.__enter__ = MagicMock(return_value=response)
    response.__exit__ = MagicMock(return_value=False)
    head = MagicMock(status_code=200, headers={"Content-Length": "3"})
    client.http.head.return_value = head
    client.http.get.return_value = response
    dest = tmp_path / "out" / "file.bin"
    with patch.object(client, "ensure_auth"):
        client.download_file("remote/file.bin", dest)
    assert dest.read_bytes() == b"abc"


def test_ensure_auth_expired(monkeypatch):
    monkeypatch.setenv("AIRD_CLI_CONFIG_DIR", "/tmp/unused-aird-cli")
    client = AirdClient("https://example.com")
    with patch.object(client, "check_auth", side_effect=AirdAuthError("no")):
        with pytest.raises(AirdAuthError, match="Session expired"):
            client.ensure_auth()


def test_upload_tree(tmp_path, monkeypatch):
    monkeypatch.setenv("AIRD_CLI_CONFIG_DIR", "/tmp/unused-aird-cli")
    client = AirdClient("https://example.com")
    (tmp_path / "f.txt").write_text("x", encoding="utf-8")
    with patch.object(client, "clone") as clone_mock:
        clone = MagicMock()
        clone_mock.return_value = clone
        count = client.upload_tree(tmp_path, "remote")
    assert count == 1
    clone.upload_file.assert_called_once()
    with pytest.raises(NotADirectoryError):
        client.upload_tree(tmp_path / "f.txt")


def test_download_tree(tmp_path, monkeypatch):
    monkeypatch.setenv("AIRD_CLI_CONFIG_DIR", "/tmp/unused-aird-cli")
    client = AirdClient("https://example.com")
    with patch.object(client, "iter_tree", return_value=[("a.txt", 1)]), patch.object(
        client, "clone", return_value=client
    ), patch.object(client, "download_file") as dl:
        assert client.download_tree("", tmp_path) == 1
        dl.assert_called_once()


def test_share_helpers(monkeypatch):
    monkeypatch.setenv("AIRD_CLI_CONFIG_DIR", "/tmp/unused-aird-cli")
    client = AirdClient("https://example.com")
    client.http = MagicMock()
    with patch.object(client, "ensure_auth"):
        client.http.get.return_value = MagicMock(
            status_code=200, json=MagicMock(return_value={"shares": {"s1": {"id": "s1"}}})
        )
        share, mine = client.find_share("s1")
        assert mine is True
        assert share["id"] == "s1"

        client.http.get.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value={"shares": {}, "shared_with_me": [{"id": "s2"}]}),
        )
        share, mine = client.find_share("s2")
        assert mine is False

        client.http.get.return_value = MagicMock(
            status_code=200,
            text='<script id="files-json">["a.txt"]</script>',
        )
        assert client.share_file_paths("s1") == ["a.txt"]


def test_expand_paths_to_files(monkeypatch):
    monkeypatch.setenv("AIRD_CLI_CONFIG_DIR", "/tmp/unused-aird-cli")
    client = AirdClient("https://example.com")
    with patch.object(client, "list_dir"), patch.object(
        client, "iter_tree", return_value=[("docs/a.txt", 1)]
    ):
        assert client.expand_paths_to_files(["docs"]) == ["docs/a.txt"]

    with patch.object(client, "list_dir", side_effect=AirdAPIError("missing", 404)):
        assert client.expand_paths_to_files(["solo.txt"]) == ["solo.txt"]


def test_verify_share(monkeypatch):
    monkeypatch.setenv("AIRD_CLI_CONFIG_DIR", "/tmp/unused-aird-cli")
    client = AirdClient("https://example.com")
    client.http = MagicMock()
    with patch.object(client, "refresh_xsrf"), patch.object(client, "_xsrf_header", return_value={}), patch.object(
        client, "save"
    ):
        client.http.post.return_value = MagicMock(status_code=200)
        client.verify_share("sid", "tok")
        client.http.post.return_value = MagicMock(status_code=403)
        with pytest.raises(AirdAPIError):
            client.verify_share("sid", "bad")


def test_download_share_flows(tmp_path, monkeypatch):
    monkeypatch.setenv("AIRD_CLI_CONFIG_DIR", "/tmp/unused-aird-cli")
    client = AirdClient("https://example.com")
    with patch.object(
        client, "find_share", return_value=({"paths": ["a.txt"]}, True)
    ), patch.object(client, "expand_paths_to_files", return_value=["a.txt"]), patch.object(
        client, "clone"
    ) as clone_mock:
        clone = MagicMock()
        clone_mock.return_value = clone
        count = client.download_share("s1", tmp_path)
    assert count == 1
    clone.download_file.assert_called_once()

    with patch.object(
        client, "find_share", return_value=(None, False)
    ), patch.object(client, "verify_share"), patch.object(
        client, "share_file_paths", return_value=["b.txt"]
    ), patch.object(client, "clone", return_value=clone):
        count = client.download_share("s2", tmp_path, share_token="tok")
    assert count == 1

    with patch.object(client, "list_shares", return_value={"shares": {"s1": {}}, "shared_with_me": []}), patch.object(
        client, "download_share", return_value=2
    ):
        assert client.download_all_shares(tmp_path) == 2
