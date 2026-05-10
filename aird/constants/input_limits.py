"""Maximum sizes for text fields and JSON bodies (DoS / abuse prevention).

Server-side checks are authoritative; HTML maxlength attributes are hints only.
"""

# --- Form posts: whole body (Content-Length) ---
LOGIN_FORM_MAX_BYTES = 64 * 1024
ADMIN_LOGIN_FORM_MAX_BYTES = 64 * 1024
PROFILE_FORM_MAX_BYTES = 128 * 1024
ADMIN_HTML_FORM_MAX_BYTES = 512 * 1024  # admin overview + network share create, etc.

# --- JSON request bodies ---
# Default for parse_json_body when max_bytes not overridden
DEFAULT_JSON_BODY_MAX_BYTES = 512 * 1024
# Share payloads may include many paths
SHARE_JSON_BODY_MAX_BYTES = 8 * 1024 * 1024
# Full-file save via JSON (~50 MiB readable window + JSON overhead)
EDIT_JSON_BODY_MAX_BYTES = 52 * 1024 * 1024

# --- Login / credentials (character counts) ---
LOGIN_USERNAME_MAX_LEN = 256
LOGIN_PASSWORD_MAX_LEN = 32 * 1024
ACCESS_TOKEN_MAX_LEN = 16 * 1024
ADMIN_TOKEN_MAX_LEN = 16 * 1024

# --- Redirect ---
SAFE_NEXT_URL_MAX_LEN = 2048

# --- Share create ---
MAX_SHARE_PATHS = 20_000
MAX_SHARE_PATH_STRING_LEN = 4096
MAX_SHARE_USERNAMES = 500
SHARE_USERNAME_ENTRY_MAX_LEN = 128
MAX_SHARE_GLOB_LINES = 500  # allow_list / avoid_list split from UI
MAX_SHARE_GLOB_LINE_LEN = 512
SHARE_TAG_NAME_MAX_LEN = 64
SHARE_ID_MAX_LEN = 128

# --- File / API query strings ---
REL_PATH_MAX_LEN = 32_000
USER_SEARCH_QUERY_MAX_LEN = 128
API_LAST_N_MAX = 10_000_000  # upper bound for stream line count param

# --- Bulk API ---
MAX_BULK_PATHS = 2_000
MAX_BULK_JSON_BYTES = 8 * 1024 * 1024

# --- ABAC / admin JSON ---
RESOURCE_TAG_MAX_LEN = 64
GLOB_PATTERN_MAX_LEN = 255
POLICY_NAME_MAX_LEN = 120
POLICY_DESCRIPTION_MAX_LEN = 2000
POLICY_CONDITION_JSON_MAX_CHARS = 256_000
MAX_POLICY_TARGET_ACTIONS = 200
MAX_TAG_RULE_BULK_DELETE_IDS = 2_000

# User attribute API (align with admin_user_attributes.html)
USER_ATTR_KEY_MAX_LEN = 64
USER_ATTR_VALUE_MAX_LEN = 255
# --- LDAP forms ---
LDAP_NAME_MAX_LEN = 120
LDAP_SERVER_MAX_LEN = 512
LDAP_DN_MAX_LEN = 2048
LDAP_MEMBER_ATTR_MAX_LEN = 256
LDAP_USER_TEMPLATE_MAX_LEN = 1024

# --- Network share form ---
NETWORK_SHARE_NAME_MAX_LEN = 120
NETWORK_SHARE_FOLDER_PATH_MAX_LEN = 2048

# --- WebSocket search ---
WS_SEARCH_PATTERN_MAX_LEN = 4096
WS_SEARCH_TEXT_MAX_LEN = 131_072  # 128 KiB


class InputTooLongError(ValueError):
    """Raised when a decoded field exceeds configured limits."""
