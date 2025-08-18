import os
import secrets
import argparse
import json
from typing import Set
import logging
import asyncio

import tornado.ioloop
import tornado.web
import socket
import tornado.websocket
import shutil
from collections import deque
from ldap3 import Server, Connection, ALL
from datetime import datetime
import gzip
import mimetypes
from io import BytesIO

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
    "file_edit": True,
    "file_share": True,
    "compression": True,  # ✅ NEW: Enable gzip compression
}


# Maximum upload size: 10 GB
MAX_FILE_SIZE = 10 * 1024 * 1024 * 1024
MAX_READABLE_FILE_SIZE = 10 * 1024 * 1024
CHUNK_SIZE = 1024 * 64

SHARES = {}

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
        return True

    @classmethod
    def send_updates(cls):
        for connection in cls.connections:
            connection.write_message(json.dumps(FEATURE_FLAGS))


class BaseHandler(tornado.web.RequestHandler):
    def get_current_user(self) -> str | None:
        return self.get_secure_cookie("user")

    def get_current_admin(self) -> str | None:
        return self.get_secure_cookie("admin")

class RootHandler(BaseHandler):
    def get(self):
        self.redirect("/files/")

class LDAPLoginHandler(BaseHandler):
    def get(self):
        if self.current_user:
            self.redirect("/files/")
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
                self.redirect("/files/")
            else:
                self.render("login.html", error="Invalid username or password.", settings=self.settings)
        except Exception as e:
            self.render("login.html", error=f"LDAP connection failed: {e}", settings=self.settings)

class LoginHandler(BaseHandler):
    def get(self):
        if self.current_user:
            next_url = self.get_argument("next", "/files/")
            self.redirect(next_url)
            return
        next_url = self.get_argument("next", None)
        self.render("login.html", error=None, settings=self.settings, next_url=next_url)

    def post(self):
        token = self.get_argument("token", "")
        next_url = self.get_argument("next", "/files/")
        if token == ACCESS_TOKEN:
            self.set_secure_cookie("user", "authenticated")
            self.redirect(next_url)
        else:
            self.render("login.html", error="Invalid token. Try again.", settings=self.settings, next_url=next_url)

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
        FEATURE_FLAGS["compression"] = self.get_argument("compression", "off") == "on"
        if not self.get_current_admin():
            self.set_status(403)
            self.write("Forbidden")
            return
        
        FEATURE_FLAGS["file_upload"] = self.get_argument("file_upload", "off") == "on"
        FEATURE_FLAGS["file_delete"] = self.get_argument("file_delete", "off") == "on"
        FEATURE_FLAGS["file_rename"] = self.get_argument("file_rename", "off") == "on"
        FEATURE_FLAGS["file_download"] = self.get_argument("file_download", "off") == "on"
        FEATURE_FLAGS["file_edit"] = self.get_argument("file_edit", "off") == "on"
        FEATURE_FLAGS["file_share"] = self.get_argument("file_share", "off") == "on"
        
        FeatureFlagSocketHandler.send_updates()
        self.redirect("/admin")

def get_relative_path(path, root):
    if path.startswith(root):
        return os.path.relpath(path, root)
    return path

class MainHandler(BaseHandler):
    @tornado.web.authenticated
    async def get(self, path):
        abspath = os.path.abspath(os.path.join(ROOT_DIR, path))

        if not abspath.startswith(ROOT_DIR):
            self.set_status(403)
            self.write("Forbidden")
            return

        if os.path.isdir(abspath):
            # Collect all shared paths for efficient lookup
            all_shared_paths = set()
            for share in SHARES.values():
                for p in share.get('paths', []):
                    all_shared_paths.add(p)

            files = get_files_in_directory(abspath)
            
            # Augment file data with shared status
            for file_info in files:
                full_path = join_path(path, file_info['name'])
                file_info['is_shared'] = full_path in all_shared_paths

            parent_path = os.path.dirname(path) if path else None
            self.render(
                "browse.html", 
                current_path=path, 
                parent_path=parent_path, 
                files=files,
                join_path=join_path,
                get_file_icon=get_file_icon,
                features=FEATURE_FLAGS,
                max_file_size=MAX_FILE_SIZE
            )
        elif os.path.isfile(abspath):
            filename = os.path.basename(abspath)
            if self.get_argument('download', None):
                if not FEATURE_FLAGS.get("file_download", True):
                    self.set_status(403)
                    self.write("File download is disabled.")
                    return

                self.set_header('Content-Disposition', f'attachment; filename="{filename}"')

                # Guess MIME type
                mime_type, _ = mimetypes.guess_type(abspath)
                mime_type = mime_type or "application/octet-stream"
                self.set_header('Content-Type', mime_type)

                # Check for compressible types
                if FEATURE_FLAGS.get("compression", True):
                    compressible_types = ['text/', 'application/json', 'application/javascript', 'application/xml']
                    if any(mime_type.startswith(prefix) for prefix in compressible_types):
                        self.set_header("Content-Encoding", "gzip")

                        buffer = BytesIO()
                        with open(abspath, 'rb') as f_in, gzip.GzipFile(fileobj=buffer, mode='wb') as f_out:
                            shutil.copyfileobj(f_in, f_out)

                        self.write(buffer.getvalue())
                        await self.flush()
                        return

                # Raw fallback
                with open(abspath, 'rb') as f:
                    while True:
                        chunk = f.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        self.write(chunk)
                        await self.flush()
                return

            # File viewing (stream/filter/text)
            start_streaming = self.get_argument('stream', None) is not None
            if start_streaming:
                self.set_header('Content-Type', 'text/plain; charset=utf-8')
                self.write(f"Streaming file: {filename}\n\n")
                await self.flush()
                # Stream line-by-line as soon as it's read
                with open(abspath, 'r', encoding='utf-8', errors='replace') as f:
                    for raw in f:
                        # Avoid double spacing: strip one trailing newline and let browser render line breaks
                        line = raw[:-1] if raw.endswith('\n') else raw
                        self.write(line + '\n')
                        await self.flush()
                return

            filter_substring = self.get_argument('filter', None)
            # Legacy param no longer used for inline editing, kept for compatibility
            _ = self.get_argument('edit', None)
            start_line = self.get_argument('start_line', None)
            end_line = self.get_argument('end_line', None)

            # Parse line range parameters with defaults and clamping
            try:
                start_line = int(start_line) if start_line is not None else 1
            except ValueError:
                start_line = 1
            if start_line < 1:
                start_line = 1

            try:
                end_line = int(end_line) if end_line is not None else 100
            except ValueError:
                end_line = 100
            
            # Ensure start_line <= end_line
            if start_line > end_line:
                start_line = end_line
            
            # Stream through file once: build range slice without scanning the rest
            file_content_parts: list[str] = []
            lines_items: list[dict] = []
            total_lines = 0
            display_index = 0  # used when filtering; numbering restarts at 1
            reached_EOF =  False
            with open(abspath, 'r', encoding='utf-8', errors='replace') as f:
                for line in f:
                    total_lines += 1
                    if total_lines < start_line:
                        continue
                    if total_lines > end_line:
                        # don't continue scanning; we don't need total file length for view
                        break
                    if filter_substring:
                        if filter_substring in line:
                            display_index += 1
                            file_content_parts.append(line)
                            lines_items.append({
                                "n": display_index,
                                "text": line.rstrip('\n')
                            })
                    else:
                        file_content_parts.append(line)
                        lines_items.append({
                            "n": total_lines,
                            "text": line.rstrip('\n')
                        })
                else:
                    reached_EOF = True
            # When filtering, restart numbering from 1 in the rendered view
            if filter_substring:
                start_line = 1
            file_content = ''.join(file_content_parts)

            filter_html = f'''
            <form method="get" style="margin-bottom:10px;">
                <input type="hidden" name="path" value="{path}">
                <input type="text" name="filter" placeholder="Filter lines..." value="{filter_substring or ''}" style="width:200px;">
                <button type="submit">Apply Filter</button>
            </form>
            '''
            self.render("file.html", 
                      filename=filename, 
                      path=path, 
                      file_content=file_content, 
                      filter_html=filter_html, 
                      features=FEATURE_FLAGS,
                      start_line=start_line,
                      end_line=end_line,
                      lines=lines_items,
                      open_editor=False,
                      full_file_content="",
                      reached_EOF=reached_EOF)
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
        # Number of tail lines to send on connect
        try:
            n_param = self.get_query_argument('n', default='100')
            self.tail_n = int(n_param)
            if self.tail_n < 1:
                self.tail_n = 100
        except Exception:
            self.tail_n = 100
        if not os.path.isfile(self.file_path):
            await self.write_message(f"File not found: {self.file_path}")
            self.close()
            return

        try:
            with open(self.file_path, 'r', encoding='utf-8', errors='replace') as f:
                last_n_lines = deque(f, self.tail_n)
            if last_n_lines:
                for line in last_n_lines:
                    await self.write_message(line)
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
        # Stream near real-time
        self.periodic = tornado.ioloop.PeriodicCallback(self.send_new_lines, 100)
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
            self.set_status(400)
            self.write("No files provided in the 'files' field.")
            return

        for file_info in file_infos:
            if len(file_info['body']) > MAX_FILE_SIZE:
                self.set_status(413)
                self.write(f"File {file_info['filename']} is too large.")
                return
            
            # Use the filename from the multipart header
            filename = file_info['filename']
            file_body = file_info['body']
            
            final_path = os.path.join(ROOT_DIR, directory, filename)
            final_path_abs = os.path.abspath(final_path)

            if not final_path_abs.startswith(os.path.abspath(os.path.join(ROOT_DIR, directory))):
                self.set_status(403)
                self.write(f"Forbidden path: {filename}")
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
        self.redirect("/files/" + parent if parent else "/files/")

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
        self.redirect("/files/" + parent if parent else "/files/")


class EditHandler(BaseHandler):
    @tornado.web.authenticated
    def post(self):
        if not FEATURE_FLAGS.get("file_edit"):
            self.set_status(403)
            self.write("File editing is disabled.")
            return

        path = self.get_argument("path", "")
        content = self.get_argument("content", "")
        
        abspath = os.path.abspath(os.path.join(ROOT_DIR, path))
        
        if not abspath.startswith(ROOT_DIR):
            self.set_status(403)
            self.write("Forbidden")
            return
            
        if not os.path.isfile(abspath):
            self.set_status(404)
            self.write("File not found")
            return

        try:
            with open(abspath, 'w', encoding='utf-8') as f:
                f.write(content)
            self.set_status(200)
            self.write("File saved successfully.")
        except Exception as e:
            self.set_status(500)
            self.write(f"Error saving file: {e}")

class EditViewHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self, path):
        if not FEATURE_FLAGS.get("file_edit"):
            self.set_status(403)
            self.write("File editing is disabled.")
            return

        abspath = os.path.abspath(os.path.join(ROOT_DIR, path))
        if not (abspath.startswith(ROOT_DIR)):
            self.set_status(403)
            self.write("Forbidden")
            return
        if not os.path.isfile(abspath):
            self.set_status(404)
            self.write("File not found")
            return

        # Prevent loading extremely large files into memory in the editor
        try:
            file_size = os.path.getsize(abspath)
        except OSError:
            file_size = 0
        if file_size > MAX_READABLE_FILE_SIZE:
            self.set_status(413)
            self.write(f"File too large to edit in browser. Size: {file_size} bytes (limit {MAX_READABLE_FILE_SIZE} bytes)")
            return

        filename = os.path.basename(abspath)
        # Read once into memory (bounded by MAX_READABLE_FILE_SIZE)
        with open(abspath, 'r', encoding='utf-8', errors='replace') as f:
            full_file_content = f.read()
        total_lines = full_file_content.count('\n') + 1 if full_file_content else 0

        self.render(
            "edit.html",
            filename=filename,
            path=path,
            full_file_content=full_file_content,
            total_lines=total_lines,
            features=FEATURE_FLAGS,
        )

class FileListAPIHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self, path):
        print(f"DEBUG: FileListAPIHandler called with path: '{path}'")
        self.set_header("Content-Type", "application/json")
        
        # Normalize path
        path = path.strip('/')
        abspath = os.path.abspath(os.path.join(ROOT_DIR, path))
        print(f"DEBUG: Normalized path: '{path}', abspath: '{abspath}'")
        
        if not abspath.startswith(ROOT_DIR):
            print(f"DEBUG: Forbidden - abspath doesn't start with ROOT_DIR")
            self.set_status(403)
            self.write({"error": "Forbidden"})
            return

        if not os.path.isdir(abspath):
            print(f"DEBUG: Directory not found: {abspath}")
            self.set_status(404)
            self.write({"error": "Directory not found"})
            return

        try:
            files = get_files_in_directory(abspath)
            print(f"DEBUG: Found {len(files)} files")
            result = {
                "path": path,
                "files": [
                    {
                        "name": f["name"],
                        "is_dir": f["is_dir"],
                        "size_str": f.get("size_str", "-"),
                        "modified": f.get("modified", "-")
                    }
                    for f in files
                ]
            }
            self.write(result)
        except Exception as e:
            print(f"DEBUG: Exception: {e}")
            self.set_status(500)
            self.write({"error": str(e)})

class ShareFilesHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self):
        if not FEATURE_FLAGS.get("file_share"):
            self.set_status(403)
            self.write("File sharing is disabled")
            return
        # Just render the template - files will be loaded on-the-fly via JavaScript
        self.render("share.html", shares=SHARES)

class ShareCreateHandler(BaseHandler):
    @tornado.web.authenticated
    def post(self):
        if not FEATURE_FLAGS.get("file_share"):
            self.set_status(403)
            self.write({"error": "File sharing is disabled"})
            return
        try:
            data = json.loads(self.request.body or b'{}')
            paths = data.get('paths', [])
            valid_paths = []
            for p in paths:
                ap = os.path.abspath(os.path.join(ROOT_DIR, p))
                if ap.startswith(ROOT_DIR) and os.path.isfile(ap):
                    valid_paths.append(p)
            if not valid_paths:
                self.set_status(400)
                self.write({"error": "No valid files"})
                return
            sid = secrets.token_urlsafe(8)
            SHARES[sid] = {"paths": valid_paths, "created": datetime.utcnow().isoformat()}
            self.write({"id": sid, "url": f"/shared/{sid}"})
        except Exception as e:
            self.set_status(500)
            self.write({"error": str(e)})

class ShareRevokeHandler(BaseHandler):
    @tornado.web.authenticated
    def post(self):
        if not FEATURE_FLAGS.get("file_share"):
            self.set_status(403)
            self.write({"error": "File sharing is disabled"})
            return
        sid = self.get_argument('id', '')
        if sid in SHARES:
            del SHARES[sid]
        if self.request.headers.get('Accept') == 'application/json':
            self.write({'ok': True})
            return
        self.redirect('/share')

class ShareListAPIHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self):
        if not FEATURE_FLAGS.get("file_share"):
            self.set_status(403)
            self.write({"error": "File sharing is disabled"})
            return
        self.write({"shares": SHARES})

class SharedListHandler(tornado.web.RequestHandler):
    def get(self, sid):
        share = SHARES.get(sid)
        if not share:
            self.set_status(404)
            self.write("Invalid share link")
            return
        self.render("shared_list.html", share_id=sid, files=share['paths'])

class SharedFileHandler(tornado.web.RequestHandler):
    def get(self, sid, path):
        share = SHARES.get(sid)
        if not share:
            self.set_status(404)
            self.write("Invalid share link")
            return
        if path not in share['paths']:
            self.set_status(403)
            self.write("File not in share")
            return
        abspath = os.path.abspath(os.path.join(ROOT_DIR, path))
        if not (abspath.startswith(ROOT_DIR) and os.path.isfile(abspath)):
            self.set_status(404)
            self.write("File not found")
            return
        self.set_header('Content-Type', 'text/plain; charset=utf-8')
        with open(abspath, 'r', encoding='utf-8', errors='replace') as f:
            self.write(f.read())


def make_app(settings, ldap_enabled=False, ldap_server=None, ldap_base_dn=None):
    settings["template_path"] = os.path.join(os.path.dirname(__file__), "templates")
    # Limit request size to avoid Tornado rejecting large uploads with
    # "Content-Length too long" before our handler can respond.
    settings.setdefault("max_body_size", MAX_FILE_SIZE)
    settings.setdefault("max_buffer_size", MAX_FILE_SIZE)
    
    if ldap_enabled:
        settings["ldap_server"] = ldap_server
        settings["ldap_base_dn"] = ldap_base_dn
        login_handler = LDAPLoginHandler
    else:
        login_handler = LoginHandler

    return tornado.web.Application([
        (r"/", RootHandler),
        (r"/login", login_handler),
        (r"/admin/login", AdminLoginHandler),
        (r"/admin", AdminHandler),
        (r"/stream/(.*)", FileStreamHandler),
        (r"/features", FeatureFlagSocketHandler),
        (r"/upload", UploadHandler),
        (r"/delete", DeleteHandler),
        (r"/rename", RenameHandler),
    (r"/edit/(.*)", EditViewHandler),
        (r"/edit", EditHandler),
        (r"/api/files/(.*)", FileListAPIHandler),
        (r"/share", ShareFilesHandler),
        (r"/share/create", ShareCreateHandler),
        (r"/share/revoke", ShareRevokeHandler),
        (r"/share/list", ShareListAPIHandler),
        (r"/shared/([A-Za-z0-9_\-]+)", SharedListHandler),
        (r"/shared/([A-Za-z0-9_\-]+)/file/(.*)", SharedFileHandler),
        (r"/files/(.*)", MainHandler),
    ], **settings)


def main():
    parser = argparse.ArgumentParser(description="Run Aird")
    parser.add_argument("--config", help="Path to JSON config file")
    parser.add_argument("--root", help="Root directory to serve")
    parser.add_argument("--port", type=int, help="Port to listen on")
    parser.add_argument("--token", help="Access token for login")
    parser.add_argument("--admin-token", help="Access token for admin login")
    parser.add_argument("--ldap", action="store_true", help="Enable LDAP authentication")
    parser.add_argument("--ldap-server", help="LDAP server address")
    parser.add_argument("--ldap-base-dn", help="LDAP base DN for user search")
    parser.add_argument("--hostname", help="Host name for the server")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(levelname)s:%(name)s:%(message)s')

    config = {}
    if args.config:
        with open(args.config) as f:
            config = json.load(f)

    root = args.root or config.get("root") or os.getcwd()
    port = args.port or config.get("port") or 8000
    token = args.token or config.get("token") or os.environ.get("AIRD_ACCESS_TOKEN") or secrets.token_urlsafe(32)
    admin_token = args.admin_token or config.get("admin_token") or secrets.token_urlsafe(32)

    ldap_enabled = args.ldap or config.get("ldap", False)
    ldap_server = args.ldap_server or config.get("ldap_server")
    ldap_base_dn = args.ldap_base_dn or config.get("ldap_base_dn")
    host_name = args.hostname or config.get("hostname") or socket.getfqdn()

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
            app.listen(
                port,
                max_body_size=MAX_FILE_SIZE,
                max_buffer_size=MAX_FILE_SIZE,
            )
            print(f"Serving HTTP on 0.0.0.0 port {port} (http://0.0.0.0:{port}/) ...")
            print(f"http://{host_name}:{port}/")
            tornado.ioloop.IOLoop.current().start()
            break
        except OSError:
            port += 1
    
if __name__ == "__main__":
    main()
