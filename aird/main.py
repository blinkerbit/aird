import traceback
import json
import logging
import os
import secrets
import socket
import sqlite3
import ssl
import sys
import threading
import time

import tornado.ioloop
import tornado.web
import tornado.websocket

import aird.constants as constants
import aird.config as config
from aird.db import (
    load_allowed_extensions,
    save_allowed_extensions,
    get_all_network_shares,
    load_feature_flags,
    init_db,
    load_upload_config,
    start_ldap_sync_scheduler,
    assign_admin_privileges,
    cleanup_expired_shares,
)
from aird.database.db import get_data_dir
from aird.network_share_manager import NetworkShareManager
from aird.handlers.admin_handlers import (
    AdminAuditHandler,
    AdminHandler,
    AdminNetworkShareDeleteHandler,
    AdminNetworkSharesHandler,
    AdminNetworkShareToggleHandler,
    AdminUsersHandler,
    LDAPConfigCreateHandler,
    LDAPConfigDeleteHandler,
    LDAPConfigEditHandler,
    LDAPConfigHandler,
    LDAPSyncHandler,
    UserCreateHandler,
    UserDeleteHandler,
    UserEditHandler,
    WebSocketStatsHandler,
)
from aird.handlers.api_handlers import (
    FeatureFlagSocketHandler,
    FileListAPIHandler,
    FileStreamHandler,
    ShareDetailsAPIHandler,
    ShareDetailsByIdAPIHandler,
    ShareListAPIHandler,
    SuperSearchHandler,
    SuperSearchWebSocketHandler,
    UserSearchAPIHandler,
)
from aird.handlers.auth_handlers import (
    AdminLoginHandler,
    LDAPLoginHandler,
    LoginHandler,
    LogoutHandler,
    ProfileHandler,
)
from aird.handlers.file_op_handlers import (
    CloudUploadHandler,
    CopyHandler,
    CreateFolderHandler,
    DeleteHandler,
    EditHandler,
    MoveHandler,
    BulkHandler,
    RenameHandler,
    UploadHandler,
)
from aird.handlers.health_handler import HealthHandler
from aird.handlers.share_handlers import (
    ShareCreateHandler,
    ShareFilesHandler,
    ShareRevokeHandler,
    ShareUpdateHandler,
    SharedFileHandler,
    SharedListHandler,
    TokenVerificationHandler,
)
from aird.handlers.view_handlers import (
    CloudDownloadHandler,
    CloudFilesHandler,
    CloudProvidersHandler,
    MainHandler,
    RootHandler,
    EditViewHandler,
)
from aird.handlers.p2p_handlers import (
    P2PTransferHandler,
    P2PSignalingHandler,
)

# Set up module logger
logger = logging.getLogger(__name__)

# Secure password hashing (Priority 1)
try:
    from argon2 import PasswordHasher
    from argon2 import exceptions as argon2_exceptions

    ARGON2_AVAILABLE = True
    PH = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
except Exception:
    ARGON2_AVAILABLE = False
    PH = None

RUST_AVAILABLE = False
HybridFileHandler = None
HybridCompressionHandler = None

# Import handlers from modules


def make_app(
    settings,
    ldap_enabled=False,
    ldap_server=None,
    ldap_base_dn=None,
    ldap_user_template=None,
    ldap_filter_template=None,
    ldap_attributes=None,
    ldap_attribute_map=None,
    admin_users=None,
):
    settings["template_path"] = os.path.join(os.path.dirname(__file__), "templates")
    # Limit request size to avoid Tornado rejecting large uploads with
    # "Content-Length too long" before our handler can respond.
    settings.setdefault("max_body_size", constants.MAX_UPLOAD_FILE_SIZE_HARD_LIMIT)
    settings.setdefault("max_buffer_size", constants.MAX_UPLOAD_FILE_SIZE_HARD_LIMIT)

    if ldap_enabled:
        settings["ldap_server"] = ldap_server
        settings["ldap_base_dn"] = ldap_base_dn
        settings["ldap_user_template"] = ldap_user_template
        settings["ldap_filter_template"] = ldap_filter_template
        settings["ldap_attributes"] = ldap_attributes
        settings["ldap_attribute_map"] = ldap_attribute_map

    # Add admin users configuration to settings
    if admin_users:
        settings["admin_users"] = admin_users

    # Inject global dependencies into application settings
    import aird.constants as constants_module

    settings["db_conn"] = constants_module.DB_CONN
    settings["feature_flags"] = constants_module.FEATURE_FLAGS
    settings["cloud_manager"] = constants_module.CLOUD_MANAGER
    settings["network_share_manager"] = constants_module.NETWORK_SHARE_MANAGER

    if ldap_enabled:
        login_handler = LDAPLoginHandler
    else:
        login_handler = LoginHandler

    # Build routes list
    routes = [
        (r"/", RootHandler),
        (r"/health", HealthHandler),
        (r"/login", login_handler),
        (r"/logout", LogoutHandler),
        (r"/profile", ProfileHandler),
        (r"/admin/login", AdminLoginHandler),
        (r"/admin", AdminHandler),
        (r"/admin/users", AdminUsersHandler),
        (r"/admin/users/create", UserCreateHandler),
        (r"/admin/users/edit/([0-9]+)", UserEditHandler),
        (r"/admin/users/delete", UserDeleteHandler),
        (r"/admin/websocket-stats", WebSocketStatsHandler),
        (r"/admin/audit", AdminAuditHandler),
        (r"/admin/network-shares", AdminNetworkSharesHandler),
        (r"/admin/network-shares/delete", AdminNetworkShareDeleteHandler),
        (r"/admin/network-shares/toggle", AdminNetworkShareToggleHandler),
        (r"/stream/(.*)", FileStreamHandler),
        (r"/features", FeatureFlagSocketHandler),
        (r"/upload", UploadHandler),
        (r"/mkdir", CreateFolderHandler),
        (r"/delete", DeleteHandler),
        (r"/rename", RenameHandler),
        (r"/copy", CopyHandler),
        (r"/move", MoveHandler),
        (r"/api/bulk", BulkHandler),
        (r"/edit/(.*)", EditViewHandler),
        (r"/edit", EditHandler),
        (r"/api/files/(.*)", FileListAPIHandler),
        (r"/api/users/search", UserSearchAPIHandler),
        (r"/api/cloud/providers", CloudProvidersHandler),
        (r"/api/cloud/([a-z0-9_\-]+)/files", CloudFilesHandler),
        (r"/api/cloud/([a-z0-9_\-]+)/download", CloudDownloadHandler),
        (r"/api/cloud/([a-z0-9_\-]+)/upload", CloudUploadHandler),
        (r"/api/share/details", ShareDetailsAPIHandler),
        (r"/api/share/details_by_id", ShareDetailsByIdAPIHandler),
        (r"/share", ShareFilesHandler),
        (r"/share/create", ShareCreateHandler),
        (r"/share/revoke", ShareRevokeHandler),
        (r"/share/list", ShareListAPIHandler),
        (r"/share/update", ShareUpdateHandler),
        (r"/shared/([A-Za-z0-9_\-]+)/verify", TokenVerificationHandler),
        (r"/shared/([A-Za-z0-9_\-]+)", SharedListHandler),
        (r"/shared/([A-Za-z0-9_\-]+)/file/(.*)", SharedFileHandler),
        (r"/search", SuperSearchHandler),
        (r"/search/ws", SuperSearchWebSocketHandler),
        (r"/p2p", P2PTransferHandler),
        (r"/p2p/signal", P2PSignalingHandler),
        (r"/files/(.*)", MainHandler),
    ]

    # Add LDAP routes only if LDAP is enabled
    if ldap_enabled:
        routes.extend(
            [
                (r"/admin/ldap", LDAPConfigHandler),
                (r"/admin/ldap/create", LDAPConfigCreateHandler),
                (r"/admin/ldap/edit/([0-9]+)", LDAPConfigEditHandler),
                (r"/admin/ldap/delete", LDAPConfigDeleteHandler),
                (r"/admin/ldap/sync", LDAPSyncHandler),
            ]
        )

    return tornado.web.Application(routes, **settings)


def print_banner():
    """Log ASCII art banner for aird"""
    banner = """
 █████╗ ██╗██████╗ ██████╗ 
██╔══██╗██║██╔══██╗██╔══██╗
███████║██║██████╔╝██║  ██║
██╔══██║██║██╔══██╗██║  ██║
██║  ██║██║██║  ██║██████╔╝
╚═╝  ╚═╝╚═╝╚═╝  ╚═╝╚═════╝ 
"""
    print(banner)


def _validate_ldap_config() -> bool:
    """Return False (logging errors) if LDAP is enabled but mis-configured."""
    if not config.LDAP_ENABLED:
        return True
    checks = [
        (config.LDAP_SERVER, "--ldap-server"),
        (config.LDAP_BASE_DN, "--ldap-base-dn"),
        (config.LDAP_USER_TEMPLATE, "--ldap-user-template"),
        (config.LDAP_FILTER_TEMPLATE, "--ldap-filter-template"),
        (config.LDAP_ATTRIBUTES, "--ldap-attributes"),
    ]
    for value, flag in checks:
        if not value:
            logger.error("LDAP is enabled, but %s is not configured.", flag)
            return False
    return True


def _validate_ssl_config() -> bool:
    """Return False (logging errors) if SSL certificate/key config is invalid."""
    if config.SSL_CERT and not config.SSL_KEY:
        logger.error(
            "SSL certificate provided but SSL key is missing. "
            "Both --ssl-cert and --ssl-key are required for SSL."
        )
        return False
    if config.SSL_KEY and not config.SSL_CERT:
        logger.error(
            "SSL key provided but SSL certificate is missing. "
            "Both --ssl-cert and --ssl-key are required for SSL."
        )
        return False
    if config.SSL_CERT and config.SSL_KEY:
        if not os.path.exists(config.SSL_CERT):
            logger.error(f"SSL certificate file not found: {config.SSL_CERT}")
            return False
        if not os.path.exists(config.SSL_KEY):
            logger.error(f"SSL key file not found: {config.SSL_KEY}")
            return False
    return True


def _init_database() -> None:
    try:
        data_dir = get_data_dir()
        constants.DB_PATH = os.path.join(data_dir, "aird.sqlite3")
        db_exists = os.path.exists(constants.DB_PATH)
        logger.info(f"SQLite database path: {constants.DB_PATH}")
        logger.info(
            f"Database already exists: {'Yes' if db_exists else 'No (will be created)'}"
        )
        constants.DB_CONN = sqlite3.connect(constants.DB_PATH, check_same_thread=False)
        init_db(constants.DB_CONN)
        # Load persisted feature flags and merge
        persisted_flags = load_feature_flags(constants.DB_CONN)
        if persisted_flags:
            for k, v in persisted_flags.items():
                constants.FEATURE_FLAGS[k] = bool(v)
                logger.debug(f"Feature flag '{k}' set to {bool(v)} from database")

        # Log final feature flags status
        logger.info("Final feature flags:")
        for k, v in constants.FEATURE_FLAGS.items():
            logger.info(f"  {k}: {v}")

        # Start LDAP sync scheduler
        start_ldap_sync_scheduler(constants.DB_CONN)
        # Database-only persistence for shares
        logger.info("Shares are now persisted directly in database")

        # Load persisted upload config and merge
        persisted_upload_config = load_upload_config(constants.DB_CONN)
        if persisted_upload_config:
            for k, v in persisted_upload_config.items():
                constants.UPLOAD_CONFIG[k] = int(v)
                logger.debug(f"Upload config '{k}' set to {int(v)} from database")
        constants.MAX_FILE_SIZE = (
            constants.UPLOAD_CONFIG["max_file_size_mb"] * 1024 * 1024
        )
        logger.info(
            f"Max upload file size: {constants.UPLOAD_CONFIG['max_file_size_mb']} MB"
        )
        # Load allowed upload extensions (when "allow all" is off)
        constants.UPLOAD_ALLOWED_EXTENSIONS = load_allowed_extensions(constants.DB_CONN)
        if not constants.UPLOAD_ALLOWED_EXTENSIONS:
            constants.UPLOAD_ALLOWED_EXTENSIONS = set(
                constants.ALLOWED_UPLOAD_EXTENSIONS
            )
            save_allowed_extensions(
                constants.DB_CONN, constants.UPLOAD_ALLOWED_EXTENSIONS
            )
            logger.info("Seeded upload allowed extensions from defaults")

        # Assign admin privileges to configured admin users
        assign_admin_privileges(constants.DB_CONN, config.ADMIN_USERS)

        # Initialize network share manager and auto-start enabled shares
        constants.NETWORK_SHARE_MANAGER = NetworkShareManager()
        try:
            enabled_shares = [
                s for s in get_all_network_shares(constants.DB_CONN) if s.get("enabled")
            ]
            for share in enabled_shares:
                constants.NETWORK_SHARE_MANAGER.start_share(share)
            if enabled_shares:
                logger.info("Auto-started %d network share(s)", len(enabled_shares))
        except Exception as ns_err:
            logger.warning("Failed to auto-start network shares: %s", ns_err)

        # Ensure database connection is working
        if constants.DB_CONN is None:
            logger.warning("Database connection is None, attempting to create...")
            try:
                constants.DB_PATH = os.path.join(
                    os.path.expanduser("~"), ".local", "aird", "aird.sqlite3"
                )
                os.makedirs(os.path.dirname(constants.DB_PATH), exist_ok=True)
                constants.DB_CONN = sqlite3.connect(
                    constants.DB_PATH, check_same_thread=False
                )
                init_db(constants.DB_CONN)
                logger.info(
                    f"Created emergency database connection: {constants.DB_CONN}"
                )
            except Exception as db_error:
                logger.error(
                    f"Failed to create emergency database connection: {db_error}"
                )

    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        constants.DB_CONN = None
        logger.warning("DB_CONN set to None")


def _print_server_urls(port: int, hostname: str, scheme: str) -> None:
    """Print accessible server URLs to stdout."""
    print(f"{scheme}://localhost:{port}/")
    if hostname and hostname != "localhost":
        print(f"{scheme}://{hostname}:{port}/")
    fqdn = socket.getfqdn()
    if fqdn and fqdn != hostname and fqdn != "localhost":
        print(f"{scheme}://{fqdn}:{port}/")


def _run_cleanup_expired_shares():
    if constants.DB_CONN:
        deleted = cleanup_expired_shares(constants.DB_CONN)
        if deleted > 0:
            logger.info(f"Cleaned up {deleted} expired share(s)")
    tornado.ioloop.IOLoop.current().call_later(3600, _run_cleanup_expired_shares)


def _start_server(app, ssl_options, port: int, hostname: str) -> None:
    while True:
        try:
            if ssl_options:
                proto = "https"
                app.listen(
                    port,
                    ssl_options=ssl_options,
                    max_body_size=constants.MAX_UPLOAD_FILE_SIZE_HARD_LIMIT,
                    max_buffer_size=constants.MAX_UPLOAD_FILE_SIZE_HARD_LIMIT,
                )
                logger.info(
                    f"Serving HTTPS on 0.0.0.0 port {port} ({proto}://0.0.0.0:{port}/) ..."
                )
            else:
                proto = "http"
                app.listen(
                    port,
                    max_body_size=constants.MAX_UPLOAD_FILE_SIZE_HARD_LIMIT,
                    max_buffer_size=constants.MAX_UPLOAD_FILE_SIZE_HARD_LIMIT,
                )
                logger.info(
                    f"Serving HTTP on 0.0.0.0 port {port} ({proto}://0.0.0.0:{port}/) ..."
                )
            _print_server_urls(port, hostname, proto)
            tornado.ioloop.IOLoop.current().call_later(3600, _run_cleanup_expired_shares)
            tornado.ioloop.IOLoop.current().start()
            break
        except OSError:
            port += 1


def main():
    print_banner()
    config.init_config()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")

    if not _validate_ldap_config():
        return
    if not _validate_ssl_config():
        return

    constants.ACCESS_TOKEN = config.ACCESS_TOKEN
    constants.ADMIN_TOKEN = config.ADMIN_TOKEN
    constants.ROOT_DIR = os.path.abspath(config.ROOT_DIR)

    cookie_secret = secrets.token_urlsafe(64)
    settings = {
        "cookie_secret": cookie_secret,
        "xsrf_cookies": True,
        "login_url": "/login",
        "admin_login_url": "/admin/login",
        "cloud_manager": constants.CLOUD_MANAGER,
    }

    _init_database()

    app = make_app(
        settings,
        config.LDAP_ENABLED,
        config.LDAP_SERVER,
        config.LDAP_BASE_DN,
        config.LDAP_USER_TEMPLATE,
        config.LDAP_FILTER_TEMPLATE,
        config.LDAP_ATTRIBUTES,
        config.LDAP_ATTRIBUTE_MAP,
        config.ADMIN_USERS,
    )

    ssl_options = None
    if config.SSL_CERT and config.SSL_KEY:
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
        ssl_context.load_cert_chain(config.SSL_CERT, config.SSL_KEY)
        ssl_options = ssl_context

    _start_server(app, ssl_options, config.PORT, config.HOSTNAME)


if __name__ == "__main__":
    main()
