"""Client IP normalization for bulk ticket binding."""

from __future__ import annotations

import ipaddress


def _strip_ipv6_brackets(raw: str) -> str:
    if raw.startswith("[") and "]" in raw:
        return raw[1 : raw.index("]")]
    return raw


def _strip_trailing_port(raw: str) -> str:
    if "%" in raw:
        return raw.split("%", 1)[0]
    if ":" not in raw:
        return raw
    host, maybe_port = raw.rsplit(":", 1)
    if not maybe_port.isdigit():
        return raw
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        if raw.count(":") == 1 and "." in host:
            return host
    return raw


def normalize_client_ip(addr: str | None) -> str | None:
    if not addr:
        return None
    raw = _strip_ipv6_brackets(addr.strip())
    if not raw:
        return None
    raw = _strip_trailing_port(raw)
    try:
        return str(ipaddress.ip_address(raw))
    except ValueError:
        return raw


def client_ips_match(expected: str | None, observed: str | None) -> bool:
    """Return True when ticket IP binding is satisfied (or not set)."""
    exp = normalize_client_ip(expected)
    obs = normalize_client_ip(observed)
    if not exp or not obs:
        return True
    if exp == obs:
        return True
    try:
        a = ipaddress.ip_address(exp)
        b = ipaddress.ip_address(obs)
        if a.is_loopback and b.is_loopback:
            return True
    except ValueError:
        pass
    return False
