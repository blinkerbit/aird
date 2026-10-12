"""Build ldap3 servers. LDAPS validates certificates."""

from __future__ import annotations

import os
import ssl


def make_ldap_server(url: str, get_info=None):
    """Return an ldap3 Server. ldaps:// uses CERT_REQUIRED."""
    from ldap3 import Server, Tls

    kwargs = {}
    if str(url or "").lower().startswith("ldaps://"):
        ca_file = os.environ.get("AIRD_LDAP_CA_FILE", "").strip() or None
        kwargs["tls"] = Tls(validate=ssl.CERT_REQUIRED, ca_certs_file=ca_file)
    if get_info is not None:
        kwargs["get_info"] = get_info
    return Server(url, **kwargs)
