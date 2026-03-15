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
    MAX_UPLOAD_FILE_SIZE_HARD_LIMIT as _MAX_UPLOAD_FILE_SIZE_HARD_LIMIT,
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
DB_CONN = None
MAX_FILE_SIZE = _MAX_FILE_SIZE
MAX_READABLE_FILE_SIZE = _MAX_READABLE_FILE_SIZE
ALLOWED_UPLOAD_EXTENSIONS = _ALLOWED_UPLOAD_EXTENSIONS
MMAP_MIN_SIZE = _MMAP_MIN_SIZE
CHUNK_SIZE = _CHUNK_SIZE
MAX_UPLOAD_FILE_SIZE_HARD_LIMIT = _MAX_UPLOAD_FILE_SIZE_HARD_LIMIT


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
    gd_token_file = gdrive_config.get("token_file")

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
            CLOUD_MANAGER.register(
                GoogleDriveProvider(
                    credentials_file=gd_credentials_file,
                    token_file=gd_token_file,
                    root_id=gdrive_root,
                    include_shared_drives=bool(include_shared),
                )
            )
    except CloudProviderError as exc:
        logging.error("Failed to configure Google Drive provider: %s", exc)
    except Exception as exc:
        logging.error("Unexpected error configuring Google Drive provider: %s", exc)


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
    od_client_secret = onedrive_config.get("client_secret")
    od_redirect_uri = onedrive_config.get("redirect_uri")
    od_token_file = onedrive_config.get("token_file")

    try:
        if onedrive_token:
            CLOUD_MANAGER.register(OneDriveProvider(onedrive_token, drive_id=drive_id))
        elif od_client_id and od_redirect_uri:
            CLOUD_MANAGER.register(
                OneDriveProvider(
                    client_id=od_client_id,
                    client_secret=od_client_secret,
                    redirect_uri=od_redirect_uri,
                    token_file=od_token_file,
                    drive_id=drive_id,
                )
            )
    except CloudProviderError as exc:
        logging.error("Failed to configure OneDrive provider: %s", exc)
    except Exception as exc:
        logging.error("Unexpected error configuring OneDrive provider: %s", exc)


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


def init_config():
    """
    Initializes the application configuration by parsing command-line arguments,
    reading a config file, and setting environment variables.
    """
    global CONFIG_FILE, ROOT_DIR, PORT, ACCESS_TOKEN, ADMIN_TOKEN, LDAP_ENABLED, LDAP_SERVER
    global LDAP_BASE_DN, LDAP_USER_TEMPLATE, LDAP_FILTER_TEMPLATE, LDAP_ATTRIBUTES
    global LDAP_ATTRIBUTE_MAP, HOSTNAME, SSL_CERT, SSL_KEY, ADMIN_USERS, FEATURE_FLAGS, CLOUD_MANAGER

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
    args = parser.parse_args()

    config = {}
    if args.config:
        CONFIG_FILE = args.config
        with open(CONFIG_FILE) as f:
            config = json.load(f)
    else:
        config = {}

    _configure_cloud_providers(config)

    ROOT_DIR = args.root or config.get("root") or os.getcwd()
    PORT = args.port or config.get("port") or 8000

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

    ldap_settings = _parse_ldap_settings(args, config)
    LDAP_ENABLED = ldap_settings["enabled"]
    LDAP_SERVER = ldap_settings["server"]
    LDAP_BASE_DN = ldap_settings["base_dn"]
    LDAP_USER_TEMPLATE = ldap_settings["user_template"]
    LDAP_FILTER_TEMPLATE = ldap_settings["filter_template"]
    LDAP_ATTRIBUTES = ldap_settings["attributes"]
    LDAP_ATTRIBUTE_MAP = ldap_settings["attribute_map"]

    _apply_feature_flags_from_config(config)

    SSL_CERT = args.ssl_cert or config.get("ssl_cert")
    SSL_KEY = args.ssl_key or config.get("ssl_key")

    ADMIN_USERS = config.get("admin_users", [])

    HOSTNAME = args.hostname or config.get("hostname") or socket.getfqdn()

    # Print tokens when they were not explicitly provided (masked for security)
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
