
import json

from aird.utils.util import *
import aird.config as config_module


class BaseHandler(tornado.web.RequestHandler):
    def set_default_headers(self):
        # Security headers
        self.set_header("X-Content-Type-Options", "nosniff")
        self.set_header("X-Frame-Options", "DENY")
        self.set_header("X-XSS-Protection", "1; mode=block")
        self.set_header("Referrer-Policy", "strict-origin-when-cross-origin")
        # Content Security Policy
        csp = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:;"
        self.set_header("Content-Security-Policy", csp)

    def get_current_user(self):
        # Check for user session from secure cookie
        user_json = self.get_secure_cookie("user")
        if user_json:
            try:
                from aird.db import get_user_by_username
                import aird.constants as constants_module
                # Handle both JSON-encoded user data and plain string usernames
                try:
                    user_data = json.loads(user_json)
                    username = user_data.get("username", "")
                except (json.JSONDecodeError, TypeError):
                    # If it's not JSON, treat it as a plain username string
                    username = user_json.decode('utf-8') if isinstance(user_json, bytes) else str(user_json)
                
                # Re-verify user from DB to ensure they are still valid
                db_conn = constants_module.DB_CONN
                if db_conn:
                    user = get_user_by_username(db_conn, username)
                    if user:
                        return user
                # If DB check fails but we have a username, return basic user info for token-authenticated users
                if username == "token_authenticated":
                    return {"username": "token_user", "role": "admin"}
            except Exception:
                # If cookie parsing fails, check if it's a token-authenticated user
                if isinstance(user_json, bytes):
                    user_str = user_json.decode('utf-8', errors='ignore')
                    if user_str == "token_authenticated":
                        return {"username": "token_user", "role": "admin"}
                return None
        
        # Fallback to token-based authentication from Authorization header
        auth_header = self.request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]
            # Access token directly from module to ensure we have the latest value
            current_access_token = config_module.ACCESS_TOKEN
            if current_access_token and token.strip().strip("'\"") == current_access_token.strip().strip("'\""):
                # Return a generic user object for token-based access
                return {"username": "token_user", "role": "admin"}
        
        return None

    def write_error(self, status_code, **kwargs):
        # Custom error page rendering
        try:
            self.render(
                "error.html",
                status_code=status_code,
                error_message=self._reason,
            )
        except Exception:
            # Fallback if template rendering fails
            self.write(f"<html><body><h1>Error {status_code}</h1><p>{self._reason}</p></body></html>")

    def on_finish(self):
        # Ensure cleanup logic is robust
        try:
            super().on_finish()
        except Exception:
            pass

    def is_admin_user(self) -> bool:
        """Return True if the current user is an admin.
        Checks the user object (if provided by get_current_user) and an 'admin' secure cookie.
        """
        try:
            if hasattr(self, 'get_current_admin'):
                try:
                    if self.get_current_admin():
                        return True
                except Exception:
                    pass
            user = self.get_current_user()
            if isinstance(user, dict) and user.get('role') == 'admin':
                return True
            if isinstance(user, str) and 'admin' in user.lower():
                return True
        except Exception:
            pass
        try:
            return bool(self.get_secure_cookie('admin'))
        except Exception:
            return False
    
    def get_display_username(self) -> str:
        """Get username for display purposes"""
        user = self.get_current_user()
        if user:
            # Handle dict user objects (from get_current_user)
            if isinstance(user, dict):
                username = user.get('username', '')
                role = user.get('role', '')
                
                # Handle token-authenticated users
                if username == "token_user" or username == "token_authenticated":
                    return "Admin (Token)"
                
                # Show role for regular users
                if role == "admin":
                    return f"{username} (Admin)"
                elif role:
                    return f"{username} (User)"
                else:
                    return username or "Guest"
            
            # Handle string/bytes usernames (legacy support)
            if isinstance(user, bytes):
                user_str = user.decode('utf-8', errors='ignore')
            else:
                user_str = str(user)
            
            # Check for token-authenticated users
            if user_str == "token_authenticated" or user_str == "authenticated":
                return "Admin (Token)"
            
            # Try to get role from cookie
            role_cookie = self.get_secure_cookie("user_role")
            if role_cookie:
                if isinstance(role_cookie, bytes):
                    role = role_cookie.decode('utf-8', errors='ignore')
                else:
                    role = str(role_cookie)
                
                if role == "admin":
                    return f"{user_str} (Admin)"
                elif role:
                    return f"{user_str} (User)"
            
            return user_str
        
        return "Guest"

