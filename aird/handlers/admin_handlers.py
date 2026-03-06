import csv
import io
import logging
import os
import re
import secrets as _secrets
import socket as _socket
import time

import json
import tornado.web

from aird.handlers.base_handler import BaseHandler
from aird.db import (
    get_all_users,
    create_user,
    update_user,
    delete_user,
    get_all_ldap_configs,
    get_ldap_sync_logs,
    create_ldap_config,
    get_ldap_config_by_id,
    update_ldap_config,
    delete_ldap_config,
    save_feature_flags,
    save_websocket_config,
    save_upload_config,
    load_feature_flags,
    get_audit_logs,
    load_allowed_extensions,
    save_allowed_extensions,
    create_network_share,
    get_all_network_shares,
    update_network_share,
    delete_network_share,
    log_audit,
)
from aird.constants import (
    FEATURE_FLAGS,
    WEBSOCKET_CONFIG,
    UPLOAD_CONFIG,
)
import aird.constants as constants_module
from aird.utils.util import get_current_websocket_config
from aird.core.security import validate_password


class AdminHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self):
        if not self.is_admin_user():
            self.redirect("/admin/login")
            return
        
        # Get current feature flags from SQLite for consistency
        current_features = {}
        db_conn = constants_module.DB_CONN
        if db_conn is not None:
            try:
                persisted_flags = load_feature_flags(db_conn)
                if persisted_flags:
                    current_features = persisted_flags.copy()
                    # Merge with any runtime changes
                    for k, v in FEATURE_FLAGS.items():
                        current_features[k] = bool(v)
                else:
                    current_features = FEATURE_FLAGS.copy()
            except Exception:
                current_features = FEATURE_FLAGS.copy()
        else:
            current_features = FEATURE_FLAGS.copy()

        # Get current WebSocket configuration
        current_websocket_config = get_current_websocket_config()
        
        # Check if LDAP is enabled
        ldap_enabled = self.settings.get('ldap_server') is not None
        # For "allowed file types" UI: list of options and currently allowed set
        db_conn = constants_module.DB_CONN
        allowed_current = load_allowed_extensions(db_conn) if db_conn else set(constants_module.UPLOAD_ALLOWED_EXTENSIONS)
        available_extensions = sorted(constants_module.ALLOWED_UPLOAD_EXTENSIONS)
        
        self.render("admin.html",
                   features=current_features,
                   websocket_config=current_websocket_config,
                   upload_config=UPLOAD_CONFIG,
                   ldap_enabled=ldap_enabled,
                   available_extensions=available_extensions,
                   allowed_extensions_current=allowed_current)

    @tornado.web.authenticated
    def post(self):
        if not self.is_admin_user():
            self.set_status(403)
            self.write("Access denied: You don't have permission to perform this action")
            return
        
        FEATURE_FLAGS["file_upload"] = self.get_argument("file_upload", "off") == "on"
        FEATURE_FLAGS["file_delete"] = self.get_argument("file_delete", "off") == "on"
        FEATURE_FLAGS["file_rename"] = self.get_argument("file_rename", "off") == "on"
        FEATURE_FLAGS["file_download"] = self.get_argument("file_download", "off") == "on"
        FEATURE_FLAGS["file_edit"] = self.get_argument("file_edit", "off") == "on"
        FEATURE_FLAGS["file_share"] = self.get_argument("file_share", "off") == "on"
        FEATURE_FLAGS["super_search"] = self.get_argument("super_search", "off") == "on"
        FEATURE_FLAGS["compression"] = self.get_argument("compression", "off") == "on"
        FEATURE_FLAGS["p2p_transfer"] = self.get_argument("p2p_transfer", "off") == "on"
        FEATURE_FLAGS["folder_create"] = self.get_argument("folder_create", "off") == "on"
        FEATURE_FLAGS["folder_delete"] = self.get_argument("folder_delete", "off") == "on"
        
        # Update WebSocket configuration
        websocket_config = {}
        try:
            # Parse and validate WebSocket settings
            websocket_config["feature_flags_max_connections"] = max(1, min(1000, int(self.get_argument("feature_flags_max_connections", "50"))))
            websocket_config["feature_flags_idle_timeout"] = max(30, min(7200, int(self.get_argument("feature_flags_idle_timeout", "600"))))
            websocket_config["file_streaming_max_connections"] = max(1, min(1000, int(self.get_argument("file_streaming_max_connections", "200"))))
            websocket_config["file_streaming_idle_timeout"] = max(30, min(7200, int(self.get_argument("file_streaming_idle_timeout", "300"))))
            websocket_config["search_max_connections"] = max(1, min(1000, int(self.get_argument("search_max_connections", "100"))))
            websocket_config["search_idle_timeout"] = max(30, min(7200, int(self.get_argument("search_idle_timeout", "180"))))
            
            # Update in-memory configuration
            WEBSOCKET_CONFIG.update(websocket_config)
            
        except (ValueError, TypeError):
            # If parsing fails, use current values
            pass
        
        # Update upload configuration
        try:
            max_file_size_mb = max(1, min(10240, int(self.get_argument("max_file_size_mb", "512"))))
            UPLOAD_CONFIG["max_file_size_mb"] = max_file_size_mb
            constants_module.MAX_FILE_SIZE = max_file_size_mb * 1024 * 1024
            UPLOAD_CONFIG["allow_all_file_types"] = 1 if self.get_argument("allow_all_file_types", "off") == "on" else 0
        except (ValueError, TypeError):
            pass

        # Persist feature flags, WebSocket, and upload configuration
        try:
            db_conn = constants_module.DB_CONN
            if db_conn is not None:
                save_feature_flags(db_conn, FEATURE_FLAGS)
                save_websocket_config(db_conn, WEBSOCKET_CONFIG)
                save_upload_config(db_conn, UPLOAD_CONFIG)
                # When "allow all file types" is off, persist selected extensions from checkboxes
                if not UPLOAD_CONFIG.get("allow_all_file_types"):
                    selected_extensions = {e for e in self.get_arguments("allow_ext") if e and e.startswith(".")}
                    save_allowed_extensions(db_conn, selected_extensions)
                    constants_module.UPLOAD_ALLOWED_EXTENSIONS = selected_extensions
        except Exception:
            pass

        from aird.handlers.api_handlers import FeatureFlagSocketHandler
        FeatureFlagSocketHandler.send_updates()
        self.redirect("/admin")

class WebSocketStatsHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self):
        """Return WebSocket connection statistics"""
        if not self.is_admin_user():
            self.set_status(403)
            self.write("Forbidden")
            return
            
        from aird.handlers.api_handlers import FeatureFlagSocketHandler
        from aird.handlers.api_handlers import FileStreamHandler
        from aird.handlers.api_handlers import SuperSearchWebSocketHandler
        stats = {
            'feature_flags': FeatureFlagSocketHandler.connection_manager.get_stats(),
            'file_streaming': FileStreamHandler.connection_manager.get_stats(),
            'super_search': SuperSearchWebSocketHandler.connection_manager.get_stats(),
            'timestamp': time.time()
        }
        
        self.set_header('Content-Type', 'application/json')
        self.write(json.dumps(stats, indent=2))


class AdminAuditHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self):
        """Display audit log (admin only). ?format=csv for export."""
        if not self.is_admin_user():
            self.redirect("/admin/login")
            return
        db_conn = constants_module.DB_CONN
        limit = min(1000, max(1, int(self.get_argument("limit", "500"))))
        offset = max(0, int(self.get_argument("offset", "0")))
        if self.get_argument("format", "") == "csv":
            self.set_header("Content-Type", "text/csv")
            self.set_header("Content-Disposition", "attachment; filename=audit_log.csv")
            rows = get_audit_logs(db_conn, limit=10000, offset=0)
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["id", "created_at", "username", "action", "details", "ip"])
            for r in rows:
                w.writerow([r.get("id"), r.get("created_at"), r.get("username") or "", r.get("action") or "", r.get("details") or "", r.get("ip") or ""])
            self.write(buf.getvalue())
            return
        logs = get_audit_logs(db_conn, limit=limit, offset=offset)
        self.render("admin_audit.html", logs=logs, limit=limit, offset=offset, logs_count=len(logs))


class AdminUsersHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self):
        """Display user management interface"""
        if not self.is_admin_user():
            self.redirect("/admin/login")
            return
            
        users = []
        db_conn = constants_module.DB_CONN
        if db_conn is not None:
            users = get_all_users(db_conn)
            
        self.render("admin_users.html", users=users)

class UserCreateHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self):
        """Show create user form"""
        if not self.is_admin_user():
            self.set_status(403)
            self.write("Access denied: You don't have permission to perform this action")
            return
            
        self.render("user_create.html", error=None)
    
    @tornado.web.authenticated
    def post(self):
        """Create a new user"""
        if not self.is_admin_user():
            self.set_status(403)
            self.write("Access denied: You don't have permission to perform this action")
            return
        
        db_conn = constants_module.DB_CONN
        if db_conn is None:
            self.render("user_create.html", error="Database not available")
            return
            
        username = self.get_argument("username", "").strip()
        password = self.get_argument("password", "").strip()
        role = self.get_argument("role", "user").strip()
        
        # Input validation
        if not username or not password:
            self.render("user_create.html", error="Username and password are required")
            return
            
        if len(username) < 3 or len(username) > 50:
            self.render("user_create.html", error="Username must be between 3 and 50 characters")
            return
            
        is_valid, error = validate_password(password)
        if not is_valid:
            self.render("user_create.html", error=error)
            return
            
        if role not in ['user', 'admin']:
            self.render("user_create.html", error="Invalid role")
            return
            
        # Check for valid username format (alphanumeric + underscore/hyphen)
        if not re.match(r'^[a-zA-Z0-9_-]+$', username):
            self.render("user_create.html", error="Username can only contain letters, numbers, underscores, and hyphens")
            return
            
        try:
            create_user(db_conn, username, password, role)
            self.redirect("/admin/users")
        except ValueError as e:
            self.render("user_create.html", error=str(e))
        except Exception as e:
            self.render("user_create.html", error="Failed to create user")

class UserEditHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self, user_id):
        """Show edit user form"""
        if not self.is_admin_user():
            self.set_status(403)
            self.write("Access denied: You don't have permission to perform this action")
            return
        
        db_conn = constants_module.DB_CONN
        if db_conn is None:
            self.set_status(500)
            self.write("Service temporarily unavailable: Database connection error")
            return
            
        try:
            user_id = int(user_id)
            # Get user by ID
            users = get_all_users(db_conn)
            user = next((u for u in users if u['id'] == user_id), None)
            
            if not user:
                self.set_status(404)
                self.write("User not found: The requested user does not exist")
                return
                
            self.render("user_edit.html", user=user, error=None)
        except ValueError:
            self.set_status(400)
            self.write("Invalid request: Please provide a valid user ID")
    
    @tornado.web.authenticated
    def post(self, user_id):
        """Update user information"""
        if not self.is_admin_user():
            self.set_status(403)
            self.write("Access denied: You don't have permission to perform this action")
            return
        
        db_conn = constants_module.DB_CONN
        if db_conn is None:
            self.set_status(500)
            self.write("Service temporarily unavailable: Database connection error")
            return
            
        try:
            user_id = int(user_id)
            # Get existing user
            users = get_all_users(db_conn)
            user = next((u for u in users if u['id'] == user_id), None)
            
            if not user:
                self.set_status(404)
                self.write("User not found: The requested user does not exist")
                return
            
            username = self.get_argument("username", "").strip()
            password = self.get_argument("password", "").strip()
            role = self.get_argument("role", "user").strip()
            active = self.get_argument("active", "off") == "on"
            
            # Input validation
            if not username:
                self.render("user_edit.html", user=user, error="Username is required")
                return
                
            if len(username) < 3 or len(username) > 50:
                self.render("user_edit.html", user=user, error="Username must be between 3 and 50 characters")
                return
                
            if password:
                is_valid, error = validate_password(password)
                if not is_valid:
                    self.render("user_edit.html", user=user, error=error)
                    return
                
            if role not in ['user', 'admin']:
                self.render("user_edit.html", user=user, error="Invalid role")
                return
            
            # Check for valid username format
            if not re.match(r'^[a-zA-Z0-9_-]+$', username):
                self.render("user_edit.html", user=user, error="Username can only contain letters, numbers, underscores, and hyphens")
                return
            
            # Update user
            update_data = {
                'username': username,
                'role': role,
                'active': active
            }
            
            # Check if LDAP is enabled - disable password updates for LDAP users
            if password and self.settings.get('ldap_server'):
                self.render("user_edit.html", user=user, error="Password changes are not allowed for LDAP users. Please change the password through the LDAP directory.")
                return
            
            if password:  # Only update password if provided
                update_data['password'] = password
                
            if update_user(db_conn, user_id, **update_data):
                self.redirect("/admin/users")
            else:
                self.render("user_edit.html", user=user, error="Failed to update user")
                
        except ValueError:
            self.set_status(400)
            self.write("Invalid request: Please provide a valid user ID")
        except Exception as e:
            logging.error(f"User update error: {e}")
            self.render("user_edit.html", user=user, error="Error updating user. Please try again.")

class UserDeleteHandler(BaseHandler):
    @tornado.web.authenticated
    def post(self):
        """Delete a user"""
        if not self.is_admin_user():
            self.set_status(403)
            self.write("Access denied: You don't have permission to perform this action")
            return
        
        db_conn = constants_module.DB_CONN
        if db_conn is None:
            self.set_status(500)
            self.write("Service temporarily unavailable: Database connection error")
            return
            
        try:
            user_id = int(self.get_argument("user_id", "0"))
            
            if user_id <= 0:
                self.set_status(400)
                self.write("Invalid user ID")
                return
                
            if delete_user(db_conn, user_id):
                self.redirect("/admin/users")
            else:
                self.set_status(404)
                self.write("User not found: The requested user does not exist")
                
        except ValueError:
            self.set_status(400)
            self.write("Invalid request: Please provide a valid user ID")

class LDAPConfigHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self):
        """Display LDAP configuration management interface"""
        if not self.is_admin_user():
            self.redirect("/admin/login")
            return
            
        configs = []
        sync_logs = []
        db_conn = constants_module.DB_CONN
        if db_conn is not None:
            configs = get_all_ldap_configs(db_conn)
            sync_logs = get_ldap_sync_logs(db_conn, limit=20)
            
        self.render("admin_ldap.html", configs=configs, sync_logs=sync_logs)

class LDAPConfigCreateHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self):
        """Show create LDAP configuration form"""
        if not self.is_admin_user():
            self.set_status(403)
            self.write("Access denied: You don't have permission to perform this action")
            return
            
        self.render("ldap_config_create.html", error=None)
    
    @tornado.web.authenticated
    def post(self):
        """Create a new LDAP configuration"""
        if not self.is_admin_user():
            self.set_status(403)
            self.write("Access denied: You don't have permission to perform this action")
            return
        
        db_conn = constants_module.DB_CONN
        if db_conn is None:
            self.render("ldap_config_create.html", error="Database not available")
            return
            
        name = self.get_argument("name", "").strip()
        server = self.get_argument("server", "").strip()
        ldap_base_dn = self.get_argument("ldap_base_dn", "").strip()
        ldap_member_attributes = self.get_argument("ldap_member_attributes", "member").strip()
        user_template = self.get_argument("user_template", "").strip()
        
        # Input validation
        if not all([name, server, ldap_base_dn, user_template]):
            self.render("ldap_config_create.html", error="All fields are required")
            return
            
        if len(name) < 3 or len(name) > 50:
            self.render("ldap_config_create.html", error="Configuration name must be between 3 and 50 characters")
            return
            
        try:
            create_ldap_config(db_conn, name, server, ldap_base_dn, ldap_member_attributes, user_template)
            self.redirect("/admin/ldap")
        except ValueError as e:
            self.render("ldap_config_create.html", error=str(e))
        except Exception as e:
            logging.error(f"LDAP config creation error: {e}")
            self.render("ldap_config_create.html", error="Error creating configuration. Please try again.")

class LDAPConfigEditHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self, config_id):
        """Show edit LDAP configuration form"""
        if not self.is_admin_user():
            self.set_status(403)
            self.write("Access denied: You don't have permission to perform this action")
            return
        
        db_conn = constants_module.DB_CONN
        if db_conn is None:
            self.set_status(500)
            self.write("Service temporarily unavailable: Database connection error")
            return
            
        try:
            config_id = int(config_id)
            config = get_ldap_config_by_id(db_conn, config_id)
            
            if not config:
                self.set_status(404)
                self.write("Configuration not found: The requested configuration does not exist")
                return
                
            self.render("ldap_config_edit.html", config=config, error=None)
        except ValueError:
            self.write("Invalid request: Please provide a valid configuration ID")
    
    @tornado.web.authenticated
    def post(self, config_id):
        """Update LDAP configuration"""
        if not self.is_admin_user():
            self.set_status(403)
            self.write("Access denied: You don't have permission to perform this action")
            return
        
        db_conn = constants_module.DB_CONN
        if db_conn is None:
            self.set_status(500)
            self.write("Service temporarily unavailable: Database connection error")
            return
            
        try:
            config_id = int(config_id)
            config = get_ldap_config_by_id(db_conn, config_id)
            
            if not config:
                self.set_status(404)
                self.write("Configuration not found: The requested configuration does not exist")
                return
            
            name = self.get_argument("name", "").strip()
            server = self.get_argument("server", "").strip()
            ldap_base_dn = self.get_argument("ldap_base_dn", "").strip()
            ldap_member_attributes = self.get_argument("ldap_member_attributes", "member").strip()
            user_template = self.get_argument("user_template", "").strip()
            active = self.get_argument("active", "off") == "on"
            
            # Input validation
            if not all([name, server, ldap_base_dn, user_template]):
                self.render("ldap_config_edit.html", config=config, error="All fields are required")
                return
                
            if len(name) < 3 or len(name) > 50:
                self.render("ldap_config_edit.html", config=config, error="Configuration name must be between 3 and 50 characters")
                return
            
            # Update configuration
            if update_ldap_config(db_conn, config_id, 
                                 name=name, server=server, ldap_base_dn=ldap_base_dn,
                                 ldap_member_attributes=ldap_member_attributes, 
                                 user_template=user_template, active=active):
                self.redirect("/admin/ldap")
            else:
                self.render("ldap_config_edit.html", config=config, error="Failed to update configuration")
                
        except ValueError:
            self.write("Invalid request: Please provide a valid configuration ID")
        except Exception as e:
            logging.error(f"LDAP config update error: {e}")
            self.render("ldap_config_edit.html", config=config, error="Error updating configuration. Please try again.")

class LDAPConfigDeleteHandler(BaseHandler):
    @tornado.web.authenticated
    def post(self):
        """Delete LDAP configuration"""
        if not self.is_admin_user():
            self.set_status(403)
            self.write("Access denied: You don't have permission to perform this action")
            return
        
        db_conn = constants_module.DB_CONN
        if db_conn is None:
            self.set_status(500)
            self.write("Service temporarily unavailable: Database connection error")
            return
            
        try:
            config_id = int(self.get_argument("config_id", "0"))
            
            if config_id <= 0:
                self.set_status(400)
                self.write("Invalid request: Please provide a valid configuration ID")
                return
                
            if delete_ldap_config(db_conn, config_id):
                self.redirect("/admin/ldap")
            else:
                self.set_status(404)
                self.write("Configuration not found: The requested configuration does not exist")
                
        except ValueError:
            self.set_status(400)
            self.write("Invalid request: Please provide a valid configuration ID")

class LDAPSyncHandler(BaseHandler):
    @tornado.web.authenticated
    def post(self):
        if not self.is_admin_user():
            self.set_status(403)
            self.write({"error": "Access denied"})
            return
        
        # In a real application, you would trigger the LDAP sync here.
        # For now, we'll just return a success message.
        self.write({"status": "Sync started"})


# -------------------------------------------------------
# Network Shares (SMB / WebDAV)
# -------------------------------------------------------


class AdminNetworkSharesHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self):
        if not self.is_admin_user():
            self.redirect("/admin/login")
            return
        db_conn = constants_module.DB_CONN
        shares = get_all_network_shares(db_conn) if db_conn else []
        mgr = constants_module.NETWORK_SHARE_MANAGER
        for s in shares:
            s["running"] = mgr.is_running(s["id"]) if mgr else False
        error = self.get_argument("error", None)
        # Resolve a network-reachable address for mount commands
        server_host = _socket.getfqdn()
        if server_host in ("localhost", "localhost.localdomain", ""):
            try:
                server_host = _socket.gethostbyname(_socket.gethostname())
            except Exception:
                server_host = "127.0.0.1"
        self.render("admin_network_shares.html", shares=shares, error=error, server_host=server_host)

    @tornado.web.authenticated
    def post(self):
        if not self.is_admin_user():
            self.set_status(403)
            return
        db_conn = constants_module.DB_CONN
        if db_conn is None:
            self.redirect("/admin/network-shares?error=Database+unavailable")
            return

        name = self.get_argument("name", "").strip()
        folder_path = self.get_argument("folder_path", "").strip()
        protocol = self.get_argument("protocol", "webdav").strip().lower()
        username = self.get_argument("share_username", "").strip()
        password = self.get_argument("share_password", "").strip()
        read_only = self.get_argument("read_only", "off") == "on"

        try:
            port = int(self.get_argument("port", "8443"))
        except (ValueError, TypeError):
            port = 8443

        if not name or not folder_path or not username or not password:
            self.redirect("/admin/network-shares?error=All+fields+are+required")
            return
        if protocol not in ("smb", "webdav"):
            self.redirect("/admin/network-shares?error=Invalid+protocol")
            return
        if not os.path.isdir(folder_path):
            self.redirect("/admin/network-shares?error=Folder+does+not+exist")
            return
        if port < 1 or port > 65535:
            self.redirect("/admin/network-shares?error=Port+must+be+1-65535")
            return

        share_id = _secrets.token_urlsafe(8)
        ok = create_network_share(db_conn, share_id, name, folder_path, protocol, port,
                                  username, password, read_only)
        if not ok:
            self.redirect("/admin/network-shares?error=Failed+to+create+share")
            return

        log_audit(db_conn, "network_share_create",
                  username=self.get_display_username(),
                  details=f"name={name} protocol={protocol} port={port} folder={folder_path}",
                  ip=self.request.remote_ip)

        mgr = constants_module.NETWORK_SHARE_MANAGER
        if mgr:
            share_dict = {
                "id": share_id, "name": name, "folder_path": folder_path,
                "protocol": protocol, "port": port, "username": username,
                "password": password, "read_only": read_only,
            }
            mgr.start_share(share_dict)

        self.redirect("/admin/network-shares")


class AdminNetworkShareDeleteHandler(BaseHandler):
    @tornado.web.authenticated
    def post(self):
        if not self.is_admin_user():
            self.set_status(403)
            return
        db_conn = constants_module.DB_CONN
        share_id = self.get_argument("share_id", "").strip()
        if not share_id or db_conn is None:
            self.redirect("/admin/network-shares")
            return

        mgr = constants_module.NETWORK_SHARE_MANAGER
        if mgr:
            mgr.stop_share(share_id)

        delete_network_share(db_conn, share_id)
        log_audit(db_conn, "network_share_delete",
                  username=self.get_display_username(),
                  details=f"share_id={share_id}",
                  ip=self.request.remote_ip)
        self.redirect("/admin/network-shares")


class AdminNetworkShareToggleHandler(BaseHandler):
    @tornado.web.authenticated
    def post(self):
        if not self.is_admin_user():
            self.set_status(403)
            return
        db_conn = constants_module.DB_CONN
        share_id = self.get_argument("share_id", "").strip()
        if not share_id or db_conn is None:
            self.redirect("/admin/network-shares")
            return

        shares = get_all_network_shares(db_conn)
        share = next((s for s in shares if s["id"] == share_id), None)
        if share is None:
            self.redirect("/admin/network-shares")
            return

        new_enabled = not share["enabled"]
        update_network_share(db_conn, share_id, enabled=new_enabled)

        mgr = constants_module.NETWORK_SHARE_MANAGER
        if mgr:
            if new_enabled:
                mgr.start_share({**share, "enabled": True})
            else:
                mgr.stop_share(share_id)

        action = "enabled" if new_enabled else "disabled"
        log_audit(db_conn, "network_share_toggle",
                  username=self.get_display_username(),
                  details=f"share_id={share_id} action={action}",
                  ip=self.request.remote_ip)
        self.redirect("/admin/network-shares")

