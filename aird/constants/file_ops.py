"""String constants for file operation handlers."""

# Shared / common
ACCESS_DENIED = "Access denied: You don't have permission to perform this action"
ACCESS_DENIED_PATH = "Access denied: This path is not allowed for security reasons"
ACCESS_DENIED_SHORT = "Access denied"
BAD_REQUEST = "Bad request"
INVALID_FILENAME = (
    "Invalid filename: Please use a valid filename without special characters"
)
FILENAME_TOO_LONG = "Filename too long: Please use a shorter filename"
FILE_NOT_FOUND = "File not found: The requested file may have been moved or deleted"

# DeleteHandler
FOLDER_DELETE_DISABLED = (
    "Feature disabled: Folder deletion is currently disabled by administrator"
)
FOLDER_NOT_EMPTY = "Folder is not empty: use recursive=1 to delete anyway"
FILE_DELETE_DISABLED = (
    "Feature disabled: File deletion is currently disabled by administrator"
)
FILE_OR_FOLDER_NOT_FOUND = "File or folder not found"

# UploadHandler
FILE_UPLOAD_DISABLED = "File upload is disabled."
MISSING_UPLOAD_FILENAME_HEADER = "Missing X-Upload-Filename header"
FILE_UPLOAD_DISABLED_ADMIN = (
    "Feature disabled: File upload is currently disabled by administrator"
)
FILE_TOO_LARGE_TEMPLATE = (
    "File too large: Please choose a file smaller than {limit_mb} MB"
)
UNSUPPORTED_FILE_TYPE = (
    "Unsupported file type: This file type is not allowed for upload"
)
UPLOAD_SAVE_FAILED = "Failed to save upload. Please try again."
UPLOAD_SUCCESSFUL = "Upload successful"

# CreateFolderHandler
FOLDER_CREATE_DISABLED = (
    "Feature disabled: Create folder is currently disabled by administrator"
)
INVALID_FOLDER_NAME = (
    "Invalid request: Folder name is required and must not contain / or \\"
)
FOLDER_NAME_TOO_LONG = "Folder name too long"
CONFLICT_EXISTS = "Conflict: A file or folder with that name already exists"
FOLDER_CREATE_FAILED = "Failed to create folder"

# RenameHandler
FILE_RENAME_DISABLED = (
    "Feature disabled: File renaming is currently disabled by administrator"
)
INVALID_REQUEST_PATH_AND_NAME = (
    "Invalid request: Both file path and new name are required"
)
RENAME_FAILED = "Operation failed: Unable to rename the file"

# CopyHandler
COPY_DISABLED = "Feature disabled: Copy is currently disabled by administrator"
INVALID_REQUEST_PATH_DEST = "Invalid request: path and dest are required"
SOURCE_NOT_FOUND = "Source not found"
DESTINATION_EXISTS = "Destination already exists"
COPY_FAILED = "Copy failed"

# MoveHandler
MOVE_DISABLED = "Feature disabled: Move is currently disabled by administrator"
MOVE_FAILED = "Move failed"

# BulkHandler
INVALID_JSON = "Invalid JSON"
PATHS_REQUIRED = "paths must be a non-empty array"
INVALID_PATH = "invalid path"
ACCESS_DENIED_LOWER = "access denied"
NOT_FOUND_LOWER = "not found"
FOLDER_DELETE_DISABLED_LOWER = "folder delete disabled"
FILE_DELETE_DISABLED_LOWER = "file delete disabled"
SHARE_ID_REQUIRED = "share_id required"
DATABASE_UNAVAILABLE = "database unavailable"
SHARE_NOT_FOUND = "share not found"
UPDATE_FAILED = "update failed"
UNSUPPORTED_ACTION = "unsupported action (use delete or add_to_share)"

# EditHandler
FILE_EDIT_DISABLED = (
    "Feature disabled: File editing is currently disabled by administrator"
)
INVALID_JSON_REQUEST = "Invalid request: Please provide valid JSON data"
ACCESS_DENIED_WITH_PERIOD = (
    "Access denied: You don't have permission to perform this action."
)
FILE_SAVED_SUCCESSFULLY = "File saved successfully."
FILE_SAVE_ERROR = "Error saving file. Please try again."

# CloudUploadHandler
PROVIDER_NOT_CONFIGURED = "Provider not configured"
NO_FILE_UPLOADED = "No file uploaded"
FILE_TOO_LARGE = "File too large for upload"
CLOUD_UPLOAD_FAILED = "Failed to upload file"
