# Aird - A Lightweight Web-Based File Browser & Editor

Aird is a modern, lightweight, and fast web-based file browser, editor, and streamer built with Python and Tornado. It provides a comprehensive file management solution with real-time streaming, in-browser editing, and mobile-responsive design through a clean and intuitive web interface.

<img width="1696" height="715" alt="image" src="https://github.com/user-attachments/assets/95a9569d-5d0c-4d96-aab9-69e0b4cd98bf" />

<img width="1599" height="1126" alt="image" src="https://github.com/user-attachments/assets/1551cafc-1d6e-4668-86fb-b1acb5fdb7b2" />

## ✨ Features

### 🗂️ File Management
- **Smart File Browser:** Navigate through your server's directory structure with resizable columns and mobile-friendly design
- **File Operations:**
  - Download files with progress indicators
  - Upload files with drag-and-drop support (can be disabled)
  - Delete files and directories (can be disabled)
  - Rename files and directories (can be disabled)
  - **NEW:** In-browser file editing with syntax highlighting and line numbers
- **File Sharing:** Create secure, temporary public links for files and directories
  - Select multiple files and folders to share together
  - Generate unique, time-limited shareable URLs
  - No login required for shared link access
  - Manage active shares with easy revocation

### 📡 Real-time Streaming
- **WebSocket-based File Streaming:** Stream large files with animated progress indicators
- **Live Updates:** Feature changes in the admin panel are reflected instantly for all connected users
- **Performance Optimized:** Chunked file operations with configurable buffer sizes

### 🔐 Security & Authentication
- **Token-based Authentication:** Secure access with customizable access tokens
- **LDAP/Active Directory Integration:** Enterprise-grade authentication support
- **Path Traversal Protection:** Built-in security measures to prevent unauthorized access
- **Role-based Permissions:** Assign tokens to viewer, editor, or admin roles for granular control

### ⚙️ Administration
- **Admin Panel:** Dedicated admin interface to toggle features on the fly
- **Feature Flags:** Granular control over file operations (upload, delete, rename, edit, download)
- **Real-time Configuration:** Changes apply instantly without server restart

### 📱 Modern UI/UX
- **Mobile-Responsive Design:** Optimized for smartphones and tablets
- **Resizable Columns:** Customize the file browser layout to your preference
- **Animated Indicators:** Visual feedback for streaming and loading operations
- **Intuitive Interface:** Clean, minimalist design with keyboard shortcuts

## 🚀 Installation

### Option 1: Install from PyPI (Recommended)
```bash
pip install aird
```

### Option 2: Install from Source
1.  **Clone the repository:**
    ```bash
    git clone https://github.com/blinkerbit/aird.git
    cd aird
    ```

2.  **Create a virtual environment (recommended):**
    ```bash
    python -m venv venv
    source venv/bin/activate  # On Windows: venv\Scripts\activate
    ```

3.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

4.  **Install the package:**
    ```bash
    pip install .
    ```

## 📖 Usage

### Quick Start

After installation, you can run Aird using the `aird` command:

```bash
# Basic usage with access token
aird --port 8888 --access-token "your-secret-token"

# With admin capabilities
aird --port 8888 --access-token "user-token" --admin-token "admin-token"

# Serve from specific directory
aird --root-dir "/path/to/files" --access-token "your-token"
```

Navigate to `http://localhost:8888` and enter your access token to start browsing files.

### 🎮 Command-Line Arguments

| Argument          | Description                                                              | Default                |
| ----------------- | ------------------------------------------------------------------------ | ---------------------- |
| `--host`          | The host address to bind to                                              | `0.0.0.0`              |
| `--port`          | The port to listen on                                                    | `8888`                 |
| `--root-dir`      | The root directory to serve files from                                   | Current directory      |
| `--access-token`  | The token required for user login                                        | `None`                 |
| `--admin-token`   | The token required for admin login                                       | `None`                 |
| `--config`        | Path to a JSON configuration file                                        | `None`                 |
| `--enable-ldap`   | Enable LDAP authentication                                               | `False`                |
| `--ldap-server`   | The LDAP server address                                                  | `None`                 |
| `--ldap-base-dn`  | The base DN for LDAP searches                                            | `None`                 |

### ⚙️ Configuration File

For advanced setups, use a JSON configuration file to define all settings:

**Example `config.json`:**
```json
{
  "host": "0.0.0.0",
  "port": 8080,
  "root_dir": "/path/to/your/files",
  "access_token": "your-secret-token",
  "admin_token": "your-admin-secret-token",
  "users": {
    "viewer-token": "viewer",
    "editor-token": "editor"
  },
  "enable_ldap": false,
  "ldap_server": null,
  "ldap_base_dn": null,
  "feature_flags": {
    "file_upload": true,
    "file_delete": true,
    "file_rename": true,
    "file_download": true,
    "file_edit": true
  },
  "max_file_size": 10485760,
  "max_readable_file_size": 10485760,
  "chunk_size": 65536
}
```

Run with configuration file:
```bash
aird --config /path/to/config.json
```

### 🔐 LDAP Authentication

For enterprise environments, enable LDAP authentication:

```bash
aird --enable-ldap \
     --ldap-server "ldap://your.ldap.server" \
     --ldap-base-dn "ou=users,dc=example,dc=com" \
     --access-token "fallback-token"
```

Users can authenticate with their LDAP credentials, with token authentication as fallback.

### 🔗 File Sharing

The file sharing feature allows you to create public, temporary links for files and directories:

1. **Access the Share Page:**
   - Navigate to `/share` after logging in
   - Or click the "Share Files" button in the main file browser

2. **Select Files to Share:**
   - Browse directories and select files using checkboxes
   - Navigate between folders to select files from different locations
   - Use "Select All (Current Dir)" to quickly select all visible files

3. **Generate Share Links:**
   - Click "Generate Share Link" after selecting files
   - Copy the generated URL using the "Copy Link" button
   - Share the URL with others for public access (no login required)

4. **Manage Active Shares:**
   - View all active shares in the bottom panel
   - Copy existing share links or open them in a new tab
   - Revoke shares instantly when no longer needed

**Example URLs:**
- Share page: `http://localhost:8888/share`
- Public shared files: `http://localhost:8888/shared/abc123def456`

## 👑 Admin Panel

The admin panel provides real-time control over server features and capabilities.

### Access the Admin Panel

1.  **Start server with admin token:**
    ```bash
    aird --admin-token "your-admin-secret-token"
    ```

2.  **Navigate to admin interface:**
    Visit `http://localhost:8888/admin` and authenticate with your admin token

3.  **Feature Management:**
    - **File Upload:** Toggle file upload capability
    - **File Delete:** Enable/disable file and directory deletion
    - **File Rename:** Control rename functionality
    - **File Edit:** Toggle in-browser file editing
    - **File Download:** Control file download access

All changes apply immediately to all connected users via WebSocket updates.

## 🎯 Key Features in Detail

### 📝 In-Browser File Editing
- **Real-time editing** with syntax highlighting
- **Line numbers** with toggle capability
- **Auto-save functionality** with keyboard shortcuts
- **Large file support** with efficient loading
- **Responsive design** for mobile editing

### 📊 File Browser Enhancements
- **Resizable columns** for Name, Size, and Modified date
- **Mobile-optimized** responsive layout
- **Drag-and-drop upload** with visual feedback
- **Real-time file streaming** with progress animations
- **Keyboard navigation** support

### 🔗 File Sharing System
- **Multi-file selection:** Choose multiple files and directories to share in a single link
- **On-the-fly browsing:** Navigate directories dynamically without pre-loading all files
- **Secure URL generation:** Each share gets a unique, hard-to-guess identifier
- **Public access:** Shared files can be viewed without authentication
- **Active share management:** View, copy, and revoke existing shares in real-time
- **One-click copy:** Copy shareable URLs to clipboard with visual feedback
- **Temporary access:** All shares are session-based and can be easily revoked

### 🚀 Performance Features
- **Chunked file operations** for large files
- **Async WebSocket streaming** for real-time updates
- **Configurable buffer sizes** for optimal performance
- **Memory-efficient** file handling

## 📋 Requirements

- **Python:** 3.10 or higher
- **Dependencies:** Tornado, ldap3 (automatically installed)
- **Storage:** Minimal disk space for the application
- **Network:** HTTP/HTTPS and WebSocket support

## 🤝 Contributing

We welcome contributions! Here's how you can help:

1. **Fork the repository**
2. **Create a feature branch:** `git checkout -b feature/amazing-feature`
3. **Make your changes** and test thoroughly
4. **Commit your changes:** `git commit -m 'Add amazing feature'`
5. **Push to the branch:** `git push origin feature/amazing-feature`
6. **Open a Pull Request**

### Development Setup

```bash
git clone https://github.com/blinkerbit/aird.git
cd aird
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
pip install -e .  # Install in development mode
```

## � Contributors & Thanks

We extend our heartfelt gratitude to all contributors who have helped make aird better:


### 💡 Community Contributors
A special thanks to everyone who has contributed through:
- **Bug reports and feature requests**
- **Code contributions and pull requests** 
- **Documentation improvements**
- **Testing and feedback**
- **Spreading the word about aird**

### 🔧 Feature Contributors
Thanks to all contributors who helped implement key features:
- **File editing capabilities** with syntax highlighting
- **Mobile-responsive design** and resizable columns
- **Real-time streaming** with WebSocket support
- **Admin panel** with live feature toggling
- **Security enhancements** and path traversal protection

*Want to see your name here? Check out our [Contributing Guidelines](#-contributing) and join our community!*

---

**🎉 Thank you to all our stars, forks, and users who make this project worthwhile!**

If you've benefited from aird, consider:
- ⭐ **Starring the repository**
- 🍴 **Forking and contributing**
- 🐛 **Reporting issues**
- 💝 **Sharing with others**

## �📄 License

This project is licensed under a **Custom License** that prohibits commercial use without explicit written consent from the author. See the `LICENSE` file for complete details.

### Key License Points:
- ✅ **Free for personal and non-commercial use**
- ✅ **Open source for educational purposes**
- ❌ **Commercial use requires written permission**
- ❌ **No warranty or liability coverage**

## 🔗 Links

- **GitHub Repository:** [https://github.com/blinkerbit/aird](https://github.com/blinkerbit/aird)
- **PyPI Package:** [https://pypi.org/project/aird/](https://pypi.org/project/aird/)
- **Issue Tracker:** [https://github.com/blinkerbit/aird/issues](https://github.com/blinkerbit/aird/issues)

## 🎯 Roadmap & Future Plans

### 🔜 Upcoming Features
- **File Previews:** In-browser previews for images, PDFs, and Markdown
- **Search & Sort:** Advanced search functionality and sortable columns
- **Multi-File Operations:** Batch actions for multiple files
- **Theme Support:** Dark mode and customizable themes

### 🚀 Advanced Features (Planned)
- **File History:** Version tracking and backup features
- **API Integration:** RESTful API for external integrations
- **Plugin System:** Extensible architecture for custom features

---

**Made with ❤️ by Viswantha Srinivas P**

*Star ⭐ this repo if you find it useful!*
