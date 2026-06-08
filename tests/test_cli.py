"""Tests for aird-cli helpers."""

from unittest.mock import MagicMock

from aird.cli.config import config_dir, get_parallel_jobs, load_config, save_config
from aird.cli.main import build_parser
from aird.cli.session import AirdClient, _remote_url


def test_remote_url_encoding():
    assert _remote_url("/api/files", "") == "/api/files/"
    assert _remote_url("/api/files", "docs/report.pdf") == "/api/files/docs/report.pdf"
    assert _remote_url("/files", "a b/c") == "/files/a%20b/c"


def test_parser_has_commands():
    parser = build_parser()
    args = parser.parse_args(["login", "-u", "alice"])
    assert args.command == "login"
    assert args.username == "alice"


def test_config_load_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("AIRD_CLI_CONFIG_DIR", str(tmp_path))
    assert load_config() == {}
    assert config_dir() == tmp_path


def test_parallel_jobs_from_config(tmp_path, monkeypatch):
    monkeypatch.setenv("AIRD_CLI_CONFIG_DIR", str(tmp_path))
    save_config({"parallel_uploads": 4})
    assert get_parallel_jobs() == 4


def test_client_clone_separate_session(monkeypatch):
    monkeypatch.setenv("AIRD_CLI_CONFIG_DIR", "/tmp/aird-cli-test-unused")
    parent = AirdClient("https://example.com")
    parent._xsrf = "abc"
    child = parent.clone()
    assert child.server == parent.server
    assert child.http is not parent.http
    assert child._xsrf == "abc"


def test_expand_paths_to_files(monkeypatch):
    monkeypatch.setenv("AIRD_CLI_CONFIG_DIR", "/tmp/aird-cli-test-unused")
    client = AirdClient("https://example.com")
    client.list_dir = MagicMock(return_value=[{"name": "a.txt", "is_dir": False}])
    client.iter_tree = MagicMock(return_value=iter([("proj/a.txt", 1)]))
    out = client.expand_paths_to_files(["proj"])
    assert out == ["proj/a.txt"]
