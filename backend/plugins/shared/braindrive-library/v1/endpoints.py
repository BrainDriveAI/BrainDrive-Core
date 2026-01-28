"""
BrainDrive Library Plugin - API Endpoints

Provides structured project management for the BrainDrive Library:
- List projects by lifecycle (active, completed, ideas, archived)
- Get aggregated project context for AI consumption
- Create new projects from templates

All paths are relative to LIBRARY_PATH configured in settings.
"""

import os
from pathlib import Path
from typing import List, Optional, Dict, Any
from datetime import datetime

import structlog

from app.plugins.decorators import plugin_endpoint, PluginRequest
from app.core.config import settings

logger = structlog.get_logger()

# Valid project lifecycles
VALID_LIFECYCLES = ("active", "completed", "ideas", "archived")

# Files to include in project context by default
DEFAULT_CONTEXT_FILES = ["AGENT.md", "spec.md", "build-plan.md", "decisions.md", "notes.md"]

# Allowed file extensions for reading
ALLOWED_EXTENSIONS = {".md", ".txt", ".json", ".yaml", ".yml"}


def get_library_path() -> Path:
    """Get the resolved library path from settings."""
    path = settings.LIBRARY_PATH
    if path.startswith("~"):
        path = os.path.expanduser(path)
    return Path(path).resolve()


def validate_path(relative_path: str, library_path: Path) -> Path:
    """
    Validate and resolve a relative path within the library.

    Raises:
        ValueError: If path traversal is attempted or path is outside library
    """
    if ".." in relative_path:
        raise ValueError("Path traversal not allowed")

    # Resolve the full path
    full_path = (library_path / relative_path).resolve()

    # Ensure it's within the library
    try:
        full_path.relative_to(library_path)
    except ValueError:
        raise ValueError("Path is outside the library")

    return full_path


def is_hidden(name: str) -> bool:
    """Check if a file/folder name is hidden."""
    return name.startswith(".")


@plugin_endpoint("/projects", methods=["GET"])
async def list_projects(request: PluginRequest) -> Dict[str, Any]:
    """
    List projects by lifecycle.

    Query Parameters:
        lifecycle: Filter by lifecycle (active, completed, ideas, archived)
                   If not specified, returns active projects.

    Returns:
        List of projects with metadata (name, has_agent_md, has_spec, etc.)
    """
    # Get lifecycle from query params
    lifecycle = request.query_params.get("lifecycle", "active")

    if lifecycle not in VALID_LIFECYCLES:
        return {
            "success": False,
            "error": f"Invalid lifecycle '{lifecycle}'. Must be one of: {', '.join(VALID_LIFECYCLES)}",
        }

    library_path = get_library_path()
    projects_path = library_path / "projects" / lifecycle

    if not projects_path.exists():
        return {
            "success": True,
            "lifecycle": lifecycle,
            "projects": [],
            "message": f"No projects folder found for lifecycle '{lifecycle}'",
        }

    projects = []
    try:
        for item in projects_path.iterdir():
            if item.is_dir() and not is_hidden(item.name):
                project_info = {
                    "name": item.name,
                    "slug": item.name,
                    "lifecycle": lifecycle,
                    "path": f"projects/{lifecycle}/{item.name}",
                    "has_agent_md": (item / "AGENT.md").exists(),
                    "has_spec": (item / "spec.md").exists(),
                    "has_build_plan": (item / "build-plan.md").exists(),
                    "has_decisions": (item / "decisions.md").exists(),
                }
                projects.append(project_info)

        # Sort by name
        projects.sort(key=lambda p: p["name"].lower())

        logger.info(
            "Listed projects",
            user_id=request.user_id,
            lifecycle=lifecycle,
            count=len(projects),
        )

        return {
            "success": True,
            "lifecycle": lifecycle,
            "projects": projects,
            "count": len(projects),
        }

    except Exception as e:
        logger.error("Failed to list projects", error=str(e), lifecycle=lifecycle)
        return {
            "success": False,
            "error": f"Failed to list projects: {str(e)}",
        }


@plugin_endpoint("/project/{slug}/context", methods=["GET"])
async def get_project_context(request: PluginRequest, slug: str) -> Dict[str, Any]:
    """
    Get aggregated project context for AI consumption.

    Path Parameters:
        slug: Project slug/name

    Query Parameters:
        lifecycle: Project lifecycle (default: active)
        files: Comma-separated list of files to include (default: AGENT.md,spec.md,build-plan.md,decisions.md,notes.md)

    Returns:
        Aggregated content from specified project files
    """
    lifecycle = request.query_params.get("lifecycle", "active")
    files_param = request.query_params.get("files", "")

    if lifecycle not in VALID_LIFECYCLES:
        return {
            "success": False,
            "error": f"Invalid lifecycle '{lifecycle}'",
        }

    # Determine which files to include
    if files_param:
        files_to_include = [f.strip() for f in files_param.split(",") if f.strip()]
    else:
        files_to_include = DEFAULT_CONTEXT_FILES

    library_path = get_library_path()
    project_path = library_path / "projects" / lifecycle / slug

    if not project_path.exists():
        return {
            "success": False,
            "error": f"Project '{slug}' not found in {lifecycle}",
        }

    context = {
        "project": slug,
        "lifecycle": lifecycle,
        "files": {},
        "missing_files": [],
    }

    try:
        for filename in files_to_include:
            file_path = project_path / filename

            # Validate extension
            ext = Path(filename).suffix.lower()
            if ext and ext not in ALLOWED_EXTENSIONS:
                context["missing_files"].append({
                    "file": filename,
                    "reason": f"Extension '{ext}' not allowed",
                })
                continue

            if file_path.exists() and file_path.is_file():
                try:
                    content = file_path.read_text(encoding="utf-8")
                    context["files"][filename] = {
                        "content": content,
                        "size": len(content),
                        "modified": datetime.fromtimestamp(
                            file_path.stat().st_mtime
                        ).isoformat(),
                    }
                except Exception as e:
                    context["missing_files"].append({
                        "file": filename,
                        "reason": f"Read error: {str(e)}",
                    })
            else:
                context["missing_files"].append({
                    "file": filename,
                    "reason": "File not found",
                })

        context["success"] = True
        context["files_found"] = len(context["files"])
        context["files_missing"] = len(context["missing_files"])

        logger.info(
            "Got project context",
            user_id=request.user_id,
            project=slug,
            lifecycle=lifecycle,
            files_found=context["files_found"],
        )

        return context

    except Exception as e:
        logger.error(
            "Failed to get project context",
            error=str(e),
            project=slug,
            lifecycle=lifecycle,
        )
        return {
            "success": False,
            "error": f"Failed to get project context: {str(e)}",
        }


@plugin_endpoint("/projects", methods=["POST"])
async def create_project(request: PluginRequest) -> Dict[str, Any]:
    """
    Create a new project from templates.

    Request Body:
        name: Project name (will be used as folder name)
        lifecycle: Target lifecycle (default: active)
        description: Optional project description

    Returns:
        Created project info
    """
    try:
        body = await request.json()
    except Exception:
        return {
            "success": False,
            "error": "Invalid JSON body",
        }

    name = body.get("name", "").strip()
    lifecycle = body.get("lifecycle", "active")
    description = body.get("description", "")

    if not name:
        return {
            "success": False,
            "error": "Project name is required",
        }

    # Sanitize name for use as folder name
    slug = name.lower().replace(" ", "-")
    # Remove any characters that aren't alphanumeric or hyphens
    slug = "".join(c for c in slug if c.isalnum() or c == "-")

    if not slug:
        return {
            "success": False,
            "error": "Invalid project name - must contain alphanumeric characters",
        }

    if lifecycle not in VALID_LIFECYCLES:
        return {
            "success": False,
            "error": f"Invalid lifecycle '{lifecycle}'",
        }

    library_path = get_library_path()
    project_path = library_path / "projects" / lifecycle / slug

    if project_path.exists():
        return {
            "success": False,
            "error": f"Project '{slug}' already exists in {lifecycle}",
        }

    # Check for templates
    templates_path = library_path / "system" / "templates"

    try:
        # Create project directory
        project_path.mkdir(parents=True, exist_ok=True)

        created_files = []

        # Create AGENT.md from template or default
        agent_template = templates_path / "project-agent-template.md"
        agent_content = _create_agent_content(name, description, agent_template)
        (project_path / "AGENT.md").write_text(agent_content, encoding="utf-8")
        created_files.append("AGENT.md")

        # Create spec.md from template or default
        spec_template = templates_path / "spec-template.md"
        spec_content = _create_spec_content(name, description, spec_template)
        (project_path / "spec.md").write_text(spec_content, encoding="utf-8")
        created_files.append("spec.md")

        # Create build-plan.md from template or default
        build_template = templates_path / "build-plan-template.md"
        build_content = _create_build_plan_content(name, build_template)
        (project_path / "build-plan.md").write_text(build_content, encoding="utf-8")
        created_files.append("build-plan.md")

        # Create decisions.md
        decisions_content = f"# {name} - Decision Log\n\n> Technical and product decisions with rationale.\n\n---\n\n## Decisions\n\n*No decisions recorded yet.*\n"
        (project_path / "decisions.md").write_text(decisions_content, encoding="utf-8")
        created_files.append("decisions.md")

        logger.info(
            "Created project",
            user_id=request.user_id,
            project=slug,
            lifecycle=lifecycle,
            files=created_files,
        )

        return {
            "success": True,
            "project": {
                "name": name,
                "slug": slug,
                "lifecycle": lifecycle,
                "path": f"projects/{lifecycle}/{slug}",
                "created_files": created_files,
            },
            "message": f"Project '{name}' created successfully",
        }

    except Exception as e:
        logger.error(
            "Failed to create project",
            error=str(e),
            project=slug,
            lifecycle=lifecycle,
        )
        # Clean up partial creation
        if project_path.exists():
            import shutil
            shutil.rmtree(project_path, ignore_errors=True)

        return {
            "success": False,
            "error": f"Failed to create project: {str(e)}",
        }


def _create_agent_content(name: str, description: str, template_path: Path) -> str:
    """Create AGENT.md content from template or default."""
    if template_path.exists():
        try:
            content = template_path.read_text(encoding="utf-8")
            content = content.replace("{{PROJECT_NAME}}", name)
            content = content.replace("{{DESCRIPTION}}", description or "No description provided.")
            content = content.replace("{{DATE}}", datetime.now().strftime("%Y-%m-%d"))
            return content
        except Exception:
            pass

    # Default content
    return f"""# {name} - Agent Context

> **Start here** when working on this project.

**Status:** Not Started
**Created:** {datetime.now().strftime("%Y-%m-%d")}

---

## Quick Context

{description or "No description provided."}

---

## What to Read

| Order | File | Why |
|-------|------|-----|
| 1 | `spec.md` | Requirements and scope |
| 2 | `build-plan.md` | Implementation plan |
| 3 | `decisions.md` | Key choices and rationale |

---

## Current Focus

*Define initial tasks here.*

---

*This file is updated at the end of each coding session.*
"""


def _create_spec_content(name: str, description: str, template_path: Path) -> str:
    """Create spec.md content from template or default."""
    if template_path.exists():
        try:
            content = template_path.read_text(encoding="utf-8")
            content = content.replace("{{PROJECT_NAME}}", name)
            content = content.replace("{{DESCRIPTION}}", description or "No description provided.")
            content = content.replace("{{DATE}}", datetime.now().strftime("%Y-%m-%d"))
            return content
        except Exception:
            pass

    return f"""# Spec: {name}

> **Project:** {name.lower().replace(" ", "-")}
> **Created:** {datetime.now().strftime("%Y-%m-%d")}

## Overview

### What We're Building

{description or "Describe what this project will build."}

### Problem Statement

*What problem does this solve?*

## User Stories

*Define user stories here.*

## Detailed Requirements

### Core Functionality

- [ ] *Requirement 1*
- [ ] *Requirement 2*

## Scope

### MVP Scope

*What's included in the first version?*

### Explicitly Excluded

*What's NOT included?*

---

## Approval

- [ ] Reviewed by: _______________
- [ ] Date: _______________
"""


def _create_build_plan_content(name: str, template_path: Path) -> str:
    """Create build-plan.md content from template or default."""
    if template_path.exists():
        try:
            content = template_path.read_text(encoding="utf-8")
            content = content.replace("{{PROJECT_NAME}}", name)
            content = content.replace("{{DATE}}", datetime.now().strftime("%Y-%m-%d"))
            return content
        except Exception:
            pass

    return f"""# Build Plan: {name}

> **Project:** {name.lower().replace(" ", "-")}
> **Created:** {datetime.now().strftime("%Y-%m-%d")}

**Status:** Not Started

---

## Overview

*High-level implementation approach.*

---

## Phases

### Phase 1: *Phase Name*

**Goal:** *What this phase accomplishes*

#### Tasks

- [ ] Task 1
- [ ] Task 2

### Success Criteria

*How do we know this phase is complete?*

---

## Work Log

### {datetime.now().strftime("%Y-%m-%d")} - Project Created

*Initial project setup.*

---
"""
