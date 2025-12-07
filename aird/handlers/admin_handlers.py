import tornado.web
import json
import time
import re

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
    load_feature_flags,
)
from aird.constants import (
    FEATURE_FLAGS,
    WEBSOCKET_CONFIG,
)
import aird.constants as constants_module
from aird.utils.util import get_current_websocket_config


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
        
        self.render("admin.html",
                   features=current_features,
                   websocket_config=current_websocket_config,
                   ldap_enabled=ldap_enabled)

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
        
        # Persist both feature flags and WebSocket configuration
        try:
            db_conn = constants_module.DB_CONN
            if db_conn is not None:
                save_feature_flags(db_conn, FEATURE_FLAGS)
                save_websocket_config(db_conn, WEBSOCKET_CONFIG)
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
            
        if len(password) < 6:
            self.render("user_create.html", error="Password must be at least 6 characters")
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
                
            if password and len(password) < 6:
                self.render("user_edit.html", user=user, error="Password must be at least 6 characters")
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
            self.render("user_edit.html", user=user, error=f"Error updating user: {str(e)}")

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
            self.render("ldap_config_create.html", error=f"Error creating configuration: {str(e)}")

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
            self.render("ldap_config_edit.html", config=config, error=f"Error updating configuration: {str(e)}")

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

