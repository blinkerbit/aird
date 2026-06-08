"""Share ownership vs editor (modify_users) permissions."""

from unittest.mock import MagicMock, patch

from aird.handlers.api_handlers import _classify_share_for_user
from aird.handlers.share_handlers import (
    ShareRevokeHandler,
    _share_update_payload_for_user,
)
from tests.handler_helpers import _default_services, patch_db_conn, prepare_handler
from tests.test_share_handlers import _flags_file_share_only


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
