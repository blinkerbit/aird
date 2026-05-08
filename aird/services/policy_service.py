"""Policy decision point (PDP) for Aird's ABAC engine.

The service evaluates an :class:`~aird.domain.models.AccessRequest` against
the policies stored in the ``policies`` table and returns a single
:class:`~aird.domain.models.AccessDecision`.

Evaluation rules (in order):
1. Resolve resource tags via :class:`~aird.services.tag_service.TagService`.
2. Filter policies whose ``target_actions`` cover ``request.action``
   (a literal entry of ``"*"`` matches every action).
3. Sort matching policies by ``priority`` descending then ``id`` ascending.
4. Evaluate each policy's condition AST against the request attributes.
5. Explicit ``deny`` always wins; otherwise the first matching ``permit``
   becomes the decision; otherwise the request is denied by default.
6. The decision is emitted via the event bus and persisted to
   ``policy_decisions`` (when audit is enabled).
"""

from __future__ import annotations

import ipaddress
import logging
import threading
import time
from dataclasses import replace
from datetime import datetime, time as dtime
from typing import Any, Iterable

from aird.core.events import EventBus, PolicyDecisionEvent, now_ts
from aird.db.policies import list_policies
from aird.db.policy_decisions import log_policy_decision
from aird.domain.models import (
    AccessDecision,
    AccessRequest,
    EnvironmentContext,
    ResourceAttributes,
    SubjectAttributes,
)
from aird.services.tag_service import TagService

logger = logging.getLogger(__name__)

_DEFAULT_TTL = 5.0  # seconds


class PolicyEvaluationError(ValueError):
    """Raised when a condition AST cannot be evaluated safely."""


class PolicyService:
    """ABAC Policy Decision Point."""

    def __init__(
        self,
        tag_service: TagService,
        *,
        event_bus: EventBus | None = None,
        cache_ttl: float = _DEFAULT_TTL,
    ):
        self._tags = tag_service
        self._event_bus = event_bus
        self._cache_ttl = cache_ttl
        self._cache: list[dict] | None = None
        self._cache_loaded_at: float = 0.0
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def invalidate(self) -> None:
        with self._lock:
            self._cache = None
            self._cache_loaded_at = 0.0

    def evaluate(
        self,
        conn: Any,
        request: AccessRequest,
        *,
        audit: bool = True,
    ) -> AccessDecision:
        """Evaluate *request* against the active policies.

        When *audit* is True (default) the resulting decision is logged
        to the ``policy_decisions`` table and broadcast on the event bus.
        """
        request = self._enrich_resource(conn, request)
        attrs = request.to_attrs()
        policies = self._load_policies(conn)
        relevant = [p for p in policies if _matches_action(p, request.action)]
        relevant.sort(
            key=lambda p: (-int(p.get("priority", 0)), int(p.get("id", 0)))
        )

        permit_match: dict | None = None
        for policy in relevant:
            if not policy.get("enabled", True):
                continue
            try:
                if _evaluate_condition(policy.get("condition") or {}, attrs):
                    if policy.get("effect") == "deny":
                        return self._finalise(
                            conn,
                            request,
                            "deny",
                            reason=policy.get("description")
                            or f"Denied by policy '{policy.get('name')}'",
                            policy_id=policy.get("id"),
                            policy_name=policy.get("name"),
                            audit=audit,
                        )
                    if permit_match is None and policy.get("effect") == "permit":
                        permit_match = policy
            except PolicyEvaluationError as exc:
                logger.warning(
                    "Skipping malformed policy %s: %s", policy.get("name"), exc
                )

        if permit_match is not None:
            return self._finalise(
                conn,
                request,
                "permit",
                reason=permit_match.get("description")
                or f"Permitted by policy '{permit_match.get('name')}'",
                policy_id=permit_match.get("id"),
                policy_name=permit_match.get("name"),
                audit=audit,
            )

        return self._finalise(
            conn,
            request,
            "deny",
            reason="No matching permit policy (default deny)",
            policy_id=None,
            policy_name=None,
            audit=audit,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_policies(self, conn: Any) -> list[dict]:
        with self._lock:
            now = time.time()
            if (
                self._cache is not None
                and (now - self._cache_loaded_at) < self._cache_ttl
            ):
                return self._cache
            self._cache = list_policies(conn, enabled_only=True)
            self._cache_loaded_at = now
            return self._cache

    def _enrich_resource(
        self, conn: Any, request: AccessRequest
    ) -> AccessRequest:
        """Resolve tags from the resource path if not already populated."""
        resource = request.resource
        if resource.path and not resource.tags:
            tags = self._tags.resolve(conn, resource.path)
            if tags:
                return replace(request, resource=replace(resource, tags=tags))
        return request

    def _finalise(
        self,
        conn: Any,
        request: AccessRequest,
        effect: str,
        *,
        reason: str,
        policy_id: int | None,
        policy_name: str | None,
        audit: bool,
    ) -> AccessDecision:
        decision = AccessDecision(
            effect=effect,
            reason=reason,
            matched_policy_id=policy_id,
            matched_policy_name=policy_name,
        )
        if audit:
            try:
                log_policy_decision(
                    conn,
                    username=request.subject.username,
                    action=request.action,
                    decision=effect,
                    resource=request.resource.path,
                    reason=reason,
                    policy_id=policy_id,
                    attributes=request.to_attrs(),
                    ip=request.environment.ip,
                )
            except Exception:
                logger.debug("policy_decisions log failed", exc_info=True)
        if self._event_bus is not None:
            try:
                self._event_bus.publish(
                    PolicyDecisionEvent(
                        username=request.subject.username,
                        action=request.action,
                        resource=request.resource.path,
                        decision=effect,
                        reason=reason,
                        matched_policy_id=policy_id,
                        matched_policy_name=policy_name,
                        ip=request.environment.ip,
                        decided_at=now_ts(),
                    )
                )
            except Exception:
                logger.debug("policy.decision publish failed", exc_info=True)
        return decision


# ---------------------------------------------------------------------------
# Helpers used by both PolicyService and unit tests
# ---------------------------------------------------------------------------


def build_request(
    *,
    username: str,
    role: str = "user",
    action: str,
    resource_path: str | None = None,
    ip: str | None = None,
    timestamp: datetime | None = None,
    clearance: str | None = None,
    groups: Iterable[str] = (),
    is_corporate_ip: bool = False,
    is_managed_device: bool = False,
    extra_attrs: dict[str, Any] | None = None,
) -> AccessRequest:
    """Convenience constructor for handlers/tests."""
    if clearance is None:
        clearance = "admin" if role == "admin" else "internal"
    extras = tuple((str(k), str(v)) for k, v in (extra_attrs or {}).items())
    subject = SubjectAttributes(
        username=username,
        role=role,
        clearance=clearance,
        groups=tuple(groups),
        extra=extras,
    )
    resource = ResourceAttributes(
        path=resource_path,
        extension=_extension(resource_path) if resource_path else None,
    )
    env = EnvironmentContext(
        timestamp=timestamp or datetime.now(),
        ip=ip,
        is_corporate_ip=is_corporate_ip,
        is_managed_device=is_managed_device,
    )
    return AccessRequest(
        subject=subject,
        action=action,
        resource=resource,
        environment=env,
    )


def _extension(path: str) -> str | None:
    if "." not in path:
        return None
    return path.rsplit(".", 1)[-1].lower()


def _matches_action(policy: dict, action: str) -> bool:
    targets = policy.get("target_actions") or []
    return "*" in targets or action in targets


# ---------------------------------------------------------------------------
# Condition AST evaluator
# ---------------------------------------------------------------------------


def _evaluate_condition(node: Any, attrs: dict[str, Any]) -> bool:
    """Evaluate a condition AST node against a flattened attribute dict.

    Supported node shapes (lists are treated as ``and`` for convenience):

    * ``True`` / ``False`` — terminal literals
    * ``{}`` — empty dict treated as ``True`` (always matches)
    * ``{"and": [..]}`` / ``{"or": [..]}`` / ``{"not": ..}``
    * ``{"equals": {"left": <expr>, "right": <expr>}}``
    * ``{"in": {"value": <expr>, "list": [..]}}``
    * ``{"tag_present": "pii"}`` — checks ``resource.tags``
    * ``{"time_between": {"start": "09:00", "end": "18:00",
                            "value": <expr (optional)>}}``
    * ``{"ip_in_cidr": {"ip": <expr>, "cidr": "10.0.0.0/8"}}``
    * ``{"attr": "subject.role"}`` — resolves a dotted attribute path.
    * Bare strings/numbers/bools are returned as-is when used as values
      inside ``equals`` / ``in`` / ``time_between`` etc.
    """
    if node is None:
        return False
    if isinstance(node, bool):
        return node
    if isinstance(node, list):
        return all(_evaluate_condition(child, attrs) for child in node)
    if not isinstance(node, dict):
        raise PolicyEvaluationError(f"Unsupported AST node: {node!r}")
    if not node:
        return True

    if len(node) > 1:
        # Treat a multi-key dict as an implicit AND of single-key clauses.
        return all(_evaluate_condition({k: v}, attrs) for k, v in node.items())

    op, payload = next(iter(node.items()))
    op = str(op).lower()

    if op == "and":
        return all(_evaluate_condition(c, attrs) for c in (payload or []))
    if op == "or":
        return any(_evaluate_condition(c, attrs) for c in (payload or []))
    if op == "not":
        return not _evaluate_condition(payload, attrs)
    if op == "equals":
        left = _resolve_value(payload.get("left"), attrs)
        right = _resolve_value(payload.get("right"), attrs)
        return left == right
    if op == "not_equals":
        left = _resolve_value(payload.get("left"), attrs)
        right = _resolve_value(payload.get("right"), attrs)
        return left != right
    if op == "in":
        value = _resolve_value(payload.get("value"), attrs)
        candidate = _resolve_value(payload.get("list"), attrs)
        if isinstance(candidate, (list, tuple, set)):
            return value in candidate
        if isinstance(candidate, str):
            return value == candidate
        return False
    if op == "tag_present":
        tag = _resolve_value(payload, attrs)
        tags = attrs.get("resource", {}).get("tags") or []
        return tag in tags
    if op == "time_between":
        start = _parse_time(payload.get("start"))
        end = _parse_time(payload.get("end"))
        if start is None or end is None:
            raise PolicyEvaluationError("time_between requires start/end")
        ts_value = payload.get("value")
        if ts_value is None:
            iso = attrs.get("environment", {}).get("timestamp")
            if not iso:
                return False
            try:
                stamp = datetime.fromisoformat(iso)
            except ValueError:
                return False
        else:
            stamp = _resolve_value(ts_value, attrs)
            if isinstance(stamp, str):
                try:
                    stamp = datetime.fromisoformat(stamp)
                except ValueError:
                    return False
            if not isinstance(stamp, datetime):
                return False
        current = stamp.time()
        if start <= end:
            return start <= current <= end
        # window crosses midnight
        return current >= start or current <= end
    if op == "ip_in_cidr":
        ip_expr = payload.get("ip")
        cidr = _resolve_value(payload.get("cidr"), attrs)
        ip_value = (
            _resolve_value(ip_expr, attrs)
            if ip_expr is not None
            else attrs.get("environment", {}).get("ip")
        )
        if not ip_value or not cidr:
            return False
        try:
            return ipaddress.ip_address(str(ip_value)) in ipaddress.ip_network(
                str(cidr), strict=False
            )
        except ValueError:
            return False
    if op == "attr":
        return _resolve_attr(payload, attrs) is not None

    raise PolicyEvaluationError(f"Unknown operator '{op}'")


def _resolve_value(node: Any, attrs: dict[str, Any]) -> Any:
    if isinstance(node, dict) and "attr" in node and len(node) == 1:
        return _resolve_attr(node["attr"], attrs)
    if isinstance(node, dict) and len(node) == 1:
        op = next(iter(node))
        if op == "literal":
            return node[op]
    return node


def _resolve_attr(path: Any, attrs: dict[str, Any]) -> Any:
    if not isinstance(path, str) or not path:
        return None
    cursor: Any = attrs
    for part in path.split("."):
        if isinstance(cursor, dict) and part in cursor:
            cursor = cursor[part]
        else:
            return None
    return cursor


def _parse_time(value: Any) -> dtime | None:
    if value is None:
        return None
    if isinstance(value, dtime):
        return value
    if isinstance(value, str):
        try:
            parts = value.split(":")
            if len(parts) == 2:
                return dtime(int(parts[0]), int(parts[1]))
            if len(parts) == 3:
                return dtime(int(parts[0]), int(parts[1]), int(parts[2]))
        except (ValueError, IndexError):
            return None
    return None
