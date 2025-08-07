# Aird - A Lightweight Web-Based File Browser

Aird is a simple, lightweight, and fast web-based file browser and streamer built with Python and Tornado. It allows you to browse, download, upload, rename, and delete files on your server through a clean and modern web interface.

## Features

- **File Browser:** Navigate through your server's directory structure.
- **File Operations:**
  - Download files.
  - Upload files (can be disabled).
  - Delete files and directories (can be disabled).
  - Rename files and directories (can be disabled).
- **Authentication:**
  - Secure access with a simple access token.
  - LDAP/Active Directory integration for enterprise environments.
- **Admin Panel:** A dedicated admin area to toggle features like file uploads, deletions, and renames on the fly.
- **Real-time Updates:** Feature changes in the admin panel are reflected instantly for all connected users without needing a page refresh, thanks to WebSockets.
- **Configurable:** Easily configure the server through command-line arguments or a JSON configuration file.

## Installation

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/blinkerbit/aird.git
    cd aird
    ```

2.  **Install dependencies:**
    It is recommended to use a virtual environment.
    ```bash
    python -m venv venv
    source venv/bin/activate  # On Windows, use `venv\Scripts\activate`
    pip install -r requirements.txt
    ```

3.  **Install the package:**
    ```bash
    pip install .
    ```

## Usage

You can run Aird using the `aird` command.

### Basic Usage

To start the server with a simple access token, run:

```bash
aird --port 8888 --access-token "your-secret-token"
```

This will start the server on port 8888. You can access it by navigating to `http://localhost:8888` in your web browser. You will be prompted to enter the access token to log in.

### Command-Line Arguments

| Argument          | Description                                                              | Default                |
| ----------------- | ------------------------------------------------------------------------ | ---------------------- |
| `--host`          | The host address to bind to.                                             | `0.0.0.0`              |
| `--port`          | The port to listen on.                                                   | `8888`                 |
| `--root-dir`      | The root directory to serve files from.                                  | Current directory      |
| `--access-token`  | The token required for user login.                                       | `None`                 |
| `--admin-token`   | The token required for admin login.                                      | `None`                 |
| `--config`        | Path to a JSON configuration file.                                       | `None`                 |
| `--enable-ldap`   | Enable LDAP authentication.                                              | `False`                |
| `--ldap-server`   | The LDAP server address.                                                 | `None`                 |
| `--ldap-base-dn`  | The base DN for LDAP searches.                                           | `None`                 |

### Configuration File

You can also use a JSON file to configure Aird.

**Example `config.json`:**
```json
{
  "host": "0.0.0.0",
  "port": 8080,
  "root_dir": "/path/to/your/files",
  "access_token": "your-secret-token",
  "admin_token": "your-admin-secret-token",
  "enable_ldap": false,
  "ldap_server": null,
  "ldap_base_dn": null,
  "feature_flags": {
    "file_upload": true,
    "file_delete": false,
    "file_rename": true,
    "file_download": true
  }
}
```

To run with a configuration file:
```bash
aird --config /path/to/config.json
```

### LDAP Authentication

To use LDAP authentication, you need to provide the LDAP server and base DN.

```bash
aird --enable-ldap --ldap-server "ldap://your.ldap.server" --ldap-base-dn "ou=users,dc=example,dc=com"
```

Users can then log in with their LDAP credentials.

## Admin Panel

The admin panel allows you to enable or disable features in real-time.

1.  **Start the server with an admin token:**
    ```bash
    aird --admin-token "your-admin-secret-token"
    ```

2.  **Access the admin panel:**
    Navigate to `http://localhost:8888/admin` and log in with the admin token.

3.  **Manage Features:**
    You can toggle file uploads, deletions, and renames. The changes will be applied to all active user sessions immediately.

## License

This project is licensed under the MIT License. See the `LICENSE` file for details.
- [ ] **File Previews:** Add support for in-browser previews for common file types (images, PDFs, Markdown).
- [ ] **Search and Sort:** Add a search bar and make the directory listing sortable by name, size, or modification date.

### Core Functionality
- [ ] **Multi-File Operations:** Allow users to select multiple files/folders for batch actions (e.g., delete, download as zip).
- [ ] **In-Browser File Editor:** Embed a code editor like CodeMirror or Monaco to allow for in-browser text file editing.
- [ ] **User Management:** Expand to a full user management system with different roles and permissions.
- [ ] **Advanced Permissions:** Implement a role-based permission system (e.g., read-only users, upload-only users).
- [ ] **Create Files and Folders:** Add UI elements to create new empty files and folders.

### Performance
- [ ] **Asynchronous File I/O:** Use `aiofiles` to perform file operations asynchronously.
- [ ] **Pagination for Large Directories:** Implement pagination for directories with a large number of files.
