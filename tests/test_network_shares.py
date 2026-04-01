"""
Tests for the network share feature: DB CRUD, NetworkShareManager, and admin handlers.
"""

import os
import sqlite3
import threading
import time

import pytest
from unittest.mock import MagicMock, patch

from aird.db import (
    init_db,
    create_network_share,
    get_all_network_shares,
    get_network_share,
    update_network_share,
    delete_network_share,
)
from aird.network_share_manager import NetworkShareManager

try:
    from aird.handlers.admin_handlers import (
        AdminNetworkSharesHandler,
        AdminNetworkShareDeleteHandler,
        AdminNetworkShareToggleHandler,
    )
    from tests.handler_helpers import _default_services, authenticate, patch_db_conn

    HANDLERS_AVAILABLE = True
except ImportError:
    HANDLERS_AVAILABLE = False


# ----------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------


@pytest.fixture
def db():
    """In-memory SQLite with schema initialised."""
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    yield conn
    conn.close()


@pytest.fixture
def share_dir(tmp_path):
    """Real temporary directory to use as a share folder."""
    d = tmp_path / "shared_folder"
    d.mkdir()
    return str(d)


@pytest.fixture
def sample_share(share_dir):
    """A dict representing a valid share for the manager."""
    return {
        "id": "test-share-1",
        "name": "TestShare",
        "folder_path": share_dir,
        "protocol": "webdav",
        "port": 19876,
        "username": "tester",
        "password": "secret123",
        "read_only": False,
        "enabled": True,
    }


# ================================================================
# DB CRUD tests
# ================================================================


class TestNetworkShareDB:

    def test_create_and_get(self, db, share_dir):
        ok = create_network_share(
            db, "s1", "MyShare", share_dir, "webdav", 8443, "user1", "pass1"
        )
        assert ok is True
        share = get_network_share(db, "s1")
        assert share is not None
        assert share["name"] == "MyShare"
        assert share["folder_path"] == share_dir
        assert share["protocol"] == "webdav"
        assert share["port"] == 8443
        assert share["username"] == "user1"
        assert share["password"] == "pass1"
        assert share["enabled"] is True
        assert share["read_only"] is False

    def test_create_read_only(self, db, share_dir):
        create_network_share(
            db, "s2", "RO", share_dir, "smb", 4455, "u", "p", read_only=True
        )
        share = get_network_share(db, "s2")
        assert share["read_only"] is True

    def test_get_nonexistent(self, db):
        assert get_network_share(db, "does-not-exist") is None

    def test_get_all(self, db, share_dir):
        create_network_share(db, "a", "A", share_dir, "webdav", 8001, "u", "p")
        create_network_share(db, "b", "B", share_dir, "smb", 8002, "u", "p")
        shares = get_all_network_shares(db)
        assert len(shares) == 2
        ids = {s["id"] for s in shares}
        assert ids == {"a", "b"}

    def test_get_all_empty(self, db):
        assert get_all_network_shares(db) == []

    def test_update_enabled(self, db, share_dir):
        create_network_share(db, "s3", "S", share_dir, "webdav", 8443, "u", "p")
        assert get_network_share(db, "s3")["enabled"] is True
        update_network_share(db, "s3", enabled=False)
        assert get_network_share(db, "s3")["enabled"] is False

    def test_update_multiple_fields(self, db, share_dir):
        create_network_share(db, "s4", "S", share_dir, "webdav", 8443, "u", "p")
        update_network_share(db, "s4", port=9999, password="newpass", read_only=True)
        s = get_network_share(db, "s4")
        assert s["port"] == 9999
        assert s["password"] == "newpass"
        assert s["read_only"] is True

    def test_update_no_valid_fields(self, db, share_dir):
        create_network_share(db, "s5", "S", share_dir, "webdav", 8443, "u", "p")
        result = update_network_share(db, "s5", bogus="value")
        assert result is False

    def test_delete(self, db, share_dir):
        create_network_share(db, "s6", "S", share_dir, "webdav", 8443, "u", "p")
        assert delete_network_share(db, "s6") is True
        assert get_network_share(db, "s6") is None

    def test_delete_nonexistent(self, db):
        assert delete_network_share(db, "nope") is False

    def test_duplicate_id_fails(self, db, share_dir):
        create_network_share(db, "dup", "A", share_dir, "webdav", 8443, "u", "p")
        ok = create_network_share(db, "dup", "B", share_dir, "smb", 4455, "u2", "p2")
        assert ok is False

    def test_update_nonexistent_share(self, db):
        result = update_network_share(db, "ghost", enabled=False)
        assert result is True  # SQL executes without error even if no row matches

    def test_update_name(self, db, share_dir):
        create_network_share(db, "s7", "Old", share_dir, "webdav", 8443, "u", "p")
        update_network_share(db, "s7", name="New")
        assert get_network_share(db, "s7")["name"] == "New"

    def test_update_folder_path(self, db, share_dir, tmp_path):
        new_dir = str(tmp_path / "new_dir")
        os.makedirs(new_dir)
        create_network_share(db, "s8", "S", share_dir, "webdav", 8443, "u", "p")
        update_network_share(db, "s8", folder_path=new_dir)
        assert get_network_share(db, "s8")["folder_path"] == new_dir

    def test_update_protocol(self, db, share_dir):
        create_network_share(db, "s9", "S", share_dir, "webdav", 8443, "u", "p")
        update_network_share(db, "s9", protocol="smb")
        assert get_network_share(db, "s9")["protocol"] == "smb"

    def test_update_username(self, db, share_dir):
        create_network_share(db, "s10", "S", share_dir, "webdav", 8443, "u", "p")
        update_network_share(db, "s10", username="newuser")
        assert get_network_share(db, "s10")["username"] == "newuser"

    def test_create_with_special_chars_in_name(self, db, share_dir):
        ok = create_network_share(
            db, "sp1", "My Share (Dev) #1!", share_dir, "webdav", 8443, "u", "p"
        )
        assert ok is True
        assert get_network_share(db, "sp1")["name"] == "My Share (Dev) #1!"

    def test_create_preserves_unicode_name(self, db, share_dir):
        ok = create_network_share(
            db, "uni1", "\u203a\u00e9l\u00e8ve", share_dir, "webdav", 8443, "u", "p"
        )
        assert ok is True
        assert get_network_share(db, "uni1")["name"] == "\u203a\u00e9l\u00e8ve"

    def test_get_all_returns_sorted_by_created_desc(self, db, share_dir):
        create_network_share(db, "first", "First", share_dir, "webdav", 8001, "u", "p")
        create_network_share(
            db, "second", "Second", share_dir, "webdav", 8002, "u", "p"
        )
        shares = get_all_network_shares(db)
        assert shares[0]["id"] == "second"
        assert shares[1]["id"] == "first"

    def test_create_on_closed_db_returns_false(self, share_dir):
        conn = sqlite3.connect(":memory:")
        init_db(conn)
        conn.close()
        ok = create_network_share(conn, "x", "X", share_dir, "webdav", 8443, "u", "p")
        assert ok is False

    def test_get_all_on_closed_db_returns_empty(self):
        conn = sqlite3.connect(":memory:")
        init_db(conn)
        conn.close()
        assert get_all_network_shares(conn) == []

    def test_get_on_closed_db_returns_none(self):
        conn = sqlite3.connect(":memory:")
        init_db(conn)
        conn.close()
        assert get_network_share(conn, "x") is None

    def test_delete_on_closed_db_returns_false(self):
        conn = sqlite3.connect(":memory:")
        init_db(conn)
        conn.close()
        assert delete_network_share(conn, "x") is False

    def test_boundary_port_values(self, db, share_dir):
        create_network_share(db, "lo", "Lo", share_dir, "webdav", 1, "u", "p")
        assert get_network_share(db, "lo")["port"] == 1
        create_network_share(db, "hi", "Hi", share_dir, "webdav", 65535, "u", "p")
        assert get_network_share(db, "hi")["port"] == 65535

    def test_toggle_read_only_false_to_true_and_back(self, db, share_dir):
        create_network_share(db, "ro1", "RO", share_dir, "webdav", 8443, "u", "p")
        assert get_network_share(db, "ro1")["read_only"] is False
        update_network_share(db, "ro1", read_only=True)
        assert get_network_share(db, "ro1")["read_only"] is True
        update_network_share(db, "ro1", read_only=False)
        assert get_network_share(db, "ro1")["read_only"] is False

    def test_has_created_at_field(self, db, share_dir):
        create_network_share(db, "ts1", "TS", share_dir, "webdav", 8443, "u", "p")
        share = get_network_share(db, "ts1")
        assert share["created_at"] is not None
        assert len(share["created_at"]) > 0


# ================================================================
# NetworkShareManager tests
# ================================================================


class TestNetworkShareManager:

    def test_init(self):
        mgr = NetworkShareManager()
        assert mgr._servers == {}

    def test_is_running_unknown_id(self):
        mgr = NetworkShareManager()
        assert mgr.is_running("nonexistent") is False

    def test_get_status_unknown_id(self):
        mgr = NetworkShareManager()
        status = mgr.get_status("nonexistent")
        assert status == {"running": False}

    def test_stop_share_unknown_id(self):
        mgr = NetworkShareManager()
        assert mgr.stop_share("nonexistent") is False

    def test_start_share_missing_folder(self, sample_share):
        mgr = NetworkShareManager()
        bad = {**sample_share, "folder_path": "/nonexistent/path/xyz"}
        assert mgr.start_share(bad) is False

    def test_start_share_already_running(self, sample_share):
        mgr = NetworkShareManager()
        alive_thread = MagicMock()
        alive_thread.is_alive.return_value = True
        mgr._servers["test-share-1"] = {
            "thread": alive_thread,
            "server": None,
            "share": sample_share,
        }
        assert mgr.start_share(sample_share) is False

    def test_start_share_cleans_dead_thread(self, sample_share):
        """A dead thread entry should be cleaned up so the share can restart."""
        mgr = NetworkShareManager()
        dead_thread = MagicMock()
        dead_thread.is_alive.return_value = False
        mgr._servers["test-share-1"] = {
            "thread": dead_thread,
            "server": None,
            "share": sample_share,
        }

        with patch.object(mgr, "_start_webdav", return_value=True) as mock_start:
            result = mgr.start_share(sample_share)
        assert result is True
        mock_start.assert_called_once()

    def test_stop_share_calls_shutdown(self, sample_share):
        mgr = NetworkShareManager()
        mock_server = MagicMock()
        mock_thread = MagicMock()
        mgr._servers["test-share-1"] = {
            "thread": mock_thread,
            "server": mock_server,
            "share": sample_share,
        }
        assert mgr.stop_share("test-share-1") is True
        mock_server.stop.assert_called_once()
        assert "test-share-1" not in mgr._servers

    def test_stop_all(self, sample_share):
        mgr = NetworkShareManager()
        for i in range(3):
            sid = f"share-{i}"
            mgr._servers[sid] = {
                "thread": MagicMock(),
                "server": MagicMock(),
                "share": {**sample_share, "id": sid},
            }
        mgr.stop_all()
        assert mgr._servers == {}

    def test_start_smb_not_available(self, sample_share):
        mgr = NetworkShareManager()
        smb_share = {**sample_share, "protocol": "smb"}
        with patch("aird.network_share_manager._SMB_AVAILABLE", False):
            assert mgr._start_smb(smb_share) is False

    def test_start_webdav_not_available(self, sample_share):
        mgr = NetworkShareManager()
        with patch("aird.network_share_manager._WEBDAV_AVAILABLE", False):
            assert mgr._start_webdav(sample_share) is False

    def test_start_webdav_creates_thread(self, sample_share):
        mgr = NetworkShareManager()
        with patch("aird.network_share_manager._WEBDAV_AVAILABLE", True), patch(
            "aird.network_share_manager.WsgiDAVApp"
        ), patch("aird.network_share_manager.cheroot_wsgi"):
            result = mgr._start_webdav(sample_share)
        assert result is True
        assert "test-share-1" in mgr._servers
        entry = mgr._servers["test-share-1"]
        assert entry["thread"].daemon is True
        assert entry["thread"].name == "webdav-test-share-1"

    def test_start_smb_creates_thread(self, sample_share):
        mgr = NetworkShareManager()
        smb_share = {**sample_share, "protocol": "smb"}
        with patch("aird.network_share_manager._SMB_AVAILABLE", True), patch(
            "aird.network_share_manager.PySMBServer"
        ):
            result = mgr._start_smb(smb_share)
        assert result is True
        assert "test-share-1" in mgr._servers
        entry = mgr._servers["test-share-1"]
        assert entry["thread"].daemon is True
        assert entry["thread"].name == "smb-test-share-1"

    def test_shutdown_server_with_stop(self):
        entry = {"server": MagicMock()}
        NetworkShareManager._shutdown_server(entry)
        entry["server"].stop.assert_called_once()

    def test_shutdown_server_none(self):
        entry = {"server": None}
        NetworkShareManager._shutdown_server(entry)

    def test_shutdown_server_no_stop_attr(self):
        server = object()
        entry = {"server": server}
        NetworkShareManager._shutdown_server(entry)

    def test_get_status_running(self, sample_share):
        mgr = NetworkShareManager()
        t = MagicMock()
        t.is_alive.return_value = True
        mgr._servers["s"] = {"thread": t, "server": None, "share": sample_share}
        status = mgr.get_status("s")
        assert status["running"] is True
        assert status["protocol"] == "webdav"
        assert status["port"] == 19876

    def test_protocol_routing_smb(self, sample_share):
        mgr = NetworkShareManager()
        smb_share = {**sample_share, "protocol": "smb"}
        with patch.object(mgr, "_start_smb", return_value=True) as m:
            mgr.start_share(smb_share)
        m.assert_called_once_with(smb_share)

    def test_protocol_routing_webdav(self, sample_share):
        mgr = NetworkShareManager()
        with patch.object(mgr, "_start_webdav", return_value=True) as m:
            mgr.start_share(sample_share)
        m.assert_called_once_with(sample_share)

    def test_protocol_routing_default(self, sample_share):
        """Unknown/missing protocol defaults to webdav."""
        mgr = NetworkShareManager()
        no_proto = {**sample_share, "protocol": ""}
        no_proto.pop("protocol")
        with patch.object(mgr, "_start_webdav", return_value=True) as m:
            mgr.start_share(no_proto)
        m.assert_called_once()

    def test_stop_all_empty(self):
        mgr = NetworkShareManager()
        mgr.stop_all()
        assert mgr._servers == {}

    def test_stop_share_handles_shutdown_exception(self, sample_share):
        """stop_share returns True even if the server.stop() raises."""
        mgr = NetworkShareManager()
        bad_server = MagicMock()
        bad_server.stop.side_effect = RuntimeError("shutdown error")
        mgr._servers["test-share-1"] = {
            "thread": MagicMock(),
            "server": bad_server,
            "share": sample_share,
        }
        assert mgr.stop_share("test-share-1") is True
        assert "test-share-1" not in mgr._servers

    def test_is_running_dead_thread(self, sample_share):
        mgr = NetworkShareManager()
        t = MagicMock()
        t.is_alive.return_value = False
        mgr._servers["s"] = {"thread": t, "server": None, "share": sample_share}
        assert mgr.is_running("s") is False

    def test_get_status_dead_thread(self, sample_share):
        mgr = NetworkShareManager()
        t = MagicMock()
        t.is_alive.return_value = False
        mgr._servers["s"] = {"thread": t, "server": None, "share": sample_share}
        status = mgr.get_status("s")
        assert status["running"] is False
        assert status["protocol"] == "webdav"

    def test_start_share_with_read_only(self, sample_share):
        mgr = NetworkShareManager()
        ro_share = {**sample_share, "read_only": True}
        with patch.object(mgr, "_start_webdav", return_value=True) as m:
            result = mgr.start_share(ro_share)
        assert result is True
        m.assert_called_once_with(ro_share)

    def test_start_share_protocol_case_insensitive(self, sample_share):
        mgr = NetworkShareManager()
        upper = {**sample_share, "protocol": "SMB"}
        with patch.object(mgr, "_start_smb", return_value=True) as m:
            mgr.start_share(upper)
        m.assert_called_once()

    def test_concurrent_start_stop(self, sample_share):
        """Multiple threads starting/stopping shares should not corrupt state."""
        mgr = NetworkShareManager()
        results = {"starts": 0, "stops": 0}

        def start_share(sid):
            s = {**sample_share, "id": sid}
            with patch.object(mgr, "_start_webdav", return_value=True):
                if mgr.start_share(s):
                    results["starts"] += 1

        def stop_share(sid):
            if mgr.stop_share(sid):
                results["stops"] += 1

        threads = []
        for i in range(10):
            sid = f"concurrent-{i}"
            threads.append(threading.Thread(target=start_share, args=(sid,)))
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        stop_threads = []
        for i in range(10):
            sid = f"concurrent-{i}"
            stop_threads.append(threading.Thread(target=stop_share, args=(sid,)))
        for t in stop_threads:
            t.start()
        for t in stop_threads:
            t.join(timeout=5)

        assert mgr._servers == {}

    def test_stop_all_calls_shutdown_for_each(self, sample_share):
        mgr = NetworkShareManager()
        servers = []
        for i in range(3):
            sid = f"share-{i}"
            srv = MagicMock()
            servers.append(srv)
            mgr._servers[sid] = {
                "thread": MagicMock(),
                "server": srv,
                "share": {**sample_share, "id": sid},
            }
        mgr.stop_all()
        for srv in servers:
            srv.stop.assert_called_once()

    def test_start_webdav_read_only_flag_passed(self, sample_share):
        mgr = NetworkShareManager()
        ro_share = {**sample_share, "read_only": True}
        # WsgiDAVApp runs inside a daemon thread; keep patches active until invoked.
        with patch("aird.network_share_manager._WEBDAV_AVAILABLE", True), patch(
            "aird.network_share_manager.WsgiDAVApp"
        ) as mock_app, patch("aird.network_share_manager.cheroot_wsgi"):
            mgr._start_webdav(ro_share)
            deadline = time.time() + 5.0
            while not mock_app.called and time.time() < deadline:
                time.sleep(0.01)
            assert mock_app.called, "WsgiDAVApp was not called by WebDAV thread"
            config_passed = mock_app.call_args[0][0]
            assert config_passed["fs_dav_provider"]["readonly"] is True


# ================================================================
# Admin handler tests
# ================================================================


@pytest.mark.skipif(not HANDLERS_AVAILABLE, reason="handler imports not available")
class TestAdminNetworkShareHandlers:

    @pytest.fixture(autouse=True)
    def _mock_getfqdn(self):
        with patch(
            "aird.handlers.admin_handlers._socket.getfqdn", return_value="test.local"
        ):
            yield

    def _make_handler(self, handler_cls, app, request):
        handler = handler_cls(app, request)
        authenticate(handler, role="admin")
        handler.request.remote_ip = "127.0.0.1"
        return handler

    # -- AdminNetworkSharesHandler GET --

    def test_get_redirects_non_admin(self, mock_tornado_app, mock_tornado_request):
        handler = AdminNetworkSharesHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler, role="user")
        with patch.object(handler, "is_admin_user", return_value=False), patch.object(
            handler, "redirect"
        ) as mock_redir:
            handler.get()
            mock_redir.assert_called_once_with("/admin/login")

    def test_get_renders_template(self, mock_tornado_app, mock_tornado_request, db):
        handler = self._make_handler(
            AdminNetworkSharesHandler, mock_tornado_app, mock_tornado_request
        )
        with patch_db_conn(db), patch.object(
            handler, "render"
        ) as mock_render, patch.object(handler, "get_argument", return_value=None):
            mock_tornado_app.settings["network_share_manager"] = None
            handler.get()
            assert mock_render.called
            args, kwargs = mock_render.call_args
            assert args[0] == "admin_network_shares.html"
            assert "shares" in kwargs
            assert "server_host" in kwargs
            assert "error" in kwargs

    def test_get_includes_running_status(
        self, mock_tornado_app, mock_tornado_request, db, share_dir
    ):
        create_network_share(db, "s1", "S1", share_dir, "webdav", 8443, "u", "p")
        handler = self._make_handler(
            AdminNetworkSharesHandler, mock_tornado_app, mock_tornado_request
        )
        mock_mgr = MagicMock()
        mock_mgr.is_running.return_value = True
        mock_tornado_app.settings["network_share_manager"] = mock_mgr
        with patch_db_conn(db), patch.object(
            handler, "render"
        ) as mock_render, patch.object(handler, "get_argument", return_value=None):
            handler.get()
            shares_passed = mock_render.call_args[1]["shares"]
            assert shares_passed[0]["running"] is True

    # -- AdminNetworkSharesHandler POST --

    def test_post_redirects_non_admin(self, mock_tornado_app, mock_tornado_request):
        handler = AdminNetworkSharesHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler, role="user")
        with patch.object(handler, "is_admin_user", return_value=False), patch.object(
            handler, "set_status"
        ) as mock_status:
            handler.post()
            mock_status.assert_called_with(403)

    def test_post_validates_required_fields(
        self, mock_tornado_app, mock_tornado_request, db
    ):
        handler = self._make_handler(
            AdminNetworkSharesHandler, mock_tornado_app, mock_tornado_request
        )

        def get_arg(name, default=""):
            return {
                "name": "TestShare",
                "folder_path": "",
                "protocol": "webdav",
                "share_username": "u",
                "share_password": "p",
                "port": "8443",
                "read_only": "off",
            }.get(name, default)

        with patch_db_conn(db), patch.object(
            handler, "get_argument", side_effect=get_arg
        ), patch.object(handler, "redirect") as mock_redir:
            handler.post()
            mock_redir.assert_called_once()
            assert "required" in mock_redir.call_args[0][0]

    def test_post_validates_protocol(
        self, mock_tornado_app, mock_tornado_request, db, share_dir
    ):
        handler = self._make_handler(
            AdminNetworkSharesHandler, mock_tornado_app, mock_tornado_request
        )

        def get_arg(name, default=""):
            return {
                "name": "S",
                "folder_path": share_dir,
                "protocol": "ftp",
                "share_username": "u",
                "share_password": "p",
                "port": "8443",
                "read_only": "off",
            }.get(name, default)

        with patch_db_conn(db), patch.object(
            handler, "get_argument", side_effect=get_arg
        ), patch.object(handler, "redirect") as mock_redir:
            handler.post()
            assert "Invalid+protocol" in mock_redir.call_args[0][0]

    def test_post_validates_folder_exists(
        self, mock_tornado_app, mock_tornado_request, db
    ):
        handler = self._make_handler(
            AdminNetworkSharesHandler, mock_tornado_app, mock_tornado_request
        )

        def get_arg(name, default=""):
            return {
                "name": "S",
                "folder_path": "/no/such/dir",
                "protocol": "webdav",
                "share_username": "u",
                "share_password": "p",
                "port": "8443",
                "read_only": "off",
            }.get(name, default)

        with patch_db_conn(db), patch.object(
            handler, "get_argument", side_effect=get_arg
        ), patch.object(handler, "redirect") as mock_redir:
            handler.post()
            assert "Folder+does+not+exist" in mock_redir.call_args[0][0]

    def test_post_creates_share_successfully(
        self, mock_tornado_app, mock_tornado_request, db, share_dir
    ):
        handler = self._make_handler(
            AdminNetworkSharesHandler, mock_tornado_app, mock_tornado_request
        )

        def get_arg(name, default=""):
            return {
                "name": "NewShare",
                "folder_path": share_dir,
                "protocol": "webdav",
                "share_username": "admin",
                "share_password": "pass123",
                "port": "9999",
                "read_only": "off",
            }.get(name, default)

        mock_mgr = MagicMock()
        mock_tornado_app.settings["network_share_manager"] = mock_mgr
        with patch_db_conn(db), patch.object(
            handler, "get_argument", side_effect=get_arg
        ), patch.object(
            handler, "get_display_username", return_value="admin (Admin)"
        ), patch.object(
            handler, "redirect"
        ) as mock_redir:
            handler.post()
            mock_redir.assert_called_with("/admin/network-shares")
            shares = get_all_network_shares(db)
            assert len(shares) == 1
            assert shares[0]["name"] == "NewShare"
            assert shares[0]["port"] == 9999
            mock_mgr.start_share.assert_called_once()

    # -- AdminNetworkShareDeleteHandler --

    def test_delete_stops_and_removes(
        self, mock_tornado_app, mock_tornado_request, db, share_dir
    ):
        create_network_share(db, "del1", "D", share_dir, "webdav", 8443, "u", "p")
        handler = self._make_handler(
            AdminNetworkShareDeleteHandler, mock_tornado_app, mock_tornado_request
        )
        mock_mgr = MagicMock()
        mock_tornado_app.settings["network_share_manager"] = mock_mgr

        def get_arg(name, default=""):
            return {"share_id": "del1"}.get(name, default)

        with patch_db_conn(db), patch.object(
            handler, "get_argument", side_effect=get_arg
        ), patch.object(
            handler, "get_display_username", return_value="admin"
        ), patch.object(
            handler, "redirect"
        ) as mock_redir:
            handler.post()
            mock_mgr.stop_share.assert_called_once_with("del1")
            assert get_network_share(db, "del1") is None
            mock_redir.assert_called_with("/admin/network-shares")

    def test_delete_non_admin(self, mock_tornado_app, mock_tornado_request):
        handler = AdminNetworkShareDeleteHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler, role="user")
        with patch.object(handler, "is_admin_user", return_value=False), patch.object(
            handler, "set_status"
        ) as mock_status:
            handler.post()
            mock_status.assert_called_with(403)

    # -- AdminNetworkShareToggleHandler --

    def test_toggle_disables_share(
        self, mock_tornado_app, mock_tornado_request, db, share_dir
    ):
        create_network_share(db, "tog1", "T", share_dir, "webdav", 8443, "u", "p")
        handler = self._make_handler(
            AdminNetworkShareToggleHandler, mock_tornado_app, mock_tornado_request
        )
        mock_mgr = MagicMock()
        mock_tornado_app.settings["network_share_manager"] = mock_mgr

        def get_arg(name, default=""):
            return {"share_id": "tog1"}.get(name, default)

        with patch_db_conn(db), patch.object(
            handler, "get_argument", side_effect=get_arg
        ), patch.object(
            handler, "get_display_username", return_value="admin"
        ), patch.object(
            handler, "redirect"
        ):
            handler.post()
            s = get_network_share(db, "tog1")
            assert s["enabled"] is False
            mock_mgr.stop_share.assert_called_once_with("tog1")

    def test_toggle_enables_share(
        self, mock_tornado_app, mock_tornado_request, db, share_dir
    ):
        create_network_share(db, "tog2", "T", share_dir, "webdav", 8443, "u", "p")
        update_network_share(db, "tog2", enabled=False)
        handler = self._make_handler(
            AdminNetworkShareToggleHandler, mock_tornado_app, mock_tornado_request
        )
        mock_mgr = MagicMock()
        mock_tornado_app.settings["network_share_manager"] = mock_mgr

        def get_arg(name, default=""):
            return {"share_id": "tog2"}.get(name, default)

        with patch_db_conn(db), patch.object(
            handler, "get_argument", side_effect=get_arg
        ), patch.object(
            handler, "get_display_username", return_value="admin"
        ), patch.object(
            handler, "redirect"
        ):
            handler.post()
            s = get_network_share(db, "tog2")
            assert s["enabled"] is True
            mock_mgr.start_share.assert_called_once()

    def test_toggle_nonexistent_share(self, mock_tornado_app, mock_tornado_request, db):
        handler = self._make_handler(
            AdminNetworkShareToggleHandler, mock_tornado_app, mock_tornado_request
        )

        def get_arg(name, default=""):
            return {"share_id": "nope"}.get(name, default)

        with patch_db_conn(db), patch.object(
            handler, "get_argument", side_effect=get_arg
        ), patch.object(handler, "redirect") as mock_redir:
            handler.post()
            mock_redir.assert_called_with("/admin/network-shares")

    # -- POST: database unavailable --

    def test_post_db_unavailable(self, mock_tornado_app, mock_tornado_request):
        handler = self._make_handler(
            AdminNetworkSharesHandler, mock_tornado_app, mock_tornado_request
        )
        mock_tornado_app.settings["db_conn"] = None
        with patch.object(handler, "redirect") as mock_redir:
            handler.post()
            mock_redir.assert_called_once()
            assert "Database+unavailable" in mock_redir.call_args[0][0]

    # -- POST: invalid port (non-numeric) --

    def test_post_invalid_port_uses_default(
        self, mock_tornado_app, mock_tornado_request, db, share_dir
    ):
        handler = self._make_handler(
            AdminNetworkSharesHandler, mock_tornado_app, mock_tornado_request
        )

        def get_arg(name, default=""):
            return {
                "name": "S",
                "folder_path": share_dir,
                "protocol": "webdav",
                "share_username": "u",
                "share_password": "p",
                "port": "notanumber",
                "read_only": "off",
            }.get(name, default)

        mock_mgr = MagicMock()
        mock_tornado_app.settings["network_share_manager"] = mock_mgr
        with patch_db_conn(db), patch.object(
            handler, "get_argument", side_effect=get_arg
        ), patch.object(
            handler, "get_display_username", return_value="admin"
        ), patch.object(
            handler, "redirect"
        ) as mock_redir:
            handler.post()
            mock_redir.assert_called_with("/admin/network-shares")
            shares = get_all_network_shares(db)
            assert len(shares) == 1
            assert shares[0]["port"] == 8443  # default fallback

    # -- POST: port out of range --

    def test_post_port_out_of_range_zero(
        self, mock_tornado_app, mock_tornado_request, db, share_dir
    ):
        handler = self._make_handler(
            AdminNetworkSharesHandler, mock_tornado_app, mock_tornado_request
        )

        def get_arg(name, default=""):
            return {
                "name": "S",
                "folder_path": share_dir,
                "protocol": "webdav",
                "share_username": "u",
                "share_password": "p",
                "port": "0",
                "read_only": "off",
            }.get(name, default)

        with patch_db_conn(db), patch.object(
            handler, "get_argument", side_effect=get_arg
        ), patch.object(handler, "redirect") as mock_redir:
            handler.post()
            assert "Port+must+be+1-65535" in mock_redir.call_args[0][0]

    def test_post_port_out_of_range_too_high(
        self, mock_tornado_app, mock_tornado_request, db, share_dir
    ):
        handler = self._make_handler(
            AdminNetworkSharesHandler, mock_tornado_app, mock_tornado_request
        )

        def get_arg(name, default=""):
            return {
                "name": "S",
                "folder_path": share_dir,
                "protocol": "webdav",
                "share_username": "u",
                "share_password": "p",
                "port": "70000",
                "read_only": "off",
            }.get(name, default)

        with patch_db_conn(db), patch.object(
            handler, "get_argument", side_effect=get_arg
        ), patch.object(handler, "redirect") as mock_redir:
            handler.post()
            assert "Port+must+be+1-65535" in mock_redir.call_args[0][0]

    # -- POST: create_network_share failure --

    def test_post_create_db_failure(
        self, mock_tornado_app, mock_tornado_request, db, share_dir
    ):
        handler = self._make_handler(
            AdminNetworkSharesHandler, mock_tornado_app, mock_tornado_request
        )

        def get_arg(name, default=""):
            return {
                "name": "S",
                "folder_path": share_dir,
                "protocol": "webdav",
                "share_username": "u",
                "share_password": "p",
                "port": "8443",
                "read_only": "off",
            }.get(name, default)

        with patch_db_conn(db), patch(
            "aird.services.network_share_service.create_network_share", return_value=False
        ), patch.object(handler, "get_argument", side_effect=get_arg), patch.object(
            handler, "redirect"
        ) as mock_redir:
            handler.post()
            assert "Failed+to+create+share" in mock_redir.call_args[0][0]

    # -- POST: read_only checkbox on --

    def test_post_read_only_on(
        self, mock_tornado_app, mock_tornado_request, db, share_dir
    ):
        handler = self._make_handler(
            AdminNetworkSharesHandler, mock_tornado_app, mock_tornado_request
        )

        def get_arg(name, default=""):
            return {
                "name": "RO",
                "folder_path": share_dir,
                "protocol": "webdav",
                "share_username": "u",
                "share_password": "p",
                "port": "8443",
                "read_only": "on",
            }.get(name, default)

        mock_mgr = MagicMock()
        mock_tornado_app.settings["network_share_manager"] = mock_mgr
        with patch_db_conn(db), patch.object(
            handler, "get_argument", side_effect=get_arg
        ), patch.object(
            handler, "get_display_username", return_value="admin"
        ), patch.object(
            handler, "redirect"
        ):
            handler.post()
            shares = get_all_network_shares(db)
            assert len(shares) == 1
            assert shares[0]["read_only"] is True
            share_dict = mock_mgr.start_share.call_args[0][0]
            assert share_dict["read_only"] is True

    # -- POST: no manager available --

    def test_post_creates_share_without_manager(
        self, mock_tornado_app, mock_tornado_request, db, share_dir
    ):
        handler = self._make_handler(
            AdminNetworkSharesHandler, mock_tornado_app, mock_tornado_request
        )

        def get_arg(name, default=""):
            return {
                "name": "NoMgr",
                "folder_path": share_dir,
                "protocol": "webdav",
                "share_username": "u",
                "share_password": "p",
                "port": "8443",
                "read_only": "off",
            }.get(name, default)

        with patch_db_conn(db), patch.object(
            handler, "get_argument", side_effect=get_arg
        ), patch.object(
            handler, "get_display_username", return_value="admin"
        ), patch.object(
            handler, "redirect"
        ) as mock_redir:
            mock_tornado_app.settings["network_share_manager"] = None
            handler.post()
            mock_redir.assert_called_with("/admin/network-shares")
            assert len(get_all_network_shares(db)) == 1

    # -- POST: SMB protocol --

    def test_post_smb_protocol(
        self, mock_tornado_app, mock_tornado_request, db, share_dir
    ):
        handler = self._make_handler(
            AdminNetworkSharesHandler, mock_tornado_app, mock_tornado_request
        )

        def get_arg(name, default=""):
            return {
                "name": "SMBShare",
                "folder_path": share_dir,
                "protocol": "smb",
                "share_username": "u",
                "share_password": "p",
                "port": "4455",
                "read_only": "off",
            }.get(name, default)

        mock_mgr = MagicMock()
        mock_tornado_app.settings["network_share_manager"] = mock_mgr
        with patch_db_conn(db), patch.object(
            handler, "get_argument", side_effect=get_arg
        ), patch.object(
            handler, "get_display_username", return_value="admin"
        ), patch.object(
            handler, "redirect"
        ):
            handler.post()
            shares = get_all_network_shares(db)
            assert shares[0]["protocol"] == "smb"

    # -- DELETE: empty share_id --

    def test_delete_empty_share_id(self, mock_tornado_app, mock_tornado_request, db):
        handler = self._make_handler(
            AdminNetworkShareDeleteHandler, mock_tornado_app, mock_tornado_request
        )

        def get_arg(name, default=""):
            return {"share_id": ""}.get(name, default)

        with patch_db_conn(db), patch.object(
            handler, "get_argument", side_effect=get_arg
        ), patch.object(handler, "redirect") as mock_redir:
            handler.post()
            mock_redir.assert_called_with("/admin/network-shares")

    def test_delete_no_db(self, mock_tornado_app, mock_tornado_request):
        handler = self._make_handler(
            AdminNetworkShareDeleteHandler, mock_tornado_app, mock_tornado_request
        )

        def get_arg(name, default=""):
            return {"share_id": "some-id"}.get(name, default)

        mock_tornado_app.settings["db_conn"] = None
        with patch.object(handler, "get_argument", side_effect=get_arg), patch.object(
            handler, "redirect"
        ) as mock_redir:
            handler.post()
            mock_redir.assert_called_with("/admin/network-shares")

    def test_delete_without_manager(
        self, mock_tornado_app, mock_tornado_request, db, share_dir
    ):
        create_network_share(db, "del2", "D", share_dir, "webdav", 8443, "u", "p")
        handler = self._make_handler(
            AdminNetworkShareDeleteHandler, mock_tornado_app, mock_tornado_request
        )

        def get_arg(name, default=""):
            return {"share_id": "del2"}.get(name, default)

        with patch_db_conn(db), patch.object(
            handler, "get_argument", side_effect=get_arg
        ), patch.object(
            handler, "get_display_username", return_value="admin"
        ), patch.object(
            handler, "redirect"
        ) as mock_redir:
            mock_tornado_app.settings["network_share_manager"] = None
            handler.post()
            assert get_network_share(db, "del2") is None
            mock_redir.assert_called_with("/admin/network-shares")

    # -- TOGGLE: non-admin --

    def test_toggle_non_admin(self, mock_tornado_app, mock_tornado_request):
        handler = AdminNetworkShareToggleHandler(mock_tornado_app, mock_tornado_request)
        authenticate(handler, role="user")
        with patch.object(handler, "is_admin_user", return_value=False), patch.object(
            handler, "set_status"
        ) as mock_status:
            handler.post()
            mock_status.assert_called_with(403)

    # -- TOGGLE: empty share_id --

    def test_toggle_empty_share_id(self, mock_tornado_app, mock_tornado_request, db):
        handler = self._make_handler(
            AdminNetworkShareToggleHandler, mock_tornado_app, mock_tornado_request
        )

        def get_arg(name, default=""):
            return {"share_id": ""}.get(name, default)

        with patch_db_conn(db), patch.object(
            handler, "get_argument", side_effect=get_arg
        ), patch.object(handler, "redirect") as mock_redir:
            handler.post()
            mock_redir.assert_called_with("/admin/network-shares")

    def test_toggle_no_db(self, mock_tornado_app, mock_tornado_request):
        handler = self._make_handler(
            AdminNetworkShareToggleHandler, mock_tornado_app, mock_tornado_request
        )

        def get_arg(name, default=""):
            return {"share_id": "any"}.get(name, default)

        mock_tornado_app.settings["db_conn"] = None
        with patch.object(handler, "get_argument", side_effect=get_arg), patch.object(
            handler, "redirect"
        ) as mock_redir:
            handler.post()
            mock_redir.assert_called_with("/admin/network-shares")

    # -- GET: db_conn is None --

    def test_get_no_db(self, mock_tornado_app, mock_tornado_request):
        handler = self._make_handler(
            AdminNetworkSharesHandler, mock_tornado_app, mock_tornado_request
        )
        mock_tornado_app.settings["network_share_manager"] = None
        mock_tornado_app.settings["db_conn"] = None
        with patch.object(handler, "render") as mock_render, patch.object(
            handler, "get_argument", return_value=None
        ):
            handler.get()
            assert mock_render.called
            shares_passed = mock_render.call_args[1]["shares"]
            assert shares_passed == []

    # -- GET: error query parameter forwarded --

    def test_get_forwards_error_param(self, mock_tornado_app, mock_tornado_request, db):
        handler = self._make_handler(
            AdminNetworkSharesHandler, mock_tornado_app, mock_tornado_request
        )
        mock_tornado_app.settings["network_share_manager"] = None
        with patch_db_conn(db), patch.object(
            handler, "render"
        ) as mock_render, patch.object(
            handler, "get_argument", return_value="Something+went+wrong"
        ):
            handler.get()
            assert mock_render.call_args[1]["error"] == "Something+went+wrong"

    # -- TOGGLE: without manager --

    def test_toggle_disables_without_manager(
        self, mock_tornado_app, mock_tornado_request, db, share_dir
    ):
        create_network_share(db, "tog3", "T", share_dir, "webdav", 8443, "u", "p")
        handler = self._make_handler(
            AdminNetworkShareToggleHandler, mock_tornado_app, mock_tornado_request
        )

        def get_arg(name, default=""):
            return {"share_id": "tog3"}.get(name, default)

        mock_tornado_app.settings["network_share_manager"] = None
        with patch_db_conn(db), patch.object(
            handler, "get_argument", side_effect=get_arg
        ), patch.object(
            handler, "get_display_username", return_value="admin"
        ), patch.object(
            handler, "redirect"
        ):
            handler.post()
            assert get_network_share(db, "tog3")["enabled"] is False
