"""Admin handlers for ABAC tags, policies, and the live decision feed."""

from __future__ import annotations

import json
import logging

import tornado.web
import tornado.websocket

from aird.core.events import PolicyDecisionEvent
from aird.core.security import is_valid_websocket_origin
from aird.db.policies import (
    delete_policy,
    get_policy,
    insert_policy,
    list_policies,
    update_policy,
)
from aird.db.policy_decisions import get_policy_decisions
from aird.db.resource_tags import (
    delete_resource_tag,
    delete_resource_tag_by_name,
    insert_resource_tag,
    list_resource_tags,
    update_resource_tag,
)
from aird.db.tag_colors import delete_tag_color, get_tag_colors_map, set_tag_color
from aird.db.user_attributes import (
    delete_user_attribute,
    list_all_user_attributes,
    set_user_attribute,
)
from aird.handlers.base_handler import (
    BaseHandler,
    ManagedWebSocketMixin,
    XSRFTokenMixin,
    require_admin,
    require_db,
)
from aird.utils.util import WebSocketConnectionManager
from aird.constants.input_limits import (
    GLOB_PATTERN_MAX_LEN,
    MAX_TAG_RULE_BULK_DELETE_IDS,
    InputTooLongError,
    RESOURCE_TAG_MAX_LEN,
)
from aird.utils.tag_display import tag_chip_inline_style
from aird.core.input_validation import (
    validate_abac_tag_rule,
    validate_policy_payload as check_policy_payload_sizes,
    validate_user_attribute,
)

logger = logging.getLogger(__name__)
AUDIT_LOG_FAILED_MSG = "audit_service.log failed"

URL_ADMIN_LOGIN = "/admin/login"
URL_ADMIN_TAGS = "/admin/tags"
URL_ADMIN_POLICIES = "/admin/policies"
CONTENT_TYPE_JSON = "application/json"


def _bool_arg(value: str | None) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "on", "yes"}


def _parse_actions(raw: str) -> list[str]:
    """Accept comma-separated or JSON-array strings; return normalised list."""
    if not raw:
        return []
    raw = raw.strip()
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if str(x).strip()]
        except json.JSONDecodeError:
            pass
    return [item.strip() for item in raw.split(",") if item.strip()]


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------


class AdminTagsHandler(BaseHandler):
    """HTML page listing every glob-based resource tag."""

    @tornado.web.authenticated
    @require_admin(redirect_url=URL_ADMIN_LOGIN)
    def get(self) -> None:
        db_conn = self.db_conn
        tags = list_resource_tags(db_conn) if db_conn is not None else []
        tag_colors = get_tag_colors_map(db_conn) if db_conn is not None else {}
        self.render(
            "admin_tags.html",
            tags=tags,
            tag_colors=tag_colors,
            tag_chip_style=tag_chip_inline_style,
            error=self.get_argument("error", ""),
        )


class AdminTagAPIHandler(XSRFTokenMixin, BaseHandler):
    """JSON CRUD for tag rules."""

    def _audit(self, action: str, details: str) -> None:
        try:
            self.get_service("audit_service").log(
                self.db_conn,
                action,
                username=self.get_display_username(),
                details=details,
                ip=self.request.remote_ip,
            )
        except Exception:
            logger.debug(AUDIT_LOG_FAILED_MSG, exc_info=True)

    def _invalidate_caches(self) -> None:
        try:
            from aird.db.shares import clear_tag_file_cache

            clear_tag_file_cache()
        except Exception:
            logger.debug("clear_tag_file_cache failed", exc_info=True)
        for key in ("tag_service", "policy_service"):
            svc = self.get_service(key)
            if svc and hasattr(svc, "invalidate"):
                try:
                    svc.invalidate()
                except Exception:
                    logger.debug("%s.invalidate failed", key, exc_info=True)

    @tornado.web.authenticated
    @require_admin(deny_status=403, deny_body="Access denied")
    @require_db
    def get(self) -> None:
        self.set_header("Content-Type", CONTENT_TYPE_JSON)
        self.write({
            "tags": list_resource_tags(self.db_conn),
            "colors": get_tag_colors_map(self.db_conn),
        })

    @tornado.web.authenticated
    @require_admin(deny_status=403, deny_body="Access denied")
    @require_db
    def post(self) -> None:
        payload = self.parse_json_body() or {}
        tag = str(payload.get("tag", "")).strip()
        glob_pattern = str(payload.get("glob_pattern", "")).strip()
        priority = int(payload.get("priority") or 0)
        if not tag or not glob_pattern:
            self.set_status(400)
            self.write({"error": "tag and glob_pattern are required"})
            return
        try:
            validate_abac_tag_rule(tag, glob_pattern)
        except InputTooLongError:
            self.set_status(400)
            self.write({"error": "tag or glob_pattern exceeds maximum length"})
            return
        color_raw = payload.get("color")
        if color_raw is not None and str(color_raw).strip():
            from aird.utils.tag_display import normalize_tag_color

            if normalize_tag_color(color_raw) is None:
                self.set_status(400)
                self.write({"error": "Invalid tag color"})
                return
        new_id = insert_resource_tag(
            self.db_conn,
            tag,
            glob_pattern,
            priority=priority,
            created_by=self.get_display_username(),
        )
        if new_id is None:
            self.set_status(409)
            self.write({"error": "Tag rule already exists or could not be created"})
            return
        if color_raw is not None and str(color_raw).strip():
            if not set_tag_color(self.db_conn, tag, str(color_raw)):
                delete_resource_tag(self.db_conn, int(new_id))
                self.set_status(400)
                self.write({"error": "Invalid tag color"})
                return
        self._invalidate_caches()
        self._audit(
            "abac_tag_create",
            f"id={new_id} tag={tag} glob={glob_pattern} priority={priority}",
        )
        self.set_status(201)
        self.write({"id": new_id, "tag": tag, "glob_pattern": glob_pattern, "priority": priority})

    @tornado.web.authenticated
    @require_admin(deny_status=403, deny_body="Access denied")
    @require_db
    def _delete_by_name(self, tag_name: str) -> None:
        tn = str(tag_name).strip()
        if len(tn) > RESOURCE_TAG_MAX_LEN:
            self.set_status(400)
            self.write({"error": "tag exceeds maximum length"})
            return
        count = delete_resource_tag_by_name(self.db_conn, tn)
        delete_tag_color(self.db_conn, tn)
        self._invalidate_caches()
        self._audit("abac_tag_delete_all", f"tag={tag_name} count={count}")
        self.write({"deleted": True, "tag": tag_name, "count": count})

    def _delete_bulk(self, ids: list) -> None:
        if not isinstance(ids, list) or not ids:
            self.set_status(400)
            self.write({"error": "ids must be a non-empty list"})
            return
        if len(ids) > MAX_TAG_RULE_BULK_DELETE_IDS:
            self.set_status(400)
            self.write({"error": "too many ids"})
            return
        deleted = []
        for tid in ids:
            try:
                if delete_resource_tag(self.db_conn, int(tid)):
                    deleted.append(int(tid))
            except (ValueError, TypeError):
                continue  # skip non-integer ids
        self._invalidate_caches()
        self._audit("abac_tag_bulk_delete", f"ids={deleted}")
        self.write({"deleted": True, "ids": deleted})

    def _delete_single(self, tag_id: int | str) -> None:
        if tag_id is None:
            self.set_status(400)
            self.write({"error": "id or ids is required"})
            return
        ok = delete_resource_tag(self.db_conn, int(tag_id))
        if not ok:
            self.set_status(404)
            self.write({"error": "Tag not found"})
            return
        self._invalidate_caches()
        self._audit("abac_tag_delete", f"id={tag_id}")
        self.write({"deleted": True, "id": int(tag_id)})

    @tornado.web.authenticated
    @require_admin(deny_status=403, deny_body="Access denied")
    @require_db
    def delete(self) -> None:
        payload = self.parse_json_body() or {}
        if "tag" in payload:
            self._delete_by_name(payload["tag"])
        elif "ids" in payload:
            self._delete_bulk(payload["ids"])
        else:
            self._delete_single(payload.get("id"))

    @tornado.web.authenticated
    @require_admin(deny_status=403, deny_body="Access denied")
    @require_db
    def put(self) -> None:
        payload = self.parse_json_body() or {}
        tag_id = payload.get("id")
        if tag_id is None:
            self.set_status(400)
            self.write({"error": "id is required"})
            return
        tag = str(payload["tag"]).strip() if payload.get("tag") is not None else None
        glob_pattern = str(payload["glob_pattern"]).strip() if payload.get("glob_pattern") is not None else None
        if tag is not None and len(tag) > RESOURCE_TAG_MAX_LEN:
            self.set_status(400)
            self.write({"error": "tag exceeds maximum length"})
            return
        if glob_pattern is not None and len(glob_pattern) > GLOB_PATTERN_MAX_LEN:
            self.set_status(400)
            self.write({"error": "glob_pattern exceeds maximum length"})
            return
        priority = int(payload["priority"]) if payload.get("priority") is not None else None
        ok = update_resource_tag(
            self.db_conn,
            int(tag_id),
            tag=tag,
            glob_pattern=glob_pattern,
            priority=priority,
        )
        if not ok:
            self.set_status(404)
            self.write({"error": "Tag not found or no changes"})
            return
        self._invalidate_caches()
        self._audit("abac_tag_update", f"id={tag_id} tag={tag} glob={glob_pattern} priority={priority}")
        self.write({"updated": True, "id": int(tag_id)})


class AdminTagColorAPIHandler(XSRFTokenMixin, BaseHandler):
    """Set or clear per-tag chip colors."""

    def _audit(self, action: str, details: str) -> None:
        try:
            self.get_service("audit_service").log(
                self.db_conn,
                action,
                username=self.get_display_username(),
                details=details,
                ip=self.request.remote_ip,
            )
        except Exception:
            logger.debug(AUDIT_LOG_FAILED_MSG, exc_info=True)

    @tornado.web.authenticated
    @require_admin(deny_status=403, deny_body="Access denied")
    @require_db
    def get(self) -> None:
        self.set_header("Content-Type", CONTENT_TYPE_JSON)
        self.write({"colors": get_tag_colors_map(self.db_conn)})

    @tornado.web.authenticated
    @require_admin(deny_status=403, deny_body="Access denied")
    @require_db
    def put(self) -> None:
        payload = self.parse_json_body() or {}
        tag = str(payload.get("tag", "")).strip()
        if not tag or len(tag) > RESOURCE_TAG_MAX_LEN:
            self.set_status(400)
            self.write({"error": "tag is required"})
            return
        color = payload.get("color")
        if color is not None and str(color).strip() and not set_tag_color(
            self.db_conn, tag, str(color)
        ):
            self.set_status(400)
            self.write({"error": "Invalid color"})
            return
        if color is not None and not str(color).strip():
            delete_tag_color(self.db_conn, tag)
        self._audit("abac_tag_color", f"tag={tag} color={color}")
        self.write({"ok": True, "tag": tag, "color": get_tag_colors_map(self.db_conn).get(tag)})


# ---------------------------------------------------------------------------
# Policies
# ---------------------------------------------------------------------------


class AdminPoliciesHandler(BaseHandler):
    @tornado.web.authenticated
    @require_admin(redirect_url=URL_ADMIN_LOGIN)
    def get(self) -> None:
        db_conn = self.db_conn
        policies = list_policies(db_conn) if db_conn is not None else []
        for policy in policies:
            policy["condition_json_str"] = json.dumps(
                policy.get("condition") or {}, indent=2
            )
        self.render(
            "admin_policies.html",
            policies=policies,
            error=self.get_argument("error", ""),
        )


def _parse_target_actions(target_actions: Any) -> list[str]:
    if isinstance(target_actions, str):
        return _parse_actions(target_actions)
    if isinstance(target_actions, list):
        return target_actions
    return []


def _parse_condition(condition: Any) -> tuple[dict, str | None]:
    if isinstance(condition, str):
        try:
            return (json.loads(condition) if condition.strip() else {}), None
        except json.JSONDecodeError as exc:
            return {}, f"invalid condition JSON: {exc.msg}"
    if condition is None:
        return {}, None
    if not isinstance(condition, dict):
        return {}, "condition must be a JSON object"
    return condition, None


def _validate_policy_payload(payload: dict) -> tuple[dict, str | None]:
    name = str(payload.get("name", "")).strip()
    if not name:
        return {}, "name is required"
    effect = str(payload.get("effect", "")).strip().lower()
    if effect not in ("permit", "deny"):
        return {}, "effect must be 'permit' or 'deny'"
    
    target_actions = _parse_target_actions(payload.get("target_actions"))
    if not target_actions:
        return {}, "at least one target_action is required"
        
    condition, cond_err = _parse_condition(payload.get("condition"))
    if cond_err:
        return {}, cond_err

    desc_stripped = str(payload.get("description", "") or "").strip()
    try:
        check_policy_payload_sizes(name, desc_stripped, target_actions, condition)
    except InputTooLongError as exc:
        return {}, str(exc)
        
    enabled_val = payload.get("enabled", True)
    enabled = _bool_arg(enabled_val) if isinstance(enabled_val, str) else bool(enabled_val)
    
    return (
        {
            "name": name,
            "description": desc_stripped or None,
            "effect": effect,
            "target_actions": target_actions,
            "condition": condition,
            "priority": int(payload.get("priority") or 0),
            "enabled": enabled,
        },
        None,
    )


class AdminPolicyAPIHandler(XSRFTokenMixin, BaseHandler):
    """JSON CRUD for policies."""

    def _audit(self, action: str, details: str) -> None:
        try:
            self.get_service("audit_service").log(
                self.db_conn,
                action,
                username=self.get_display_username(),
                details=details,
                ip=self.request.remote_ip,
            )
        except Exception:
            logger.debug(AUDIT_LOG_FAILED_MSG, exc_info=True)

    def _invalidate_policy_cache(self) -> None:
        svc = self.get_service("policy_service")
        if svc is not None and hasattr(svc, "invalidate"):
            try:
                svc.invalidate()
            except Exception:
                logger.debug("policy_service.invalidate failed", exc_info=True)

    @tornado.web.authenticated
    @require_admin(deny_status=403, deny_body="Access denied")
    @require_db
    def get(self, policy_id: str | None = None) -> None:
        self.set_header("Content-Type", CONTENT_TYPE_JSON)
        if policy_id:
            policy = get_policy(self.db_conn, int(policy_id))
            if policy is None:
                self.set_status(404)
                self.write({"error": "Policy not found"})
                return
            self.write(policy)
            return
        self.write({"policies": list_policies(self.db_conn)})

    @tornado.web.authenticated
    @require_admin(deny_status=403, deny_body="Access denied")
    @require_db
    def post(self, policy_id: str | None = None) -> None:
        payload = self.parse_json_body() or {}
        if policy_id:
            data, error = _validate_policy_payload(payload)
            if error:
                self.set_status(400)
                self.write({"error": error})
                return
            ok = update_policy(self.db_conn, int(policy_id), **data)
            if not ok:
                self.set_status(404)
                self.write({"error": "Policy not found or unchanged"})
                return
            self._invalidate_policy_cache()
            self._audit("abac_policy_update", f"id={policy_id} name={data['name']}")
            self.write({"updated": True, "id": int(policy_id)})
            return
        data, error = _validate_policy_payload(payload)
        if error:
            self.set_status(400)
            self.write({"error": error})
            return
        new_id = insert_policy(self.db_conn, **data)
        if new_id is None:
            self.set_status(409)
            self.write({"error": "Policy with that name already exists"})
            return
        self._invalidate_policy_cache()
        self._audit("abac_policy_create", f"id={new_id} name={data['name']}")
        self.set_status(201)
        self.write({"id": new_id, **data})

    @tornado.web.authenticated
    @require_admin(deny_status=403, deny_body="Access denied")
    @require_db
    def delete(self, policy_id: str | None = None) -> None:
        if not policy_id:
            payload = self.parse_json_body() or {}
            policy_id = payload.get("id")
        if policy_id is None:
            self.set_status(400)
            self.write({"error": "id is required"})
            return
        ok = delete_policy(self.db_conn, int(policy_id))
        if not ok:
            self.set_status(404)
            self.write({"error": "Policy not found"})
            return
        self._invalidate_policy_cache()
        self._audit("abac_policy_delete", f"id={policy_id}")
        self.write({"deleted": True, "id": int(policy_id)})


# ---------------------------------------------------------------------------
# Live decision feed (admin only)
# ---------------------------------------------------------------------------


class PolicyDecisionsAPIHandler(BaseHandler):
    """Recent PDP decisions (paged)."""

    @tornado.web.authenticated
    @require_admin(deny_status=403, deny_body="Access denied")
    @require_db
    def get(self) -> None:
        limit = min(500, max(1, int(self.get_argument("limit", "100"))))
        offset = max(0, int(self.get_argument("offset", "0")))
        decisions = get_policy_decisions(self.db_conn, limit=limit, offset=offset)
        self.set_header("Content-Type", CONTENT_TYPE_JSON)
        self.write({"decisions": decisions, "limit": limit, "offset": offset})


class PolicyDecisionsWebSocket(ManagedWebSocketMixin, tornado.websocket.WebSocketHandler):
    """Streams PolicyDecisionEvent payloads to admin clients."""

    connection_manager = WebSocketConnectionManager(
        config_prefix="policy_decisions",
        default_max_connections=20,
        default_idle_timeout=300,
    )

    @property
    def app_context(self):
        return self.settings.get("app_context")

    @property
    def event_bus(self):
        if self.app_context is not None:
            return self.app_context.event_bus
        return self.settings.get("event_bus")

    def get_service(self, name: str, default=None):
        if self.app_context is not None:
            return self.app_context.get_service(name, default)
        return self.settings.get("services", {}).get(name, default)

    def check_origin(self, origin: str) -> bool:
        return is_valid_websocket_origin(self, origin)

    def open(self) -> None:
        user = self.get_current_user()
        is_admin = isinstance(user, dict) and user.get("role") == "admin"
        if not is_admin:
            self.close(code=4401, reason="admin only")
            return
        if not self.register_connection():
            return
        bus = self.event_bus
        if bus is not None:
            bus.subscribe(PolicyDecisionEvent, self._on_decision)

    def _on_decision(self, event: PolicyDecisionEvent) -> None:
        try:
            self.write_message(
                json.dumps(
                    {
                        "type": "policy_decision",
                        "username": event.username,
                        "action": event.action,
                        "resource": event.resource,
                        "decision": event.decision,
                        "reason": event.reason,
                        "policy_id": event.matched_policy_id,
                        "policy_name": event.matched_policy_name,
                        "ip": event.ip,
                        "decided_at": event.decided_at,
                    }
                )
            )
        except Exception:
            logger.debug("policy decision broadcast failed", exc_info=True)


# ---------------------------------------------------------------------------
# User attributes (subject dimension)
# ---------------------------------------------------------------------------


class AdminUserAttributesHandler(BaseHandler):
    """HTML page listing and editing per-user ABAC attributes."""

    @tornado.web.authenticated
    @require_admin(redirect_url=URL_ADMIN_LOGIN)
    def get(self) -> None:
        db_conn = self.db_conn
        attrs = list_all_user_attributes(db_conn) if db_conn is not None else []
        self.render(
            "admin_user_attributes.html",
            attrs=attrs,
            error=self.get_argument("error", ""),
        )


class AdminUserAttributeAPIHandler(XSRFTokenMixin, BaseHandler):
    """JSON CRUD for user attributes."""

    def _audit(self, action: str, details: str) -> None:
        try:
            self.get_service("audit_service").log(
                self.db_conn,
                action,
                username=self.get_display_username(),
                details=details,
                ip=self.request.remote_ip,
            )
        except Exception:
            logger.debug(AUDIT_LOG_FAILED_MSG, exc_info=True)

    @tornado.web.authenticated
    @require_admin(deny_status=403, deny_body="Access denied")
    @require_db
    def get(self) -> None:
        self.set_header("Content-Type", CONTENT_TYPE_JSON)
        self.write({"attrs": list_all_user_attributes(self.db_conn)})

    @tornado.web.authenticated
    @require_admin(deny_status=403, deny_body="Access denied")
    @require_db
    def post(self) -> None:
        payload = self.parse_json_body() or {}
        username = str(payload.get("username", "")).strip()
        key = str(payload.get("key", "")).strip()
        value = str(payload.get("value", "")).strip()
        if not username or not key:
            self.set_status(400)
            self.write({"error": "username and key are required"})
            return
        try:
            validate_user_attribute(username, key, value)
        except InputTooLongError as exc:
            self.set_status(400)
            self.write({"error": str(exc)})
            return
        ok = set_user_attribute(self.db_conn, username, key, value)
        if not ok:
            self.set_status(500)
            self.write({"error": "Failed to set attribute"})
            return
        self._audit("abac_user_attr_set", f"user={username} key={key}")
        self.write({"ok": True, "username": username, "key": key, "value": value})

    @tornado.web.authenticated
    @require_admin(deny_status=403, deny_body="Access denied")
    @require_db
    def delete(self) -> None:
        payload = self.parse_json_body() or {}
        username = str(payload.get("username", "")).strip()
        key = str(payload.get("key", "")).strip()
        if not username or not key:
            self.set_status(400)
            self.write({"error": "username and key are required"})
            return
        try:
            validate_user_attribute(username, key, "")
        except InputTooLongError as exc:
            self.set_status(400)
            self.write({"error": str(exc)})
            return
        ok = delete_user_attribute(self.db_conn, username, key)
        if not ok:
            self.set_status(404)
            self.write({"error": "Attribute not found"})
            return
        self._audit("abac_user_attr_delete", f"user={username} key={key}")
        self.write({"deleted": True, "username": username, "key": key})
