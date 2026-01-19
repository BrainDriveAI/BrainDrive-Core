"""
BrainDrive Library API Endpoints

Provides read access to the BrainDrive-Library for plugins that need
to access project context, documentation, and other Library content.

Security:
- All paths are validated to stay within ~/BrainDrive-Library/
- Path traversal attacks (../) are blocked
- Requires authenticated user
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from pathlib import Path
import os

from app.core.auth_deps import require_user
from app.core.auth_context import AuthContext

router = APIRouter(tags=["library"])

# Base path for the BrainDrive Library
LIBRARY_BASE = Path.home() / "BrainDrive-Library"


class ProjectInfo(BaseModel):
    """Information about a Library project."""
    slug: str
    name: str
    path: str
    has_agent_md: bool
    has_spec_md: bool
    has_build_plan: bool
    status: Optional[str] = None


class FileReadRequest(BaseModel):
    """Request to read a file from the Library."""
    project_slug: str
    filename: str


class FileReadResponse(BaseModel):
    """Response containing file content."""
    filename: str
    content: str
    exists: bool


def _validate_path(requested_path: Path) -> bool:
    """
    Validate that a path is within the Library directory.
    Prevents path traversal attacks.
    """
    try:
        # Resolve to absolute path and check it's within LIBRARY_BASE
        resolved = requested_path.resolve()
        library_resolved = LIBRARY_BASE.resolve()
        return str(resolved).startswith(str(library_resolved))
    except (ValueError, RuntimeError):
        return False


def _extract_status_from_agent_md(agent_md_path: Path) -> Optional[str]:
    """Extract project status from AGENT.md if present."""
    try:
        if agent_md_path.exists():
            content = agent_md_path.read_text(encoding='utf-8')
            for line in content.split('\n'):
                if line.startswith('**Status:**'):
                    return line.replace('**Status:**', '').strip()
    except Exception:
        pass
    return None


@router.get("/library/projects", response_model=List[ProjectInfo])
async def list_projects(
    category: str = "active",
    auth: AuthContext = Depends(require_user),
) -> List[ProjectInfo]:
    """
    List all projects in the BrainDrive Library.

    Args:
        category: Project category (active, completed, archived). Defaults to "active".

    Returns:
        List of projects with metadata.
    """
    # Validate category to prevent path traversal
    allowed_categories = ["active", "completed", "archived"]
    if category not in allowed_categories:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid category. Must be one of: {allowed_categories}"
        )

    projects_dir = LIBRARY_BASE / "projects" / category

    if not projects_dir.exists():
        return []

    if not _validate_path(projects_dir):
        raise HTTPException(status_code=403, detail="Access denied")

    projects = []
    try:
        for item in projects_dir.iterdir():
            if item.is_dir() and not item.name.startswith('.'):
                agent_md = item / "AGENT.md"
                projects.append(ProjectInfo(
                    slug=item.name,
                    name=item.name.replace('-', ' ').title(),
                    path=str(item),
                    has_agent_md=agent_md.exists(),
                    has_spec_md=(item / "spec.md").exists(),
                    has_build_plan=(item / "build-plan.md").exists(),
                    status=_extract_status_from_agent_md(agent_md)
                ))
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied reading Library")

    # Sort by name
    projects.sort(key=lambda p: p.name)
    return projects


@router.post("/library/read-file", response_model=FileReadResponse)
async def read_project_file(
    request: FileReadRequest,
    auth: AuthContext = Depends(require_user),
) -> FileReadResponse:
    """
    Read a file from a Library project.

    Only allows reading specific documentation files (markdown, text).

    Args:
        request: Contains project_slug and filename to read.

    Returns:
        File content if found.
    """
    # Allowed file extensions for security
    allowed_extensions = {'.md', '.txt', '.json', '.yaml', '.yml'}

    # Validate filename - no path components allowed
    if '/' in request.filename or '\\' in request.filename:
        raise HTTPException(
            status_code=400,
            detail="Filename cannot contain path separators"
        )

    # Check extension
    ext = Path(request.filename).suffix.lower()
    if ext not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"File type not allowed. Allowed: {allowed_extensions}"
        )

    # Validate project slug - no path components
    if '/' in request.project_slug or '\\' in request.project_slug or '..' in request.project_slug:
        raise HTTPException(
            status_code=400,
            detail="Invalid project slug"
        )

    # Build and validate full path
    # Check in active, then completed, then archived
    file_path = None
    for category in ["active", "completed", "archived"]:
        candidate = LIBRARY_BASE / "projects" / category / request.project_slug / request.filename
        if candidate.exists():
            file_path = candidate
            break

    if file_path is None:
        # Return exists=False instead of 404 so caller can handle gracefully
        return FileReadResponse(
            filename=request.filename,
            content="",
            exists=False
        )

    # Final path validation
    if not _validate_path(file_path):
        raise HTTPException(status_code=403, detail="Access denied")

    try:
        content = file_path.read_text(encoding='utf-8')
        return FileReadResponse(
            filename=request.filename,
            content=content,
            exists=True
        )
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File is not valid UTF-8 text")


@router.get("/library/project/{project_slug}/context")
async def get_project_context(
    project_slug: str,
    auth: AuthContext = Depends(require_user),
) -> dict:
    """
    Get full context for a project (AGENT.md + spec.md + build-plan.md).

    Convenience endpoint that reads all standard project files at once.

    Args:
        project_slug: The project folder name.

    Returns:
        Dictionary with content of each file (empty string if not found).
    """
    # Validate project slug
    if '/' in project_slug or '\\' in project_slug or '..' in project_slug:
        raise HTTPException(status_code=400, detail="Invalid project slug")

    # Find project directory
    project_dir = None
    for category in ["active", "completed", "archived"]:
        candidate = LIBRARY_BASE / "projects" / category / project_slug
        if candidate.exists() and candidate.is_dir():
            project_dir = candidate
            break

    if project_dir is None:
        raise HTTPException(status_code=404, detail="Project not found")

    if not _validate_path(project_dir):
        raise HTTPException(status_code=403, detail="Access denied")

    # Read standard files
    context = {
        "project_slug": project_slug,
        "agent_md": "",
        "spec_md": "",
        "build_plan_md": "",
        "research_findings_md": "",
    }

    file_mapping = {
        "agent_md": "AGENT.md",
        "spec_md": "spec.md",
        "build_plan_md": "build-plan.md",
        "research_findings_md": "research-findings.md",
    }

    for key, filename in file_mapping.items():
        file_path = project_dir / filename
        if file_path.exists():
            try:
                context[key] = file_path.read_text(encoding='utf-8')
            except (PermissionError, UnicodeDecodeError):
                pass  # Leave as empty string

    return context
