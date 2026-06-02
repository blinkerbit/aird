import json

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from aird.handlers.share_handlers import (
    ShareFilesHandler,
    ShareCreateHandler,
    ShareRevokeHandler,
    ShareUpdateHandler,
    TokenVerificationHandler,
    SharedListHandler,
    SharedFileHandler,
    _build_token_update_fields,
    _collect_paths_from_request,
    _get_provided_token,
    _check_share_access,
    _normalize_path_entry,
    _parse_path_entries_for_update,
    _parse_paths_for_update,
    _resolve_final_paths_dynamic,
    _resolve_final_paths_static,
    _get_share_file_list,
    _apply_metadata_updates,
)
from aird.cloud import CloudProviderError

from tests.handler_helpers import _default_services, authenticate, patch_db_conn, prepare_handler
from aird.constants import FEATURE_FLAGS as _FEATURE_FLAGS_DEFAULTS


def _flags_file_share_only(key: str, default: bool = False) -> bool:
    if key == "file_share":
        return True
    return bool(_FEATURE_FLAGS_DEFAULTS.get(key, default))
class TestShareFilesHandler:
    def setup_method(self):
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_app.settings = {"cookie_secret": "test_secret", "services": _default_services()}

    def test_get_share_page(self):
        handler = prepare_handler(ShareFilesHandler(self.mock_app, self.mock_request))
        authenticate(handler, role="user")

        with patch(
            "aird.handlers.base_handler.is_feature_enabled",
            side_effect=_flags_file_share_only,
        ), patch.object(handler, "render") as mock_render:

            handler.get()
            mock_render.assert_called_with("share.html", shares={})

    def test_get_feature_disabled(self):
        handler = prepare_handler(ShareFilesHandler(self.mock_app, self.mock_request))
        authenticate(handler, role="user")
        handler.set_status = MagicMock()

        with patch(
            "aird.handlers.base_handler.is_feature_enabled", return_value=False
        ), patch.object(handler, "write") as mock_write:

            handler.get()
            handler.set_status.assert_called_with(403)
            mock_write.assert_called_with(
                "Feature disabled: File sharing is currently disabled by administrator"
            )


class TestShareCreateHandler:
    def setup_method(self):
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_app.settings = {
            "cookie_secret": "test_secret",
            "db_conn": MagicMock(),
            "services": _default_services(),
        }

    def _build_handler(self, body):
        handler = prepare_handler(ShareCreateHandler(self.mock_app, self.mock_request))
        authenticate(handler, role="user")
        handler.check_xsrf_cookie = MagicMock()
        handler.request.headers = {"X-XSRFToken": "token"}
        handler.get_cookie = MagicMock(return_value="token")
        handler.request.body = json.dumps(body).encode("utf-8")
        handler.set_status = MagicMock()
        return handler

    def test_create_share_success(self):
        handler = self._build_handler({"paths": ["test.txt"]})

        with patch(
            "aird.handlers.base_handler.is_feature_enabled",
            side_effect=_flags_file_share_only,
        ), patch("os.path.abspath", return_value="/root/test.txt"), patch(
            "aird.handlers.share_handlers.is_within_root", return_value=True
        ), patch(
            "os.path.isfile", return_value=True
        ), patch_db_conn(
            MagicMock(), modules=["aird.handlers.share_handlers"]
        ), patch(
            "aird.services.share_service.insert_share", return_value=True
        ), patch.object(
            handler, "write"
        ) as mock_write:

            handler.post()
            mock_write.assert_called()
            assert "id" in mock_write.call_args[0][0]

    def test_create_share_feature_disabled(self):
        handler = self._build_handler({"paths": ["test.txt"]})

        with patch(
            "aird.handlers.base_handler.is_feature_enabled", return_value=False
        ), patch.object(handler, "write") as mock_write:
            handler.post()
            handler.set_status.assert_called_with(403)
            mock_write.assert_called_with({"error": "File sharing is disabled"})

    def test_create_share_no_valid_files(self):
        handler = self._build_handler({"paths": ["missing.txt"]})

        with patch(
            "aird.handlers.base_handler.is_feature_enabled",
            side_effect=_flags_file_share_only,
        ), patch("os.path.abspath", return_value="/root/missing.txt"), patch(
            "aird.handlers.share_handlers.is_within_root", return_value=True
        ), patch(
            "os.path.isfile", return_value=False
        ), patch(
            "os.path.isdir", return_value=False
        ), patch_db_conn(
            MagicMock(), modules=["aird.handlers.share_handlers"]
        ), patch(
            "aird.handlers.share_handlers.remove_share_cloud_dir"
        ) as mock_remove, patch.object(
            handler, "write"
        ) as mock_write:

            handler.post()
            handler.set_status.assert_called_with(400)
            mock_write.assert_called_with({"error": "No valid files or directories"})
            mock_remove.assert_called()

    def test_create_share_dynamic_requires_directory(self):
        handler = self._build_handler({"paths": [], "share_type": "dynamic"})

        with patch(
            "aird.handlers.base_handler.is_feature_enabled",
            side_effect=_flags_file_share_only,
        ), patch_db_conn(MagicMock(), modules=["aird.handlers.share_handlers"]), patch(
            "aird.handlers.share_handlers.remove_share_cloud_dir"
        ) as mock_remove, patch.object(
            handler, "write"
        ) as mock_write:

            handler.post()
            handler.set_status.assert_called_with(400)
            mock_write.assert_called_with(
                {"error": "No valid directories for dynamic share"}
            )
            mock_remove.assert_called()

    def test_create_share_cloud_error(self):
        handler = self._build_handler(
            {"paths": [{"type": "cloud", "path": "cloud://item"}]}
        )

        with patch(
            "aird.handlers.base_handler.is_feature_enabled",
            side_effect=_flags_file_share_only,
        ), patch_db_conn(MagicMock(), modules=["aird.handlers.share_handlers"]), patch(
            "aird.handlers.share_handlers.download_cloud_items",
            side_effect=CloudProviderError("boom"),
        ), patch(
            "aird.handlers.share_handlers.remove_share_cloud_dir"
        ) as mock_remove, patch.object(
            handler, "write"
        ) as mock_write:

            handler.post()
            handler.set_status.assert_called_with(400)
            mock_write.assert_called_with({"error": "boom"})
            mock_remove.assert_called()

    def test_create_share_db_missing(self):
        handler = self._build_handler({"paths": ["file.txt"]})
        self.mock_app.settings["db_conn"] = None

        with patch(
            "aird.handlers.base_handler.is_feature_enabled",
            side_effect=_flags_file_share_only,
        ), patch("os.path.abspath", return_value="/root/file.txt"), patch(
            "aird.handlers.share_handlers.is_within_root", return_value=True
        ), patch(
            "os.path.isfile", return_value=True
        ), patch.object(
            handler, "write"
        ) as mock_write:

            handler.post()
            handler.set_status.assert_called_with(500)
            mock_write.assert_called_with(
                {"error": "Database connection not available"}
            )

    def test_create_share_static_empty_folder(self):
        handler = self._build_handler({"paths": ["emptydir"], "share_type": "static"})

        with patch(
            "aird.handlers.base_handler.is_feature_enabled",
            side_effect=_flags_file_share_only,
        ), patch("os.path.abspath", side_effect=lambda p: p), patch(
            "aird.handlers.share_handlers.is_within_root", return_value=True
        ), patch("os.path.isfile", return_value=False), patch(
            "os.path.isdir", return_value=True
        ), patch(
            "aird.handlers.share_handlers.get_all_files_recursive", return_value=[]
        ), patch_db_conn(
            MagicMock(), modules=["aird.handlers.share_handlers"]
        ), patch(
            "aird.services.share_service.insert_share", return_value=True
        ) as mock_insert, patch.object(
            handler, "write"
        ) as mock_write:

            handler.post()
            mock_write.assert_called()
            assert "id" in mock_write.call_args[0][0]
            inserted_paths = mock_insert.call_args[0][3]
            assert inserted_paths == ["emptydir"]


class TestShareRevokeHandler:
    def setup_method(self):
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_app.settings = {
            "cookie_secret": "test_secret",
            "db_conn": MagicMock(),
            "services": _default_services(),
        }

    def test_revoke_share_redirect(self):
        handler = prepare_handler(ShareRevokeHandler(self.mock_app, self.mock_request))
        authenticate(handler, role="user")
        handler.get_argument = MagicMock(return_value="share1")
        share_svc = self.mock_app.settings["services"]["share_service"]

        with patch(
            "aird.handlers.base_handler.is_feature_enabled",
            side_effect=_flags_file_share_only,
        ), patch_db_conn(MagicMock(), modules=["aird.handlers.share_handlers"]), patch.object(
            share_svc,
            "get_share",
            return_value={"id": "share1", "created_by": "user"},
        ), patch.object(
            share_svc, "delete_share"
        ) as mock_delete, patch(
            "aird.handlers.share_handlers.remove_share_cloud_dir"
        ), patch.object(
            handler, "redirect"
        ) as mock_redirect:

            handler.post()
            assert mock_delete.call_args[0][1] == "share1"
            mock_redirect.assert_called_with("/share")

    def test_revoke_share_json_response(self):
        handler = prepare_handler(ShareRevokeHandler(self.mock_app, self.mock_request))
        authenticate(handler, role="user")
        handler.get_argument = MagicMock(return_value="share1")
        handler.request.headers = {"Accept": "application/json"}
        share_svc = self.mock_app.settings["services"]["share_service"]

        with patch(
            "aird.handlers.base_handler.is_feature_enabled",
            side_effect=_flags_file_share_only,
        ), patch_db_conn(MagicMock(), modules=["aird.handlers.share_handlers"]), patch.object(
            share_svc,
            "get_share",
            return_value={"id": "share1", "created_by": "user"},
        ), patch.object(
            share_svc, "delete_share"
        ), patch(
            "aird.handlers.share_handlers.remove_share_cloud_dir"
        ), patch.object(
            handler, "write"
        ) as mock_write:

            handler.post()
            mock_write.assert_called_with({"ok": True})

    def test_revoke_forbidden_non_manager(self):
        handler = prepare_handler(ShareRevokeHandler(self.mock_app, self.mock_request))
        authenticate(handler, role="user")
        handler.get_argument = MagicMock(return_value="share1")
        share_svc = self.mock_app.settings["services"]["share_service"]

        with patch(
            "aird.handlers.base_handler.is_feature_enabled",
            side_effect=_flags_file_share_only,
        ), patch_db_conn(MagicMock(), modules=["aird.handlers.share_handlers"]), patch.object(
            share_svc,
            "get_share",
            return_value={"id": "share1", "created_by": "other"},
        ), patch.object(
            share_svc, "delete_share"
        ) as mock_delete, patch.object(
            handler, "write"
        ) as mock_write:

            handler.post()
            handler.set_status.assert_called_with(403)
            mock_write.assert_called_with(
                {"error": "Only the share owner can revoke this share"}
            )
            mock_delete.assert_not_called()

    def setup_method(self):
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_app.settings = {
            "cookie_secret": "test_secret",
            "db_conn": MagicMock(),
            "services": _default_services(),
        }

    def _build_handler(self, body):
        handler = prepare_handler(ShareUpdateHandler(self.mock_app, self.mock_request))
        authenticate(handler, role="user")
        handler.check_xsrf_cookie = MagicMock()
        handler.request.body = json.dumps(body).encode("utf-8")
        handler.set_status = MagicMock()
        return handler

    def test_update_share_missing_id(self):
        handler = self._build_handler({})

        with patch(
            "aird.handlers.base_handler.is_feature_enabled",
            side_effect=_flags_file_share_only,
        ), patch.object(handler, "write") as mock_write:

            handler.post()
            handler.set_status.assert_called_with(400)
            mock_write.assert_called_with({"error": "Share ID is required"})

    def test_update_share_not_found(self):
        handler = self._build_handler({"share_id": "missing"})
        share_svc = self.mock_app.settings["services"]["share_service"]

        with patch(
            "aird.handlers.base_handler.is_feature_enabled",
            side_effect=_flags_file_share_only,
        ), patch_db_conn(MagicMock(), modules=["aird.handlers.share_handlers"]), patch.object(
            share_svc, "get_share", return_value=None
        ), patch.object(
            handler, "write"
        ) as mock_write:

            handler.post()
            handler.set_status.assert_called_with(404)
            mock_write.assert_called_with({"error": "Share not found"})

    def test_update_share_forbidden_non_manager(self):
        handler = self._build_handler({"share_id": "share1", "paths": []})
        share_svc = self.mock_app.settings["services"]["share_service"]

        with patch(
            "aird.handlers.base_handler.is_feature_enabled",
            side_effect=_flags_file_share_only,
        ), patch_db_conn(MagicMock(), modules=["aird.handlers.share_handlers"]), patch.object(
            share_svc,
            "get_share",
            return_value={
                "paths": [],
                "share_type": "static",
                "created_by": "other",
            },
        ), patch.object(
            handler, "write"
        ) as mock_write:

            handler.post()
            handler.set_status.assert_called_with(403)
            mock_write.assert_called_with({"error": "Access denied"})

    def test_update_share_db_missing(self):
        handler = self._build_handler({"share_id": "share1"})

        with patch(
            "aird.handlers.base_handler.is_feature_enabled",
            side_effect=_flags_file_share_only,
        ), patch_db_conn(None, modules=["aird.handlers.share_handlers"]), patch.object(
            handler, "write"
        ) as mock_write:

            handler.post()
            handler.set_status.assert_called_with(500)
            mock_write.assert_called_with(
                {"error": "Database connection not available"}
            )

    def test_update_share_dynamic_cloud_error(self):
        body = {
            "share_id": "share1",
            "paths": [{"type": "cloud", "path": "cloud://file"}],
            "share_type": "static",
        }
        handler = self._build_handler(body)
        share_svc = self.mock_app.settings["services"]["share_service"]

        with patch(
            "aird.handlers.base_handler.is_feature_enabled",
            side_effect=_flags_file_share_only,
        ), patch_db_conn(MagicMock(), modules=["aird.handlers.share_handlers"]), patch.object(
            share_svc,
            "get_share",
            return_value={
                "paths": [],
                "share_type": "static",
                "created_by": "user",
            },
        ), patch(
            "aird.handlers.share_handlers.download_cloud_items",
            side_effect=CloudProviderError("boom"),
        ), patch.object(
            handler, "write"
        ) as mock_write:

            handler.post()
            handler.set_status.assert_called_with(400)
            mock_write.assert_called_with({"error": "boom"})

    def test_update_share_disable_token_generates_new(self):
        body = {
            "share_id": "share1",
            "disable_token": False,
            "paths": [],
        }
        handler = self._build_handler(body)

        share_data = {
            "paths": [],
            "share_type": "static",
            "secret_token": None,
            "created_by": "user",
        }
        share_svc = self.mock_app.settings["services"]["share_service"]
        with patch(
            "aird.handlers.base_handler.is_feature_enabled",
            side_effect=_flags_file_share_only,
        ), patch_db_conn(MagicMock(), modules=["aird.handlers.share_handlers"]), patch.object(
            share_svc,
            "get_share",
            side_effect=[share_data, {"secret_token": "newtoken", "created_by": "user"}],
        ), patch.object(
            share_svc, "update_share", return_value=True
        ), patch.object(
            handler, "write"
        ) as mock_write:

            handler.post()
            response = mock_write.call_args[0][0]
            assert response["success"] is True
            assert response["new_token"] == "newtoken"


class TestTokenVerificationHandler:
    def setup_method(self):
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_app.settings = {
            "cookie_secret": "test_secret",
            "db_conn": MagicMock(),
            "services": _default_services(),
        }

    def test_verify_token_success(self):
        handler = prepare_handler(
            TokenVerificationHandler(self.mock_app, self.mock_request)
        )
        handler.request.body = json.dumps({"token": "secret"}).encode("utf-8")

        with patch_db_conn(
            MagicMock(), modules=["aird.handlers.share_handlers"]
        ), patch(
            "aird.services.share_service.get_share_by_id",
            return_value={"secret_token": "secret"},
        ), patch.object(
            handler, "write"
        ) as mock_write:

            handler.post("share1")
            mock_write.assert_called_with({"success": True})

    def test_verify_token_missing_token(self):
        handler = prepare_handler(
            TokenVerificationHandler(self.mock_app, self.mock_request)
        )
        handler.request.body = json.dumps({}).encode("utf-8")
        handler.set_status = MagicMock()

        with patch_db_conn(
            MagicMock(), modules=["aird.handlers.share_handlers"]
        ), patch(
            "aird.services.share_service.get_share_by_id",
            return_value={"secret_token": "secret"},
        ), patch.object(
            handler, "write"
        ) as mock_write:

            handler.post("share1")
            handler.set_status.assert_called_with(400)
            mock_write.assert_called_with({"error": "Token is required"})

    def test_verify_token_invalid(self):
        handler = prepare_handler(
            TokenVerificationHandler(self.mock_app, self.mock_request)
        )
        handler.request.body = json.dumps({"token": "bad"}).encode("utf-8")
        handler.set_status = MagicMock()

        with patch_db_conn(
            MagicMock(), modules=["aird.handlers.share_handlers"]
        ), patch(
            "aird.services.share_service.get_share_by_id",
            return_value={"secret_token": "secret"},
        ), patch.object(
            handler, "write"
        ) as mock_write:

            handler.post("share1")
            handler.set_status.assert_called_with(403)
            mock_write.assert_called_with({"error": "Invalid token"})


class TestSharedListHandler:
    def setup_method(self):
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_app.settings = {
            "cookie_secret": "test_secret",
            "db_conn": MagicMock(),
            "services": _default_services(),
        }

    def test_get_shared_list_static(self):
        handler = prepare_handler(SharedListHandler(self.mock_app, self.mock_request))

        share_data = {
            "paths": ["test.txt"],
            "share_type": "static",
            "secret_token": None,
        }
        with patch_db_conn(
            MagicMock(), modules=["aird.handlers.share_handlers"]
        ), patch(
            "aird.services.share_service.get_share_by_id", return_value=share_data
        ), patch(
            "aird.services.share_service.is_share_expired", return_value=False
        ), patch(
            "aird.handlers.share_handlers.filter_files_by_patterns",
            return_value=["test.txt"],
        ), patch.object(
            handler, "render"
        ) as mock_render:

            handler.get("share1")
            mock_render.assert_called()
            assert mock_render.call_args[1]["share_id"] == "share1"

    def test_shared_list_expired(self):
        handler = prepare_handler(SharedListHandler(self.mock_app, self.mock_request))
        handler.set_status = MagicMock()

        share_data = {"paths": ["test.txt"], "share_type": "static"}
        with patch_db_conn(
            MagicMock(), modules=["aird.handlers.share_handlers"]
        ), patch(
            "aird.services.share_service.get_share_by_id", return_value=share_data
        ), patch(
            "aird.services.share_service.is_share_expired", return_value=True
        ), patch.object(
            handler, "write"
        ) as mock_write:

            handler.get("share1")
            handler.set_status.assert_called_with(410)
            mock_write.assert_called_with(
                "Share expired: This share is no longer available"
            )

    def test_shared_list_requires_token_redirects(self):
        handler = prepare_handler(SharedListHandler(self.mock_app, self.mock_request))
        handler.redirect = MagicMock()
        # Ensure get_cookie returns None (not a MagicMock) to simulate missing cookie
        handler.get_cookie = MagicMock(return_value=None)
        # Mock request.headers.get to return empty string for Authorization header
        handler.request.headers = MagicMock()
        handler.request.headers.get = MagicMock(return_value="")

        share_data = {
            "paths": ["test.txt"],
            "share_type": "static",
            "secret_token": "abc",
        }
        with patch_db_conn(
            MagicMock(), modules=["aird.handlers.share_handlers"]
        ), patch(
            "aird.services.share_service.get_share_by_id", return_value=share_data
        ), patch(
            "aird.services.share_service.is_share_expired", return_value=False
        ):

            handler.get("share1")
            handler.redirect.assert_called_with("/shared/share1/verify")


class TestSharedFileHandler:
    def setup_method(self):
        self.mock_app = MagicMock()
        self.mock_request = MagicMock()
        self.mock_app.settings = {
            "cookie_secret": "test_secret",
            "db_conn": MagicMock(),
            "services": _default_services(),
        }

    @pytest.mark.asyncio
    async def test_get_shared_file_success(self):
        handler = prepare_handler(SharedFileHandler(self.mock_app, self.mock_request))
        handler.set_status = MagicMock()

        share_data = {
            "paths": ["test.txt"],
            "share_type": "static",
            "secret_token": None,
        }
        with patch_db_conn(
            MagicMock(), modules=["aird.handlers.share_handlers"]
        ), patch(
            "aird.services.share_service.get_share_by_id", return_value=share_data
        ), patch(
            "aird.services.share_service.is_share_expired", return_value=False
        ), patch(
            "aird.handlers.share_handlers.filter_files_by_patterns",
            return_value=["test.txt"],
        ), patch(
            "os.path.abspath", return_value="/root/test.txt"
        ), patch(
            "aird.handlers.share_handlers.share_covers_relative_path", return_value=True
        ), patch(
            "aird.handlers.share_handlers.is_within_root", return_value=True
        ), patch(
            "os.path.isfile", return_value=True
        ), patch(
            "aird.handlers.share_handlers.MainHandler"
        ) as mock_main_handler:

            mock_main_handler.serve_file = AsyncMock()

            await handler.get("share1", "test.txt")
            mock_main_handler.serve_file.assert_awaited_with(handler, "/root/test.txt")

    @pytest.mark.asyncio
    async def test_shared_file_requires_token(self):
        handler = prepare_handler(SharedFileHandler(self.mock_app, self.mock_request))
        handler.set_status = MagicMock()
        handler.write = MagicMock()
        # Ensure get_cookie returns None (not a MagicMock)
        handler.get_cookie = MagicMock(return_value=None)
        # Mock request.headers.get to return empty string for Authorization header
        handler.request.headers = MagicMock()
        handler.request.headers.get = MagicMock(return_value="")

        share_data = {
            "paths": ["test.txt"],
            "share_type": "static",
            "secret_token": "secret",
        }
        with patch_db_conn(
            MagicMock(), modules=["aird.handlers.share_handlers"]
        ), patch(
            "aird.services.share_service.get_share_by_id", return_value=share_data
        ), patch(
            "aird.services.share_service.is_share_expired", return_value=False
        ):

            await handler.get("share1", "test.txt")
            handler.set_status.assert_called_with(403)
            handler.write.assert_called_with(
                "Access denied: Invalid or expired access token"
            )


class TestApplyMetadataUpdates:
    def test_clearing_allowed_users_does_not_implicitly_clear_token(self):
        uf = {}
        _apply_metadata_updates(
            {"allowed_users": [], "disable_token": None},
            {"secret_token": "keep"},
            uf,
        )
        assert uf["allowed_users"] is None
        assert "secret_token" not in uf
        assert "disable_token" not in uf

    def test_explicit_disable_token_false_keeps_when_clearing_allow_list(self):
        uf = {}
        _apply_metadata_updates(
            {"allowed_users": [], "disable_token": False},
            {"secret_token": "preserve"},
            uf,
        )
        assert uf["allowed_users"] is None
        assert uf["secret_token"] == "preserve"
        assert uf["disable_token"] is False

    def test_expiry_date_none_clears_in_update_fields(self):
        uf = {}
        _apply_metadata_updates({"expiry_date": None}, {}, uf)
        assert "expiry_date" in uf
        assert uf["expiry_date"] is None


class TestShareHandlerPathHelpers:
    def test_normalize_path_entry_cloud_dict(self):
        assert _normalize_path_entry({"type": "cloud", "id": "1"}) == (None, True)

    def test_normalize_path_entry_local_dict(self):
        assert _normalize_path_entry({"type": "local", "path": " /x/ "}) == (
            "/x/",
            False,
        )

    def test_normalize_path_entry_plain_string(self):
        assert _normalize_path_entry("file.txt") == ("file.txt", False)
        assert _normalize_path_entry("   ") == (None, False)
        assert _normalize_path_entry(123) == (None, False)

    def test_get_provided_token_bearer(self):
        req = MagicMock()
        req.headers.get = MagicMock(return_value="Bearer secret-token")
        assert _get_provided_token("sid1", req, MagicMock()) == "secret-token"

    def test_get_provided_token_cookie_fallback(self):
        req = MagicMock()
        req.headers.get = MagicMock(return_value="")
        get_cookie = MagicMock(return_value="from-cookie")
        assert _get_provided_token("sid1", req, get_cookie) == "from-cookie"
        get_cookie.assert_called_with("share_token_sid1")

    def test_check_share_access_no_secret_no_users(self):
        # Public
        share = {"secret_token": None}
        allowed, redirect, err = _check_share_access(share, "s", MagicMock(), MagicMock(), MagicMock(return_value=None))
        assert allowed is True

    def test_check_share_access_token_matches(self):
        share = {"secret_token": "abc"}
        req = MagicMock()
        req.headers.get = MagicMock(return_value="Bearer abc")
        allowed, redirect, err = _check_share_access(share, "s", req, MagicMock(), MagicMock(return_value=None))
        assert allowed is True

    def test_check_share_access_missing_cookie_restricted(self):
        share = {"secret_token": "abc"}
        req = MagicMock()
        req.headers.get = MagicMock(return_value="")
        get_cookie = MagicMock(return_value=None)
        get_sc = MagicMock(return_value=None)
        allowed, redirect, err = _check_share_access(share, "s", req, get_cookie, get_sc)
        assert allowed is False
        assert redirect is True
        assert err[0] == 403

    def test_check_share_access_user_explicitly_allowed(self):
        share = {"secret_token": "abc", "allowed_users": ["user1"]}
        get_sc = MagicMock(return_value=b'{"username": "user1"}')
        allowed, redirect, err = _check_share_access(share, "s", MagicMock(), MagicMock(), get_sc)
        assert allowed is True

    def test_check_share_access_modify_user_allowed(self):
        share = {"secret_token": "abc", "allowed_users": ["bob"], "modify_users": ["alice"]}
        get_sc = MagicMock(return_value=b'{"username": "alice"}')
        allowed, redirect, err = _check_share_access(share, "s", MagicMock(), MagicMock(), get_sc)
        assert allowed is True
        assert redirect is False

    def test_build_token_update_fields(self):
        assert _build_token_update_fields(
            True, {}
        ) == {"secret_token": None, "disable_token": True}
        out = _build_token_update_fields(False, {"secret_token": "keep"})
        assert out["disable_token"] is False
        assert out["secret_token"] == "keep"
        assert _build_token_update_fields(None, {}) == {}
        rot = _build_token_update_fields(
            None, {"secret_token": "old"}, rotate_existing=True
        )
        assert rot["disable_token"] is False
        assert rot["secret_token"] != "old"

    def test_parse_path_entries_for_update(self):
        paths, remote = _parse_path_entries_for_update(
            [
                {"type": "cloud"},
                {"type": "local", "path": "a"},
                "b",
            ]
        )
        assert remote == [{"type": "cloud"}]
        assert paths == ["a", "b"]

    def test_parse_paths_for_update_dynamic_rejects_cloud(self):
        deduped, new_c, removed, err = _parse_paths_for_update(
            [{"type": "cloud"}], "sid", "dynamic", []
        )
        assert err == (
            400,
            {"error": "Cloud files are not supported in dynamic shares"},
        )
        assert deduped is None

    def test_parse_paths_for_update_cloud_error(self):
        with patch(
            "aird.handlers.share_handlers.download_cloud_items",
            side_effect=CloudProviderError("fail"),
        ), patch(
            "aird.handlers.share_handlers.remove_share_cloud_dir"
        ):
            deduped, new_c, removed, err = _parse_paths_for_update(
                [{"type": "cloud", "x": 1}], "sid", "static", []
            )
        assert err == (400, {"error": "fail"})

    def test_resolve_final_paths_dynamic_cloud_rejected(self):
        with patch("aird.handlers.share_handlers.remove_share_cloud_dir"):
            final, err = _resolve_final_paths_dynamic([], [{"type": "cloud"}], "s")
        assert err == (
            400,
            {"error": "Cloud files are not supported in dynamic shares"},
        )

    def test_resolve_final_paths_dynamic_no_folders(self):
        with patch("aird.handlers.share_handlers.remove_share_cloud_dir"):
            final, err = _resolve_final_paths_dynamic([], [], "s")
        assert err == (
            400,
            {"error": "No valid directories for dynamic share"},
        )

    def test_resolve_final_paths_static_cloud_provider_error(self):
        with patch(
            "aird.handlers.share_handlers.download_cloud_items",
            side_effect=CloudProviderError("x"),
        ), patch("aird.handlers.share_handlers.remove_share_cloud_dir"):
            final, err = _resolve_final_paths_static([], [{"type": "cloud"}], "s")
        assert err == (400, {"error": "x"})

    def test_collect_paths_from_request_skips_outside_root(self):
        with patch(
            "os.path.abspath", side_effect=lambda p: p
        ), patch(
            "aird.handlers.share_handlers.is_within_root", return_value=False
        ):
            valid, dyn, remote = _collect_paths_from_request(
                ["inside"], "static", root_dir="/root"
            )
        assert valid == []
        assert dyn == []
        assert remote == []

    def test_collect_paths_static_empty_directory(self):
        with patch("os.path.abspath", side_effect=lambda p: p), patch(
            "aird.handlers.share_handlers.is_within_root", return_value=True
        ), patch("os.path.isfile", return_value=False), patch(
            "os.path.isdir", return_value=True
        ), patch(
            "aird.handlers.share_handlers.get_all_files_recursive", return_value=[]
        ):
            valid, dyn, remote = _collect_paths_from_request(
                ["emptydir"], "static", root_dir="/root"
            )
        assert valid == ["emptydir"]
        assert dyn == []
        assert remote == []

    def test_resolve_final_paths_static_empty_directory(self):
        with patch("aird.handlers.share_handlers.remove_share_cloud_dir"):
            final, err = _resolve_final_paths_static(["emptydir"], [], "sid")
        assert err is None
        assert final == ["emptydir"]

    def test_get_share_file_list_static_omits_directory_paths(self, tmp_path):
        empty = tmp_path / "emptydir"
        empty.mkdir()
        share = {"share_type": "static", "paths": ["emptydir"], "allow_list": [], "avoid_list": []}
        with patch(
            "aird.handlers.share_handlers._root_dir_for_share",
            return_value=str(tmp_path),
        ):
            files = _get_share_file_list(share)
        assert files == []

    def test_get_share_file_list_static_expands_directory(self, tmp_path):
        folder = tmp_path / "docs"
        folder.mkdir()
        (folder / "a.txt").write_text("a", encoding="utf-8")
        (folder / "b.txt").write_text("b", encoding="utf-8")
        share = {"share_type": "static", "paths": ["docs"], "allow_list": [], "avoid_list": []}
        with patch(
            "aird.handlers.share_handlers._root_dir_for_share",
            return_value=str(tmp_path),
        ):
            files = _get_share_file_list(share)
        assert sorted(files) == ["docs/a.txt", "docs/b.txt"]

    def test_get_share_file_list_dynamic_includes_files(self, tmp_path):
        (tmp_path / "one.txt").write_text("1", encoding="utf-8")
        (tmp_path / "two.txt").write_text("2", encoding="utf-8")
        share = {
            "share_type": "dynamic",
            "paths": ["one.txt", "two.txt"],
            "allow_list": [],
            "avoid_list": [],
        }
        with patch(
            "aird.handlers.share_handlers._root_dir_for_share",
            return_value=str(tmp_path),
        ):
            files = _get_share_file_list(share)
        assert sorted(files) == ["one.txt", "two.txt"]
