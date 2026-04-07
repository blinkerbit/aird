import traceback
import logging
import os
import secrets
import socket
import sqlite3
import ssl

import tornado.ioloop
import tornado.web


import aird.constants as constants
import aird.config as config
from aird.app_context import AppContext
from aird.core.events import (
    EventBus,
    ShareCreatedEvent,
    TransferStartedEvent,
    UserAuthenticatedEvent,
)
from aird.db import (
    get_all_network_shares,
    init_db,
    load_allowed_extensions,
    load_feature_flags,
    load_upload_config,
    assign_admin_privileges,
    cleanup_expired_shares,
    save_allowed_extensions,
)
from aird.services import (
    AuditService,
    ConfigService,
    EventLoggingSubscriber,
    EventMetricsSubscriber,
    FavoritesService,
    NetworkShareService,
    P2PSignalingService,
    QuotaService,
    ShareService,
    UserService,
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
    FavoriteToggleAPIHandler,
    FavoritesListAPIHandler,
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
    NoCacheStaticFileHandler,
    RootHandler,
    EditViewHandler,
)
from aird.handlers.p2p_handlers import (
    P2PRoomManager,
    P2PTransferHandler,
    P2PSignalingHandler,
)

# Set up module logger
logger = logging.getLogger(__name__)

RUST_AVAILABLE = False
HybridFileHandler = None
HybridCompressionHandler = None


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
    settings["static_path"] = os.path.join(os.path.dirname(__file__), "static")
    settings.setdefault("static_url_prefix", "/static/")
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

    app_context: AppContext | None = settings.get("app_context")
    if app_context is None:
        app_context = _build_app_context()
        settings["app_context"] = app_context

    # Backward-compatible keys for handlers still reading from settings directly.
    settings["db_conn"] = app_context.db_conn
    settings["feature_flags"] = app_context.feature_flags
    settings["cloud_manager"] = app_context.cloud_manager
    settings["network_share_manager"] = app_context.network_share_manager
    settings["room_manager"] = app_context.room_manager
    settings["event_bus"] = app_context.event_bus
    settings["event_metrics"] = app_context.event_metrics
    settings["services"] = app_context.services

    if ldap_enabled:
        login_handler = LDAPLoginHandler
    else:
        login_handler = LoginHandler

    # Build routes list
    routes = [
        (
            r"/static/(.*)",
            NoCacheStaticFileHandler,
            {"path": settings["static_path"]},
        ),
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
        (r"/api/favorites/toggle", FavoriteToggleAPIHandler),
        (r"/api/favorites", FavoritesListAPIHandler),
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


def _create_emergency_db_connection() -> None:
    logger.warning("Database connection is None, attempting to create...")
    try:
        constants.DB_PATH = os.path.join(
            os.path.expanduser("~"), ".local", "aird", "aird.sqlite3"
        )
        os.makedirs(os.path.dirname(constants.DB_PATH), exist_ok=True)
        constants.DB_CONN = sqlite3.connect(constants.DB_PATH, check_same_thread=False)
        init_db(constants.DB_CONN)
        logger.info(f"Created emergency database connection: {constants.DB_CONN}")
    except Exception as db_error:
        logger.error(f"Failed to create emergency database connection: {db_error}")


def _load_and_merge_configs(db_conn) -> None:
    # Keep legacy helper function references for test compatibility while
    # preserving behavior equivalent to ConfigService.merge_from_db.
    persisted_flags = load_feature_flags(db_conn)
    if persisted_flags:
        for key, value in persisted_flags.items():
            constants.FEATURE_FLAGS[key] = bool(value)
            logger.debug("Feature flag '%s' set to %s from database", key, bool(value))

    persisted_upload = load_upload_config(db_conn)
    if persisted_upload:
        for key, value in persisted_upload.items():
            constants.UPLOAD_CONFIG[key] = int(value)
            logger.debug("Upload config '%s' set to %s from database", key, int(value))
    constants.MAX_FILE_SIZE = constants.UPLOAD_CONFIG["max_file_size_mb"] * 1024 * 1024

    constants.UPLOAD_ALLOWED_EXTENSIONS = load_allowed_extensions(db_conn)
    if not constants.UPLOAD_ALLOWED_EXTENSIONS:
        constants.UPLOAD_ALLOWED_EXTENSIONS = set(constants.ALLOWED_UPLOAD_EXTENSIONS)
        save_allowed_extensions(db_conn, constants.UPLOAD_ALLOWED_EXTENSIONS)
        logger.info("Seeded upload allowed extensions from defaults")

    logger.info("Final feature flags:")
    for key, value in constants.FEATURE_FLAGS.items():
        logger.info("  %s: %s", key, value)
    logger.info(
        "Max upload file size: %s MB",
        constants.UPLOAD_CONFIG["max_file_size_mb"],
    )


def _auto_start_network_shares(db_conn) -> None:
    constants.NETWORK_SHARE_MANAGER = NetworkShareManager()
    try:
        enabled_shares = [
            s for s in get_all_network_shares(db_conn) if s.get("enabled")
        ]
        for share in enabled_shares:
            constants.NETWORK_SHARE_MANAGER.start_share(share)
        if enabled_shares:
            logger.info("Auto-started %d network share(s)", len(enabled_shares))
    except Exception as ns_err:
        logger.warning("Failed to auto-start network shares: %s", ns_err)


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

        _load_and_merge_configs(constants.DB_CONN)

        # Database-only persistence for shares
        logger.info("Shares are now persisted directly in database")

        # Assign admin privileges to configured admin users
        assign_admin_privileges(constants.DB_CONN, config.ADMIN_USERS)

        _auto_start_network_shares(constants.DB_CONN)

        # Ensure database connection is working
        if constants.DB_CONN is None:
            _create_emergency_db_connection()

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


def _build_app_context() -> AppContext:
    """Construct application-level dependencies in one place."""
    room_manager = P2PRoomManager()
    event_bus = EventBus()
    event_metrics = EventMetricsSubscriber()
    event_logging = EventLoggingSubscriber()
    event_bus.subscribe(UserAuthenticatedEvent, event_metrics.on_user_authenticated)
    event_bus.subscribe(UserAuthenticatedEvent, event_logging.on_user_authenticated)
    event_bus.subscribe(ShareCreatedEvent, event_metrics.on_share_created)
    event_bus.subscribe(ShareCreatedEvent, event_logging.on_share_created)
    event_bus.subscribe(TransferStartedEvent, event_metrics.on_transfer_started)
    event_bus.subscribe(TransferStartedEvent, event_logging.on_transfer_started)
    services = {
        "audit_service": AuditService(),
        "config_service": ConfigService(),
        "favorites_service": FavoritesService(),
        "network_share_service": NetworkShareService(),
        "p2p_signaling_service": P2PSignalingService(room_manager),
        "quota_service": QuotaService(),
        "share_service": ShareService(),
        "user_service": UserService(),
    }
    return AppContext(
        db_conn=constants.DB_CONN,
        feature_flags=constants.FEATURE_FLAGS,
        cloud_manager=constants.CLOUD_MANAGER,
        network_share_manager=constants.NETWORK_SHARE_MANAGER,
        room_manager=room_manager,
        event_bus=event_bus,
        event_metrics=event_metrics,
        services=services,
    )


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
            tornado.ioloop.IOLoop.current().call_later(
                3600, _run_cleanup_expired_shares
            )
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
    constants.MULTI_USER = config.MULTI_USER

    if constants.MULTI_USER:
        logger.info(
            "Multi-user mode ENABLED — each user gets a private home folder under %s",
            constants.ROOT_DIR,
        )
    else:
        logger.info("Single-user mode — all users share root: %s", constants.ROOT_DIR)

    cookie_secret = secrets.token_urlsafe(64)
    settings = {
        "cookie_secret": cookie_secret,
        "xsrf_cookies": True,
        "login_url": "/login",
        "admin_login_url": "/admin/login",
        "cloud_manager": constants.CLOUD_MANAGER,
    }

    _init_database()
    settings["app_context"] = _build_app_context()

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
