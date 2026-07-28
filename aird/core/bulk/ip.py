"""Client IP normalization for bulk ticket binding."""

from __future__ import annotations

import ipaddress


def normalize_client_ip(addr: str | None) -> str | None:
    if not addr:
        return None
    raw = addr.strip()
    if not raw:
        return None
    if raw.startswith("[") and "]" in raw:
        raw = raw[1 : raw.index("]")]
    if "%" in raw:
        raw = raw.split("%", 1)[0]
    elif ":" in raw:
        host, maybe_port = raw.rsplit(":", 1)
        if maybe_port.isdigit():
            try:
                ipaddress.ip_address(host)
                raw = host
            except ValueError:
                if raw.count(":") == 1 and "." in host:
                    raw = host
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
