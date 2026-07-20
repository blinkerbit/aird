"""Database package -- re-exports all public symbols for backward compatibility.

Import from domain modules for new code:
    from aird.db.users import create_user
    from aird.db.shares import get_share_by_id

Or import from the package root (legacy):
    from aird.db import create_user
"""

from aird.db.schema import init_db, PRAGMA_TABLE_INFO  # noqa: F401
from aird.db.sync import DB_LOCK, db_sync, wrap_connection  # noqa: F401

from aird.db.users import (  # noqa: F401
    ARGON2_AVAILABLE,
    PH,
    LDAP3_AVAILABLE,
    hash_password,
    verify_password,
    create_user,
    get_user_by_username,
    get_all_users,
    search_users,
    update_user,
    assign_admin_privileges,
    delete_user,
    authenticate_user,
)

from aird.db.shares import (  # noqa: F401
    insert_share,
    delete_share,
    update_share,
    is_share_expired,
    cleanup_expired_shares,
    get_share_by_id,
    get_all_shares,
    get_shares_for_path,
    get_share_download_count,
)

from aird.db.config import (  # noqa: F401
    load_feature_flags,
    save_feature_flags,
    load_upload_config,
    save_upload_config,
    load_server_config,
    save_server_config,
    load_allowed_extensions,
    save_allowed_extensions,
    load_websocket_config,
    save_websocket_config,
)

from aird.db.audit import log_audit, get_audit_logs  # noqa: F401

from aird.db.network_shares import (  # noqa: F401
    create_network_share,
    get_all_network_shares,
    get_network_share,
    update_network_share,
    delete_network_share,
)

from aird.db.favorites import toggle_favorite, get_user_favorites  # noqa: F401

from aird.db.quota import (  # noqa: F401
    get_user_quota,
    update_user_used_bytes,
    set_user_quota,
)

from aird.db.user_attributes import (  # noqa: F401
    set_user_attribute,
    delete_user_attribute,
    get_user_attributes,
    list_all_user_attributes,
)

from aird.db.resource_tags import (  # noqa: F401
    insert_resource_tag,
    delete_resource_tag,
    list_resource_tags,
)

from aird.db.policies import (  # noqa: F401
    insert_policy,
    update_policy,
    delete_policy,
    get_policy,
    get_policy_by_name,
    list_policies,
)

from aird.db.policy_decisions import (  # noqa: F401
    log_policy_decision,
    get_policy_decisions,
)

DB_CONN = None
DB_PATH = "aird.db"
