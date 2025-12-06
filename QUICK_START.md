# Quick Start: Using the Refactored AIRD

## What Changed?

The application has been modularized for better maintainability. Instead of one 5,419-line file, functionality is now organized into logical modules.

## Directory Structure

```
aird/
├── main.py                 # Main application (3,646 lines, down from 5,419)
├── constants.py            # Configuration constants
├── core/                   # Core utilities
│   ├── security.py        # Path validation, WebSocket security
│   ├── websocket_manager.py  # WebSocket connection management
│   ├── filter_expression.py  # Search expression parser
│   ├── mmap_handler.py       # Memory-mapped file operations
│   └── file_operations.py    # File utilities
├── database/              # Database operations
│   ├── db.py             # Database initialization
│   ├── users.py          # User management
│   ├── shares.py         # Share management
│   ├── ldap.py           # LDAP integration
│   └── feature_flags.py  # Feature flags
├── handlers/              # HTTP/WebSocket handlers (future)
├── templates/             # HTML templates
└── utils/                 # Utilities

## Running the Application

Nothing has changed from a user perspective:

```bash
# Run with default settings
python -m aird

# Run with config file
python -m aird --config config.json

# Run with specific options
python -m aird --root /path/to/files --port 8080
```

## For Developers

### Importing Modules

```python
# Import core utilities
from aird.core.security import is_within_root, is_valid_websocket_origin
from aird.core.filter_expression import FilterExpression

# Import database functions
from aird.database.users import create_user, authenticate_user
from aird.database.shares import get_share_by_id, insert_share

# Import file operations
from aird.core.file_operations import get_all_files_recursive
```

### Testing Specific Modules

```python
# Test security utilities
from aird.core.security import is_within_root
assert is_within_root("/home/user/files/doc.txt", "/home/user/files")

# Test user management
from aird.database.db import init_db, get_db_conn
import sqlite3
conn = sqlite3.connect(":memory:")
init_db(conn)

from aird.database.users import create_user
user = create_user(conn, "testuser", "password123")
assert user["username"] == "testuser"
```

### Adding New Features

1. **New database function**: Add to appropriate module in `database/`
2. **New utility**: Add to appropriate module in `core/`
3. **New handler**: Add to `main.py` (or `handlers/` in Phase 2)

### Code Quality

All modules include:
- ✅ Docstrings for functions and classes
- ✅ Type hints for better IDE support
- ✅ Error handling
- ✅ Clean separation of concerns

## Benefits

1. **Easier Navigation**: Find code faster with organized modules
2. **Better Testing**: Test modules independently
3. **Team Collaboration**: Multiple developers can work on different modules
4. **Reusability**: Use functions across different parts of the application
5. **Maintainability**: Changes are localized to specific modules

## Backward Compatibility

100% backward compatible:
- All existing features work exactly the same
- All API endpoints unchanged
- All configuration options unchanged
- All command-line arguments unchanged

## Need Help?

See the full documentation:
- `REFACTORING_COMPLETED.md` - Detailed refactoring report
- `MODULARIZATION_SUMMARY.md` - Module breakdown
- `README.md` - Original project documentation
