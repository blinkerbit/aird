"""Android app facets for Credential Manager passkeys."""

from __future__ import annotations

import base64
import os
import re

_PACKAGE = re.compile(r"[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+")


def normalize_fingerprint(value: str) -> str | None:
    if not isinstance(value, str):
        return None
    compact = re.sub(r"[^0-9a-fA-F]", "", value.strip())
    if len(compact) != 64:
        return None
    return ":".join(compact[i : i + 2].upper() for i in range(0, 64, 2))


def apk_key_hash_origin(fingerprint: str) -> str:
    raw = bytes.fromhex(fingerprint.replace(":", ""))
    token = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return f"android:apk-key-hash:{token}"


def android_package_name() -> str:
    name = os.environ.get("AIRD_ANDROID_PACKAGE", "com.aird").strip()
    if _PACKAGE.fullmatch(name):
        return name
    return "com.aird"


def env_fingerprints() -> list[str]:
    raw = os.environ.get("AIRD_ANDROID_CERT_SHA256", "")
    found: list[str] = []
    for part in raw.split(","):
        norm = normalize_fingerprint(part)
        if norm and norm not in found:
            found.append(norm)
    return found


def assetlinks_document(fingerprints: list[str]) -> list[dict]:
    found: list[str] = []
    for item in fingerprints:
        norm = normalize_fingerprint(item)
        if norm and norm not in found:
            found.append(norm)
    if not found:
        return []
    return [
        {
            "relation": [
                "delegate_permission/common.handle_all_urls",
                "delegate_permission/common.get_login_creds",
            ],
            "target": {
                "namespace": "android_app",
                "package_name": android_package_name(),
                "sha256_cert_fingerprints": found,
            },
        }
    ]


def merge_android_origins(origins: list[str], fingerprints: list[str]) -> list[str]:
    merged = list(origins)
    for item in fingerprints:
        norm = normalize_fingerprint(item)
        if not norm:
            continue
        origin = apk_key_hash_origin(norm)
        if origin not in merged:
            merged.append(origin)
    return merged
