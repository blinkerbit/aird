"""Targeted tests for modules with low coverage (quick wins)."""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import MagicMock, mock_open, patch

import pytest

from aird.app_context import AppContext
from aird.core.events import (
    EventBus,
    PolicyDecisionEvent,
    ShareCreatedEvent,
    TransferStartedEvent,
    UserAuthenticatedEvent,
    now_ts,
)
from aird.core.input_validation import (
    bound_access_token,
    bound_login_password,
    bound_username_for_login,
    require_max_chars,
    validate_abac_tag_rule,
    validate_policy_payload,
    validate_share_create_struct,
    validate_share_update_struct,
    validate_super_search_glob,
    validate_user_attribute,
    validate_ws_search,
)
from aird.core.share_root import (
    creator_folder_username_from_share_field,
    filesystem_root_for_share,
    login_matches_share_creator_field,
)
from aird.db import init_db
from aird.db.audit import get_audit_logs, log_audit
from aird.db.favorites import get_user_favorites, toggle_favorite
from aird.db.policies import (
    delete_policy,
    get_policy,
    get_policy_by_name,
    insert_policy,
    list_policies,
    update_policy,
)
from aird.db.policy_decisions import get_policy_decisions, log_policy_decision
from aird.db.quota import get_user_quota, set_user_quota, update_user_used_bytes
from aird.db.ranged_uploads import (
    create_session,
    delete_session,
    get_session,
    update_ranges,
)
from aird.db.resource_tags import (
    delete_resource_tag,
    delete_resource_tag_by_name,
    insert_resource_tag,
    list_resource_tags,
    update_resource_tag,
)
from aird.db.user_attributes import (
    delete_user_attribute,
    get_user_attributes,
    list_all_user_attributes,
    set_user_attribute,
)
from aird.event_loop import install_uvloop_if_linux
from aird.handlers.health_handler import HealthHandler
from aird.server_runtime import (
    describe_worker_layout,
    detect_physical_cpu_count,
    detect_threads_per_core,
    resolve_worker_count,
)
from aird.services.audit_service import AuditService
from aird.services.config_service import ConfigService
from aird.services.email_subscriber import EmailNotificationSubscriber
from aird.services.event_subscribers import (
    EventLoggingSubscriber,
    EventMetricsSubscriber,
    PolicyDecisionMetricsSubscriber,
)
from aird.services.favorites_service import FavoritesService
from aird.services.network_share_service import NetworkShareService
from aird.services.quota_service import QuotaService
from aird.sql_identifiers import (
    format_select_columns,
    format_shares_select_by_id_sql,
    format_shares_select_sql,
    format_update_by_id_sql,
)
from aird.constants.input_limits import InputTooLongError
from tests.handler_helpers import _default_services, authenticate, patch_db_conn, prepare_handler


@pytest.fixture
def db_conn():
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    yield conn
    conn.close()


class TestInputValidation:
  def test_require_max_chars(self):
      assert require_max_chars(None, max_len=10) == ""
      assert require_max_chars("  hi  ", max_len=10) == "  hi  "
      with pytest.raises(InputTooLongError):
          require_max_chars("x" * 11, max_len=10, field="name")

  def test_bound_login_fields(self):
      handler = MagicMock()
      handler.get_argument.side_effect = lambda k, default="": {
          "username": " alice ",
          "password": "secret",
          "token": "tok",
      }.get(k, default)
      assert bound_username_for_login(handler) == "alice"
      assert bound_login_password(handler) == "secret"
      assert bound_access_token(handler) == "tok"

  def test_bound_fields_too_long(self):
      handler = MagicMock()
      handler.get_argument.return_value = "x" * 50_000
      with pytest.raises(InputTooLongError):
          bound_username_for_login(handler)
      with pytest.raises(InputTooLongError):
          bound_login_password(handler)
      with pytest.raises(InputTooLongError):
          bound_access_token(handler)

  def test_validate_ws_search(self):
      assert validate_ws_search("*.txt", "needle") == ("*.txt", "needle")
      with pytest.raises(InputTooLongError):
          validate_ws_search("x" * 5000, "")

  def test_validate_super_search_glob(self):
      assert validate_super_search_glob("*.pdf") is None
      assert validate_super_search_glob("") == "pattern is empty"
      assert validate_super_search_glob("//abs") == "pattern must not be an absolute or UNC-style path"
      assert validate_super_search_glob("C:foo") == "patterns with a drive letter are not allowed"
      assert validate_super_search_glob("a/../b") == "pattern must not contain '..' path segments"
      assert validate_super_search_glob("a\x00b") == "pattern contains invalid characters"

  def test_validate_abac_and_policy(self):
      validate_abac_tag_rule("tag", "*.txt")
      with pytest.raises(InputTooLongError):
          validate_abac_tag_rule("x" * 200, "p")
      with pytest.raises(InputTooLongError):
          validate_policy_payload("n" * 300, "", [], {})

  def test_validate_share_create_struct(self):
      assert validate_share_create_struct({"share_type": "tag", "tag_name": "docs"}) is None
      assert validate_share_create_struct({"share_type": "tag", "tag_name": "x" * 200}) == "tag_name too long"
      assert validate_share_create_struct({"paths": "bad"}) == "paths must be a list"
      assert validate_share_create_struct({"paths": ["ok"], "allowed_users": "bad"}) == "allowed_users must be a list"

  def test_validate_share_update_struct(self):
      assert validate_share_update_struct({"share_id": "x" * 300}) == "share_id too long"
      assert validate_share_update_struct({"disable_token": "yes"}) == "disable_token must be a boolean"
      assert validate_share_update_struct({"rotate_token": 1}) == "rotate_token must be a boolean"
      assert validate_share_update_struct({"remove_files": "nope"}) == "remove_files must be a list"
      assert validate_share_update_struct({}) is None

  def test_validate_user_attribute(self):
      validate_user_attribute("user", "dept", "eng")
      with pytest.raises(InputTooLongError):
          validate_user_attribute("u" * 500, "k", "v")


class TestShareRoot:
  def test_login_matches_share_creator_field(self):
      assert login_matches_share_creator_field("alice", "alice") is True
      assert login_matches_share_creator_field("alice (Admin)", "alice") is True
      assert login_matches_share_creator_field(None, "alice") is False
      assert login_matches_share_creator_field("", "alice") is False
      assert login_matches_share_creator_field("bob", "alice") is False

  def test_creator_folder_username_from_share_field(self):
      assert creator_folder_username_from_share_field("alice (User)") == "alice"
      assert creator_folder_username_from_share_field("Admin (Token)") == ""
      assert creator_folder_username_from_share_field("plain") == "plain"

  def test_filesystem_root_for_share(self, temp_dir):
      import aird.constants as constants

      with patch.object(constants, "MULTI_USER", False), patch.object(
          constants, "ROOT_DIR", temp_dir
      ):
          assert filesystem_root_for_share({"created_by": "alice"}) == temp_dir

      with patch.object(constants, "MULTI_USER", True), patch.object(
          constants, "ROOT_DIR", temp_dir
      ):
          root = filesystem_root_for_share({"created_by": "alice (User)"})
          assert root.endswith("alice")
          assert filesystem_root_for_share({"created_by": "token_user"}) == temp_dir


class TestDbFavoritesQuota:
  def test_favorites_toggle_and_list(self, db_conn):
      assert toggle_favorite(None, "u", "/a") is False
      assert get_user_favorites(None, "u") == []
      assert toggle_favorite(db_conn, "alice", "/docs/a.pdf") is True
      assert toggle_favorite(db_conn, "alice", "/docs/a.pdf") is False
      assert toggle_favorite(db_conn, "alice", "/docs/b.pdf") is True
      assert get_user_favorites(db_conn, "alice") == ["/docs/b.pdf"]

  def test_quota_operations(self, db_conn):
      from aird.db.users import create_user

      create_user(db_conn, "quota_user", "hash", role="user")
      assert get_user_quota(None, "x") == {"quota_bytes": None, "used_bytes": 0}
      set_user_quota(db_conn, "quota_user", 1000)
      update_user_used_bytes(db_conn, "quota_user", 250)
      info = get_user_quota(db_conn, "quota_user")
      assert info["quota_bytes"] == 1000
      assert info["used_bytes"] == 250
      update_user_used_bytes(None, "quota_user", 10)


class TestDbRangedUploads:
  def test_session_lifecycle(self, db_conn):
      create_session(
          db_conn,
          session_id="sess-1",
          username="alice",
          upload_dir="/tmp",
          filename="big.bin",
          temp_path="/tmp/big.bin.part",
          total_size=100,
      )
      session = get_session(db_conn, "sess-1")
      assert session is not None
      assert session["total_size"] == 100
      assert session["ranges"] == []
      from aird.core.http_range import ByteRange

      update_ranges(db_conn, "sess-1", [ByteRange(0, 49)])
      session = get_session(db_conn, "sess-1")
      assert len(session["ranges"]) == 1
      delete_session(db_conn, "sess-1")
      assert get_session(db_conn, "sess-1") is None


class TestDbUserAttributes:
  def test_user_attributes_crud(self, db_conn):
      assert set_user_attribute(None, "u", "k", "v") is False
      assert set_user_attribute(db_conn, "", "k", "v") is False
      assert set_user_attribute(db_conn, "alice", "dept", "eng") is True
      assert get_user_attributes(db_conn, "alice") == {"dept": "eng"}
      assert set_user_attribute(db_conn, "alice", "dept", "sales") is True
      assert delete_user_attribute(db_conn, "alice", "dept") is True
      assert delete_user_attribute(db_conn, "alice", "missing") is False
      rows = list_all_user_attributes(db_conn)
      assert isinstance(rows, list)


class TestDbPolicyDecisions:
  def test_log_and_query(self, db_conn):
      assert log_policy_decision(None, username="u", action="read", decision="permit") is None
      row_id = log_policy_decision(
          db_conn,
          username="alice",
          action="read",
          decision="permit",
          resource="/a.txt",
          reason="ok",
          policy_id=1,
          attributes={"role": "user"},
          ip="127.0.0.1",
      )
      assert row_id is not None
      all_rows = get_policy_decisions(db_conn)
      assert len(all_rows) == 1
      assert all_rows[0]["attributes"] == {"role": "user"}
      deny_rows = get_policy_decisions(db_conn, decision="deny")
      assert deny_rows == []
      user_rows = get_policy_decisions(db_conn, username="alice", decision="permit")
      assert len(user_rows) == 1


class TestDbPolicies:
  def test_policy_crud(self, db_conn):
      assert insert_policy(None, name="p", effect="permit", target_actions=[], condition={}) is None
      pid = insert_policy(
          db_conn,
          name="allow-read",
          effect="permit",
          target_actions=["read"],
          condition={"role": "user"},
          description="desc",
          priority=5,
      )
      assert pid is not None
      policy = get_policy(db_conn, pid)
      assert policy["name"] == "allow-read"
      assert get_policy_by_name(db_conn, "allow-read")["id"] == pid
      assert update_policy(db_conn, pid, description="updated", enabled=False) is True
      assert update_policy(db_conn, pid, effect="invalid") is False
      assert delete_policy(db_conn, pid) is True
      assert delete_policy(None, 1) is False
      assert get_policy(None, 1) is None
      assert list_policies(None) == []


class TestDbResourceTags:
  def test_resource_tags_crud(self, db_conn):
      assert insert_resource_tag(None, "conf", "*.secret") is None
      tag_id = insert_resource_tag(db_conn, "conf", "*.secret", priority=2, created_by="admin")
      assert tag_id is not None
      tags = list_resource_tags(db_conn)
      assert len(tags) == 1
      assert update_resource_tag(db_conn, tag_id, tag="classified") is True
      assert delete_resource_tag(db_conn, tag_id) is True
      tag_id2 = insert_resource_tag(db_conn, "tmp", "*.tmp")
      assert delete_resource_tag_by_name(db_conn, "tmp") == 1


class TestDbResourceTagsErrors:
  def test_resource_tag_guard_paths(self, db_conn):
      assert insert_resource_tag(db_conn, "", "*.x") is None
      assert delete_resource_tag(None, 1) is False
      assert delete_resource_tag_by_name(None, "t") == 0
      assert update_resource_tag(db_conn, 99999, tag="nope") is False
      assert list_resource_tags(None) == []


class TestDbAudit:
  def test_audit_log(self, db_conn):
      log_audit(None, "noop")
      log_audit(db_conn, "login", username="alice", details="ok", ip="1.2.3.4")
      logs = get_audit_logs(db_conn, limit=10)
      assert logs[0]["action"] == "login"
      assert get_audit_logs(None) == []

  def test_audit_log_write_failure(self, db_conn):
      broken = MagicMock()
      broken.execute.side_effect = RuntimeError("db")
      log_audit(broken, "x")


class TestServices:
  def test_favorites_and_quota_services(self, db_conn):
      fav = FavoritesService()
      assert fav.toggle(db_conn, "alice", "/x") is True
      assert fav.get_favorites(db_conn, "alice") == ["/x"]
      quota = QuotaService()
      from aird.db.users import create_user

      create_user(db_conn, "q2", "hash")
      quota.update_used_bytes(db_conn, "q2", 42)
      assert quota.get_quota(db_conn, "q2")["used_bytes"] == 42

  def test_audit_service(self, db_conn):
      svc = AuditService()
      svc.log(db_conn, "action", username="u")
      assert len(svc.get_logs(db_conn)) == 1

  def test_config_service_merge_from_db(self, db_conn):
      import copy

      import aird.constants as constants
      from aird.db.config import save_allowed_extensions, save_feature_flags, save_upload_config

      orig_upload = copy.deepcopy(constants.UPLOAD_CONFIG)
      orig_max = constants.MAX_FILE_SIZE
      orig_ext = set(constants.UPLOAD_ALLOWED_EXTENSIONS)
      try:
          save_feature_flags(db_conn, {"favorites": 1})
          save_upload_config(db_conn, {"max_file_size_mb": 10})
          save_allowed_extensions(db_conn, {".txt"})
          svc = ConfigService()
          svc.merge_from_db(db_conn)
          assert constants.FEATURE_FLAGS["favorites"] is True
          assert constants.MAX_FILE_SIZE == 10 * 1024 * 1024
          assert ".txt" in constants.UPLOAD_ALLOWED_EXTENSIONS
      finally:
          constants.UPLOAD_CONFIG.clear()
          constants.UPLOAD_CONFIG.update(orig_upload)
          constants.MAX_FILE_SIZE = orig_max
          constants.UPLOAD_ALLOWED_EXTENSIONS = orig_ext
          constants.refresh_upload_derived_constants()

  def test_network_share_service(self, db_conn):
      svc = NetworkShareService()
      manager = svc.build_manager()
      assert manager is not None
      svc.auto_start_enabled(db_conn, manager)
      assert svc.list_all(db_conn) == []


class TestEventSubscribers:
  def test_metrics_and_logging(self):
      metrics = EventMetricsSubscriber()
      metrics.on_user_authenticated(
          UserAuthenticatedEvent("u", "user", "127.0.0.1", now_ts())
      )
      metrics.on_share_created(
          ShareCreatedEvent("s1", "alice", 2, now_ts())
      )
      metrics.on_transfer_started(
          TransferStartedEvent("r1", "alice", False, now_ts())
      )
      snap = metrics.snapshot()
      assert snap["user_authenticated"] == 1

      logging_sub = EventLoggingSubscriber()
      logging_sub.on_user_authenticated(
          UserAuthenticatedEvent("u", "user", "127.0.0.1", now_ts())
      )
      logging_sub.on_share_created(
          ShareCreatedEvent("s1", "alice", 2, now_ts())
      )
      logging_sub.on_transfer_started(
          TransferStartedEvent("r1", "alice", False, now_ts())
      )
      logging_sub.on_policy_decision(
          PolicyDecisionEvent("u", "read", "/a", "permit", "ok", 1, "p", None, now_ts())
      )

      pdm = PolicyDecisionMetricsSubscriber()
      pdm.on_policy_decision(
          PolicyDecisionEvent("u", "read", "/a", "permit", "ok", 1, "p", None, now_ts())
      )
      assert pdm.snapshot()["policy_permit"] == 1

  def test_event_bus_handles_subscriber_errors(self):
      bus = EventBus()

      def bad_handler(_event):
          raise RuntimeError("boom")

      bus.subscribe(UserAuthenticatedEvent, bad_handler)
      bus.publish(UserAuthenticatedEvent("u", "user", "ip", now_ts()))


class TestEmailSubscriber:
  def test_share_created_disabled(self):
      email = MagicMock()
      email.enabled = False
      sub = EmailNotificationSubscriber(email_service=email)
      sub.on_share_created(
          ShareCreatedEvent("s1", "alice", 1, now_ts(), allowed_users=("bob",))
      )
      email.notify_share_created.assert_not_called()

  def test_deliver_share_created(self):
      email = MagicMock()
      email.enabled = True
      email.notify_share_created.return_value = 2
      sub = EmailNotificationSubscriber(
          email_service=email, db_conn_getter=lambda: object()
      )
      sub._deliver_share_created(
          ShareCreatedEvent("s1", "alice", 1, now_ts(), modify_users=("bob",))
      )
      email.notify_share_created.assert_called_once()

  def test_deliver_share_created_logs_exception(self):
      email = MagicMock()
      email.enabled = True
      email.notify_share_created.side_effect = RuntimeError("smtp down")
      sub = EmailNotificationSubscriber(
          email_service=email, db_conn_getter=lambda: object()
      )
      sub._deliver_share_created(
          ShareCreatedEvent("s1", "alice", 1, now_ts())
      )


class TestNetworkShareServiceExtended:
  def test_auto_start_enabled_shares(self, db_conn):
      from aird.db.network_shares import create_network_share
      from aird.services.network_share_service import NetworkShareService

      create_network_share(
          db_conn,
          "ns1",
          "smb1",
          "/tmp",
          "smb",
          445,
          "user",
          "pass",
      )
      svc = NetworkShareService()
      manager = MagicMock()
      svc.auto_start_enabled(db_conn, manager)
      manager.start_share.assert_called_once()

  def test_health_ok(self):
      handler = HealthHandler(MagicMock(), MagicMock())
      prepare_handler(handler)
      with patch("aird.handlers.health_handler.constants_module.DB_CONN", None):
          handler.get()
      handler.set_status.assert_not_called()
      payload = handler.write.call_args[0][0]
      assert payload["status"] == "ok"
      assert payload["db"] == "not_configured"

  def test_health_db_ok(self, db_conn):
      handler = HealthHandler(MagicMock(), MagicMock())
      prepare_handler(handler)
      with patch("aird.handlers.health_handler.constants_module.DB_CONN", db_conn):
          handler.get()
      handler.set_status.assert_not_called()
      assert handler.write.call_args[0][0]["db"] == "ok"

  def test_health_db_error(self):
      handler = HealthHandler(MagicMock(), MagicMock())
      prepare_handler(handler)
      broken = MagicMock()
      broken.execute.side_effect = sqlite3.OperationalError("db down")
      with patch("aird.handlers.health_handler.constants_module.DB_CONN", broken):
          handler.get()
      handler.set_status.assert_called_once_with(503)
      payload = handler.write.call_args[0][0]
      assert payload["status"] == "error"
      assert payload["db"] == "error"


class TestEventLoop:
  def test_install_uvloop_paths(self):
      import aird.event_loop as el

      el._uvloop_installed = False
      with patch("aird.event_loop.sys.platform", "darwin"):
          assert install_uvloop_if_linux() is False
      el._uvloop_installed = False
      with patch("aird.event_loop.sys.platform", "linux"), patch.dict(
          "sys.modules", {"uvloop": None}
      ):
          with patch("builtins.__import__", side_effect=ImportError("no uvloop")):
              assert install_uvloop_if_linux() is False
      el._uvloop_installed = False
      with patch("aird.event_loop.sys.platform", "linux"), patch.dict(
          "sys.modules", {"uvloop": MagicMock()}), patch(
          "uvloop.EventLoopPolicy"
      ), patch("asyncio.set_event_loop_policy") as set_policy:
          assert install_uvloop_if_linux() is True
          set_policy.assert_called_once()
      el._uvloop_installed = True
      assert install_uvloop_if_linux() is True

      el._uvloop_installed = False
      with patch("aird.event_loop.sys.platform", "linux"), patch(
          "asyncio.set_event_loop_policy", side_effect=RuntimeError("fail")
      ):
          assert install_uvloop_if_linux() is False


class TestServerRuntimeExtended:
  def test_detect_threads_per_core_env(self):
      with patch.dict("os.environ", {"AIRD_THREADS_PER_CORE": "4"}):
          assert detect_threads_per_core() == 4.0
      with patch.dict("os.environ", {"AIRD_THREADS_PER_CORE": "bad"}):
          assert detect_threads_per_core() == 2.0

  def test_detect_physical_cpu_count_linux_proc(self):
      cpuinfo = "core id\t: 0\ncore id\t: 1\n"
      with patch("aird.server_runtime.sys.platform", "linux"), patch(
          "builtins.open", mock_open(read_data=cpuinfo)
      ):
          assert detect_physical_cpu_count() == 2

  def test_resolve_worker_count_env(self):
      with patch("aird.server_runtime.sys.platform", "linux"), patch.dict(
          "os.environ", {"AIRD_WORKERS": "5"}
      ):
          assert resolve_worker_count() == 5
      with patch("aird.server_runtime.sys.platform", "linux"), patch.dict(
          "os.environ", {"AIRD_WORKERS": "0"}
      ), patch("aird.server_runtime.compute_default_worker_count", return_value=3):
          assert resolve_worker_count() == 3

  def test_describe_worker_layout(self):
      text = describe_worker_layout(4)
      assert "workers=4" in text


class TestSqlIdentifiers:
  def test_format_helpers(self):
      allowed = frozenset({"id", "name"})
      assert format_select_columns(["id", "name"], allowed) == "id, name"
      with pytest.raises(ValueError):
          format_select_columns(["id", "evil"], allowed)
      sql = format_update_by_id_sql("users", "name = ?")
      assert "UPDATE users SET name = ? WHERE id = ?" == sql
      with pytest.raises(ValueError):
          format_update_by_id_sql("evil", "x = ?")
      assert format_shares_select_sql("id") == "SELECT id FROM shares"
      assert format_shares_select_by_id_sql("id") == "SELECT id FROM shares WHERE id = ?"


class TestAppContext:
  def test_service_accessors(self):
      ctx = AppContext(services={"audit_service": AuditService()})
      assert ctx.get_service("missing", 42) == 42
      assert ctx.audit_service is not None
      assert ctx.config_service is None
      assert ctx.favorites_service is None
      assert ctx.network_share_service is None
      assert ctx.p2p_signaling_service is None
      assert ctx.quota_service is None
      assert ctx.share_service is None
      assert ctx.user_service is None
      assert ctx.tag_service is None
      assert ctx.policy_service is None


class TestDbWebAuthn:
  def test_webauthn_lifecycle(self, db_conn):
      from aird.db.webauthn import (
          CHALLENGE_TTL_SECONDS,
          consume_challenge,
          create_credential,
          credential_id_to_b64,
          delete_credential,
          ensure_prf_salt,
          get_credential_by_credential_id,
          get_credential_by_id,
          get_prf_salt,
          list_credentials,
          store_challenge,
          update_sign_count,
      )

      from datetime import datetime, timezone

      def _now_iso() -> str:
          return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

      challenge = b"challenge-bytes-12345"
      assert store_challenge(None, challenge, "register") is False
      with patch("aird.db.webauthn._utcnow_iso", side_effect=_now_iso):
          assert store_challenge(db_conn, challenge, "register", username="alice") is True
          assert consume_challenge(db_conn, challenge, "register") == "alice"
      assert consume_challenge(db_conn, challenge, "register") is None

      cred_id = credential_id_to_b64(b"cred-id-bytes")
      assert create_credential(
          db_conn,
          username="alice",
          credential_id=cred_id,
          public_key=b"pk",
          sign_count=0,
          transports="usb",
          aaguid=None,
          prf_capable=True,
          nickname="key",
      )
      creds = list_credentials(db_conn, "alice")
      assert len(creds) == 1
      row = get_credential_by_id(db_conn, creds[0]["id"], "alice")
      assert row["credential_id"] == cred_id
      assert get_credential_by_credential_id(db_conn, cred_id)["username"] == "alice"
      assert update_sign_count(db_conn, creds[0]["id"], 1) is True
      salt = ensure_prf_salt(db_conn, "alice")
      assert salt
      assert get_prf_salt(db_conn, "alice") == salt
      assert delete_credential(db_conn, creds[0]["id"], "alice") is True

  def test_webauthn_error_paths(self, db_conn):
      from aird.db.webauthn import consume_challenge, create_credential, list_credentials

      assert list_credentials(None, "alice") == []
      assert create_credential(
          db_conn, username="", credential_id="x", public_key=b"k", sign_count=0,
          transports=None, aaguid=None, prf_capable=False
      ) is False
      assert consume_challenge(db_conn, b"", "auth") is None


class TestDbPolicyDecisionsExtended:
  def test_malformed_attributes_json(self, db_conn):
      from datetime import datetime, timezone

      created = datetime.now(timezone.utc).isoformat() + "Z"
      db_conn.execute(
          "INSERT INTO policy_decisions "
          "(created_at, username, action, resource, decision, reason, policy_id, attributes_json, ip) "
          "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
          (created, "u", "read", None, "permit", None, None, "not-json", None),
      )
      db_conn.commit()
      rows = get_policy_decisions(db_conn)
      assert rows[0]["attributes"] is None


class TestDbSharesCoverage:
  def test_share_covers_paths(self, db_conn, temp_dir):
      import os

      from aird.db.resource_tags import insert_resource_tag
      from aird.db.shares import (
          get_share_download_count,
          list_files_for_tag_share,
          list_shares_accessible_to_user,
          share_covers_relative_path,
          share_paths_cover_target,
      )

      os.makedirs(os.path.join(temp_dir, "docs"), exist_ok=True)
      with open(os.path.join(temp_dir, "docs", "a.txt"), "w", encoding="utf-8") as fh:
          fh.write("x")
      with open(os.path.join(temp_dir, "readme.txt"), "w", encoding="utf-8") as fh:
          fh.write("y")

      static = {"share_type": "static", "paths": ["docs/a.txt"], "allow_list": [], "avoid_list": []}
      assert share_covers_relative_path(db_conn, static, "docs/a.txt", temp_dir)
      assert share_paths_cover_target(["docs/a.txt"], "docs") is True

      dynamic = {"share_type": "dynamic", "paths": ["docs"], "allow_list": [], "avoid_list": []}
      assert share_covers_relative_path(db_conn, dynamic, "docs/a.txt", temp_dir)

      insert_resource_tag(db_conn, "docs", "*.txt")
      tag_share = {
          "share_type": "tag",
          "tag_name": "docs",
          "allow_list": [],
          "avoid_list": [],
      }
      assert share_covers_relative_path(db_conn, tag_share, "docs/a.txt", temp_dir)
      files = list_files_for_tag_share(db_conn, "docs", temp_dir, [], [])
      assert "docs/a.txt" in files

      from aird.db.shares import insert_share

      insert_share(
          db_conn,
          "s1",
          "2024-01-01T00:00:00Z",
          ["readme.txt"],
          allowed_users=["bob"],
      )
      accessible = list_shares_accessible_to_user(db_conn, "bob")
      assert len(accessible) == 1

      insert_share(
          db_conn,
          "s2",
          "2024-01-01T00:00:00Z",
          ["other.txt"],
          allowed_users=["bob"],
          created_by="bob",
      )
      assert list_shares_accessible_to_user(db_conn, "bob") == accessible
      from aird.db.audit import log_audit

      log_audit(db_conn, "share_download", details="share_id=s1 user=bob")
      assert get_share_download_count(db_conn, "s1") == 1


class TestFileOperationsGlobs:
  def test_double_star_glob_patterns(self):
      from aird.core.file_operations import filter_files_by_patterns, matches_glob_patterns

      files = ["a.py", "src/a.py", "src/deep/a.py", "readme.md"]
      assert matches_glob_patterns("src/a.py", ["**/*.py"])
      assert filter_files_by_patterns(files, allow_list=["**/*.py"]) == [
          "a.py",
          "src/a.py",
          "src/deep/a.py",
      ]
      assert matches_glob_patterns("docs", ["docs/**"])
      assert matches_glob_patterns("docs/x", ["docs/**"])


class TestFileOperationsScan:
  def test_get_all_files_recursive(self, temp_dir):
      import os

      from aird.core.file_operations import get_all_files_recursive

      os.makedirs(os.path.join(temp_dir, "sub"), exist_ok=True)
      with open(os.path.join(temp_dir, "root.txt"), "w", encoding="utf-8") as fh:
          fh.write("a")
      with open(os.path.join(temp_dir, "sub", "nested.txt"), "w", encoding="utf-8") as fh:
          fh.write("b")
      files = get_all_files_recursive(temp_dir)
      assert "root.txt" in files
      assert os.path.join("sub", "nested.txt") in files

  def test_get_all_files_os_error(self):
      from aird.core.file_operations import get_all_files_recursive

      with patch("os.walk", side_effect=OSError("nope")):
          assert get_all_files_recursive("/bad/path") == []


class TestRangedUploadHandler:
  def test_post_validation_errors(self):
      from aird.handlers.ranged_upload_handlers import RangedUploadSessionHandler

      app = MagicMock()
      app.settings = {"services": _default_services()}
      req = MagicMock()
      req.body = b"not-json"
      req.remote_ip = "127.0.0.1"
      req.connection = MagicMock()
      req.connection.context = MagicMock()
      handler = RangedUploadSessionHandler(app, req)
      authenticate(handler)
      prepare_handler(handler)

      with patch.object(handler, "require_feature", return_value=True), patch(
          "aird.handlers.ranged_upload_handlers.is_feature_enabled", return_value=True
      ):
          import asyncio

          asyncio.run(handler.post())
      handler.set_status.assert_called_with(400)
      import asyncio

      import aird.constants as constants
      from aird.handlers.ranged_upload_handlers import RangedUploadSessionHandler

      app = MagicMock()
      app.settings = {"services": _default_services()}
      req = MagicMock()
      req.body = json.dumps(
          {"filename": "tiny.txt", "total_size": 10, "upload_dir": ""}
      ).encode()
      req.remote_ip = "127.0.0.1"
      req.connection = MagicMock()
      req.connection.context = MagicMock()
      handler = RangedUploadSessionHandler(app, req)
      authenticate(handler)
      prepare_handler(handler)

      with patch_db_conn(db_conn), patch.object(
          handler, "require_feature", return_value=True
      ), patch(
          "aird.handlers.ranged_upload_handlers.is_feature_enabled", return_value=True
      ), patch.object(
          constants, "LARGE_FILE_THRESHOLD_BYTES", 100
      ):
          asyncio.run(handler.post())
      handler.set_status.assert_called_with(400)


class TestRangedUploadSessionSuccess:
  def test_create_session(self, db_conn, temp_dir):
      import asyncio

      import aird.constants as constants
      from aird.handlers.ranged_upload_handlers import RangedUploadSessionHandler

      app = MagicMock()
      app.settings = {"services": _default_services()}
      req = MagicMock()
      req.body = json.dumps(
          {
              "filename": "big.bin",
              "total_size": constants.LARGE_FILE_THRESHOLD_BYTES + 1000,
              "upload_dir": "",
          }
      ).encode()
      req.remote_ip = "127.0.0.1"
      req.connection = MagicMock()
      req.connection.context = MagicMock()
      handler = RangedUploadSessionHandler(app, req)
      authenticate(handler)
      prepare_handler(handler)
      handler.get_display_username = MagicMock(return_value="alice")

      with patch_db_conn(db_conn), patch.object(
          handler, "require_feature", return_value=True
      ), patch(
          "aird.handlers.ranged_upload_handlers.is_feature_enabled", return_value=True
      ), patch(
          "aird.handlers.ranged_upload_handlers.get_user_root", return_value=temp_dir
      ), patch.object(constants, "MAX_FILE_SIZE", 1024 * 1024 * 1024):
          asyncio.run(handler.post())
      handler.set_status.assert_called_with(201)
      payload = handler.write.call_args[0][0]
      assert "upload_id" in payload


class TestCliAuthelia:
  def test_needs_second_factor(self):
      from aird.cli.authelia import AutheliaError, _needs_second_factor, login, second_factor

      assert _needs_second_factor({"status": "OK", "data": {}}) is False
      assert _needs_second_factor({"status": "OK", "data": {"methods": ["totp"]}}) is True
      assert _needs_second_factor({"status": "Unauthorized"}) is True

      session = MagicMock()
      response = MagicMock()
      response.status_code = 200
      response.json.return_value = {"status": "OK", "data": {}}
      session.post.return_value = response
      login(session, "https://auth.example", "alice", "secret")

      response.json.return_value = {"status": "OK", "data": {"methods": ["totp"]}}
      with pytest.raises(AutheliaError, match="second_factor_required"):
          login(session, "https://auth.example", "alice", "secret")

      response.status_code = 401
      with pytest.raises(AutheliaError, match="rejected"):
          login(session, "https://auth.example", "alice", "bad")

      response.status_code = 200
      response.json.return_value = {"status": "OK", "data": {"methods": ["totp"]}}
      session.post.return_value = response
      login(session, "https://auth.example", "alice", "secret", totp="123456")

      bad_2fa = MagicMock()
      bad_2fa.status_code = 401
      session.post.return_value = bad_2fa
      with pytest.raises(AutheliaError, match="one-time code"):
          second_factor(session, "https://auth.example", "bad")

  def test_login_non_json_response(self):
      from aird.cli.authelia import AutheliaError, login

      session = MagicMock()
      response = MagicMock()
      response.status_code = 200
      response.json.side_effect = ValueError("not json")
      session.post.return_value = response
      with pytest.raises(AutheliaError, match="non-JSON"):
          login(session, "https://auth.example", "alice", "secret")


class TestInputValidationExtended:
  def test_share_path_limits(self):
      from aird.constants.input_limits import MAX_SHARE_PATHS, MAX_SHARE_PATH_STRING_LEN

      too_many = {"paths": ["a"] * (MAX_SHARE_PATHS + 1)}
      assert validate_share_create_struct(too_many) == "too many paths"
      long_path = {"paths": ["x" * (MAX_SHARE_PATH_STRING_LEN + 10)]}
      assert validate_share_create_struct(long_path) == "path too long"
      assert validate_share_update_struct({"paths": [1, 2]}) == "paths entries must be strings or objects"
      assert validate_share_update_struct({"allow_list": "bad"}) == "allow_list must be a list"

  def test_policy_payload_limits(self):
      from aird.constants.input_limits import MAX_POLICY_TARGET_ACTIONS

      with pytest.raises(InputTooLongError):
          validate_policy_payload(
              "ok",
              "",
              ["a"] * (MAX_POLICY_TARGET_ACTIONS + 1),
              {},
          )


class TestConstantsModule:
  def test_read_app_version_fallback(self):
      import aird.constants as constants

      with patch("importlib.metadata.version", side_effect=Exception("no pkg")):
          from aird.constants import _read_app_version

          assert _read_app_version() in (constants.APP_VERSION, "0.4.24", "0.4.25.dev3", "dev")

  def test_get_static_version(self):
      from aird.constants import get_static_version

      v1 = get_static_version()
      v2 = get_static_version()
      assert v1
      assert v1 == v2
