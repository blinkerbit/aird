"""String constants and URLs for admin handlers."""

# HTTP status codes (for clarity)
HTTP_FORBIDDEN = 403
HTTP_NOT_FOUND = 404
HTTP_BAD_REQUEST = 400
HTTP_INTERNAL_ERROR = 500

# Admin URLs
URL_ADMIN = "/admin"
URL_ADMIN_LOGIN = "/admin/login"
URL_ADMIN_USERS = "/admin/users"
URL_ADMIN_LDAP = "/admin/ldap"
URL_ADMIN_NETWORK_SHARES = "/admin/network-shares"
# Network share error query params (URL-encoded)
ERR_DB_UNAVAILABLE = "Database+unavailable"
ERR_ALL_FIELDS_REQUIRED = "All+fields+are+required"
ERR_INVALID_PROTOCOL = "Invalid+protocol"
ERR_FOLDER_NOT_EXIST = "Folder+does+not+exist"
ERR_PORT_RANGE = "Port+must+be+1-65535"
ERR_FAILED_CREATE_SHARE = "Failed+to+create+share"

# Response messages
ACCESS_DENIED = "Access denied: You don't have permission to perform this action"
FORBIDDEN = "Forbidden"
ACCESS_DENIED_JSON = "Access denied"
DB_UNAVAILABLE = "Service temporarily unavailable: Database connection error"
USER_NOT_FOUND = "User not found: The requested user does not exist"
CONFIG_NOT_FOUND = "Configuration not found: The requested configuration does not exist"
INVALID_USER_ID = "Invalid request: Please provide a valid user ID"
INVALID_USER_ID_SHORT = "Invalid user ID"
INVALID_CONFIG_ID = "Invalid request: Please provide a valid configuration ID"
DATABASE_NOT_AVAILABLE = "Database not available"
USERNAME_PASSWORD_REQUIRED = "Username and password are required"
USERNAME_LENGTH = "Username must be between 3 and 50 characters"
INVALID_ROLE = "Invalid role"
USERNAME_FORMAT = "Username can only contain letters, numbers, underscores, and hyphens"
FAILED_CREATE_USER = "Failed to create user"
USERNAME_REQUIRED = "Username is required"
LDAP_PASSWORD_CHANGE = (
    "Password changes are not allowed for LDAP users. "
    "Please change the password through the LDAP directory."
)
FAILED_UPDATE_USER = "Failed to update user"
ERROR_UPDATE_USER = "Error updating user. Please try again."
ALL_FIELDS_REQUIRED = "All fields are required"
CONFIG_NAME_LENGTH = "Configuration name must be between 3 and 50 characters"
ERROR_CREATE_CONFIG = "Error creating configuration. Please try again."
FAILED_UPDATE_CONFIG = "Failed to update configuration"
ERROR_UPDATE_CONFIG = "Error updating configuration. Please try again."
SYNC_STARTED = "Sync started"

# Content types
CONTENT_TYPE_JSON = "application/json"
CONTENT_TYPE_CSV = "text/csv"

# Template names
TEMPLATE_USER_CREATE = "user_create.html"
TEMPLATE_USER_EDIT = "user_edit.html"
TEMPLATE_LDAP_CONFIG_CREATE = "ldap_config_create.html"
TEMPLATE_LDAP_CONFIG_EDIT = "ldap_config_edit.html"
