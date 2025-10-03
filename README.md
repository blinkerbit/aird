# Aird - A Lightweight Web-Based File Browser, Editor and Share

**v0.4.3** - 🔐 **Secure File Sharing + Advanced Share Management + Token Security!**

A modern, lightweight, and fast web-based file browser, editor, and streamer built with Python and Tornado. It provides a comprehensive file management solution with real-time streaming, in-browser editing, and mobile-responsive design through a clean and intuitive web interface.

Note: Currently using this in conjunction with WireGuard to handle file management and sharing for internal/private cloud.

## 🆕 What's New in v0.4.3

### 🔐 **Secure File Sharing (NEW!)**
- **Secret Token Protection:** Generate secure, randomly generated tokens for share access control
- **Token-based Authentication:** Users must enter secret tokens to access shared files
- **Public/Private Shares:** Choose between token-protected or public access for shares
- **Token Management:** Enable/disable tokens, generate new tokens, and manage share security
- **Secure URL Generation:** Cryptographically secure share IDs and tokens using `secrets` module
- **Session Persistence:** Tokens stored in cookies and Authorization headers for seamless access

### 📁 **Advanced Share Management (NEW!)**
- **Complete Share Parameter Control:** Modify all share settings after creation
- **Share Type Configuration:** Switch between Static (snapshot) and Dynamic (live folder) shares
- **File Filtering System:** Advanced allow/avoid list filtering using glob patterns
- **Real-time Share Updates:** Changes apply instantly without recreating shares
- **Visual Share Management:** Intuitive interface showing current settings with visual indicators
- **Parameter Modification:** Update share type, token settings, file filters, and access controls

### 🎯 **Dynamic vs Static Shares (NEW!)**
- **Static Shares:** Snapshot of files at creation time - perfect for archival sharing
- **Dynamic Shares:** Live folder sharing - new files automatically appear in the share
- **Real-time Updates:** Dynamic shares reflect current folder contents in real-time
- **Flexible Sharing:** Choose the right sharing method for your use case
- **Automatic File Discovery:** Dynamic shares automatically include new files added to folders

### 🔍 **Advanced File Filtering (NEW!)**
- **Glob Pattern Support:** Use powerful glob patterns for file inclusion/exclusion
- **Allow Lists:** Specify which files to include using patterns like `*.pdf`, `documents/**`
- **Avoid Lists:** Exclude files using patterns like `*.tmp`, `.git/**`, `temp/*`
- **Priority System:** Avoid lists take priority over allow lists for security
- **Pattern Examples:** Support for recursive matching with `**` and complex patterns
- **Real-time Filtering:** Filters apply instantly to both static and dynamic shares

### 🛠️ **Enhanced Share Management UI (NEW!)**
- **Current Settings Display:** Shows actual share configuration instead of defaults
- **Visual Indicators:** "← Current" labels show which settings are currently active
- **Form Pre-population:** All fields automatically populated with current values
- **Mutually Exclusive Controls:** Token enable/disable checkboxes work together
- **Debug Information:** Console logging for troubleshooting share management
- **Success Notifications:** Clear feedback when shares are updated successfully

### 🔧 **Backend API Enhancements (NEW!)**
- **Share Update API:** New `/share/update` endpoint for modifying existing shares
- **Database Schema Migration:** Automatic database updates for new share features
- **Token Management:** Secure token generation, storage, and validation
- **Parameter Validation:** Comprehensive validation of all share parameters
- **Debug Endpoints:** Troubleshooting endpoints for share management issues
- **Enhanced Error Handling:** Better error messages and status codes

## 🆕 What's New in v0.4.2

### 🎨 **Custom HTML/CSS Popups (NEW!)**
- **Modern Dialog System:** Replaced all native browser alerts, confirms, and prompts with custom HTML/CSS modals
- **Consistent UI/UX:** Beautiful, responsive popups that match the application's design language
- **Enhanced User Experience:** Better visual feedback with styled buttons, proper spacing, and smooth animations
- **Cross-browser Compatibility:** Custom popups work consistently across all browsers and devices
- **Keyboard Support:** Full keyboard navigation with Enter to confirm and Escape to cancel
- **Accessibility:** Improved accessibility with proper focus management and screen reader support

### 🔗 **Enhanced Share Management (NEW!)**
- **Dynamic File Management:** Add and remove files from existing shares without recreating them
- **Interactive File Browser:** Navigate through directories to select additional files for sharing
- **Real-time Share Updates:** Instantly modify share contents with live preview of changes
- **Improved Share Modal:** Enhanced share management interface with better organization and controls
- **File Selection Preview:** See selected files before adding them to shares
- **Directory Navigation:** Full directory tree navigation within the share management interface

### 🗂️ **Advanced File Browser Navigation (NEW!)**
- **Full Directory Tree Access:** Navigate through all accessible directories when managing shares
- **Smart Path Handling:** Proper path construction and navigation for complex directory structures
- **File Type Recognition:** Enhanced file icons and type detection for better visual organization
- **Breadcrumb Navigation:** Clear path display showing current location in the directory tree
- **Error Handling:** Robust error handling with user-friendly messages for navigation issues

### 🛠️ **Backend API Enhancements (NEW!)**
- **Share Update API:** New endpoint for modifying existing shares with file additions/removals
- **Database Migration:** Automatic database schema updates for new share management features
- **Improved Error Handling:** Better error messages and status codes for API operations
- **Path Validation:** Enhanced security with proper path validation and sanitization

---

## 🆕 What's New in v0.4.1

### ⚙️ **WebSocket Admin Configuration (NEW!)**
- **Dynamic Settings:** Adjust WebSocket connection limits and timeouts through admin UI
- **Real-time Changes:** Settings apply instantly without server restart
- **Granular Control:** Separate limits for feature flags, file streaming, and search handlers
- **Performance Tuning:** Optimize for your specific traffic patterns and system resources
- **Live Statistics:** View connection stats at `/admin/websocket-stats`

### 🔍 **Super Search (NEW!)**
- **Powerful File Content Search:** Search through file contents across your entire directory tree
- **Real-time WebSocket Results:** Live search results as you type with instant feedback
- **Advanced Pattern Matching:** Support for regex patterns and multiple search terms
- **Path-aware Search:** Automatically filters results based on current directory context
- **Performance Optimized:** Fast searching even in large codebases using memory-mapped operations

### 🐍 **Enhanced Python File Icons (NEW!)**
- **Smart Python File Recognition:** Distinctive icons for different Python file types
- **Source Files (.py, .pyw):** Enhanced snake with gem (🐍💎) indicating precious/valuable source code
- **Compiled Files (.pyc, .pyo):** Snake with lightning (🐍⚡) representing fast/optimized compiled code
- **Better Visual Distinction:** Easily differentiate between source and compiled Python files at a glance
- **Consistent Branding:** Maintains Python's snake identity while adding meaningful visual context

### ⚡ **Performance & Usability Upgrades**
- **Direct Executable:** Run with simple `aird` command instead of `python -m aird`
- **50-80% Faster:** Memory-mapped file operations for massive performance gains
- **Enhanced Security:** CSRF protection, XSS prevention, and improved input validation
- **Memory Efficient:** Handle GB-sized files with constant ~64KB memory usage

---

## 🚀 Installation

### Option 1: Install from PyPI (Recommended)
```bash
pip install aird
```

### Option 2: Install from Source
1. **Clone the repository:**
   ```bash
   git clone https://github.com/blinkerbit/aird.git
   cd aird
   ```

2. **Create a virtual environment (recommended):**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Install the package:**
   ```bash
   pip install .
   ```

Run directly:
```bash
aird --help
```

<img width="1696" height="715" alt="image" src="https://github.com/user-attachments/assets/95a9569d-5d0c-4d96-aab9-69e0b4cd98bf" />

<img width="1599" height="1126" alt="image" src="https://github.com/user-attachments/assets/1551cafc-1d6e-4668-86fb-b1acb5fdb7b2" />

## ✨ Features

### 🔍 Super Search (NEW!)
- **Powerful Content Search:** Search through file contents across your entire directory tree with real-time results
- **WebSocket-powered:** Live search results as you type with instant feedback and progress indicators
- **Advanced Pattern Matching:** Support for regex patterns, case-sensitive/insensitive search, and multiple search terms
- **Path-aware Filtering:** Automatically filters results based on current directory context for focused searching
- **Smart Result Display:** Shows matching lines with context, file paths, and line numbers for easy navigation
- **Performance Optimized:** Fast searching even in large codebases using memory-mapped file operations
- **Interactive Interface:** Click any result to instantly navigate to the file and line

### 📁️ File Management
- **Smart File Browser:** Navigate through your server's directory structure with resizable columns and mobile-friendly design
- **Enhanced File Icons:** Intelligent file type recognition with special Python file icons:
  - **Python Source (\*.py, \*.pyw):** 🐍💎 Snake with gem (precious source code)
  - **Python Compiled (\*.pyc, \*.pyo):** 🐍⚡ Snake with lightning (optimized/fast)
  - **50+ File Types:** Comprehensive icon coverage for all common file formats
- **Advanced File Operations:**
  - Download files with progress indicators and compression support
  - Upload files with drag-and-drop support (can be disabled)
  - Delete files and directories (can be disabled)
  - Rename files and directories (can be disabled)
  - **In-browser File Editing:** Full-featured editor with syntax highlighting, line numbers, and memory safety
  - **Range-based Viewing:** View specific line ranges (start/end) without loading entire files
  - **Line-by-line Streaming:** Real-time file streaming for monitoring logs and large files
- **File Sharing:** Create secure, temporary public links for files and directories
  - Select multiple files and folders to share together
  - Generate unique, time-limited shareable URLs
  - No login required for shared link access
  - Manage active shares with easy revocation

### 📡 Real-time Streaming & Editing
- **WebSocket-based File Streaming:** Stream large files with animated progress indicators
- **Configurable Tail Lines:** Control how many recent lines to display when streaming (customizable Last N parameter)
- **Live File Monitoring:** Real-time updates as files change, perfect for log monitoring
- **Range-based File Viewing:** View specific line ranges without loading entire files
- **Dedicated Edit Mode:** Full-featured in-browser editor with:
  - Syntax highlighting and line numbers
  - Memory-safe editing (prevents loading huge files)
  - Save/Cancel operations with confirmation
  - Separate edit view for focused editing experience
- **Performance Optimized:** Stream line-by-line without loading entire files into memory
- **Memory Efficient:** Handles large files gracefully with size limits and streaming

### 🔐 Security & Authentication
- **Token-based Authentication:** Secure access with customizable access tokens
- **LDAP/Active Directory Integration:** Enterprise-grade authentication support with full LDAP user management
- **Dual Authentication Modes:** Support for both LDAP and token-based authentication
- **Secure Session Management:** HTTP-only cookies with CSRF protection and secure session handling
- **Path Traversal Protection:** Built-in security measures to prevent unauthorized access
- **Input Validation:** Comprehensive input sanitization and length validation for security

### ⚙️ Administration
- **Admin Panel:** Dedicated admin interface to toggle features on the fly
- **Feature Flags:** Granular control over file operations (upload, delete, rename, edit, download)
- **Real-time Configuration:** Changes apply instantly without server restart

### 📱 Modern UI/UX
- **Mobile-Responsive Design:** Optimized for smartphones and tablets with touch-friendly controls
- **Real-time Search Interface:** Modern Super Search with live results and WebSocket updates
- **Resizable Columns:** Customize the file browser layout to your preference
- **Animated Indicators:** Visual feedback for streaming, searching, and loading operations
- **Intuitive Interface:** Clean, minimalist design with well-organized toolbars
- **Streaming Controls:** Dedicated streaming toolbar with configurable options (Last N lines, play/stop controls)
- **Interactive Results:** Click-to-navigate search results with context highlighting
- **Keyboard Shortcuts:** Efficient navigation and operations via keyboard
- **Clean Layout:** Left-aligned file actions, right-aligned streaming controls for better organization
- **Custom HTML/CSS Popups (NEW!):** Modern dialog system replacing all native browser popups
- **Enhanced Share Management UI (NEW!):** Improved share management interface with better organization
- **File Browser Navigation (NEW!):** Full directory tree navigation with breadcrumb display

## 📖 Usage

### Quick Start

After installation, you can run Aird using the `aird` command:

```bash
# Basic usage with a specific port
aird --port 8000

# A token will be generated and printed. To specify one:
aird --port 8000 --token "your-secret-token"

# With admin capabilities
aird --port 8000 --token "user-token" --admin-token "admin-token"

# Serve from a specific directory
aird --root "/path/to/files" --token "your-token"

# Enable SSL/HTTPS
aird --ssl-cert "/path/to/certificate.crt" --ssl-key "/path/to/private.key" --token "your-token"
```

Navigate to `http://localhost:8000` and enter your access token to start browsing files.

### 🎮 Command-Line Arguments

| Argument        | Description                                         | Default                               |
| --------------- | --------------------------------------------------- | ------------------------------------- |
| `--port`        | The port to listen on                               | `8000`                                |
| `--root`        | The root directory to serve files from              | Current directory                     |
| `--token`       | The token required for user login (fallback for LDAP) | (auto-generated)                      |
| `--admin-token` | The token required for admin login                  | (auto-generated)                      |
| `--hostname`    | Host name for the server, used for display          | (auto-detected)                       |
| `--config`      | Path to a JSON configuration file                   | `None`                                |
| `--ldap`        | Enable LDAP authentication                          | `False`                               |
| `--ldap-server` | The LDAP server address (required if --ldap)        | `None`                                |
| `--ldap-base-dn`| The base DN for LDAP searches (required if --ldap)  | `None`                                |
| `--ldap-user-template` | LDAP user template for authentication (required if --ldap) | `None` |
| `--ldap-filter-template` | LDAP filter template for user search (required if --ldap) | `None` |
| `--ldap-ssl-cert` | Path to LDAP client SSL certificate file (this feature is in progress, do not use this flag)| `None` |
| `--ldap-ssl-key` | Path to LDAP client SSL private key file( this feature is in progress, do not use this flag) | `None` |
| `--ssl-cert` | Path to SSL certificate file for HTTPS | `None` |
| `--ssl-key` | Path to SSL private key file for HTTPS | `None` |

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
  "ldap": false,
  "ldap_server": "ldap://your.ldap.server:389",
  "ldap_base_dn": "ou=users,dc=example,dc=com",
  "ldap_user_template": "uid={username}",
  "ldap_filter_template": "(&(objectClass=person)(uid={username}))",
  "ldap_attributes": ["cn", "mail", "memberOf"],
  "ldap_attribute_map": [
    {"memberOf": "cn=aird-users,ou=groups,dc=example,dc=com"}
  ],
  "ldap_ssl_cert": "/path/to/ldap/client.crt",
  "ldap_ssl_key": "/path/to/ldap/client.key",
  "admin_users": ["admin1", "admin2", "john.doe"],
  "ssl_cert": "/path/to/your/certificate.crt",
  "ssl_key": "/path/to/your/private.key",
  "feature_flags": {
    "file_upload": true,
    "file_delete": true,
    "file_rename": true,
    "file_download": true,
    "file_edit": true,
    "file_share": true,
    "super_search": true
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

### 🔐 LDAP Authentication & Authorization

Aird provides comprehensive LDAP/Active Directory integration for enterprise environments with advanced user management capabilities.

#### **LDAP Authentication Features**
- **Enterprise Integration:** Full LDAP/Active Directory support for corporate environments
- **Dual Authentication Modes:** LDAP users can authenticate with domain credentials
- **Fallback Token Support:** Token-based authentication as backup for non-LDAP users
- **Secure Session Management:** HTTP-only cookies with CSRF protection
- **User Attribute Mapping:** Advanced LDAP attribute mapping for user authorization
- **Group-based Authorization:** Support for LDAP group membership validation

#### **LDAP Configuration**

**Command Line Setup:**
```bash
# Basic LDAP configuration
aird --ldap \
     --ldap-server "ldap://your.ldap.server:389" \
     --ldap-base-dn "ou=users,dc=example,dc=com" \
     --ldap-user-template "uid={username},{ldap_base_dn}" \
     --ldap-filter-template "(&(objectClass=person)(uid={username}))" \
     --token "fallback-token"

# With SSL/TLS encryption
aird --ldap \
     --ldap-server "ldaps://your.ldap.server:636" \
     --ldap-base-dn "ou=users,dc=example,dc=com" \
     --ldap-user-template "uid={username},{ldap_base_dn}" \
     --ldap-filter-template "(&(objectClass=person)(uid={username}))" \
     --token "fallback-token"

# With LDAP client certificate authentication
aird --ldap \
     --ldap-server "ldaps://your.ldap.server:636" \
     --ldap-base-dn "ou=users,dc=example,dc=com" \
     --ldap-user-template "uid={username},{ldap_base_dn}" \
     --ldap-filter-template "(&(objectClass=person)(uid={username}))" \
     --ldap-ssl-cert "/path/to/ldap/client.crt" \
     --ldap-ssl-key "/path/to/ldap/client.key" \
     --token "fallback-token"
```

**Configuration File Setup:**
```json
{
  "ldap": true,
  "ldap_server": "ldap://your.ldap.server:389",
  "ldap_base_dn": "ou=users,dc=example,dc=com",
  "ldap_user_template": "uid={username},{ldap_base_dn}",
  "ldap_filter_template": "(&(objectClass=person)(uid={username}))",
  "ldap_ssl_cert": "/path/to/ldap/client.crt",
  "ldap_ssl_key": "/path/to/ldap/client.key",
  "ldap_attributes": ["cn", "mail", "memberOf"],
  "ldap_attribute_map": [
    {"memberOf": "cn=aird-users,ou=groups,dc=example,dc=com"}
  ],
  "token": "fallback-token"
}
```

#### **LDAP Filter Template Configuration**

The `ldap_filter_template` parameter defines the LDAP search filter used to locate users in the directory. This is a critical component for LDAP authentication as it determines how users are found and validated.

**Key Features:**
- **Flexible Search Patterns:** Support for complex LDAP filter expressions
- **Username Substitution:** Use `{username}` placeholder for dynamic user search
- **Object Class Filtering:** Filter by specific LDAP object classes (person, user, etc.)
- **Attribute-based Search:** Search by any LDAP attribute (uid, sAMAccountName, cn, etc.)

**Common Filter Templates:**

| LDAP Server Type | Filter Template | Description |
|------------------|-----------------|-------------|
| **OpenLDAP** | `(&(objectClass=person)(uid={username}))` | Standard person object with uid attribute |
| **Active Directory** | `(&(objectClass=user)(sAMAccountName={username}))` | AD user with sAMAccountName |
| **Generic LDAP** | `(&(objectClass=inetOrgPerson)(uid={username}))` | RFC 2798 inetOrgPerson object |
| **Custom Schema** | `(&(objectClass=myUser)(myUsername={username}))` | Custom object class and attribute |

**Advanced Filter Examples:**
```bash
# Search for active users only
"(&(objectClass=person)(uid={username})(!(accountStatus=disabled)))"

# Search with multiple attributes
"(&(objectClass=person)(|(uid={username})(mail={username})))"

# Search with organizational unit filtering
"(&(objectClass=person)(uid={username})(ou=employees))"
```

#### **LDAP SSL Client Certificate Authentication**

Aird supports LDAP client certificate authentication for enhanced security. This allows the LDAP client to authenticate using SSL certificates instead of or in addition to username/password authentication.

**Key Features:**
- **Client Certificate Authentication:** Use SSL certificates for LDAP client authentication
- **TLS Before Bind:** Secure LDAP communication with TLS encryption
- **Certificate-based Authentication:** Enhanced security with client certificates
- **Flexible Configuration:** Support for both command line and configuration file setup

**LDAP SSL Configuration:**

**Command Line Setup:**
```bash
# LDAP with client certificate authentication
aird --ldap \
     --ldap-server "ldaps://your.ldap.server:636" \
     --ldap-base-dn "ou=users,dc=example,dc=com" \
     --ldap-user-template "uid={username},{ldap_base_dn}" \
     --ldap-filter-template "(&(objectClass=person)(uid={username}))" \
     --ldap-ssl-cert "/path/to/ldap/client.crt" \
     --ldap-ssl-key "/path/to/ldap/client.key" \
     --token "fallback-token"
```

**Configuration File Setup:**
```json
{
  "ldap": true,
  "ldap_server": "ldaps://your.ldap.server:636",
  "ldap_base_dn": "ou=users,dc=example,dc=com",
  "ldap_user_template": "uid={username},{ldap_base_dn}",
  "ldap_filter_template": "(&(objectClass=person)(uid={username}))",
  "ldap_ssl_cert": "/path/to/ldap/client.crt",
  "ldap_ssl_key": "/path/to/ldap/client.key",
  "ldap_attributes": ["cn", "mail", "memberOf"],
  "ldap_attribute_map": [
    {"memberOf": "cn=aird-users,ou=groups,dc=example,dc=com"}
  ]
}
```

**LDAP SSL Certificate Requirements:**
- **Certificate Format:** PEM format (.crt, .pem, .cert files)
- **Private Key Format:** PEM format (.key files)
- **Certificate Chain:** Include intermediate certificates if required
- **Key Size:** Minimum 2048-bit RSA or equivalent ECDSA keys
- **Validity:** Ensure certificates are not expired
- **LDAP Server Compatibility:** Verify LDAP server supports client certificate authentication

**LDAP SSL Security Features:**
- **TLS Encryption:** All LDAP communication encrypted with TLS
- **Client Certificate Authentication:** Enhanced security with certificate-based authentication
- **Certificate Validation:** Automatic certificate and key file validation
- **Secure Context:** Uses Python's ssl.create_default_context() for optimal security
- **Perfect Forward Secrecy:** Modern cipher suites for enhanced security

**LDAP SSL Configuration Examples:**

**Self-Signed Client Certificate:**
```bash
# Generate LDAP client certificate (for testing)
openssl req -newkey rsa:4096 -keyout ldap_client.key -out ldap_client.crt -days 365 -nodes

# Run Aird with LDAP client certificate
aird --ldap \
     --ldap-server "ldaps://your.ldap.server:636" \
     --ldap-ssl-cert "ldap_client.crt" \
     --ldap-ssl-key "ldap_client.key" \
     --ldap-base-dn "ou=users,dc=example,dc=com" \
     --ldap-user-template "uid={username},{ldap_base_dn}" \
     --ldap-filter-template "(&(objectClass=person)(uid={username}))"
```

**Production LDAP SSL Configuration:**
```json
{
  "ldap": true,
  "ldap_server": "ldaps://ldap.company.com:636",
  "ldap_base_dn": "ou=people,dc=company,dc=com",
  "ldap_user_template": "uid={username},ou=people,dc=company,dc=com",
  "ldap_filter_template": "(&(objectClass=person)(uid={username}))",
  "ldap_ssl_cert": "/etc/ssl/certs/ldap_client.crt",
  "ldap_ssl_key": "/etc/ssl/private/ldap_client.key",
  "ldap_attributes": ["cn", "mail", "memberOf"],
  "ldap_attribute_map": [
    {"memberOf": "cn=aird-users,ou=groups,dc=company,dc=com"}
  ]
}
```

**LDAP SSL Troubleshooting:**

**Common Issues:**
- **Certificate Not Found:** Ensure LDAP certificate and key file paths are correct
- **Permission Denied:** Check file permissions (certificate should be readable, key should be 600)
- **Invalid Certificate:** Verify certificate format and validity
- **LDAP Server Rejection:** Ensure LDAP server is configured to accept client certificates
- **Certificate Mismatch:** Verify certificate is issued for the correct LDAP server

**LDAP SSL Best Practices:**
- **Use Strong Certificates:** 2048-bit RSA or 256-bit ECDSA minimum
- **Regular Renewal:** Set up automatic certificate renewal
- **Secure Storage:** Store private keys securely with restricted permissions
- **Monitor Expiry:** Set up alerts for certificate expiration
- **Server Compatibility:** Ensure LDAP server supports client certificate authentication
- **Certificate Authority:** Use certificates from trusted Certificate Authorities

#### **LDAP User Management**
- **Automatic User Registration:** LDAP users are automatically added to Aird's database on first login
- **Seamless Integration:** No manual user creation required - users are created automatically
- **User Tracking:** Track user login history and activity in Aird's database
- **Automatic User Discovery:** Users are automatically discovered from LDAP directory
- **Group Membership Validation:** Validate user access based on LDAP group membership
- **Attribute-based Authorization:** Use LDAP attributes for fine-grained access control
- **Real-time User Search:** Live user search functionality for share management
- **User Profile Integration:** Display LDAP user information in the interface

#### **LDAP Security Features**
- **Secure Bind Operations:** Encrypted LDAP connections with SSL/TLS support
- **Input Validation:** Comprehensive validation of LDAP queries and user inputs
- **Error Handling:** Secure error messages that don't leak sensitive information
- **Session Security:** Secure cookie handling with proper HTTP-only and SameSite attributes
- **CSRF Protection:** Built-in CSRF protection for all LDAP-authenticated sessions

#### **LDAP Login Interface**
When LDAP is enabled, users see a simplified login interface:
- **Username/Password Fields:** Clean interface for LDAP credentials
- **Automatic Detection:** System automatically detects LDAP mode
- **Secure Authentication:** All credentials are validated against LDAP server
- **Session Management:** Secure session creation upon successful authentication

#### **LDAP Integration Benefits**
- **Enterprise Ready:** Seamless integration with existing corporate infrastructure
- **Centralized User Management:** No need to manage separate user accounts
- **Automatic User Provisioning:** LDAP users are automatically created in Aird on first login
- **Group-based Access Control:** Leverage existing LDAP groups for authorization
- **Audit Trail:** Full integration with corporate audit and logging systems
- **Single Sign-On Ready:** Compatible with SSO solutions and identity providers
- **Zero Configuration:** No manual user setup required - everything happens automatically

#### **LDAP Troubleshooting & Best Practices**

**Common LDAP Issues:**
- **Connection Timeouts:** Ensure LDAP server is accessible and firewall rules allow connections
- **Authentication Failures:** Verify user DN format and base DN configuration
- **SSL/TLS Issues:** Check certificate validity and SSL/TLS configuration
- **Group Membership:** Ensure proper LDAP group structure and user membership

**LDAP Best Practices:**
- **Use SSL/TLS:** Always use `ldaps://` for production environments
- **Proper DN Structure:** Use consistent organizational unit (OU) structure
- **Group Management:** Create dedicated groups for Aird users and administrators
- **Regular Testing:** Test LDAP connectivity and user authentication regularly
- **Backup Authentication:** Always maintain token-based fallback for emergency access

**LDAP Configuration Examples:**

**Active Directory:**
```json
{
  "ldap": true,
  "ldap_server": "ldaps://ad.company.com:636",
  "ldap_base_dn": "dc=company,dc=com",
  "ldap_user_template": "{username}@company.com",
  "ldap_filter_template": "(&(objectClass=user)(sAMAccountName={username}))",
  "ldap_attributes": ["cn", "mail", "memberOf"],
  "ldap_attribute_map": [
    {"memberOf": "CN=Aird-Users,OU=Groups,DC=company,DC=com"}
  ]
}
```

**OpenLDAP:**
```json
{
  "ldap": true,
  "ldap_server": "ldap://ldap.company.com:389",
  "ldap_base_dn": "ou=people,dc=company,dc=com",
  "ldap_user_template": "uid={username},ou=people,dc=company,dc=com",
  "ldap_filter_template": "(&(objectClass=person)(uid={username}))",
  "ldap_attributes": ["cn", "mail", "memberOf"],
  "ldap_attribute_map": [
    {"memberOf": "cn=aird-users,ou=groups,dc=company,dc=com"}
  ]
}
```

### 🔒 SSL/HTTPS Support

Aird supports SSL/HTTPS encryption for secure file access and management. SSL can be configured through command line arguments or configuration files.

#### **SSL Configuration**

**Command Line Setup:**
```bash
# Basic SSL configuration
aird --ssl-cert "/path/to/certificate.crt" --ssl-key "/path/to/private.key"

# SSL with custom port
aird --port 8443 --ssl-cert "/path/to/certificate.crt" --ssl-key "/path/to/private.key"

# SSL with LDAP authentication
aird --ldap \
     --ldap-server "ldaps://your.ldap.server:636" \
     --ldap-base-dn "ou=users,dc=example,dc=com" \
     --ldap-user-template "uid={username},{ldap_base_dn}" \
     --ldap-filter-template "(&(objectClass=person)(uid={username}))" \
     --ssl-cert "/path/to/certificate.crt" \
     --ssl-key "/path/to/private.key"
```

**Configuration File Setup:**
```json
{
  "port": 8443,
  "ssl_cert": "/path/to/your/certificate.crt",
  "ssl_key": "/path/to/your/private.key",
  "token": "your-secret-token"
}
```

#### **SSL Certificate Requirements**

- **Certificate Format:** PEM format (.crt, .pem, .cert files)
- **Private Key Format:** PEM format (.key files)
- **Certificate Chain:** Include intermediate certificates if required
- **Key Size:** Minimum 2048-bit RSA or equivalent ECDSA keys
- **Validity:** Ensure certificates are not expired

#### **SSL Security Features**

- **TLS 1.2+ Support:** Modern TLS protocol support for secure connections
- **Certificate Validation:** Automatic certificate and key file validation
- **Secure Context:** Uses Python's ssl.create_default_context() for optimal security
- **Client Authentication:** Supports client certificate authentication
- **Perfect Forward Secrecy:** Modern cipher suites for enhanced security

#### **SSL Configuration Examples**

**Self-Signed Certificate:**
```bash
# Generate self-signed certificate (for testing)
openssl req -x509 -newkey rsa:4096 -keyout private.key -out certificate.crt -days 365 -nodes

# Run Aird with self-signed certificate
aird --ssl-cert "certificate.crt" --ssl-key "private.key"
```

**Let's Encrypt Certificate:**
```bash
# Use Let's Encrypt certificates
aird --ssl-cert "/etc/letsencrypt/live/yourdomain.com/fullchain.pem" \
     --ssl-key "/etc/letsencrypt/live/yourdomain.com/privkey.pem"
```

**Production Configuration:**
```json
{
  "port": 443,
  "ssl_cert": "/etc/ssl/certs/yourdomain.com.crt",
  "ssl_key": "/etc/ssl/private/yourdomain.com.key",
  "hostname": "yourdomain.com",
  "token": "your-secure-token"
}
```

#### **SSL Troubleshooting**

**Common Issues:**
- **Certificate Not Found:** Ensure certificate and key file paths are correct
- **Permission Denied:** Check file permissions (certificate should be readable, key should be 600)
- **Invalid Certificate:** Verify certificate format and validity
- **Port Conflicts:** Ensure port is not already in use

**SSL Best Practices:**
- **Use Strong Certificates:** 2048-bit RSA or 256-bit ECDSA minimum
- **Regular Renewal:** Set up automatic certificate renewal
- **Secure Storage:** Store private keys securely with restricted permissions
- **Monitor Expiry:** Set up alerts for certificate expiration
- **Use Trusted CAs:** Prefer certificates from trusted Certificate Authorities

### 👑 Admin User Management

Aird supports automatic admin privilege assignment through configuration, making it easy to manage administrative access for LDAP and local users.

#### **Admin User Configuration**

**Configuration File Setup:**
```json
{
  "admin_users": ["admin1", "admin2", "john.doe", "jane.smith"],
  "ldap": true,
  "ldap_server": "ldaps://your.ldap.server:636",
  "ldap_base_dn": "ou=users,dc=example,dc=com"
}
```

**Key Features:**
- **Automatic Assignment:** Admin privileges are assigned automatically at startup and during first login
- **LDAP Integration:** Works seamlessly with LDAP authentication
- **Dynamic Updates:** Admin privileges are updated when users log in
- **Flexible Configuration:** Support for both LDAP and local users

#### **How Admin Assignment Works**

**At Startup:**
- Aird reads the `admin_users` list from configuration
- Existing users in the database are immediately assigned admin privileges
- Users not yet in the database are flagged for admin assignment on first login

**During First Login:**
- New LDAP users are created with admin role if they're in the `admin_users` list
- Existing users are checked and upgraded to admin if they're in the list
- All admin assignments are logged for audit purposes

**Admin Privilege Features:**
- **Full System Access:** Admin users can access the admin panel
- **User Management:** Create, edit, and delete other users
- **System Configuration:** Modify feature flags and system settings
- **WebSocket Management:** Configure connection limits and timeouts
- **Share Management:** View and manage all file shares

#### **Admin User Examples**

**LDAP Admin Users:**
```json
{
  "ldap": true,
  "ldap_server": "ldaps://company.com:636",
  "ldap_base_dn": "ou=people,dc=company,dc=com",
  "admin_users": ["john.doe", "jane.smith", "admin.user"]
}
```

**Mixed Admin Users:**
```json
{
  "ldap": false,
  "admin_users": ["localadmin", "john.doe", "jane.smith"]
}
```

**Enterprise Configuration:**
```json
{
  "ldap": true,
  "ldap_server": "ldaps://ad.company.com:636",
  "ldap_base_dn": "dc=company,dc=com",
  "ldap_user_template": "{username}@company.com",
  "ldap_filter_template": "(&(objectClass=user)(sAMAccountName={username}))",
  "admin_users": ["it.admin", "security.team", "john.doe", "jane.smith"]
}
```

#### **Admin User Benefits**

- **Centralized Management:** All admin users defined in one configuration
- **Automatic Provisioning:** No manual admin assignment required
- **LDAP Integration:** Works with existing LDAP user management
- **Audit Trail:** All admin assignments are logged
- **Flexible Updates:** Easy to add or remove admin users
- **Security:** Admin privileges are managed through configuration

#### **Admin User Best Practices**

- **Regular Review:** Periodically review the admin_users list
- **Principle of Least Privilege:** Only assign admin to users who need it
- **Documentation:** Keep track of why users have admin privileges
- **Testing:** Test admin assignment with new users
- **Monitoring:** Monitor admin user activity through access logs

### 🔍 Super Search

The Super Search feature provides powerful, real-time content searching across your entire file system:

1. **Access Super Search:**
   - Click the "🚀 Super Search" button in the main file browser
   - Or navigate directly to `/search` after logging in

2. **Search Interface:**
   - **Search Query:** Enter text, regex patterns, or multiple terms to find
   - **Current Path:** Automatically set based on your current directory (can be modified)
   - **Case Sensitive:** Toggle case-sensitive matching on/off
   - **Max Results:** Limit the number of results to control performance

3. **Real-time Results:**
   - Results appear instantly as you type via WebSocket connection
   - See matching lines with surrounding context for better understanding
   - Click any result to navigate directly to the file and specific line
   - Progress indicators show search status and completion

4. **Advanced Features:**
   - **Regex Support:** Use regular expressions for complex pattern matching
   - **Path Filtering:** Search within specific directories or file types
   - **Context Lines:** See surrounding lines for better match understanding
   - **Performance Optimized:** Fast searching even in large codebases

**Example URLs:**
- Super Search page: `http://localhost:8888/search`
- Search with preset path: `http://localhost:8888/search?path=/specific/folder`

### 🔗 Enhanced File Sharing

The enhanced file sharing feature allows you to create and manage secure, temporary links for files and directories with advanced security and management options:

1. **Access the Share Page:**
   - Navigate to `/share` after logging in
   - Or click the "Share Files" button in the main file browser

2. **Select Files to Share:**
   - Browse directories and select files using checkboxes
   - Navigate between folders to select files from different locations
   - Use "Select All (Current Dir)" to quickly select all visible files

3. **Configure Share Settings:**
   - **Share Type:** Choose between Static (snapshot) or Dynamic (live folder) sharing
   - **Security Options:** Enable/disable secret token protection for share access
   - **File Filtering:** Set allow/avoid lists using glob patterns for file inclusion/exclusion
   - **Access Control:** Configure user restrictions and permissions

4. **Generate Share Links:**
   - Click "Generate Share Link" after configuring settings
   - Copy the generated URL using the "Copy Link" button
   - **Secure Shares:** Copy the secret token for token-protected shares
   - **Public Shares:** Share the URL directly for public access (no login required)

5. **Manage Active Shares:**
   - View all active shares in the bottom panel
   - Copy existing share links or open them in a new tab
   - **NEW:** Click "Manage" to modify existing shares with full parameter control

6. **Advanced Share Management (NEW in v0.4.3!):**
   - **Complete Parameter Control:** Modify share type, token settings, file filters, and access controls
   - **Current Settings Display:** See actual share configuration instead of defaults
   - **Visual Indicators:** "← Current" labels show which settings are currently active
   - **Real-time Updates:** Changes apply instantly without recreating the share
   - **Token Management:** Enable/disable tokens, generate new tokens, and manage share security

7. **Secure Share Access (NEW in v0.4.3!):**
   - **Token Verification:** Users must enter secret tokens to access protected shares
   - **Session Persistence:** Tokens stored in cookies and Authorization headers
   - **Public Access:** Option to disable token requirement for public sharing
   - **Token Generation:** Cryptographically secure tokens using `secrets` module

8. **File Filtering System (NEW in v0.4.3!):**
   - **Allow Lists:** Specify which files to include using patterns like `*.pdf`, `documents/**`
   - **Avoid Lists:** Exclude files using patterns like `*.tmp`, `.git/**`, `temp/*`
   - **Priority System:** Avoid lists take priority over allow lists for security
   - **Pattern Examples:** Support for recursive matching with `**` and complex patterns

9. **Dynamic vs Static Shares (NEW in v0.4.3!):**
   - **Static Shares:** Snapshot of files at creation time - perfect for archival sharing
   - **Dynamic Shares:** Live folder sharing - new files automatically appear in the share
   - **Real-time Updates:** Dynamic shares reflect current folder contents in real-time

**Example URLs:**
- Share page: `http://localhost:8888/share`
- Public shared files: `http://localhost:8888/shared/abc123def456`
- Token-protected shares: `http://localhost:8888/shared/abc123def456` (requires token entry)

## 👑 Admin Panel

The admin panel provides real-time control over server features and capabilities.

### Access the Admin Panel

1. **Start server with admin token:**
   ```bash
   aird --admin-token "your-admin-secret-token"
   ```

2. **Navigate to admin interface:**
   Visit `http://localhost:8888/admin` and authenticate with your admin token

3. **Feature Management:**
   - **File Upload:** Toggle file upload capability
   - **File Delete:** Enable/disable file and directory deletion
   - **File Rename:** Control rename functionality
   - **File Edit:** Toggle in-browser file editing
   - **File Download:** Control file download access
   - **File Share:** Enable/disable file sharing functionality

4. **WebSocket Connection Management (NEW!):**
   - **Feature Flags WebSocket:** Configure max connections (1-1000) and idle timeout (30-7200s)
   - **File Streaming WebSocket:** Optimize settings for high-traffic file operations
   - **Search WebSocket:** Tune search handler performance limits
   - **Real-time Statistics:** View live connection stats at `/admin/websocket-stats`
   - **Dynamic Configuration:** All settings apply instantly without restart

5. **LDAP User Management (NEW!):**
   - **User Discovery:** Automatically discover and manage LDAP users
   - **Group Membership:** View and manage user group memberships
   - **User Search:** Real-time search functionality for user management
   - **Access Control:** Configure user permissions based on LDAP groups
   - **User Profiles:** View and edit user profile information
   - **Authentication Status:** Monitor user authentication and session status

All changes apply immediately to all connected users via WebSocket updates.

## 🎯 Key Features in Detail

### 🔍 Super Search (Latest Feature!)
- **Content-based Search:** Find text within files across your entire directory structure
- **Real-time Results:** Live search with WebSocket updates as you type
- **Regex Support:** Advanced pattern matching with regular expressions
- **Context Display:** See matching lines with surrounding context for better understanding
- **Performance Optimized:** Memory-mapped operations for fast searching in large codebases
- **Interactive Navigation:** Click any result to jump directly to the file and line

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

### 🔗 Enhanced File Sharing System
- **Multi-file selection:** Choose multiple files and directories to share in a single link
- **Dynamic Share Management:** Add and remove files from existing shares without recreating them
- **Interactive File Browser:** Navigate through directories to select additional files for sharing
- **Real-time Share Updates:** Instantly modify share contents with live preview of changes
- **On-the-fly browsing:** Navigate directories dynamically without pre-loading all files
- **Secure URL generation:** Each share gets a unique, hard-to-guess identifier
- **Public access:** Shared files can be viewed without authentication
- **Active share management:** View, copy, and revoke existing shares in real-time
- **One-click copy:** Copy shareable URLs to clipboard with visual feedback
- **Temporary access:** All shares are session-based and can be easily revoked
- **Custom HTML/CSS Popups:** Modern dialog system for all share management operations

### 🔐 **Secure File Sharing (NEW in v0.4.3!)**
- **Secret Token Protection:** Generate secure, randomly generated tokens for share access control
- **Token-based Authentication:** Users must enter secret tokens to access shared files
- **Public/Private Shares:** Choose between token-protected or public access for shares
- **Token Management:** Enable/disable tokens, generate new tokens, and manage share security
- **Secure URL Generation:** Cryptographically secure share IDs and tokens using `secrets` module
- **Session Persistence:** Tokens stored in cookies and Authorization headers for seamless access

### 📁 **Advanced Share Management (NEW in v0.4.3!)**
- **Complete Share Parameter Control:** Modify all share settings after creation
- **Share Type Configuration:** Switch between Static (snapshot) and Dynamic (live folder) shares
- **File Filtering System:** Advanced allow/avoid list filtering using glob patterns
- **Real-time Share Updates:** Changes apply instantly without recreating shares
- **Visual Share Management:** Intuitive interface showing current settings with visual indicators
- **Parameter Modification:** Update share type, token settings, file filters, and access controls

### 🎯 **Dynamic vs Static Shares (NEW in v0.4.3!)**
- **Static Shares:** Snapshot of files at creation time - perfect for archival sharing
- **Dynamic Shares:** Live folder sharing - new files automatically appear in the share
- **Real-time Updates:** Dynamic shares reflect current folder contents in real-time
- **Flexible Sharing:** Choose the right sharing method for your use case
- **Automatic File Discovery:** Dynamic shares automatically include new files added to folders

### 🔍 **Advanced File Filtering (NEW in v0.4.3!)**
- **Glob Pattern Support:** Use powerful glob patterns for file inclusion/exclusion
- **Allow Lists:** Specify which files to include using patterns like `*.pdf`, `documents/**`
- **Avoid Lists:** Exclude files using patterns like `*.tmp`, `.git/**`, `temp/*`
- **Priority System:** Avoid lists take priority over allow lists for security
- **Pattern Examples:** Support for recursive matching with `**` and complex patterns
- **Real-time Filtering:** Filters apply instantly to both static and dynamic shares

### 🚀 Performance Features (New in v0.4.1!)
- **Enhanced Python File Icons:** Smart visual distinction between source (🐍💎) and compiled (🐍⚡) Python files
- **Memory-mapped file operations:** Efficient handling of large files (>1MB) using mmap
- **Enhanced security:** CSRF protection, XSS prevention, improved input validation
- **Direct executable support:** Run with simple `aird` command instead of `python -m aird`
- **Chunked file operations** for large files
- **Async WebSocket streaming** for real-time updates
- **Configurable buffer sizes** for optimal performance
- **Memory-efficient** file handling with constant memory usage

### 🛠️ Technical Improvements (New in v0.4.3!)
- **Secure Token Management:** Cryptographically secure token generation and validation using `secrets` module
- **Database Schema Migration:** Automatic database updates for new share management features
- **API Endpoint Enhancements:** New `/share/update` endpoint for dynamic share modifications
- **Path Validation & Security:** Enhanced path handling and validation for file operations
- **Error Handling Improvements:** Better error messages and status codes throughout the application
- **Frontend Architecture:** Modular JavaScript functions for better maintainability
- **Cross-browser Compatibility:** Improved compatibility across different browsers and devices
- **Accessibility Enhancements:** Better keyboard navigation and screen reader support
- **Share Parameter Validation:** Comprehensive validation of all share parameters
- **Debug Endpoints:** Troubleshooting endpoints for share management issues
- **Token Security:** Enhanced token storage and validation with session persistence

### 🛠️ Technical Improvements (New in v0.4.2!)
- **Database Schema Migration:** Automatic database updates for new share management features
- **API Endpoint Enhancements:** New `/share/update` endpoint for dynamic share modifications
- **Path Validation & Security:** Enhanced path handling and validation for file operations
- **Error Handling Improvements:** Better error messages and status codes throughout the application
- **Frontend Architecture:** Modular JavaScript functions for better maintainability
- **Cross-browser Compatibility:** Improved compatibility across different browsers and devices
- **Accessibility Enhancements:** Better keyboard navigation and screen reader support

### 🐛 Bug Fixes & Improvements (New in v0.4.3!)
- **Fixed Token Management:** Corrected token enable/disable logic in share management
- **Fixed Share Parameter Updates:** Resolved database update issues for share parameters
- **Fixed Current Settings Display:** Share management now shows actual settings instead of defaults
- **Fixed Token Generation:** Proper token generation and storage in database
- **Fixed Form Validation:** Enhanced validation for all share management parameters
- **Fixed Debug Logging:** Added comprehensive debug logging for troubleshooting
- **Fixed API Response Handling:** Proper handling of new token generation responses
- **Fixed Share Management UI:** Improved visual indicators and form pre-population

### 🐛 Bug Fixes & Improvements (New in v0.4.2!)
- **Fixed Share Management:** Resolved "Failed to load share details" error in share management modal
- **Fixed File Browser Navigation:** Corrected directory navigation issues in the Add Files modal
- **Fixed API Response Handling:** Proper handling of API responses for file listing and share management
- **Fixed Path Construction:** Corrected path building logic for file and directory operations
- **Fixed Console Popup Dependencies:** Replaced all native browser popups with custom HTML/CSS modals
- **Fixed Async Dialog Handling:** Proper async/await handling for all confirmation dialogs
- **Fixed File Selection State:** Corrected file selection state management in share management
- **Fixed Error Display:** Improved error message display and user feedback throughout the application

## 📋 Requirements

### **Core Requirements**
- **Python:** 3.10 or higher
- **Dependencies:** Tornado, ldap3, aiofiles (automatically installed)
- **Storage:** Minimal disk space for the application
- **Network:** HTTP/HTTPS and WebSocket support

### **Development Requirements**
For development and testing, additional dependencies are available in the test extras:
```bash
pip install -e .[test]
```

This includes:
- **Testing frameworks:** pytest, pytest-asyncio, pytest-mock, pytest-cov
- **Development tools:** coverage, mock for comprehensive testing

## 🧪 Testing

The project includes a comprehensive test suite with multiple testing utilities:

### Running Tests
```bash
# Run all tests
python run_tests.py

# Run with coverage
python run_coverage.py

# Run specific test files
pytest tests/test_handlers.py

# Run tests with verbose output
pytest -v
```

### Test Coverage
- **Comprehensive Coverage:** Tests for all major components including handlers, utilities, and database operations
- **WebSocket Testing:** Real-time functionality testing with pytest-tornado
- **File Operations:** Testing file streaming, editing, and sharing features
- **Security Testing:** Path traversal protection and authentication testing
- **Performance Testing:** Large file handling and memory efficiency tests

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

## 👥 Contributors & Thanks

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

## 📄 License

This project is licensed under a **Custom License** that prohibits commercial use without explicit written consent from the author. See the `LICENSE` file for complete details.

### Key License Points:
- ✅ **Free for personal and non-commercial use**
- ✅ **Open source for educational purposes**
- ❌ **Commercial use requires written permission** (usually given for free, but requires written consent.) 
- ❌ **No warranty or liability coverage**

## 🔗 Links

- **GitHub Repository:** [https://github.com/blinkerbit/aird](https://github.com/blinkerbit/aird)
- **PyPI Package:** [https://pypi.org/project/aird/](https://pypi.org/project/aird/)
- **Issue Tracker:** [https://github.com/blinkerbit/aird/issues](https://github.com/blinkerbit/aird/issues)

## 🎯 Roadmap & Future Plans

### 🔜 Upcoming Features
- **File Previews:** In-browser previews for images, PDFs, and Markdown
- **Enhanced Search:** Advanced search filters, file type filtering, and search history
- **Multi-File Operations:** Batch actions for multiple files
- **Theme Support:** Dark mode and customizable themes

### 🚀 Advanced Features (Planned)
- **User Management:** Role-based permissions system
- **File History:** Version tracking and backup features
- **API Integration:** RESTful API for external integrations
- **Plugin System:** Extensible architecture for custom features

---

**Made with ❤️ by Viswantha Srinivas P**

*Star ⭐ this repo if you find it useful!*