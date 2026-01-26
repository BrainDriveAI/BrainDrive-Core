"""
BrainDrive Filesystem API Endpoints

Provides generic filesystem primitives for accessing files within the
configured LIBRARY_PATH. These are building blocks for higher-level
features like the Library plugin.

Configuration:
- Set LIBRARY_PATH in .env to configure the root path
- Default: ~/BrainDrive-Library

Security:
- All paths are validated to stay within LIBRARY_PATH
- Path traversal attacks (../) are blocked
- Only text file extensions allowed (.md, .txt, .json, .yaml, .yml)
- Delete operations require admin privileges
- All endpoints require authentication
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import List, Literal, Optional
from pathlib import Path
import os
import shutil
import logging

from app.core.auth_deps import require_user, require_admin
from app.core.auth_context import AuthContext
from app.core.config import settings

router = APIRouter(prefix="/fs", tags=["filesystem"])
logger = logging.getLogger(__name__)

# Allowed file extensions for read/write operations
ALLOWED_EXTENSIONS = {'.md', '.txt', '.json', '.yaml', '.yml'}


def _get_library_base() -> Path:
    """
    Get the Library base path from settings.
    Supports ~ expansion for home directory.
    """
    library_path = settings.LIBRARY_PATH
    if library_path.startswith("~"):
        return Path(library_path).expanduser()
    return Path(library_path)


def _validate_path(requested_path: Path, library_base: Path) -> bool:
    """
    Validate that a path is within the Library directory.
    Prevents path traversal attacks.

    Args:
        requested_path: The path to validate
        library_base: The root Library path

    Returns:
        True if path is valid and within library_base
    """
    try:
        # Resolve to absolute path and check it's within library_base
        resolved = requested_path.resolve()
        library_resolved = library_base.resolve()
        return str(resolved).startswith(str(library_resolved))
    except (ValueError, RuntimeError):
        return False


def _normalize_path(path: str) -> str:
    """
    Normalize a path string:
    - Convert backslashes to forward slashes
    - Remove leading/trailing slashes
    - Block dangerous patterns
    """
    # Normalize separators
    path = path.replace('\\', '/')
    # Remove leading/trailing slashes
    path = path.strip('/')
    return path


def _check_extension(filename: str) -> bool:
    """Check if a file has an allowed extension."""
    ext = Path(filename).suffix.lower()
    return ext in ALLOWED_EXTENSIONS


# --- Request/Response Models ---

class ReadResponse(BaseModel):
    """Response for file read operation."""
    path: str
    content: str
    exists: bool


class WriteRequest(BaseModel):
    """Request to write a file."""
    path: str
    content: str


class WriteResponse(BaseModel):
    """Response for file write operation."""
    path: str
    success: bool
    existed_before: bool


class AppendRequest(BaseModel):
    """Request to append content to a file."""
    path: str
    content: str


class AppendResponse(BaseModel):
    """Response for file append operation."""
    path: str
    success: bool
    created: bool


class ListItem(BaseModel):
    """A single item in a directory listing."""
    name: str
    type: Literal["file", "directory"]


class ListResponse(BaseModel):
    """Response for directory listing."""
    path: str
    items: List[ListItem]


class DeleteResponse(BaseModel):
    """Response for file delete operation."""
    path: str
    success: bool


# --- Endpoints ---

@router.get("/read", response_model=ReadResponse)
async def read_file(
    path: str,
    auth: AuthContext = Depends(require_user),
) -> ReadResponse:
    """
    Read a file from the Library.

    Args:
        path: Relative path within LIBRARY_PATH (e.g., "projects/active/my-project/AGENT.md")

    Returns:
        File content if found, exists=False if not found.

    Raises:
        400: Invalid path or file type
        403: Path traversal attempt or access denied
    """
    library_base = _get_library_base()

    # Normalize and validate path
    normalized = _normalize_path(path)

    # Block path traversal attempts
    if '..' in normalized:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Path traversal not allowed"
        )

    # Check file extension
    if not _check_extension(normalized):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File type not allowed. Allowed: {ALLOWED_EXTENSIONS}"
        )

    # Build full path
    file_path = library_base / normalized

    # Validate path is within library
    if not _validate_path(file_path, library_base):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )

    # Check if file exists
    if not file_path.exists():
        return ReadResponse(
            path=normalized,
            content="",
            exists=False
        )

    # Ensure it's a file, not a directory
    if not file_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path is not a file"
        )

    try:
        content = file_path.read_text(encoding='utf-8')
        return ReadResponse(
            path=normalized,
            content=content,
            exists=True
        )
    except PermissionError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission denied"
        )
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File is not valid UTF-8 text"
        )


@router.post("/write", response_model=WriteResponse)
async def write_file(
    request: WriteRequest,
    auth: AuthContext = Depends(require_user),
) -> WriteResponse:
    """
    Create or replace a file in the Library.

    Args:
        request.path: Relative path within LIBRARY_PATH
        request.content: File content to write

    Returns:
        Success status and whether file existed before.

    Note:
        The `existed_before` flag allows the AI to confirm before overwriting.

    Raises:
        400: Invalid path or file type
        403: Path traversal attempt or access denied
    """
    library_base = _get_library_base()

    # Normalize and validate path
    normalized = _normalize_path(request.path)

    # Block path traversal attempts
    if '..' in normalized:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Path traversal not allowed"
        )

    # Check file extension
    if not _check_extension(normalized):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File type not allowed. Allowed: {ALLOWED_EXTENSIONS}"
        )

    # Build full path
    file_path = library_base / normalized

    # Validate path is within library
    if not _validate_path(file_path, library_base):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )

    # Check if file existed before
    existed_before = file_path.exists()

    try:
        # Create parent directories if needed
        file_path.parent.mkdir(parents=True, exist_ok=True)

        # Write the file
        file_path.write_text(request.content, encoding='utf-8')

        return WriteResponse(
            path=normalized,
            success=True,
            existed_before=existed_before
        )
    except PermissionError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission denied"
        )
    except OSError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to write file: {str(e)}"
        )


@router.patch("/append", response_model=AppendResponse)
async def append_file(
    request: AppendRequest,
    auth: AuthContext = Depends(require_user),
) -> AppendResponse:
    """
    Append content to a file in the Library.

    Creates the file if it doesn't exist.

    Args:
        request.path: Relative path within LIBRARY_PATH
        request.content: Content to append

    Returns:
        Success status and whether file was created.

    Raises:
        400: Invalid path or file type
        403: Path traversal attempt or access denied
    """
    library_base = _get_library_base()

    # Normalize and validate path
    normalized = _normalize_path(request.path)

    # Block path traversal attempts
    if '..' in normalized:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Path traversal not allowed"
        )

    # Check file extension
    if not _check_extension(normalized):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File type not allowed. Allowed: {ALLOWED_EXTENSIONS}"
        )

    # Build full path
    file_path = library_base / normalized

    # Validate path is within library
    if not _validate_path(file_path, library_base):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )

    # Check if we're creating or appending
    created = not file_path.exists()

    try:
        # Create parent directories if needed
        file_path.parent.mkdir(parents=True, exist_ok=True)

        # Append to file (creates if doesn't exist)
        with open(file_path, 'a', encoding='utf-8') as f:
            f.write(request.content)

        return AppendResponse(
            path=normalized,
            success=True,
            created=created
        )
    except PermissionError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission denied"
        )
    except OSError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to append to file: {str(e)}"
        )


@router.get("/list", response_model=ListResponse)
async def list_directory(
    path: str = "",
    auth: AuthContext = Depends(require_user),
) -> ListResponse:
    """
    List contents of a directory in the Library.

    Args:
        path: Relative path within LIBRARY_PATH (empty string for root)

    Returns:
        List of files and directories (excludes hidden files starting with .)

    Raises:
        400: Path is not a directory
        403: Path traversal attempt or access denied
        404: Directory not found
    """
    library_base = _get_library_base()

    # Normalize path (allow empty for root)
    normalized = _normalize_path(path) if path else ""

    # Block path traversal attempts
    if '..' in normalized:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Path traversal not allowed"
        )

    # Build full path
    dir_path = library_base / normalized if normalized else library_base

    # Validate path is within library
    if not _validate_path(dir_path, library_base):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )

    # Check if directory exists
    if not dir_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Directory not found"
        )

    # Ensure it's a directory
    if not dir_path.is_dir():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path is not a directory"
        )

    try:
        items = []
        for item in sorted(dir_path.iterdir(), key=lambda x: x.name.lower()):
            # Skip hidden files/directories
            if item.name.startswith('.'):
                continue

            items.append(ListItem(
                name=item.name,
                type="directory" if item.is_dir() else "file"
            ))

        return ListResponse(
            path=normalized,
            items=items
        )
    except PermissionError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission denied"
        )


@router.delete("/delete", response_model=DeleteResponse)
async def delete_file(
    path: str,
    auth: AuthContext = Depends(require_admin),
) -> DeleteResponse:
    """
    Delete a file from the Library.

    **Requires admin privileges.**

    Only deletes files, not directories (for safety).

    Args:
        path: Relative path within LIBRARY_PATH

    Returns:
        Success status.

    Raises:
        400: Path is a directory (use rmdir for directories)
        403: Not admin, path traversal attempt, or access denied
        404: File not found
    """
    library_base = _get_library_base()

    # Normalize and validate path
    normalized = _normalize_path(path)

    # Block path traversal attempts
    if '..' in normalized:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Path traversal not allowed"
        )

    # Build full path
    file_path = library_base / normalized

    # Validate path is within library
    if not _validate_path(file_path, library_base):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )

    # Check if file exists
    if not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found"
        )

    # Only allow deleting files, not directories
    if file_path.is_dir():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete directories. Only files can be deleted."
        )

    try:
        file_path.unlink()
        return DeleteResponse(
            path=normalized,
            success=True
        )
    except PermissionError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission denied"
        )
    except OSError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete file: {str(e)}"
        )


# --- Library Configuration Endpoints ---

class LibraryConfigResponse(BaseModel):
    """Response for Library configuration."""
    path: str
    exists: bool
    is_configured: bool


class LibraryConfigUpdate(BaseModel):
    """Request to update Library path."""
    path: str


class LibraryInitResponse(BaseModel):
    """Response for Library initialization."""
    path: str
    created: bool
    message: str


def _get_starter_content_path() -> Optional[Path]:
    """
    Get the path to the starter content package.
    Looks in several locations for the starter content.
    """
    # Possible locations for starter content
    candidates = [
        # Development: relative to backend
        Path(__file__).parent.parent.parent.parent.parent / "starter-content",
        # BrainDrive-Library repo (if available)
        Path.home() / "BrainDrive-Library" / "projects" / "active" / "library-integration" / "starter-content",
        # Packaged with app
        Path(__file__).parent.parent.parent.parent / "data" / "starter-content",
    ]

    for candidate in candidates:
        if candidate.exists() and candidate.is_dir():
            return candidate

    return None


def _create_default_library(library_path: Path) -> bool:
    """
    Create a default Library with starter content.

    Args:
        library_path: The path where the Library should be created

    Returns:
        True if created successfully, False if starter content not found
    """
    # Create the base directory
    library_path.mkdir(parents=True, exist_ok=True)

    # Try to copy starter content
    starter_content = _get_starter_content_path()
    if starter_content:
        try:
            # Copy all contents from starter-content to library_path
            for item in starter_content.iterdir():
                if item.name == "README.md":
                    # Skip the README (it's for developers, not users)
                    continue
                dest = library_path / item.name
                if item.is_dir():
                    shutil.copytree(item, dest, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, dest)
            logger.info(f"Created Library with starter content at {library_path}")
            return True
        except Exception as e:
            logger.warning(f"Failed to copy starter content: {e}")

    # If no starter content, create minimal structure
    logger.info(f"Creating minimal Library structure at {library_path}")
    (library_path / "projects" / "active").mkdir(parents=True, exist_ok=True)
    (library_path / "projects" / "ideas").mkdir(parents=True, exist_ok=True)
    (library_path / "projects" / "completed").mkdir(parents=True, exist_ok=True)
    (library_path / "projects" / "archived").mkdir(parents=True, exist_ok=True)
    (library_path / "system" / "templates").mkdir(parents=True, exist_ok=True)
    (library_path / "pulse").mkdir(parents=True, exist_ok=True)

    # Create a welcome README
    welcome_content = """# BrainDrive Library

Welcome to your BrainDrive Library! This is your personal knowledge store.

## Folder Structure

- `projects/active/` - Your current projects
- `projects/ideas/` - Project ideas and backlog
- `projects/completed/` - Finished projects
- `projects/archived/` - Archived/deferred projects
- `system/templates/` - Templates for new projects
- `pulse/` - Task tracking

## Getting Started

1. Create a new project folder in `projects/active/`
2. Add an `AGENT.md` file to describe your project
3. Use the Library in BrainDrive chat to interact with your projects

Happy building!
"""
    (library_path / "README.md").write_text(welcome_content, encoding='utf-8')

    return True


@router.get("/config", response_model=LibraryConfigResponse)
async def get_library_config(
    auth: AuthContext = Depends(require_user),
) -> LibraryConfigResponse:
    """
    Get the current Library configuration.

    Returns:
        Current LIBRARY_PATH, whether it exists, and whether it's configured.
    """
    library_base = _get_library_base()

    return LibraryConfigResponse(
        path=str(library_base),
        exists=library_base.exists(),
        is_configured=True  # Always true since we have a default
    )


@router.put("/config", response_model=LibraryConfigResponse)
async def update_library_config(
    request: LibraryConfigUpdate,
    auth: AuthContext = Depends(require_user),
) -> LibraryConfigResponse:
    """
    Update the Library path configuration.

    Note: This endpoint validates the path but actual persistence
    requires updating the .env file or settings. This returns
    validation info for the frontend to use.

    Args:
        request.path: New path for the Library

    Returns:
        Validation status for the new path.

    Raises:
        400: Invalid path
    """
    # Expand ~ if present
    new_path = request.path
    if new_path.startswith("~"):
        new_path = str(Path(new_path).expanduser())

    path = Path(new_path)

    # Validate the path is absolute
    if not path.is_absolute():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path must be absolute"
        )

    # Check if path exists or can be created
    if path.exists():
        if not path.is_dir():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Path exists but is not a directory"
            )
        # Check if writable
        if not os.access(path, os.W_OK):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Path is not writable"
            )
    else:
        # Check if parent is writable
        parent = path.parent
        if not parent.exists():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Parent directory does not exist"
            )
        if not os.access(parent, os.W_OK):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot create directory: parent is not writable"
            )

    # Return validation info
    # Note: Actual path update would need to modify settings/env
    # For now, we validate and return the info
    return LibraryConfigResponse(
        path=str(path),
        exists=path.exists(),
        is_configured=True
    )


@router.post("/init", response_model=LibraryInitResponse)
async def initialize_library(
    auth: AuthContext = Depends(require_user),
) -> LibraryInitResponse:
    """
    Initialize the Library with default content.

    Creates the Library directory structure and copies starter content
    if available. Safe to call multiple times - won't overwrite existing
    content.

    Returns:
        Status of the initialization.
    """
    library_base = _get_library_base()

    if library_base.exists():
        # Check if it has content
        has_content = any(library_base.iterdir())
        if has_content:
            return LibraryInitResponse(
                path=str(library_base),
                created=False,
                message="Library already exists with content"
            )

    # Create the library
    created = _create_default_library(library_base)

    return LibraryInitResponse(
        path=str(library_base),
        created=True,
        message="Library created with starter content" if created else "Library created with minimal structure"
    )
