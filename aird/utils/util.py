import os
import json
import sqlite3

def _load_shares(conn: sqlite3.Connection) -> dict:
    loaded: dict = {}
    try:
        # Check if allowed_users and secret_token columns exist
        cursor = conn.execute("PRAGMA table_info(shares)")
        columns = [row[1] for row in cursor.fetchall()]
        
        if 'allowed_users' in columns and 'secret_token' in columns and 'share_type' in columns and 'allow_list' in columns and 'avoid_list' in columns and 'expiry_date' in columns:
            rows = conn.execute("SELECT id, created, paths, allowed_users, secret_token, share_type, allow_list, avoid_list, expiry_date FROM shares").fetchall()
            for sid, created, paths_json, allowed_users_json, secret_token, share_type, allow_list_json, avoid_list_json, expiry_date in rows:
                try:
                    paths = json.loads(paths_json) if paths_json else []
                except Exception:
                    paths = []
                try:
                    allowed_users = json.loads(allowed_users_json) if allowed_users_json else None
                except Exception:
                    allowed_users = None
                try:
                    allow_list = json.loads(allow_list_json) if allow_list_json else []
                except Exception:
                    allow_list = []
                try:
                    avoid_list = json.loads(avoid_list_json) if avoid_list_json else []
                except Exception:
                    avoid_list = []
                loaded[sid] = {"paths": paths, "created": created, "allowed_users": allowed_users, "secret_token": secret_token, "share_type": share_type or "static", "allow_list": allow_list, "avoid_list": avoid_list, "expiry_date": expiry_date}
        elif 'allowed_users' in columns and 'secret_token' in columns and 'share_type' in columns:
            rows = conn.execute("SELECT id, created, paths, allowed_users, secret_token, share_type FROM shares").fetchall()
            for sid, created, paths_json, allowed_users_json, secret_token, share_type in rows:
                try:
                    paths = json.loads(paths_json) if paths_json else []
                except Exception:
                    paths = []
                try:
                    allowed_users = json.loads(allowed_users_json) if allowed_users_json else None
                except Exception:
                    allowed_users = None
                loaded[sid] = {"paths": paths, "created": created, "allowed_users": allowed_users, "secret_token": secret_token, "share_type": share_type or "static", "allow_list": [], "avoid_list": [], "expiry_date": None}
        elif 'allowed_users' in columns and 'secret_token' in columns:
            rows = conn.execute("SELECT id, created, paths, allowed_users, secret_token FROM shares").fetchall()
            for sid, created, paths_json, allowed_users_json, secret_token in rows:
                try:
                    paths = json.loads(paths_json) if paths_json else []
                except Exception:
                    paths = []
                try:
                    allowed_users = json.loads(allowed_users_json) if allowed_users_json else None
                except Exception:
                    allowed_users = None
                loaded[sid] = {"paths": paths, "created": created, "allowed_users": allowed_users, "secret_token": secret_token, "share_type": "static", "allow_list": [], "avoid_list": [], "expiry_date": None}
        elif 'allowed_users' in columns:
            rows = conn.execute("SELECT id, created, paths, allowed_users FROM shares").fetchall()
            for sid, created, paths_json, allowed_users_json in rows:
                try:
                    paths = json.loads(paths_json) if paths_json else []
                except Exception:
                    paths = []
                try:
                    allowed_users = json.loads(allowed_users_json) if allowed_users_json else None
                except Exception:
                    allowed_users = None
                loaded[sid] = {"paths": paths, "created": created, "allowed_users": allowed_users, "secret_token": None, "share_type": "static", "allow_list": [], "avoid_list": [], "expiry_date": None}
        else:
            # Fallback for old schema without allowed_users column
            rows = conn.execute("SELECT id, created, paths FROM shares").fetchall()
            for sid, created, paths_json in rows:
                try:
                    paths = json.loads(paths_json) if paths_json else []
                except Exception:
                    paths = []
                loaded[sid] = {"paths": paths, "created": created, "allowed_users": None, "secret_token": None, "share_type": "static", "allow_list": [], "avoid_list": [], "expiry_date": None}
    except Exception as e:
        print(f"Error loading shares: {e}")
        import traceback
        print(f"Traceback: {traceback.format_exc()}")
        return {}
    return loaded

def get_file_icon(filename):
    ext = os.path.splitext(filename)[1].lower()
    
    # Special files by name (check first before extension)
    if filename.lower() in ["readme", "readme.md", "readme.txt"]:
        return "📖"
    elif filename.lower() in ["license", "licence", "copying"]:
        return "📜"
    elif filename.lower() in ["makefile", "cmake", "cmakelists.txt"]:
        return "🔨"
    elif filename.lower() in ["dockerfile", "docker-compose.yml", "docker-compose.yaml"]:
        return "🐳"
    elif filename.lower() in [".gitignore", ".gitattributes", ".gitmodules"]:
        return "🔧"
    elif filename.startswith(".env"):
        return "🔐"
    
    # Document files
    elif ext in [".txt", ".md", ".rst", ".text"]:
        return "📄"
    elif ext in [".doc", ".docx", ".rtf", ".odt"]:
        return "📝"
    elif ext in [".pdf"]:
        return "📕"
    elif ext in [".xls", ".xlsx", ".ods", ".csv"]:
        return "📊"
    elif ext in [".ppt", ".pptx", ".odp"]:
        return "📋"
    
    # Image files
    elif ext in [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff", ".tif"]:
        return "🖼️"
    elif ext in [".svg", ".ico"]:
        return "🎨"
    elif ext in [".psd", ".ai", ".sketch"]:
        return "🎭"
    
    # Programming files
    elif ext in [".py", ".pyw"]:
        return "🐍💎"  # Enhanced Python source files with gem (precious/valuable)
    elif ext in [".pyc", ".pyo"]:
        return "🐍⚡"  # Compiled Python files with lightning (fast/optimized)
    elif ext in [".js", ".jsx", ".ts", ".tsx", ".mjs"]:
        return "🟨"
    elif ext in [".java", ".class", ".jar"]:
        return "☕"
    elif ext in [".cpp", ".cxx", ".cc", ".c", ".h", ".hpp"]:
        return "⚙️"
    elif ext in [".cs", ".vb", ".fs"]:
        return "🔷"
    elif ext in [".php", ".phtml"]:
        return "🐘"
    elif ext in [".rb", ".rake", ".gem"]:
        return "💎"
    elif ext in [".go"]:
        return "🐹"
    elif ext in [".rs"]:
        return "🦀"
    elif ext in [".swift"]:
        return "🦉"
    elif ext in [".kt", ".kts"]:
        return "🟣"
    elif ext in [".scala"]:
        return "🔴"
    elif ext in [".r", ".rmd"]:
        return "📊"
    elif ext in [".m", ".mm"]:
        return "🍎"
    elif ext in [".pl", ".pm"]:
        return "🐪"
    elif ext in [".sh", ".bash", ".zsh", ".fish", ".bat", ".cmd", ".ps1"]:
        return "📟"
    elif ext in [".lua"]:
        return "🌙"
    elif ext in [".dart"]:
        return "🎯"
    
    # Web files
    elif ext in [".html", ".htm", ".xhtml"]:
        return "🌐"
    elif ext in [".css", ".scss", ".sass", ".less"]:
        return "🎨"
    elif ext in [".xml", ".xsl", ".xsd"]:
        return "📰"
    elif ext in [".json", ".jsonl"]:
        return "📋"
    elif ext in [".yaml", ".yml"]:
        return "📄"
    elif ext in [".toml", ".ini", ".cfg", ".conf"]:
        return "⚙️"
    
    # Archive files
    elif ext in [".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".lz", ".lzma"]:
        return "🗜️"
    elif ext in [".deb", ".rpm", ".pkg", ".dmg", ".msi", ".exe"]:
        return "📦"
    
    # Video files
    elif ext in [".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".3gp", ".ogv", ".mpg", ".mpeg"]:
        return "🎬"
    
    # Audio files
    elif ext in [".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma", ".opus", ".aiff"]:
        return "🎵"
    
    # Font files
    elif ext in [".ttf", ".otf", ".woff", ".woff2", ".eot"]:
        return "🔤"
    
    # Database files
    elif ext in [".db", ".sqlite", ".sqlite3", ".mdb", ".accdb"]:
        return "🗃️"
    
    # Log files
    elif ext in [".log", ".out", ".err"]:
        return "📜"
    
    # Data files
    elif ext in [".sql"]:
        return "🗄️"
    elif ext in [".parquet", ".avro", ".orc"]:
        return "📊"
    
    # Notebook files
    elif ext in [".ipynb"]:
        return "📓"
    
    
    # Default
    else:
        return "📦"