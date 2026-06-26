import argparse
import os
import json
import secrets
import socket
import logging
from aird.cloud import (
    CloudManager,
    CloudProviderError,
    GoogleDriveProvider,
    OneDriveProvider,
)
from aird.constants import (
    MAX_FILE_SIZE as _MAX_FILE_SIZE,
    MAX_READABLE_FILE_SIZE as _MAX_READABLE_FILE_SIZE,
    ALLOWED_UPLOAD_EXTENSIONS as _ALLOWED_UPLOAD_EXTENSIONS,
    MMAP_MIN_SIZE as _MMAP_MIN_SIZE,
    CHUNK_SIZE as _CHUNK_SIZE,
    UPLOAD_REQUEST_MAX_BODY_SIZE as _UPLOAD_REQUEST_MAX_BODY_SIZE,
)

# Module-level variables to hold configuration
CONFIG_FILE = None
ROOT_DIR = os.getcwd()
PORT = None
ACCESS_TOKEN = None
ADMIN_TOKEN = None
LDAP_ENABLED = False
LDAP_SERVER = None
LDAP_BASE_DN = None
LDAP_USER_TEMPLATE = None
LDAP_FILTER_TEMPLATE = None
LDAP_ATTRIBUTES = None
LDAP_ATTRIBUTE_MAP = None
HOSTNAME = None
SSL_CERT = None
SSL_KEY = None
ADMIN_USERS = []
FEATURE_FLAGS = {}
CLOUD_MANAGER = CloudManager()
WEBSOCKET_CONFIG = {}
MULTI_USER = False
WORKERS = None
DB_CONN = None
MAX_FILE_SIZE = _MAX_FILE_SIZE
MAX_READABLE_FILE_SIZE = _MAX_READABLE_FILE_SIZE
ALLOWED_UPLOAD_EXTENSIONS = _ALLOWED_UPLOAD_EXTENSIONS
MMAP_MIN_SIZE = _MMAP_MIN_SIZE
CHUNK_SIZE = _CHUNK_SIZE
UPLOAD_REQUEST_MAX_BODY_SIZE = _UPLOAD_REQUEST_MAX_BODY_SIZE
BREVO_API_KEY = None
BREVO_SENDER_EMAIL = None
BREVO_SENDER_NAME = "Aird"
PUBLIC_BASE_URL = None


def _apply_brevo_settings(config: dict) -> None:
    global BREVO_API_KEY, BREVO_SENDER_EMAIL, BREVO_SENDER_NAME, PUBLIC_BASE_URL
    brevo = config.get("brevo") if isinstance(config, dict) else {}
    if not isinstance(brevo, dict):
        brevo = {}
    BREVO_API_KEY = (
        os.environ.get("AIRD_BREVO_API_KEY", "").strip() or brevo.get("api_key")
    )
    BREVO_SENDER_EMAIL = (
        os.environ.get("AIRD_BREVO_SENDER_EMAIL", "").strip()
        or brevo.get("sender_email")
    )
    BREVO_SENDER_NAME = (
        os.environ.get("AIRD_BREVO_SENDER_NAME", "").strip()
        or brevo.get("sender_name")
        or "Aird"
    )
    PUBLIC_BASE_URL = (
        os.environ.get("AIRD_PUBLIC_BASE_URL", "").strip()
        or brevo.get("public_base_url")
        or None
    )


def _configure_google_drive(cloud_config: dict) -> None:
    gdrive_config = cloud_config.get("google_drive", {})
    if not isinstance(gdrive_config, dict):
        gdrive_config = {}
    gdrive_token = gdrive_config.get("access_token") or os.environ.get(
        "AIRD_GDRIVE_ACCESS_TOKEN"
    )
    gdrive_root = (
        gdrive_config.get("root_id") or os.environ.get("AIRD_GDRIVE_ROOT_ID") or "root"
    )
    include_shared = gdrive_config.get("include_shared_drives", True)
    gd_credentials_file = gdrive_config.get("credentials_file")

    try:
        if gdrive_token:
            CLOUD_MANAGER.register(
                GoogleDriveProvider(
                    gdrive_token,
                    root_id=gdrive_root,
                    include_shared_drives=bool(include_shared),
                )
            )
        elif gd_credentials_file:
            logging.error("GoogleDriveProvider currently only supports 'access_token'. 'credentials_file' is not supported.")
    except CloudProviderError:
        logging.exception("Failed to configure Google Drive provider")
    except Exception:
        logging.exception("Unexpected error configuring Google Drive provider")


def _configure_onedrive(config: dict) -> None:
    onedrive_config = config.get("one_drive")
    if not isinstance(onedrive_config, dict):
        onedrive_config = config.get("onedrive", {})
        if not isinstance(onedrive_config, dict):
            onedrive_config = {}
    onedrive_token = (
        onedrive_config.get("access_token")
        or os.environ.get("AIRD_ONEDRIVE_ACCESS_TOKEN")
        or os.environ.get("AIRD_ONE_DRIVE_ACCESS_TOKEN")
    )
    drive_id = onedrive_config.get("drive_id") or os.environ.get(
        "AIRD_ONEDRIVE_DRIVE_ID"
    )
    od_client_id = onedrive_config.get("client_id")
    od_redirect_uri = onedrive_config.get("redirect_uri")

    try:
        if onedrive_token:
            CLOUD_MANAGER.register(OneDriveProvider(onedrive_token, drive_id=drive_id))
        elif od_client_id and od_redirect_uri:
            logging.error("OneDriveProvider currently only supports 'access_token'. 'client_id' and 'redirect_uri' are not supported.")
    except CloudProviderError:
        logging.exception("Failed to configure OneDrive provider")
    except Exception:
        logging.exception("Unexpected error configuring OneDrive provider")


def _configure_cloud_providers(config: dict | None) -> None:
    """Load cloud provider configuration from config dict and environment."""
    global CLOUD_MANAGER
    CLOUD_MANAGER.reset()

    if not isinstance(config, dict):
        config = {}

    cloud_config = config.get("cloud", {})
    if not isinstance(cloud_config, dict):
        cloud_config = {}

    _configure_google_drive(cloud_config)
    _configure_onedrive(config)

    if not CLOUD_MANAGER.has_providers():
        logging.info("No cloud providers configured")


def _parse_ldap_settings(args, config: dict) -> dict:
    enabled = args.ldap or config.get("ldap", False)
    server = args.ldap_server or config.get("ldap_server")
    base_dn = args.ldap_base_dn or config.get("ldap_base_dn")
    user_template = args.ldap_user_template or config.get(
        "ldap_user_template", "uid={username},{ldap_base_dn}"
    )
    filter_template = args.ldap_filter_template or config.get("ldap_filter_template")
    attributes = args.ldap_attributes or config.get(
        "ldap_attributes", ["cn", "mail", "memberOf"]
    )
    if isinstance(attributes, str):
        attributes = [attr.strip() for attr in attributes.split(",")]
    attribute_map = config.get("ldap_attribute_map", [])
    return {
        "enabled": enabled,
        "server": server,
        "base_dn": base_dn,
        "user_template": user_template,
        "filter_template": filter_template,
        "attributes": attributes,
        "attribute_map": attribute_map,
    }


def _apply_feature_flags_from_config(config: dict) -> None:
    if "features" in config:
        features_config = config["features"]
        for feature_name, feature_value in features_config.items():
            FEATURE_FLAGS[feature_name] = bool(feature_value)


def _validate_config_path(config_path: str) -> str:
    """Resolve and validate a CLI config path before reading from disk."""
    if not isinstance(config_path, str) or not config_path.strip():
        raise ValueError("Config path must be a non-empty string")
    if "\0" in config_path:
        raise ValueError("Invalid config path")
    resolved = os.path.realpath(os.path.abspath(config_path.strip()))
    if not os.path.isfile(resolved):
        raise FileNotFoundError(f"Config file not found: {config_path}")
    return resolved


def _load_config_dict(args) -> dict:
    global CONFIG_FILE
    if not args.config:
        return {}
    CONFIG_FILE = _validate_config_path(args.config)
    with open(CONFIG_FILE, encoding="utf-8") as f:
        return json.load(f)


def _apply_access_tokens(args, config: dict) -> tuple[bool, bool]:
    """Apply ACCESS_TOKEN and ADMIN_TOKEN; return (access_explicit, admin_explicit)."""
    global ACCESS_TOKEN, ADMIN_TOKEN

    token_provided_explicitly = bool(
        args.token or config.get("token") or os.environ.get("AIRD_ACCESS_TOKEN")
    )
    admin_token_provided_explicitly = bool(
        args.admin_token or config.get("admin_token")
    )

    ACCESS_TOKEN = (
        args.token
        or config.get("token")
        or os.environ.get("AIRD_ACCESS_TOKEN")
        or secrets.token_urlsafe(64)
    )
    ADMIN_TOKEN = (
        args.admin_token or config.get("admin_token") or secrets.token_urlsafe(64)
    )
    return token_provided_explicitly, admin_token_provided_explicitly


def _apply_server_settings(args, config: dict) -> None:
    global ROOT_DIR, PORT, MULTI_USER, WORKERS, SSL_CERT, SSL_KEY, ADMIN_USERS, HOSTNAME

    ROOT_DIR = args.root or config.get("root") or os.getcwd()
    PORT = args.port or config.get("port") or 8000
    MULTI_USER = args.multi_user or config.get("multi_user", False)
    workers_arg = args.workers if args.workers is not None else config.get("workers")
    WORKERS = int(workers_arg) if workers_arg is not None else None
    SSL_CERT = args.ssl_cert or config.get("ssl_cert")
    SSL_KEY = args.ssl_key or config.get("ssl_key")
    ADMIN_USERS = config.get("admin_users", [])
    HOSTNAME = args.hostname or config.get("hostname") or socket.getfqdn()


def _apply_ldap_globals(ldap_settings: dict) -> None:
    global LDAP_ENABLED, LDAP_SERVER, LDAP_BASE_DN, LDAP_USER_TEMPLATE
    global LDAP_FILTER_TEMPLATE, LDAP_ATTRIBUTES, LDAP_ATTRIBUTE_MAP

    LDAP_ENABLED = ldap_settings["enabled"]
    LDAP_SERVER = ldap_settings["server"]
    LDAP_BASE_DN = ldap_settings["base_dn"]
    LDAP_USER_TEMPLATE = ldap_settings["user_template"]
    LDAP_FILTER_TEMPLATE = ldap_settings["filter_template"]
    LDAP_ATTRIBUTES = ldap_settings["attributes"]
    LDAP_ATTRIBUTE_MAP = ldap_settings["attribute_map"]


def _print_generated_tokens(
    token_provided_explicitly: bool, admin_token_provided_explicitly: bool
) -> None:
    if not token_provided_explicitly:
        print(f"\n{'='*60}")
        print(f"Access token (generated): {ACCESS_TOKEN}")
        print(f"{'='*60}")
        print("Note: Copy the token above exactly as shown .")
        print("WARNING: Store this token securely. It grants access to your files.")
        print(f"{'='*60}\n")
    if not admin_token_provided_explicitly:
        print(f"\n{'='*60}")
        print(f"Admin token (generated): {ADMIN_TOKEN}")
        print("WARNING: Store this token securely. It grants admin access.")
        print(f"{'='*60}\n")


def init_config():
    """
    Initializes the application configuration by parsing command-line arguments,
    reading a config file, and setting environment variables.
    """
    global CONFIG_FILE, ROOT_DIR, PORT, ACCESS_TOKEN, ADMIN_TOKEN, LDAP_ENABLED, LDAP_SERVER
    global LDAP_BASE_DN, LDAP_USER_TEMPLATE, LDAP_FILTER_TEMPLATE, LDAP_ATTRIBUTES
    global LDAP_ATTRIBUTE_MAP, HOSTNAME, SSL_CERT, SSL_KEY, ADMIN_USERS, FEATURE_FLAGS, CLOUD_MANAGER
    global MULTI_USER, WORKERS
    global BREVO_API_KEY, BREVO_SENDER_EMAIL, BREVO_SENDER_NAME, PUBLIC_BASE_URL

    parser = argparse.ArgumentParser(description="Run Aird")
    parser.add_argument("--config", help="Path to JSON config file")
    parser.add_argument("--root", help="Root directory to serve")
    parser.add_argument("--port", type=int, help="Port to listen on")
    parser.add_argument("--token", help="Access token for login")
    parser.add_argument("--admin-token", help="Access token for admin login")
    parser.add_argument(
        "--ldap", action="store_true", help="Enable LDAP authentication"
    )
    parser.add_argument("--ldap-server", help="LDAP server address")
    parser.add_argument("--ldap-base-dn", help="LDAP base DN for user search")
    parser.add_argument(
        "--ldap-user-template",
        help="LDAP user template (default: uid={username},{ldap_base_dn})",
    )
    parser.add_argument(
        "--ldap-filter-template", help="LDAP filter template for user search"
    )
    parser.add_argument(
        "--ldap-attributes", help="LDAP attributes to retrieve (comma-separated)"
    )
    parser.add_argument("--hostname", help="Host name for the server")
    parser.add_argument("--ssl-cert", help="Path to SSL certificate file")
    parser.add_argument("--ssl-key", help="Path to SSL private key file")
    parser.add_argument(
        "-mu",
        "--multi-user",
        action="store_true",
        help="Enable multi-user mode (each user gets a private home directory)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="HTTP worker processes (default: ceil(1.25 * threads_per_core * physical_cores); 1 on Windows)",
    )
    args = parser.parse_args()

    config = _load_config_dict(args)

    _configure_cloud_providers(config)

    token_provided_explicitly, admin_token_provided_explicitly = _apply_access_tokens(
        args, config
    )
    _apply_server_settings(args, config)

    ldap_settings = _parse_ldap_settings(args, config)
    _apply_ldap_globals(ldap_settings)

    _apply_feature_flags_from_config(config)
    _apply_brevo_settings(config)

    _print_generated_tokens(token_provided_explicitly, admin_token_provided_explicitly)
