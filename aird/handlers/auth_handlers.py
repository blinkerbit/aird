
import tornado.web 
import logging
from aird.handlers.base_handler import BaseHandler
from datetime import datetime
import secrets
from ldap3 import Server, Connection
from ldap3.utils.conv import escape_filter_chars
from aird.db import (
    get_user_by_username,
    create_user,
    update_user,
    authenticate_user,
)
import aird.config as config_module
import aird.constants as constants_module


class LDAPLoginHandler(BaseHandler):
    def get(self):
        if self.current_user:
            self.redirect("/files/")
            return
        self.render("login.html", error=None, settings=self.settings)

    def post(self):
        # Input validation
        username = self.get_argument("username", "").strip()
        password = self.get_argument("password", "")
        
        if not username or not password:
            self.render("login.html", error="Username and password are required.", settings=self.settings)
            return
            
        # Basic input length validation
        if len(username) > 256 or len(password) > 256:
            self.render("login.html", error="Invalid input length.", settings=self.settings)
            return
        
        try:
            server = Server(self.settings['ldap_server'])
            # Bind with the provided password (no need to escape for bind DN if template handles it, 
            # but usually username in DN is just placed. However, we are focusing on the search filter injection)
            conn = Connection(server, user=self.settings['ldap_user_template'].format(username=username), password=password, auto_bind=True)
            
            # Escape username for search filter to prevent LDAP injection
            username_escaped = escape_filter_chars(username)
            conn.search(search_base=self.settings['ldap_base_dn'],
             search_filter=self.settings['ldap_filter_template'].format(username=username_escaped),
              attributes=self.settings['ldap_attributes'])

            """
            attribute_map = [{"member":'cn=asdfasdf,dc=com,dc=io'}]
            """
            # authentication 
            
            # authorization logic - check LDAP attribute maps if configured
            ldap_attribute_map = self.settings.get('ldap_attribute_map', [])
            if ldap_attribute_map:
                # If attribute maps are configured, check authorization
                authorized = False
                for attribute_element in ldap_attribute_map:
                    for key, value in attribute_element.items():
                        try:
                            if value in conn.entries[0][key]:
                                authorized = True
                                break
                        except KeyError:
                            continue
                    if authorized:
                        break
                
                if not authorized:
                    self.render("login.html", error="Access denied. You do not have permission to access this system.", settings=self.settings)
                    return
            # If no attribute maps are configured, all LDAP users are authorized
            
            # Only create/update user in Aird's database after successful authorization
            db_conn = constants_module.DB_CONN
            if db_conn:
                existing_user = get_user_by_username(db_conn, username)
                admin_users = self.settings.get('admin_users', [])
                is_admin_user = username in admin_users
                
                if not existing_user:
                    # Create new user in Aird's database for first-time LDAP login
                    try:
                        user_role = 'admin' if is_admin_user else 'user'
                        create_user(db_conn, username, password, role=user_role)
                        logging.info(f"LDAP: Created new user '{username}' from LDAP authentication with role '{user_role}'")
                    except Exception as e:
                        logging.warning(f"LDAP: Warning: Failed to create user '{username}' in database: {e}")
                        # Continue with login even if user creation fails
                else:
                    # Update last login timestamp and check for admin role assignment
                    try:
                        update_user(db_conn, existing_user['id'], last_login=datetime.now().isoformat())
                        logging.info(f"LDAP: Updated last login for user '{username}'")
                        
                        # Check if user should have admin privileges
                        if is_admin_user and existing_user['role'] != 'admin':
                            update_user(db_conn, existing_user['id'], role='admin')
                            logging.info(f"LDAP: Assigned admin privileges to user '{username}'")
                    except Exception as e:
                        logging.warning(f"LDAP: Warning: Failed to update user '{username}': {e}")
            
            # Successful authentication and authorization
            # Get user role for cookie setting
            user_role = 'admin' if is_admin_user else 'user'
            db_conn = constants_module.DB_CONN
            if db_conn:
                existing_user = get_user_by_username(db_conn, username)
                if existing_user:
                    user_role = existing_user['role']
            
            self.set_secure_cookie("user", username, httponly=True, secure=(self.request.protocol == "https"), samesite="Strict")
            self.set_secure_cookie("user_role", user_role, httponly=True, secure=(self.request.protocol == "https"), samesite="Strict")
            self.redirect("/files/")
            return
        except Exception:
            # Generic error message to prevent information disclosure
            self.render("login.html", error="Authentication failed. Please check your credentials.", settings=self.settings)

class LoginHandler(BaseHandler):
    def get(self):
        if self.current_user:
            # Already logged in, redirect to intended destination or files page
            next_url = self.get_argument("next", "/files/")
            logging.info(f"User already authenticated, redirecting to: {next_url}")
            self.redirect(next_url)
            return
        # Not logged in, show login form with next URL preserved
        next_url = self.get_argument("next", "/files/")
        logging.debug(f"Showing login form with next_url: {next_url}")
        self.render("login.html", error=None, settings=self.settings, next_url=next_url)

    def post(self):
        try:
            # Check if it's username/password login or token login
            username = self.get_argument("username", "").strip()
            password = self.get_argument("password", "").strip()
            token = self.get_argument("token", "").strip()
            next_url = self.get_argument("next", "/files/")
            
            # Comprehensive debug logging
            logging.info(f"Login attempt - username: {bool(username)}, password: {bool(password)}, token: {bool(token)}")
            if token:
                logging.info(f"Token provided (length: {len(token)}, starts with: {token[:10] if len(token) >= 10 else token}...)")
        except Exception as e:
            logging.error(f"Error parsing login form data: {e}", exc_info=True)
            self.render("login.html", error="Error processing login request. Please try again.", settings=self.settings, next_url="/files/")
            return
        
        # Try username/password authentication first (if both provided)
        # Access DB_CONN from constants module to ensure we have the latest value
        db_conn = constants_module.DB_CONN
        logging.info(f"DB_CONN available: {db_conn is not None}")
        if username and password and db_conn is not None:
            # Input validation
            if len(username) > 256 or len(password) > 256:
                self.render("login.html", error="Invalid input length.", settings=self.settings, next_url=next_url)
                return
            
            try:
                logging.info(f"Attempting username/password authentication for user: {username}")
                user = authenticate_user(db_conn, username, password)
                if user:
                    logging.info(f"Username/password authentication successful for user: {username}, redirecting to: {next_url}")
                    self.set_secure_cookie("user", username, httponly=True, secure=(self.request.protocol == "https"), samesite="Strict")
                    self.set_secure_cookie("user_role", user['role'], httponly=True, secure=(self.request.protocol == "https"), samesite="Strict")
                    self.redirect(next_url)
                    return
                else:
                    logging.warning(f"Username/password authentication failed for user: {username}")
                    self.render("login.html", error="Invalid username or password.", settings=self.settings, next_url=next_url)
                    return
            except Exception as e:
                logging.error(f"Exception during username/password authentication: {e}", exc_info=True)
                self.render("login.html", error="Authentication failed. Please try again.", settings=self.settings, next_url=next_url)
                return
        
        # Fallback to token authentication
        if not token:
            if username or password:
                self.render("login.html", error="Invalid username or password.", settings=self.settings, next_url=next_url)
            else:
                self.render("login.html", error="Username/password or token is required.", settings=self.settings, next_url=next_url)
            return
            
        if len(token) > 512:  # Reasonable token length limit
            self.render("login.html", error="Invalid token.", settings=self.settings, next_url=next_url)
            return
        
        # Check if ACCESS_TOKEN is configured
        # Access directly from module to ensure we have the latest value
        current_access_token = config_module.ACCESS_TOKEN
        logging.info(f"ACCESS_TOKEN from config_module: {current_access_token is not None} (length: {len(current_access_token) if current_access_token else 0})")
        if not current_access_token:
            logging.error("ACCESS_TOKEN is not configured. Cannot authenticate with token.")
            self.render("login.html", error="Token authentication is not configured.", settings=self.settings, next_url=next_url)
            return
            
        # Normalize token comparison - strip all whitespace and remove surrounding quotes
        normalized_token = token.strip().strip("'\"")
        normalized_access_token = current_access_token.strip().strip("'\"") if current_access_token else ""
        
        # Detailed debug logging
        logging.info(f"Token comparison - submitted length: {len(normalized_token)}, expected length: {len(normalized_access_token)}")
        logging.info(f"Token comparison - submitted first 10 chars: {normalized_token[:10] if len(normalized_token) >= 10 else normalized_token}")
        logging.info(f"Token comparison - expected first 10 chars: {normalized_access_token[:10] if len(normalized_access_token) >= 10 else normalized_access_token}")
        logging.info(f"Tokens match: {normalized_token == normalized_access_token}")
        
        if secrets.compare_digest(normalized_token, normalized_access_token):
            logging.info(f"Token authentication successful, redirecting to: {next_url}")
            self.set_secure_cookie("user", "token_authenticated", httponly=True, secure=(self.request.protocol == "https"), samesite="Strict")
            self.set_secure_cookie("user_role", "admin", httponly=True, secure=(self.request.protocol == "https"), samesite="Strict")  # Token users get admin role
            self.redirect(next_url)
            return
        else:
            # Log failure efficiently without revealing token details
            logging.warning("Token authentication failed. Token mismatch.")
            self.render("login.html", error="Invalid credentials. Try again.", settings=self.settings, next_url=next_url)

class AdminLoginHandler(BaseHandler):
    def get(self):
        if self.is_admin_user():
            self.redirect("/admin")
            return
        self.render("admin_login.html", error=None)

    def post(self):
        # Check if it's username/password login or token login
        username = self.get_argument("username", "").strip()
        password = self.get_argument("password", "").strip()
        token = self.get_argument("token", "").strip()
        
        # Try username/password authentication first (if both provided)
        # Access DB_CONN from constants module to ensure we have the latest value
        db_conn = constants_module.DB_CONN
        if username and password and db_conn is not None:
            # Input validation
            if len(username) > 256 or len(password) > 256:
                self.render("admin_login.html", error="Invalid input length.")
                return
            
            try:
                user = authenticate_user(db_conn, username, password)
                if user and user['role'] == 'admin':
                    # Set both user and admin cookies for admin users
                    self.set_secure_cookie("user", username, httponly=True, secure=(self.request.protocol == "https"), samesite="Strict")
                    self.set_secure_cookie("user_role", user['role'], httponly=True, secure=(self.request.protocol == "https"), samesite="Strict")
                    self.set_secure_cookie("admin", "authenticated", httponly=True, secure=(self.request.protocol == "https"), samesite="Strict")  # Also set admin cookie
                    self.redirect("/admin")
                    return
                elif user and user['role'] != 'admin':
                    self.render("admin_login.html", error="Access denied. Admin privileges required.")
                    return
                else:
                    self.render("admin_login.html", error="Invalid username or password.")
                    return
            except Exception:
                self.render("admin_login.html", error="Authentication failed. Please try again.")
                return
        
        # Fallback to token authentication
        if not token:
            if username or password:
                self.render("admin_login.html", error="Invalid username or password.")
            else:
                self.render("admin_login.html", error="Username/password or token is required.")
            return
            
        if len(token) > 512:  # Reasonable token length limit
            self.render("admin_login.html", error="Invalid token.")
            return
        
        # Check if ADMIN_TOKEN is configured
        # Access directly from module to ensure we have the latest value
        current_admin_token = config_module.ADMIN_TOKEN
        if not current_admin_token:
            logging.error("ADMIN_TOKEN is not configured. Cannot authenticate with token.")
            self.render("admin_login.html", error="Admin token authentication is not configured.")
            return
            
        # Normalize token comparison - strip all whitespace and remove surrounding quotes
        normalized_token = token.strip().strip("'\"")
        normalized_admin_token = current_admin_token.strip().strip("'\"") if current_admin_token else ""
        
        # Debug logging (without exposing actual token values)
        logging.debug(f"Admin token auth attempt - submitted length: {len(normalized_token)}, expected length: {len(normalized_admin_token)}")
        
        if secrets.compare_digest(normalized_token, normalized_admin_token):
            logging.info("Admin token authentication successful")
            self.set_secure_cookie("admin", "authenticated", httponly=True, secure=(self.request.protocol == "https"), samesite="Strict")
            self.redirect("/admin")
        else:
            logging.warning("Admin token authentication failed.")
            self.render("admin_login.html", error="Invalid admin token.")

class LogoutHandler(BaseHandler):
    def get(self):
        # Clear both regular and admin auth cookies
        self.clear_cookie("user")
        self.clear_cookie("admin")
        # Redirect to login page
        self.redirect("/login")

class ProfileHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self):
        # Check if LDAP is enabled
        ldap_enabled = self.settings.get('ldap_server') is not None
        self.render("profile.html", user=self.current_user, error=None, success=None, ldap_enabled=ldap_enabled)

    @tornado.web.authenticated
    def post(self):
        # Check if LDAP is enabled
        ldap_enabled = self.settings.get('ldap_server') is not None
        
        # Get current user from DB
        db_conn = constants_module.DB_CONN
        if not db_conn:
            self.render("profile.html", user=self.current_user, error="Database connection not available", success=None, ldap_enabled=ldap_enabled)
            return
            
        user = get_user_by_username(db_conn, self.current_user['username'])
        if not user:
            self.render("profile.html", user=self.current_user, error="User not found", success=None, ldap_enabled=ldap_enabled)
            return

        new_password = self.get_argument("new_password", "")
        confirm_password = self.get_argument("confirm_password", "")

        if new_password:
            if new_password != confirm_password:
                self.render("profile.html", user=self.current_user, error="Passwords do not match", success=None, ldap_enabled=ldap_enabled)
                return
            if len(new_password) < 6:
                self.render("profile.html", user=self.current_user, error="Password must be at least 6 characters", success=None, ldap_enabled=ldap_enabled)
                return
            
            try:
                update_user(db_conn, user['id'], password=new_password)
                self.render("profile.html", user=self.current_user, success="Password updated successfully", error=None, ldap_enabled=ldap_enabled)
            except Exception as e:
                self.render("profile.html", user=self.current_user, error=f"Error updating password: {e}", success=None, ldap_enabled=ldap_enabled)
        else:
            self.render("profile.html", user=self.current_user, error=None, success=None, ldap_enabled=ldap_enabled)
