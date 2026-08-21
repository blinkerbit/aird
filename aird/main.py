import logging
import os
import socket
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import tornado.web
import logging.handlers

from aird.server_runtime import resolve_worker_count


import aird.constants as constants
import aird.config as config
from aird.app_context import AppContext
from aird.core.events import (
    EventBus,
    PolicyDecisionEvent,
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
    wrap_connection,
)
from aird.services import (
    AuditService,
    ConfigService,
    EmailNotificationSubscriber,
    EmailService,
    EventLoggingSubscriber,
    EventMetricsSubscriber,
    FavoritesService,
    NetworkShareService,
    P2PSignalingService,
    PolicyDecisionMetricsSubscriber,
    PolicyService,
    QuotaService,
    ShareService,
    TagService,
    UserService,
)

from aird.database.db import get_data_dir
from aird.network_share_manager import NetworkShareManager
from aird.handlers.abac_handlers import (
    AdminPoliciesHandler,
    AdminPolicyAPIHandler,
    AdminTagAPIHandler,
    AdminTagColorAPIHandler,
    AdminTagsHandler,
    AdminUserAttributeAPIHandler,
    AdminUserAttributesHandler,
    PolicyDecisionsAPIHandler,
    PolicyDecisionsWebSocket,
)
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
    UserPasswordResetHandler,
    WebSocketStatsHandler,
)
from aird.handlers.api_handlers import (
    FavoriteToggleAPIHandler,
    FavoritesListAPIHandler,
    FeatureFlagAPIHandler,
    FeatureFlagSocketHandler,
    RuntimeConfigAPIHandler,
    RuntimeConfigSocketHandler,
    FileListAPIHandler,
    FolderSizeAPIHandler,
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
    MandatoryPasswordHandler,
    ProfileHandler,
    SessionListAPIHandler,
    SessionRevokeAPIHandler,
)
from aird.handlers.webauthn_handlers import (
    WebAuthnAuthOptionsHandler,
    WebAuthnAuthVerifyHandler,
    WebAuthnCredentialDeleteHandler,
    WebAuthnRegisterOptionsHandler,
    WebAuthnRegisterVerifyHandler,
    WebAuthnStatusHandler,
)
from aird.handlers.bulk_handlers import BulkTicketHandler, BulkWebSocketHandler
from aird.handlers.file_op_handlers import (
    CloudUploadHandler,
    CopyHandler,
    CreateFolderHandler,
    DeleteHandler,
    EditHandler,
    MoveHandler,
    BulkHandler,
    DownloadZipHandler,
    RenameHandler,
    UploadHandler,
    UploadSessionPrepareHandler,
)
from aird.handlers.health_handler import HealthHandler, ServiceWorkerHandler
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
    TaggedFilesHandler,
)
from aird.handlers.p2p_handlers import (
    P2PRoomManager,
    P2PTransferHandler,
    P2PSignalingHandler,
)

# Set up module logger
logger = logging.getLogger(__name__)


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
    # Tornado prepends its automatic static route when static_path is set.
    # Configure that route directly; the explicit /static route below is not
    # necessarily the first match.
    settings["static_handler_class"] = NoCacheStaticFileHandler
    # Limit request size to avoid Tornado rejecting large uploads with
    # "Content-Length too long" before our handler can respond.
    settings.setdefault("max_body_size", constants.UPLOAD_REQUEST_MAX_BODY_SIZE)
    settings.setdefault("max_buffer_size", constants.UPLOAD_REQUEST_MAX_BODY_SIZE)

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
        (r"/sw-transfer.js", ServiceWorkerHandler),
        (r"/login", login_handler),
        (r"/logout", LogoutHandler),
        (r"/auth/mandatory-password", MandatoryPasswordHandler),
        (r"/profile", ProfileHandler),
        (r"/api/sessions", SessionListAPIHandler),
        (r"/api/sessions/([^/]+)/revoke", SessionRevokeAPIHandler),
        (r"/api/webauthn/status", WebAuthnStatusHandler),
        (r"/api/webauthn/register/options", WebAuthnRegisterOptionsHandler),
        (r"/api/webauthn/register/verify", WebAuthnRegisterVerifyHandler),
        (r"/api/webauthn/auth/options", WebAuthnAuthOptionsHandler),
        (r"/api/webauthn/auth/verify", WebAuthnAuthVerifyHandler),
        (r"/api/webauthn/credentials/([0-9]+)", WebAuthnCredentialDeleteHandler),
        (r"/tagged/([^/]+)", TaggedFilesHandler),
        (r"/admin/login", AdminLoginHandler),
        (r"/admin", AdminHandler),
        (r"/admin/users", AdminUsersHandler),
        (r"/admin/users/create", UserCreateHandler),
        (r"/admin/users/edit/([0-9]+)", UserEditHandler),
        (r"/admin/users/delete", UserDeleteHandler),
        (r"/admin/users/reset-password", UserPasswordResetHandler),
        (r"/admin/websocket-stats", WebSocketStatsHandler),
        (r"/admin/audit", AdminAuditHandler),
        (r"/admin/network-shares", AdminNetworkSharesHandler),
        (r"/admin/network-shares/delete", AdminNetworkShareDeleteHandler),
        (r"/admin/network-shares/toggle", AdminNetworkShareToggleHandler),
        (r"/admin/tags", AdminTagsHandler),
        (r"/admin/api/abac/tags", AdminTagAPIHandler),
        (r"/admin/api/abac/tag-colors", AdminTagColorAPIHandler),
        (r"/admin/policies", AdminPoliciesHandler),
        (r"/admin/api/abac/policies", AdminPolicyAPIHandler),
        (r"/admin/api/abac/policies/([0-9]+)", AdminPolicyAPIHandler),
        (r"/admin/api/abac/decisions", PolicyDecisionsAPIHandler),
        (r"/admin/user-attributes", AdminUserAttributesHandler),
        (r"/admin/api/abac/user-attributes", AdminUserAttributeAPIHandler),
        (r"/ws/policy-decisions", PolicyDecisionsWebSocket),
        (r"/stream/(.*)", FileStreamHandler),
        (r"/ws/bulk", BulkWebSocketHandler),
        (r"/api/folder-size", FolderSizeAPIHandler),
        (r"/features", FeatureFlagSocketHandler),
        (r"/api/features", FeatureFlagAPIHandler),
        (r"/runtime-config", RuntimeConfigSocketHandler),
        (r"/api/runtime-config", RuntimeConfigAPIHandler),
        (r"/upload", UploadHandler),
        (r"/api/upload/session", UploadSessionPrepareHandler),
        (r"/mkdir", CreateFolderHandler),
        (r"/delete", DeleteHandler),
        (r"/rename", RenameHandler),
        (r"/copy", CopyHandler),
        (r"/move", MoveHandler),
        (r"/api/bulk/ticket", BulkTicketHandler),
        (r"/api/bulk", BulkHandler),
        (r"/api/download/zip", DownloadZipHandler),
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
    try:
        print(banner)
    except UnicodeEncodeError:
        print("AIRD")


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


def _open_db_connection(db_path: str):
    """Open SQLite and wrap for free-threaded (nogil) safety."""
    raw = sqlite3.connect(db_path, check_same_thread=False)
    return wrap_connection(raw)


def _create_emergency_db_connection() -> None:
    logger.warning("Database connection is None, attempting to create...")
    try:
        constants.DB_PATH = os.path.join(
            os.path.expanduser("~"), ".local", "aird", "aird.sqlite3"
        )
        os.makedirs(os.path.dirname(constants.DB_PATH), exist_ok=True)
        constants.DB_CONN = _open_db_connection(constants.DB_PATH)
        init_db(constants.DB_CONN)
        logger.info(f"Created emergency database connection: {constants.DB_CONN}")
    except Exception:
        logger.exception("Failed to create emergency database connection")


def _load_and_merge_configs(db_conn) -> None:
    # Keep legacy helper function references for test compatibility while
    # preserving behavior equivalent to ConfigService.merge_from_db.
    from aird.services.config_service import ConfigService

    config_service = ConfigService()
    persisted_flags = load_feature_flags(db_conn)
    if persisted_flags:
        for key, value in persisted_flags.items():
            constants.FEATURE_FLAGS[key] = bool(value)
            logger.debug("Feature flag '%s' set to %s from database", key, bool(value))

    config_service.sync_upload_config_from_db(db_conn)
    config_service.sync_runtime_config_from_db(db_conn)

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

    from aird.core.rate_limit import TransferRateLimiter

    TransferRateLimiter.apply_transfer_config(constants.TRANSFER_CONFIG)


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
        constants.DB_CONN = _open_db_connection(constants.DB_PATH)
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

    except Exception:
        logger.exception("Database initialization failed")
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


from aird.core.upload_sessions import cleanup_stale_sessions


def _run_cleanup_expired_shares():
    if constants.DB_CONN:
        deleted = cleanup_expired_shares(constants.DB_CONN)
        if deleted > 0:
            logger.info(f"Cleaned up {deleted} expired share(s)")
        from aird.db.sessions import cleanup_expired_sessions

        expired_sessions = cleanup_expired_sessions(constants.DB_CONN)
        if expired_sessions > 0:
            logger.info("Cleaned up %d expired login session(s)", expired_sessions)
    try:
        cleanup_stale_sessions()
    except Exception:
        logger.debug("upload session cleanup failed", exc_info=True)


def _build_app_context() -> AppContext:
    """Construct application-level dependencies in one place."""
    room_manager = P2PRoomManager()
    event_bus = EventBus()
    event_metrics = EventMetricsSubscriber()
    event_logging = EventLoggingSubscriber()
    policy_metrics = PolicyDecisionMetricsSubscriber()
    event_bus.subscribe(UserAuthenticatedEvent, event_metrics.on_user_authenticated)
    event_bus.subscribe(UserAuthenticatedEvent, event_logging.on_user_authenticated)
    email_service = EmailService()
    email_subscriber = EmailNotificationSubscriber(email_service)
    event_bus.subscribe(ShareCreatedEvent, event_metrics.on_share_created)
    event_bus.subscribe(ShareCreatedEvent, event_logging.on_share_created)
    event_bus.subscribe(ShareCreatedEvent, email_subscriber.on_share_created)
    event_bus.subscribe(TransferStartedEvent, event_metrics.on_transfer_started)
    event_bus.subscribe(TransferStartedEvent, event_logging.on_transfer_started)
    event_bus.subscribe(PolicyDecisionEvent, event_logging.on_policy_decision)
    event_bus.subscribe(PolicyDecisionEvent, policy_metrics.on_policy_decision)

    tag_service = TagService()
    policy_service = PolicyService(tag_service, event_bus=event_bus)

    services = {
        "audit_service": AuditService(),
        "config_service": ConfigService(),
        "favorites_service": FavoritesService(),
        "network_share_service": NetworkShareService(),
        "p2p_signaling_service": P2PSignalingService(room_manager),
        "policy_service": policy_service,
        "policy_decision_metrics": policy_metrics,
        "quota_service": QuotaService(),
        "share_service": ShareService(),
        "tag_service": tag_service,
        "user_service": UserService(),
        "email_service": email_service,
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


def _build_application():
    """Create the Tornado app (call after _init_database in each process)."""
    from aird.core.auth_secrets import resolve_cookie_secret

    cookie_secret, _ = resolve_cookie_secret(constants.ROOT_DIR)
    os.environ.setdefault("AIRD_COOKIE_SECRET", cookie_secret)
    settings = {
        "cookie_secret": cookie_secret,
        "xsrf_cookies": True,
        "login_url": "/login",
        "admin_login_url": "/admin/login",
        "cloud_manager": constants.CLOUD_MANAGER,
    }
    settings["app_context"] = _build_app_context()
    return make_app(
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


def _maybe_start_bulk_server(app, http_port: int) -> None:
    """Enable bulk transfers for WireGuard / LAN.

    Browser and CLI use ``/ws/bulk`` on the main HTTP port. A separate raw TCP
    listener is started only when ``AIRD_BULK_PORT`` is explicitly set.
    """
    env_port = os.environ.get("AIRD_BULK_PORT", "").strip()
    from aird.core.bulk.engine import BulkEngine
    from aird.core.bulk.tcp_server import BulkTcpServer

    app_ctx = app.settings.get("app_context")
    secret = app.settings.get("cookie_secret")
    if not app_ctx or not secret:
        logger.warning("Bulk transfer disabled: missing app context or cookie secret")
        return
    engine = BulkEngine(app_ctx, secret)
    app.settings["bulk_engine"] = engine

    if not env_port:
        logger.info(
            "Bulk transfers on HTTP port %s via /ws/bulk "
            "(set AIRD_BULK_PORT for optional raw TCP)",
            http_port,
        )
        return

    bulk_port = int(env_port)
    server = BulkTcpServer(engine, port=bulk_port)
    server.start()
    app.settings["bulk_tcp_server"] = server


def _run_cleanup_loop_background() -> None:
    """Periodic share/session cleanup (socketify has no Tornado IOLoop.call_later)."""
    import threading
    import time

    def _loop() -> None:
        while True:
            time.sleep(3600)
            try:
                _run_cleanup_expired_shares()
            except Exception:
                logger.exception("periodic cleanup failed")

    threading.Thread(target=_loop, name="aird-cleanup", daemon=True).start()


def _run_microservices(http_port: int, ssl_options) -> None:
    """Default serving mode: Granian features + socketify transfer (no Tornado listen)."""
    import subprocess
    import sys
    import time

    from aird.event_loop import apply_io_thread_pool

    apply_io_thread_pool()
    _run_cleanup_loop_background()

    transfer_port = int(os.environ.get("AIRD_TRANSFER_PORT_HTTP", "0") or "0")
    if transfer_port <= 0:
        transfer_port = int(http_port) + 1
    os.environ["AIRD_TRANSFER_PORT_HTTP"] = str(transfer_port)
    os.environ.setdefault("AIRD_FEATURES_PORT", str(http_port))
    # Keep backends loopback-only; public ingress is Caddy.
    os.environ.setdefault("AIRD_FEATURES_HOST", "127.0.0.1")
    os.environ.setdefault("AIRD_TRANSFER_HOST", "127.0.0.1")
    os.environ.setdefault("AIRD_BROWSE_HOST", "127.0.0.1")
    # Local convenience only when no gateway mesh secret was provided.
    if not os.environ.get("AIRD_MESH_SECRET", "").strip():
        os.environ.setdefault("AIRD_ALLOW_INSECURE_MESH", "1")
        os.environ.setdefault("AIRD_DEV_OPEN_AUTH", "1")

    logger.info(
        "Microservices mode: features(Granian)=:%s transfer(socketify)=:%s",
        http_port,
        transfer_port,
    )
    if ssl_options:
        logger.warning(
            "TLS on the monolith path is unused in microservices mode; "
            "terminate TLS at Caddy (deploy/Caddyfile) instead"
        )

    env = os.environ.copy()
    env["AIRD_TRANSFER_PORT_HTTP"] = str(transfer_port)
    transfer_proc = subprocess.Popen(
        [sys.executable, "-m", "aird.ms.transfer"],
        env=env,
    )
    time.sleep(0.4)
    if transfer_proc.poll() is not None:
        raise RuntimeError("aird-transfer failed to start")

    try:
        from aird.ms.features import main as features_main

        # features main reads AIRD_FEATURES_PORT
        os.environ["AIRD_FEATURES_PORT"] = str(http_port)
        features_main()
    finally:
        transfer_proc.terminate()
        try:
            transfer_proc.wait(timeout=5)
        except Exception:
            transfer_proc.kill()


def _run_http_server(app, ssl_options, http_port: int) -> None:
    """Serve the app.

    Default: microservices (Granian features + socketify transfer).
    Legacy monolith: ``AIRD_MODE=monolith``.
    """
    mode = os.environ.get("AIRD_MODE", "microservices").strip().lower()
    if mode in ("ms", "microservices", "microservice", ""):
        _run_microservices(http_port, ssl_options)
        return
    if mode not in ("monolith", "legacy", "socketify"):
        logger.warning("Unknown AIRD_MODE=%r — using microservices", mode)
        _run_microservices(http_port, ssl_options)
        return

    from aird.event_loop import apply_io_thread_pool
    from aird.web.socketify_server import run_socketify_app

    apply_io_thread_pool()
    _maybe_start_bulk_server(app, http_port)
    _run_cleanup_loop_background()
    run_socketify_app(app, host="0.0.0.0", port=http_port, ssl_options=ssl_options)


def _start_server(ssl_options, port: int, hostname: str) -> None:
    _MAX_PORT_RETRIES = 3
    proto = "https" if ssl_options else "http"
    for attempt in range(_MAX_PORT_RETRIES):
        try:
            logger.info(
                "Serving %s on 0.0.0.0 port %d (microservices: Granian+socketify) ...",
                proto.upper(),
                port,
            )
            _init_database()
            app = _build_application()
            _print_server_urls(port, hostname, proto)
            _run_http_server(app, ssl_options, port)
            return
        except OSError:
            logger.exception("Failed to bind on port %d", port)
            if attempt < _MAX_PORT_RETRIES - 1:
                port += 1
                logger.warning(
                    "Retrying on port %d (%d/%d)", port, attempt + 2, _MAX_PORT_RETRIES
                )
            else:
                logger.error(
                    "Could not bind after %d attempts. Set a different --port and retry.",
                    _MAX_PORT_RETRIES,
                )
                raise


def main():
    from aird.event_loop import install_uvloop_if_linux

    install_uvloop_if_linux()

    print_banner()
    config.init_config()

    log_file = os.path.join(get_data_dir(), "aird.log")
    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=10 * 1024 * 1024, backupCount=5
    )
    console_handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s - %(levelname)s:%(name)s:%(message)s")
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    for handler in root_logger.handlers:
        root_logger.removeHandler(handler)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    logger.info("Logging initialized. Writing logs to %s", log_file)

    gil_checker = getattr(sys, "_is_gil_enabled", None)
    if callable(gil_checker) and not gil_checker():
        logger.info(
            "Free-threaded Python runtime detected (GIL disabled). "
            "With 50 MiB HTTP read chunks + FastUploadWriter this can outperform "
            "GIL builds on LAN/WireGuard stream uploads — prefer whichever "
            "interpreter measures faster on your host."
        )

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

    if not os.environ.get("AIRD_COOKIE_SECRET"):
        from aird.core.auth_secrets import describe_ephemeral_secret, secrets_dir_for_root

        secrets_dir = secrets_dir_for_root(constants.ROOT_DIR)
        cookie_path = secrets_dir / "cookie_secret"
        logger.warning(
            "%s",
            describe_ephemeral_secret(
                "Cookie secret",
                secrets_dir,
                "cookie_secret",
                created=not cookie_path.is_file(),
            ),
        )

    ssl_options = None
    if config.SSL_CERT and config.SSL_KEY:
        # socketify AppOptions wants PEM paths (not ssl.SSLContext).
        ssl_options = {
            "keyfile": config.SSL_KEY,
            "certfile": config.SSL_CERT,
        }

    # Warn if stale --workers / AIRD_WORKERS config is present.
    resolve_worker_count(config.WORKERS)
    _start_server(ssl_options, config.PORT, config.HOSTNAME)


if __name__ == "__main__":
    main()
