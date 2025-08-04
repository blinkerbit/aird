import os
import secrets
import argparse
import json
import time
from typing import Set
from urllib.parse import urlparse

import tornado.ioloop
import tornado.web
import socket
import tornado.websocket
import shutil
from collections import deque
from ldap3 import Server, Connection, ALL
from datetime import datetime

import os

def join_path(*parts):
    return os.path.join(*parts).replace("\\", "/")

# Add this import for template path
from tornado.web import RequestHandler, Application

# Will be set in main() after parsing configuration
ACCESS_TOKEN = None
ADMIN_TOKEN = None
ROOT_DIR = os.getcwd()

FEATURE_FLAGS = {
    "file_upload": True,
    "file_delete": True,
    "file_rename": True,
    "file_download": True,
}

def get_files_in_directory(path="."):
    files = []
    for entry in os.scandir(path):
        stat = entry.stat()
        files.append({
            "name": entry.name,
            "is_dir": entry.is_dir(),
            "size_bytes": stat.st_size,
            "size_str": f"{stat.st_size / 1024:.2f} KB" if not entry.is_dir() else "-",
            "modified": datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
            "modified_timestamp": int(stat.st_mtime)
        })
    return files

def get_file_icon(filename):
    ext = os.path.splitext(filename)[1].lower()
    if ext in [".txt", ".md"]:
        return "📄"
    elif ext in [".jpg", ".jpeg", ".png", ".gif"]:
        return "🖼️"
    elif ext in [".py", ".js", ".java", ".cpp"]:
        return "💻"
    elif ext in [".zip", ".rar"]:
        return "🗜️"
    else:
        return "📦"


class FeatureFlagSocketHandler(tornado.websocket.WebSocketHandler):
    connections: Set['FeatureFlagSocketHandler'] = set()

    def open(self):
        FeatureFlagSocketHandler.connections.add(self)
        self.write_message(json.dumps(FEATURE_FLAGS))

    def on_close(self):
        FeatureFlagSocketHandler.connections.remove(self)

    def check_origin(self, origin):
        # Only allow connections from the same origin or allowed domains
        allowed_origins = self.settings.get('allowed_origins', ['localhost', '127.0.0.1'])
        parsed_origin = urlparse(origin).netloc.split(':')[0]
        return parsed_origin in allowed_origins

    def get_compression_options(self):
        # Enable compression for WebSocket traffic
        return {'compression_level': 6, 'mem_level': 9}

    @classmethod
    def send_updates(cls):
        for connection in cls.connections:
            try:
                connection.write_message(json.dumps(FEATURE_FLAGS))
            except Exception:
                # Remove dead connections
                cls.connections.remove(connection)


class BaseHandler(tornado.web.RequestHandler):
    def get_current_user(self) -> str | None:
        return self.get_secure_cookie("user")

    def get_current_admin(self) -> str | None:
        return self.get_secure_cookie("admin")

class LDAPLoginHandler(BaseHandler):
    def get(self):
        if self.current_user:
            self.redirect("/")
            return
        self.render("login.html", error=None, settings=self.settings)

    def post(self):
        username = self.get_argument("username", "")
        password = self.get_argument("password", "")
        
        try:
            server = Server(self.settings['ldap_server'], get_info=ALL)
            conn = Connection(server, user=f"uid={username},{self.settings['ldap_base_dn']}", password=password, auto_bind=True)
            if conn.bind():
                self.set_secure_cookie("user", username)
                self.redirect("/")
            else:
                self.render("login.html", error="Invalid username or password.", settings=self.settings)
        except Exception as e:
            self.render("login.html", error=f"LDAP connection failed: {e}", settings=self.settings)

class LoginHandler(BaseHandler):
    def get(self):
        if self.current_user:
            self.redirect("/")
            return
        self.render("login.html", error=None, settings=self.settings)

    def post(self):
        token = self.get_argument("token", "")
        if token == ACCESS_TOKEN:
            self.set_secure_cookie("user", "authenticated")
            self.redirect("/")
        else:
            self.render("login.html", error="Invalid token. Try again.", settings=self.settings)

class AdminLoginHandler(BaseHandler):
    def get(self):
        if self.get_current_admin():
            self.redirect("/admin")
            return
        self.render("admin_login.html", error=None)

    def post(self):
        token = self.get_argument("token", "")
        if token == ADMIN_TOKEN:
            self.set_secure_cookie("admin", "authenticated")
            self.redirect("/admin")
        else:
            self.render("admin_login.html", error="Invalid admin token.")

class AdminHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self):
        if not self.get_current_admin():
            self.redirect("/admin/login")
            return
        self.render("admin.html", features=FEATURE_FLAGS)

    @tornado.web.authenticated
    def post(self):
        if not self.get_current_admin():
            self.set_status(403)
            self.write("Forbidden")
            return
        
        FEATURE_FLAGS["file_upload"] = self.get_argument("file_upload", "off") == "on"
        FEATURE_FLAGS["file_delete"] = self.get_argument("file_delete", "off") == "on"
        FEATURE_FLAGS["file_rename"] = self.get_argument("file_rename", "off") == "on"
        FEATURE_FLAGS["file_download"] = self.get_argument("file_download", "off") == "on"
        
        FeatureFlagSocketHandler.send_updates()
        self.redirect("/admin")

def get_relative_path(path, root):
    if path.startswith(root):
        return os.path.relpath(path, root)
    return path

class MainHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self, path):
        abspath = os.path.abspath(os.path.join(ROOT_DIR, path))
        
        if not abspath.startswith(ROOT_DIR):
            self.set_status(403)
            self.write("Forbidden")
            return

        if os.path.isdir(abspath):
            files = get_files_in_directory(abspath)
            parent_path = os.path.dirname(path) if path else None
            
            # Use the new helper function to get the correct relative path
            self.render(
                "browse.html", 
                current_path=path, 
                parent_path=parent_path, 
                files=files, 
                join_path=join_path, 
                get_file_icon=get_file_icon,
                features=FEATURE_FLAGS
            )
        elif os.path.isfile(abspath):
            filename = os.path.basename(abspath)
            if self.get_argument('download', None):
                if not FEATURE_FLAGS["file_download"]:
                    self.set_status(403)
                    self.write("File download is disabled.")
                    return
                self.set_header('Content-Type', 'application/octet-stream')
                self.set_header('Content-Disposition', f'attachment; filename="{filename}"')
                with open(abspath, 'rb') as f:
                    self.write(f.read())
            else:
                start_streaming = self.get_argument('stream', None) is not None
                if start_streaming:
                    file_content = "Initializing stream..."
                else:
                    with open(abspath, 'r', encoding='utf-8', errors='replace') as f:
                        file_content = f.read()
                self.render("file.html", filename=filename, path=path, file_content=file_content, start_streaming=start_streaming, features=FEATURE_FLAGS)
        else:
            self.set_status(404)
            self.write("File not found")

class FileStreamHandler(tornado.websocket.WebSocketHandler):
    def get_current_user(self) -> str | None:
        return self.get_secure_cookie("user")

    def check_origin(self, origin):
        return True

    async def open(self, path):
        if not self.current_user:
            self.close()
            return

        path = path.lstrip('/')
        self.file_path = os.path.abspath(os.path.join(ROOT_DIR, path))
        self.running = True
        if not os.path.isfile(self.file_path):
            await self.write_message(f"File not found: {self.file_path}")
            self.close()
            return

        try:
            with open(self.file_path, 'r', encoding='utf-8', errors='replace') as f:
                last_100_lines = deque(f, 100)
            if last_100_lines:
                await self.write_message("".join(last_100_lines))
        except Exception as e:
            await self.write_message(f"Error reading file history: {e}")

        try:
            self.file = open(self.file_path, 'r', encoding='utf-8', errors='replace')
            self.file.seek(0, os.SEEK_END)
        except Exception as e:
            await self.write_message(f"Error opening file for streaming: {e}")
            self.close()
            return
        self.loop = tornado.ioloop.IOLoop.current()
        self.periodic = tornado.ioloop.PeriodicCallback(self.send_new_lines, 500)
        self.periodic.start()

    async def send_new_lines(self):
        if not self.running:
            return
        where = self.file.tell()
        line = self.file.readline()
        while line:
            await self.write_message(line)
            where = self.file.tell()
            line = self.file.readline()
        self.file.seek(where)

    def on_close(self):
        self.running = False
        if hasattr(self, 'periodic'):
            self.periodic.stop()
        if hasattr(self, 'file'):
            self.file.close()

class UploadHandler(BaseHandler):
    @tornado.web.authenticated
    def post(self):
        if not FEATURE_FLAGS["file_upload"]:
            self.set_status(403)
            self.write("File upload is disabled.")
            return

        directory = self.get_argument("directory", "")
        file_infos = self.request.files.get('files', [])
        
        if not file_infos:
            file_info = self.request.files.get('file', [])[0]
            filename = file_info['filename']
            upload_path = os.path.join(ROOT_DIR, directory)
            if not os.path.abspath(upload_path).startswith(ROOT_DIR):
                self.set_status(403)
                self.write("Forbidden")
                return
            os.makedirs(upload_path, exist_ok=True)
            with open(os.path.join(upload_path, filename), 'wb') as f:
                f.write(file_info['body'])
            self.redirect("/" + directory)
            return

        for file_info in file_infos:
            relative_path = file_info['filename']
            file_body = file_info['body']
            
            final_path = os.path.join(ROOT_DIR, directory, relative_path)
            final_path_abs = os.path.abspath(final_path)

            if not final_path_abs.startswith(os.path.abspath(os.path.join(ROOT_DIR, directory))):
                self.set_status(403)
                self.write(f"Forbidden path: {relative_path}")
                return

            os.makedirs(os.path.dirname(final_path_abs), exist_ok=True)
            
            with open(final_path_abs, 'wb') as f:
                f.write(file_body)
        
        self.set_status(200)
        self.write("Upload successful")

class DeleteHandler(BaseHandler):
    @tornado.web.authenticated
    def post(self):
        if not FEATURE_FLAGS["file_delete"]:
            self.set_status(403)
            self.write("File delete is disabled.")
            return

        path = self.get_argument("path", "")
        abspath = os.path.abspath(os.path.join(ROOT_DIR, path))
        root = ROOT_DIR
        if not abspath.startswith(root):
            self.set_status(403)
            self.write("Forbidden")
            return
        if os.path.isdir(abspath):
            shutil.rmtree(abspath)
        elif os.path.isfile(abspath):
            os.remove(abspath)
        parent = os.path.dirname(path)
        self.redirect("/" + parent if parent else "/")

class RenameHandler(BaseHandler):
    @tornado.web.authenticated
    def post(self):
        if not FEATURE_FLAGS["file_rename"]:
            self.set_status(403)
            self.write("File rename is disabled.")
            return

        path = self.get_argument("path", "")
        new_name = self.get_argument("new_name", "")
        abspath = os.path.abspath(os.path.join(ROOT_DIR, path))
        new_abspath = os.path.abspath(os.path.join(ROOT_DIR, os.path.dirname(path), new_name))
        root = ROOT_DIR
        if not (abspath.startswith(root) and new_abspath.startswith(root)):
            self.set_status(403)
            self.write("Forbidden")
            return
        os.rename(abspath, new_abspath)
        parent = os.path.dirname(path)
        self.redirect("/" + parent if parent else "/")



def make_app(settings, ldap_enabled=False, ldap_server=None, ldap_base_dn=None):
    settings.update({
        "template_path": os.path.join(os.path.dirname(__file__), "templates"),
        "cookie_secret": os.environ.get("COOKIE_SECRET", secrets.token_hex(32)),
        "xsrf_cookies": True,
        "allowed_origins": os.environ.get("ALLOWED_ORIGINS", "localhost,127.0.0.1").split(","),
        "websocket_max_message_size": 10 * 1024 * 1024,  # 10MB max message size
        "websocket_ping_interval": 30,
        "websocket_ping_timeout": 120,
    })
    
    if ldap_enabled:
        settings["ldap_server"] = ldap_server
        settings["ldap_base_dn"] = ldap_base_dn
        login_handler = LDAPLoginHandler
    else:
        login_handler = LoginHandler

    return tornado.web.Application([
        (r"/login", login_handler),
        (r"/admin/login", AdminLoginHandler),
        (r"/admin", AdminHandler),
        (r"/stream/(.*)", FileStreamHandler),
        (r"/features", FeatureFlagSocketHandler),
        (r"/upload", UploadHandler),
        (r"/delete", DeleteHandler),
        (r"/rename", RenameHandler),
        (r"/(.*)", MainHandler),
    ], **settings)


def main():
    parser = argparse.ArgumentParser(description="Run Filey")
    parser.add_argument("--config", help="Path to JSON config file")
    parser.add_argument("--root", help="Root directory to serve")
    parser.add_argument("--port", type=int, help="Port to listen on")
    parser.add_argument("--token", help="Access token for login")
    parser.add_argument("--admin-token", help="Access token for admin login")
    parser.add_argument("--ldap", action="store_true", help="Enable LDAP authentication")
    parser.add_argument("--ldap-server", help="LDAP server address")
    parser.add_argument("--ldap-base-dn", help="LDAP base DN for user search")
    args = parser.parse_args()

    config = {}
    if args.config:
        with open(args.config) as f:
            config = json.load(f)

    root = args.root or config.get("root") or os.getcwd()
    port = args.port or config.get("port") or 8000
    token = args.token or config.get("token") or os.environ.get("WB_ACCESS_TOKEN") or secrets.token_urlsafe(32)
    admin_token = args.admin_token or config.get("admin_token") or secrets.token_urlsafe(32)

    ldap_enabled = args.ldap or config.get("ldap", False)
    ldap_server = args.ldap_server or config.get("ldap_server")
    ldap_base_dn = args.ldap_base_dn or config.get("ldap_base_dn")

    if ldap_enabled and not (ldap_server and ldap_base_dn):
        print("Error: LDAP is enabled, but --ldap-server and --ldap-base-dn are not configured.")
        return

    global ACCESS_TOKEN, ADMIN_TOKEN, ROOT_DIR
    ACCESS_TOKEN = token
    ADMIN_TOKEN = admin_token
    ROOT_DIR = os.path.abspath(root)

    settings = {
        "cookie_secret": ACCESS_TOKEN,
        "login_url": "/login",
        "admin_login_url": "/admin/login",
    }
    app = make_app(settings, ldap_enabled, ldap_server, ldap_base_dn)
    while True:
        try:
            app.listen(port)
            print(f"Serving HTTP on 0.0.0.0 port {port} (http://0.0.0.0:{port}/) ...")
            print(f"http://{socket.getfqdn()}:{port}/")
            tornado.ioloop.IOLoop.current().start()
            break
        except OSError:
            port += 1
    
if __name__ == "__main__":
    main()
