"""Media and file-type extension constants.

All frozensets here are referenced by ``aird.utils.util.get_file_icon`` and
the ``is_video_file`` / ``is_audio_file`` helpers so that extension lists live
in exactly one place.
"""

# ---------------------------------------------------------------------------
# Media extensions (also consumed by is_video_file / is_audio_file)
# ---------------------------------------------------------------------------

VIDEO_EXTENSIONS: frozenset = frozenset(
    {
        ".mp4",
        ".avi",
        ".mkv",
        ".mov",
        ".wmv",
        ".flv",
        ".webm",
        ".m4v",
        ".3gp",
        ".ogv",
        ".mpg",
        ".mpeg",
    }
)

AUDIO_EXTENSIONS: frozenset = frozenset(
    {
        ".mp3",
        ".wav",
        ".flac",
        ".aac",
        ".ogg",
        ".m4a",
        ".wma",
        ".opus",
        ".aiff",
    }
)

# ---------------------------------------------------------------------------
# Special filenames (exact, case-insensitive)
# ---------------------------------------------------------------------------

SPECIAL_FILENAMES: dict[str, str] = {
    "readme": "📖",
    "readme.md": "📖",
    "readme.txt": "📖",
    "license": "📜",
    "licence": "📜",
    "copying": "📜",
    "makefile": "🔨",
    "cmake": "🔨",
    "cmakelists.txt": "🔨",
    "dockerfile": "🐳",
    "docker-compose.yml": "🐳",
    "docker-compose.yaml": "🐳",
    ".gitignore": "🔧",
    ".gitattributes": "🔧",
    ".gitmodules": "🔧",
}

# ---------------------------------------------------------------------------
# Extension → emoji lookup table (used by get_file_icon)
# ---------------------------------------------------------------------------

EXTENSION_ICONS: dict[str, str] = {
    # Documents
    ".txt": "📄",
    ".md": "📄",
    ".rst": "📄",
    ".text": "📄",
    ".doc": "📝",
    ".docx": "📝",
    ".rtf": "📝",
    ".odt": "📝",
    ".pdf": "📕",
    ".xls": "📊",
    ".xlsx": "📊",
    ".ods": "📊",
    ".csv": "📊",
    ".ppt": "📋",
    ".pptx": "📋",
    ".odp": "📋",
    # Images
    ".jpg": "🖼️",
    ".jpeg": "🖼️",
    ".png": "🖼️",
    ".gif": "🖼️",
    ".bmp": "🖼️",
    ".webp": "🖼️",
    ".tiff": "🖼️",
    ".tif": "🖼️",
    ".svg": "🎨",
    ".ico": "🎨",
    ".psd": "🎭",
    ".ai": "🎭",
    ".sketch": "🎭",
    # Python
    ".py": "🐍💎",
    ".pyw": "🐍💎",
    ".pyc": "🐍⚡",
    ".pyo": "🐍⚡",
    # JS / TS
    ".js": "🟨",
    ".jsx": "🟨",
    ".ts": "🟨",
    ".tsx": "🟨",
    ".mjs": "🟨",
    # JVM
    ".java": "☕",
    ".class": "☕",
    ".jar": "☕",
    # C / C++
    ".cpp": "⚙️",
    ".cxx": "⚙️",
    ".cc": "⚙️",
    ".c": "⚙️",
    ".h": "⚙️",
    ".hpp": "⚙️",
    # .NET
    ".cs": "🔷",
    ".vb": "🔷",
    ".fs": "🔷",
    # PHP
    ".php": "🐘",
    ".phtml": "🐘",
    # Ruby
    ".rb": "💎",
    ".rake": "💎",
    ".gem": "💎",
    # Go / Rust / Swift / Kotlin / Scala
    ".go": "🐹",
    ".rs": "🦀",
    ".swift": "🦉",
    ".kt": "🟣",
    ".kts": "🟣",
    ".scala": "🔴",
    # R / Matlab / Perl / Shell / Lua / Dart
    ".r": "📊",
    ".rmd": "📊",
    ".m": "🍎",
    ".mm": "🍎",
    ".pl": "🐪",
    ".pm": "🐪",
    ".sh": "📟",
    ".bash": "📟",
    ".zsh": "📟",
    ".fish": "📟",
    ".bat": "📟",
    ".cmd": "📟",
    ".ps1": "📟",
    ".lua": "🌙",
    ".dart": "🎯",
    # Web
    ".html": "🌐",
    ".htm": "🌐",
    ".xhtml": "🌐",
    ".css": "🎨",
    ".scss": "🎨",
    ".sass": "🎨",
    ".less": "🎨",
    ".xml": "📰",
    ".xsl": "📰",
    ".xsd": "📰",
    ".json": "📋",
    ".jsonl": "📋",
    ".yaml": "📄",
    ".yml": "📄",
    ".toml": "⚙️",
    ".ini": "⚙️",
    ".cfg": "⚙️",
    ".conf": "⚙️",
    # Archives
    ".zip": "🗜️",
    ".rar": "🗜️",
    ".7z": "🗜️",
    ".tar": "🗜️",
    ".gz": "🗜️",
    ".bz2": "🗜️",
    ".xz": "🗜️",
    ".lz": "🗜️",
    ".lzma": "🗜️",
    # Packages / executables
    ".deb": "📦",
    ".rpm": "📦",
    ".pkg": "📦",
    ".dmg": "📦",
    ".msi": "📦",
    ".exe": "📦",
    # Fonts
    ".ttf": "🔤",
    ".otf": "🔤",
    ".woff": "🔤",
    ".woff2": "🔤",
    ".eot": "🔤",
    # Databases
    ".db": "🗃️",
    ".sqlite": "🗃️",
    ".sqlite3": "🗃️",
    ".mdb": "🗃️",
    ".accdb": "🗃️",
    # Logs
    ".log": "📜",
    ".out": "📜",
    ".err": "📜",
    # Data / analytics
    ".sql": "🗄️",
    ".parquet": "📊",
    ".avro": "📊",
    ".orc": "📊",
    # Notebooks
    ".ipynb": "📓",
}
