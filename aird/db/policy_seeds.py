"""Seed default ABAC policies on first run.

Idempotent: existing rows (matched by ``name``) are left untouched so admins
can edit the seeds freely. Only missing seeds are inserted.
"""

from __future__ import annotations

import logging
import sqlite3

from aird.db.policies import get_policy_by_name, insert_policy

logger = logging.getLogger(__name__)


# A small, opinionated starter set. The admin can disable or edit any of
# these from the UI; they exist mainly to (a) keep current admin behaviour
# working when the engine is flipped on and (b) demonstrate the AST.
DEFAULT_POLICIES: list[dict] = [
    {
        "name": "default-admin-permit",
        "description": "Permit any action when subject.role is admin (shadow-RBAC compatibility).",
        "effect": "permit",
        "target_actions": ["*"],
        "condition": {
            "equals": {
                "left": {"attr": "subject.role"},
                "right": "admin",
            }
        },
        "priority": 1000,
        "enabled": True,
    },
    {
        "name": "default-user-permit",
        "description": "Permit signed-in users (role=user|admin) to perform standard read/list actions.",
        "effect": "permit",
        "target_actions": [
            "file.read",
            "file.list",
            "share.view",
            "favorites.toggle",
        ],
        "condition": {
            "in": {
                "value": {"attr": "subject.role"},
                "list": ["user", "admin"],
            }
        },
        "priority": 500,
        "enabled": True,
    },
    {
        "name": "time-gated-pii",
        "description": "Block read of PII-tagged resources outside business hours (09:00-18:00).",
        "effect": "deny",
        "target_actions": ["file.read", "file.download"],
        "condition": {
            "and": [
                {"tag_present": "pii"},
                {
                    "not": {
                        "time_between": {"start": "09:00", "end": "18:00"}
                    }
                },
            ]
        },
        "priority": 800,
        "enabled": True,
    },
    {
        "name": "large-p2p-managed-device",
        "description": "Require a managed device for P2P transfers larger than 2 GB.",
        "effect": "deny",
        "target_actions": ["p2p.transfer"],
        "condition": {
            "and": [
                {
                    "not": {
                        "equals": {
                            "left": {"attr": "environment.is_managed_device"},
                            "right": True,
                        }
                    }
                },
                {
                    "not": {
                        "equals": {
                            "left": {"attr": "resource.size"},
                            "right": None,
                        }
                    }
                },
            ]
        },
        "priority": 700,
        "enabled": False,
    },
]
# Note: an explicit "default-deny" policy is intentionally not seeded — the
# PDP itself returns deny when no permit matches, and an always-true seed
# would short-circuit the deny-wins semantics for every meaningful permit.


def seed_default_policies(conn: sqlite3.Connection) -> int:
    """Insert any missing default policy. Returns the number of new rows created."""
    if conn is None:
        return 0
    inserted = 0
    for policy in DEFAULT_POLICIES:
        try:
            existing = get_policy_by_name(conn, policy["name"])
            if existing:
                continue
            new_id = insert_policy(
                conn,
                name=policy["name"],
                description=policy["description"],
                effect=policy["effect"],
                target_actions=policy["target_actions"],
                condition=policy["condition"],
                priority=policy["priority"],
                enabled=policy["enabled"],
            )
            if new_id is not None:
                inserted += 1
        except Exception:
            logger.debug("seed policy %s failed", policy.get("name"), exc_info=True)
    if inserted:
        logger.info("Seeded %d default ABAC policies", inserted)
    return inserted
