"""Share ownership vs editor (modify_users) permissions."""

from unittest.mock import MagicMock, patch

from aird.handlers.api_handlers import (
    ShareDetailsByIdAPIHandler,
    ShareListAPIHandler,
    _classify_share_for_user,
    _redact_share_secret_token,
)
from aird.handlers.constants import ACCESS_DENIED_MSG
from aird.handlers.share_handlers import (
    ShareRevokeHandler,
    _share_update_payload_for_user,
)
from tests.handler_helpers import (
    _default_services,
    authenticate,
    patch_db_conn,
    prepare_handler,
)
from tests.test_api_handlers import make_request_handler
from tests.test_share_handlers import _flags_file_share_only


class TestShareTokenRedaction:
    def test_redact_sets_has_token_and_clears_secret(self):
        redacted = _redact_share_secret_token({"id": "s1", "secret_token": "tok"})
        assert redacted["secret_token"] is None
        assert redacted["has_token"] is True

    def test_redact_has_token_false_when_no_token(self):
        redacted = _redact_share_secret_token({"id": "s1", "secret_token": None})
        assert redacted["has_token"] is False


class TestShareDetailsByIdAuthorization:
    def _handler(self, username, role="user"):
        handler = make_request_handler(ShareDetailsByIdAPIHandler)
        authenticate(handler, username=username, role=role)
        handler.get_argument = MagicMock(return_value="share1")
        return handler

    def _run_get(self, handler, share_data):
        handler.set_status = MagicMock()
        share_svc = handler.application.settings["services"]["share_service"]
        with patch(
            "aird.handlers.base_handler.is_feature_enabled", return_value=True
        ), patch_db_conn(MagicMock(), modules=["aird.handlers.api_handlers"]), patch.object(
            share_svc, "get_share", return_value=share_data
        ), patch.object(share_svc, "get_download_count", return_value=0):
            handler.get()
        return handler

    def test_unrelated_user_denied(self):
        share = {
            "id": "share1",
            "paths": [],
            "secret_token": "secret",
            "created_by": "alice",
            "allowed_users": ["bob"],
            "modify_users": [],
        }
        handler = self._handler("eve")
        self._run_get(handler, share)
        handler.set_status.assert_called_with(403)
        handler.write.assert_called_with({"error": ACCESS_DENIED_MSG})

    def test_viewer_denied(self):
        share = {
            "id": "share1",
            "paths": [],
            "secret_token": "secret",
            "created_by": "alice",
            "allowed_users": ["bob"],
            "modify_users": [],
        }
        handler = self._handler("bob")
        self._run_get(handler, share)
        handler.set_status.assert_called_with(403)

    def test_editor_allowed_without_secret_token(self):
        share = {
            "id": "share1",
            "paths": ["/a.txt"],
            "secret_token": "secret",
            "created_by": "alice",
            "allowed_users": [],
            "modify_users": ["bob"],
        }
        handler = self._handler("bob")
        self._run_get(handler, share)
        payload = handler.write.call_args[0][0]
        assert payload["share"]["id"] == "share1"
        assert payload["share"]["secret_token"] is None
        assert payload["share"]["has_token"] is True

    def test_owner_receives_secret_token(self):
        share = {
            "id": "share1",
            "paths": [],
            "secret_token": "secret",
            "created_by": "alice",
            "allowed_users": [],
            "modify_users": [],
        }
        handler = self._handler("alice")
        self._run_get(handler, share)
        payload = handler.write.call_args[0][0]
        assert payload["share"]["secret_token"] == "secret"
        assert payload["share"]["has_token"] is True


class TestShareListTokenRedaction:
    def test_shared_with_me_redacts_secret_token(self):
        handler = make_request_handler(ShareListAPIHandler)
        authenticate(handler, username="bob", role="user")
        share_svc = handler.application.settings["services"]["share_service"]
        shares = {
            "s1": {
                "id": "s1",
                "paths": [],
                "created_by": "alice",
                "allowed_users": ["bob"],
                "secret_token": "leaked",
            }
        }
        with patch(
            "aird.handlers.base_handler.is_feature_enabled", return_value=True
        ), patch_db_conn(MagicMock(), modules=["aird.handlers.api_handlers"]), patch.object(
            share_svc, "list_shares", return_value=shares
        ):
            handler.get()
        payload = handler.write.call_args[0][0]
        assert payload["shares"] == {}
        assert len(payload["shared_with_me"]) == 1
        entry = payload["shared_with_me"][0]
        assert entry["secret_token"] is None
        assert entry["has_token"] is True

    def test_my_shares_keeps_secret_token_for_owner(self):
        handler = make_request_handler(ShareListAPIHandler)
        authenticate(handler, username="alice", role="user")
        share_svc = handler.application.settings["services"]["share_service"]
        shares = {
            "s1": {
                "id": "s1",
                "paths": [],
                "created_by": "alice",
                "secret_token": "mine",
            }
        }
        with patch(
            "aird.handlers.base_handler.is_feature_enabled", return_value=True
        ), patch_db_conn(MagicMock(), modules=["aird.handlers.api_handlers"]), patch.object(
            share_svc, "list_shares", return_value=shares
        ):
            handler.get()
        payload = handler.write.call_args[0][0]
        assert payload["shared_with_me"] == []
        assert payload["shares"]["s1"]["secret_token"] == "mine"


class TestShareClassification:
    def test_editor_goes_to_shared_with_me(self):
        share = {
            "created_by": "alice (Admin)",
            "allowed_users": ["bob"],
            "modify_users": ["bob"],
        }
        is_mine, is_shared = _classify_share_for_user(share, "bob", False)
        assert is_mine is False
        assert is_shared is True

    def test_owner_goes_to_my_shares(self):
        share = {
            "created_by": "alice (Admin)",
            "modify_users": ["bob"],
        }
        is_mine, is_shared = _classify_share_for_user(share, "alice", False)
        assert is_mine is True
        assert is_shared is False

    def test_orphan_share_not_visible_to_regular_users(self):
        share = {"created_by": "", "allowed_users": None}
        is_mine, is_shared = _classify_share_for_user(share, "bob", False)
        assert is_mine is False
        assert is_shared is False

    def test_orphan_share_visible_to_admin(self):
        share = {"created_by": "", "allowed_users": None}
        is_mine, is_shared = _classify_share_for_user(share, "bob", True)
        assert is_mine is True
        assert is_shared is False


class TestShareUpdatePayloadForEditor:
    def test_editor_may_only_change_paths(self):
        handler = MagicMock()
        handler.can_manage_share_secrets.return_value = False
        handler.can_edit_share_paths.return_value = True
        share = {"created_by": "alice", "modify_users": ["bob"]}
        data = {
            "share_id": "s1",
            "paths": ["a.txt"],
            "allowed_users": ["eve"],
            "disable_token": True,
        }
        filtered = _share_update_payload_for_user(handler, share, data)
        assert filtered == {"share_id": "s1", "paths": ["a.txt"]}

    def test_viewer_cannot_update(self):
        handler = MagicMock()
        handler.can_manage_share_secrets.return_value = False
        handler.can_edit_share_paths.return_value = False
        assert _share_update_payload_for_user(handler, {}, {"share_id": "s1"}) is None


class TestShareRevokeEditorForbidden:
    def test_revoke_denied_for_editor(self):
        from tests.handler_helpers import authenticate

        mock_app = MagicMock()
        mock_app.settings = {
            "cookie_secret": "test_secret",
            "db_conn": MagicMock(),
            "services": _default_services(),
        }
        handler = prepare_handler(ShareRevokeHandler(mock_app, MagicMock()))
        authenticate(handler, username="bob", role="user")
        handler.get_argument = MagicMock(return_value="share1")
        share_svc = mock_app.settings["services"]["share_service"]

        with patch(
            "aird.handlers.base_handler.is_feature_enabled",
            side_effect=_flags_file_share_only,
        ), patch_db_conn(MagicMock(), modules=["aird.handlers.share_handlers"]), patch.object(
            share_svc,
            "get_share",
            return_value={
                "id": "share1",
                "created_by": "alice",
                "modify_users": ["bob"],
            },
        ), patch.object(share_svc, "delete_share") as mock_delete, patch.object(
            handler, "write"
        ) as mock_write:
            handler.post()
            handler.set_status.assert_called_with(403)
            mock_write.assert_called_with(
                {"error": "Only the share owner can revoke this share"}
            )
            mock_delete.assert_not_called()
