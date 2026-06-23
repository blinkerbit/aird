function getFileIcon(filename) {
  const ext = filename.toLowerCase().split('.').pop();
  const lowerFilename = filename.toLowerCase();

  // Special files by name - IDE-style
  if (['readme', 'readme.md', 'readme.txt'].includes(lowerFilename)) return '📖';
  if (['license', 'licence', 'copying'].includes(lowerFilename)) return '📜';
  if (['makefile', 'cmake', 'cmakelists.txt'].includes(lowerFilename)) return '🔨';
  if (['dockerfile', 'docker-compose.yml', 'docker-compose.yaml'].includes(lowerFilename)) return '🐳';
  if (['.gitignore', '.gitattributes', '.gitmodules'].includes(lowerFilename)) return '🔧';
  if (lowerFilename.startsWith('.env')) return '🔐';

  const iconMap = {
    // Document files - IDE-style
    'txt': '📄', 'md': '📄', 'rst': '📄', 'text': '📄',
    'doc': '📝', 'docx': '📝', 'rtf': '📝', 'odt': '📝',
    'pdf': '📕',
    'xls': '📊', 'xlsx': '📊', 'ods': '📊', 'csv': '📊',
    'ppt': '📋', 'pptx': '📋', 'odp': '📋',

    // Image files - IDE-style
    'jpg': '🖼️', 'jpeg': '🖼️', 'png': '🖼️', 'gif': '🖼️', 'bmp': '🖼️', 'webp': '🖼️', 'tiff': '🖼️', 'tif': '🖼️',
    'svg': '🎨', 'ico': '🎨',
    'psd': '🎭', 'ai': '🎭', 'sketch': '🎭',

    // Programming files - IDE-style (VS Code inspired)
    'py': '🐍', 'pyw': '🐍', 'pyc': '🐍', 'pyo': '🐍',
    'js': '🟨', 'jsx': '🟨', 'ts': '🟨', 'tsx': '🟨', 'mjs': '🟨',
    'java': '☕', 'class': '☕', 'jar': '☕',
    'cpp': '⚙️', 'cxx': '⚙️', 'cc': '⚙️', 'c': '⚙️', 'h': '⚙️', 'hpp': '⚙️',
    'cs': '🔷', 'vb': '🔷', 'fs': '🔷',
    'php': '🐘', 'phtml': '🐘',
    'rb': '💎', 'rake': '💎', 'gem': '💎',
    'go': '🐹',
    'rs': '🦀',
    'swift': '🦉',
    'kt': '🟣', 'kts': '🟣',
    'scala': '🔴',
    'r': '📊', 'rmd': '📊',
    'm': '🍎', 'mm': '🍎',
    'pl': '🐪', 'pm': '🐪',
    'sh': '📟', 'bash': '📟', 'zsh': '📟', 'fish': '📟', 'bat': '📟', 'cmd': '📟', 'ps1': '📟',
    'lua': '🌙',
    'dart': '🎯',

    // Web files - IDE-style
    'html': '🌐', 'htm': '🌐', 'xhtml': '🌐',
    'css': '🎨', 'scss': '🎨', 'sass': '🎨', 'less': '🎨',
    'xml': '📰', 'xsl': '📰', 'xsd': '📰',
    'json': '📋', 'jsonl': '📋',
    'yaml': '📄', 'yml': '📄',
    'toml': '⚙️', 'ini': '⚙️', 'cfg': '⚙️', 'conf': '⚙️',

    // Archive files - IDE-style
    'zip': '🗜️', 'rar': '🗜️', '7z': '🗜️', 'tar': '🗜️', 'gz': '🗜️', 'bz2': '🗜️', 'xz': '🗜️', 'lz': '🗜️', 'lzma': '🗜️',
    'deb': '📦', 'rpm': '📦', 'pkg': '📦', 'dmg': '📦', 'msi': '📦', 'exe': '📦',

    // Video files - IDE-style
    'mp4': '🎬', 'avi': '🎬', 'mkv': '🎬', 'mov': '🎬', 'wmv': '🎬', 'flv': '🎬', 'webm': '🎬', 'm4v': '🎬', '3gp': '🎬', 'ogv': '🎬', 'mpg': '🎬', 'mpeg': '🎬',

    // Audio files - IDE-style
    'mp3': '🎵', 'wav': '🎵', 'flac': '🎵', 'aac': '🎵', 'ogg': '🎵', 'm4a': '🎵', 'wma': '🎵', 'opus': '🎵', 'aiff': '🎵',

    // Font files - IDE-style
    'ttf': '🔤', 'otf': '🔤', 'woff': '🔤', 'woff2': '🔤', 'eot': '🔤',

    // Database files - IDE-style
    'db': '🗃️', 'sqlite': '🗃️', 'sqlite3': '🗃️', 'mdb': '🗃️', 'accdb': '🗃️',

    // Log files - IDE-style
    'log': '📜', 'out': '📜', 'err': '📜',

    // Data files - IDE-style
    'sql': '🗄️',
    'parquet': '📊', 'avro': '📊', 'orc': '📊',

    // Notebook files - IDE-style
    'ipynb': '📓'
  };

  return iconMap[ext] || '📦';
}

export { getFileIcon };
