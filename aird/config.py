import argparse
import os
import json
import secrets
import socket
import logging
from aird.cloud import CloudManager, CloudProviderError, GoogleDriveProvider, OneDriveProvider
from aird.constants import (
    MAX_FILE_SIZE as _MAX_FILE_SIZE,
    MAX_READABLE_FILE_SIZE as _MAX_READABLE_FILE_SIZE,
    ALLOWED_UPLOAD_EXTENSIONS as _ALLOWED_UPLOAD_EXTENSIONS,
    MMAP_MIN_SIZE as _MMAP_MIN_SIZE,
    CHUNK_SIZE as _CHUNK_SIZE,
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


def _configure_cloud_providers(config: dict | None) -> None:
    """Load cloud provider configuration from config dict and environment."""
    global CLOUD_MANAGER
    CLOUD_MANAGER.reset()

    if not isinstance(config, dict):
        config = {}

    cloud_config = config.get('cloud', {})
    if not isinstance(cloud_config, dict):
        cloud_config = {}

    # Google Drive configuration
    gdrive_config = cloud_config.get('google_drive', {})
    if not isinstance(gdrive_config, dict):
        gdrive_config = {}
    gdrive_token = (
        gdrive_config.get('access_token')
        or os.environ.get('AIRD_GDRIVE_ACCESS_TOKEN')
    )
    gdrive_root = (
        gdrive_config.get('root_id')
        or os.environ.get('AIRD_GDRIVE_ROOT_ID')
        or 'root'
    )
    include_shared = gdrive_config.get('include_shared_drives', True)

    if gdrive_token:
        try:
            CLOUD_MANAGER.register(
                GoogleDriveProvider(
                    gdrive_token,
                    root_id=gdrive_root,
                    include_shared_drives=bool(include_shared),
                )
            )
        except CloudProviderError as exc:
            logging.error("Failed to configure Google Drive provider: %s", exc)

    # OneDrive configuration
    onedrive_config = config.get('one_drive')
    if not isinstance(onedrive_config, dict):
        onedrive_config = config.get('onedrive', {})
        if not isinstance(onedrive_config, dict):
            onedrive_config = {}
    onedrive_token = (
        onedrive_config.get('access_token')
        or os.environ.get('AIRD_ONEDRIVE_ACCESS_TOKEN')
        or os.environ.get('AIRD_ONE_DRIVE_ACCESS_TOKEN')
    )
    drive_id = onedrive_config.get('drive_id') or os.environ.get('AIRD_ONEDRIVE_DRIVE_ID')

    if onedrive_token:
        try:
            CLOUD_MANAGER.register(OneDriveProvider(onedrive_token, drive_id=drive_id))
        except CloudProviderError as exc:
            logging.error("Failed to configure OneDrive provider: %s", exc)

    if not CLOUD_MANAGER.has_providers():
        logging.info("No cloud providers configured")


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
    parser.add_argument("--ldap", action="store_true", help="Enable LDAP authentication")
    parser.add_argument("--ldap-server", help="LDAP server address")
    parser.add_argument("--ldap-base-dn", help="LDAP base DN for user search")
    parser.add_argument("--ldap-user-template", help="LDAP user template (default: uid={username},{ldap_base_dn})")
    parser.add_argument("--ldap-filter-template", help="LDAP filter template for user search")
    parser.add_argument("--ldap-attributes", help="LDAP attributes to retrieve (comma-separated)")
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

    token_provided_explicitly = bool(args.token or config.get("token") or os.environ.get("AIRD_ACCESS_TOKEN"))
    admin_token_provided_explicitly = bool(args.admin_token or config.get("admin_token"))

    ACCESS_TOKEN = args.token or config.get("token") or os.environ.get("AIRD_ACCESS_TOKEN") or secrets.token_urlsafe(64)
    ADMIN_TOKEN = args.admin_token or config.get("admin_token") or secrets.token_urlsafe(64)

    LDAP_ENABLED = args.ldap or config.get("ldap", False)
    LDAP_SERVER = args.ldap_server or config.get("ldap_server")
    LDAP_BASE_DN = args.ldap_base_dn or config.get("ldap_base_dn")
    LDAP_USER_TEMPLATE = args.ldap_user_template or config.get("ldap_user_template", "uid={username},{ldap_base_dn}")
    LDAP_FILTER_TEMPLATE = args.ldap_filter_template or config.get("ldap_filter_template")
    LDAP_ATTRIBUTES = args.ldap_attributes or config.get("ldap_attributes", ["cn", "mail", "memberOf"])
    LDAP_ATTRIBUTE_MAP = config.get("ldap_attribute_map", [])

    if isinstance(LDAP_ATTRIBUTES, str):
        LDAP_ATTRIBUTES = [attr.strip() for attr in LDAP_ATTRIBUTES.split(",")]

    if "features" in config:
        features_config = config["features"]
        for feature_name, feature_value in features_config.items():
            FEATURE_FLAGS[feature_name] = bool(feature_value)

    SSL_CERT = args.ssl_cert or config.get("ssl_cert")
    SSL_KEY = args.ssl_key or config.get("ssl_key")
    
    ADMIN_USERS = config.get("admin_users", [])
    
    HOSTNAME = args.hostname or config.get("hostname") or socket.getfqdn()

    # Print tokens when they were not explicitly provided (masked for security)
    if not token_provided_explicitly:
        masked_token = ACCESS_TOKEN[:4] + "..." + ACCESS_TOKEN[-4:] if len(ACCESS_TOKEN) > 8 else "****"
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
