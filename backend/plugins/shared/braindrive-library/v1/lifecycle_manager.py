#!/usr/bin/env python3
"""
BrainDrive Library Backend Plugin - Lifecycle Manager

This is a backend-only plugin that provides structured project management
for the BrainDrive Library. It exposes REST API endpoints for listing
projects, getting project context, and creating new projects.
"""

import os
import structlog
from pathlib import Path
from typing import Dict, Any, List
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()

# Import the base lifecycle manager
try:
    from app.plugins.base_lifecycle_manager import BaseLifecycleManager
    from app.plugins.repository import PluginRepository
    logger.info("BrainDrive Library: BaseLifecycleManager imported from app.plugins")
except ImportError:
    # Fallback for development/testing
    import sys
    current_dir = os.path.dirname(os.path.abspath(__file__))
    backend_path = os.path.abspath(os.path.join(current_dir, "..", "..", "..", "..", "app", "plugins"))
    if backend_path not in sys.path:
        sys.path.insert(0, backend_path)
    from base_lifecycle_manager import BaseLifecycleManager
    from repository import PluginRepository
    logger.info(f"BrainDrive Library: BaseLifecycleManager imported from {backend_path}")


class LibraryLifecycleManager(BaseLifecycleManager):
    """
    Lifecycle manager for the BrainDrive Library backend plugin.

    This plugin provides:
    - List projects by lifecycle (active, completed, ideas, archived)
    - Get aggregated project context for AI consumption
    - Create new projects from templates
    """

    def __init__(self, plugin_slug: str = "braindrive-library", version: str = "1.0.0",
                 shared_storage_path: Path = None):
        if shared_storage_path is None:
            shared_storage_path = Path(__file__).parent
        super().__init__(plugin_slug, version, shared_storage_path)

        self.plugin_data = {
            # Core identifiers
            "name": "BrainDrive Library",
            "plugin_slug": "braindrive-library",
            "version": "1.0.0",
            "description": "Structured project management for your BrainDrive Library",

            # Plugin type - backend only
            "plugin_type": "backend",

            # Backend plugin configuration
            "endpoints_file": "endpoints.py",
            "route_prefix": "/library",
            "backend_dependencies": [],

            # Metadata
            "author": "BrainDrive",
            "official": True,
            "category": "productivity",
            "icon": "FolderOpen",
            "compatibility": "1.0.0",

            # Long description
            "long_description": """
The BrainDrive Library plugin provides structured project management capabilities,
allowing you to organize work into projects following the BrainDrive-Library
conventions. Features include:

- List projects by lifecycle (active, completed, ideas, archived)
- Get aggregated project context for AI (AGENT.md, spec.md, build-plan.md, etc.)
- Create new projects from templates

This plugin works with the core filesystem primitives (/api/v1/fs/*) to provide
a higher-level project-oriented interface.
            """.strip(),

            # Source tracking
            "source_type": "local",
            "source_url": None,
            "update_check_url": None,
            "last_update_check": None,
            "is_local": True,
        }

        logger.info(f"LibraryLifecycleManager initialized: {self.plugin_slug} v{self.version}")

    async def get_plugin_metadata(self) -> Dict[str, Any]:
        """Return plugin metadata and configuration."""
        return self.plugin_data

    async def get_module_metadata(self) -> List[Dict[str, Any]]:
        """
        Return module definitions for this plugin.

        Backend-only plugins don't have frontend modules, so this returns
        an empty list. The plugin's functionality is exposed via API endpoints.
        """
        return []

    async def _perform_user_installation(
        self,
        user_id: str,
        db: AsyncSession,
        shared_plugin_path: Path
    ) -> Dict[str, Any]:
        """
        Perform user-specific installation.

        For backend plugins, there's typically no user-specific installation
        needed - the endpoints are available to all authenticated users.
        """
        logger.info(f"Installing BrainDrive Library for user {user_id}")

        return {
            "success": True,
            "message": f"BrainDrive Library plugin installed for user {user_id}",
            "plugin_slug": self.plugin_slug,
            "version": self.version,
        }

    async def _perform_user_uninstallation(
        self,
        user_id: str,
        db: AsyncSession
    ) -> Dict[str, Any]:
        """
        Perform user-specific uninstallation.

        For backend plugins, there's typically no user-specific cleanup needed.
        """
        logger.info(f"Uninstalling BrainDrive Library for user {user_id}")

        return {
            "success": True,
            "message": f"BrainDrive Library plugin uninstalled for user {user_id}",
            "plugin_slug": self.plugin_slug,
            "version": self.version,
        }

    async def get_status(self) -> Dict[str, Any]:
        """Get current plugin status."""
        return {
            "plugin_slug": self.plugin_slug,
            "version": self.version,
            "plugin_type": "backend",
            "active_users": len(self.active_users),
            "created_at": self.created_at.isoformat(),
            "last_used": self.last_used.isoformat(),
            "endpoints": [
                "GET /projects - List projects by lifecycle",
                "GET /project/{slug}/context - Get project context",
                "POST /projects - Create new project",
            ],
        }

    # Compatibility methods for universal lifecycle API
    async def _check_existing_plugin(self, user_id: str, db: AsyncSession) -> Dict[str, Any]:
        """Check if the plugin is already installed for the user."""
        try:
            repo = PluginRepository(db)
            plugin = await repo.get_plugin_by_slug(self.plugin_slug, user_id)
            if plugin:
                return {"exists": True, "plugin_id": plugin.get("id"), "plugin_info": plugin}
            return {"exists": False}
        except Exception as e:
            logger.error(f"Library plugin: Error checking existing plugin: {e}")
            return {"exists": False, "error": str(e)}

    async def install_plugin(self, user_id: str, db: AsyncSession) -> Dict[str, Any]:
        """Install BrainDrive Library plugin for the user (compatibility method)."""
        try:
            logger.info(f"Library plugin: Starting installation for user {user_id}")

            existing_check = await self._check_existing_plugin(user_id, db)
            if existing_check.get("exists"):
                return {
                    "success": False,
                    "error": "Plugin already installed for user",
                    "plugin_id": existing_check.get("plugin_id"),
                }

            plugin_id = f"{user_id}_{self.plugin_slug}"
            plugin_type = self.plugin_data.get("plugin_type", "backend")

            plugin_record = {
                "id": plugin_id,
                "plugin_slug": self.plugin_slug,
                "name": self.plugin_data.get("name", "BrainDrive Library"),
                "description": self.plugin_data.get("description", ""),
                "version": self.plugin_data.get("version", "1.0.0"),
                "type": plugin_type,
                "pluginType": plugin_type,
                "enabled": True,
                "user_id": user_id,
                "author": self.plugin_data.get("author"),
                "official": self.plugin_data.get("official", True),
                "category": self.plugin_data.get("category"),
                "icon": self.plugin_data.get("icon"),
                "compatibility": self.plugin_data.get("compatibility"),
                "longDescription": self.plugin_data.get("long_description"),
                "sourceType": self.plugin_data.get("source_type", "local"),
                "sourceUrl": self.plugin_data.get("source_url"),
                "updateCheckUrl": self.plugin_data.get("update_check_url"),
                "lastUpdateCheck": self.plugin_data.get("last_update_check"),
                "is_local": self.plugin_data.get("is_local", True),
                "installationType": "local",
                "endpointsFile": self.plugin_data.get("endpoints_file"),
                "routePrefix": self.plugin_data.get("route_prefix"),
                "backendDependencies": self.plugin_data.get("backend_dependencies", []),
                "configFields": {},
                "messages": {},
                "dependencies": [],
                "modules": [],
            }

            repo = PluginRepository(db)
            await repo.insert_plugin(plugin_record)

            logger.info(f"Library plugin: Installed plugin record {plugin_id}")

            return {
                "success": True,
                "plugin_id": plugin_id,
                "plugin_slug": self.plugin_slug,
                "plugin_type": plugin_type,
                "modules_created": [],
                "plugin_directory": str(self.shared_path),
            }
        except Exception as e:
            logger.error(f"Library plugin: Installation failed for user {user_id}: {e}")
            return {"success": False, "error": str(e)}

    async def delete_plugin(self, user_id: str, db: AsyncSession) -> Dict[str, Any]:
        """Uninstall BrainDrive Library plugin for the user (compatibility method)."""
        try:
            logger.info(f"Library plugin: Starting uninstall for user {user_id}")

            existing_check = await self._check_existing_plugin(user_id, db)
            if not existing_check.get("exists"):
                return {"success": False, "error": "Plugin not found for user"}

            plugin_id = existing_check.get("plugin_id")
            repo = PluginRepository(db)
            deleted = await repo.delete_plugin(plugin_id)
            if not deleted:
                return {"success": False, "error": "Failed to delete plugin record"}

            logger.info(f"Library plugin: Uninstalled plugin record {plugin_id}")
            return {"success": True, "plugin_id": plugin_id, "deleted_modules": 0}
        except Exception as e:
            logger.error(f"Library plugin: Uninstall failed for user {user_id}: {e}")
            return {"success": False, "error": str(e)}


# Factory function for the lifecycle registry
def get_lifecycle_manager(shared_storage_path: Path = None) -> LibraryLifecycleManager:
    """Factory function to create a LibraryLifecycleManager instance."""
    return LibraryLifecycleManager(shared_storage_path=shared_storage_path)


# For direct instantiation
lifecycle_manager = LibraryLifecycleManager()
