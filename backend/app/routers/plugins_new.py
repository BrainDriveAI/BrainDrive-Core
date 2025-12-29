"""
Enhanced Plugin Router

Updated plugin management endpoints that use the new Plugin Lifecycle Service
for optimized multi-user plugin management with shared storage.
"""

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
from ..core.database import get_db
from ..models.plugin import Plugin, Module
from ..models.user import User
from ..core.auth_deps import require_user, require_admin
from ..core.auth_context import AuthContext
from pathlib import Path
from typing import Dict, Any, List, Optional
import structlog
import json

# Import the new Plugin Lifecycle Service
from ..plugins.lifecycle_service import PluginLifecycleService

logger = structlog.get_logger()

# Initialize the new Plugin Lifecycle Service
PLUGINS_DIR = Path(__file__).parent.parent.parent / "plugins"
plugin_lifecycle_service = PluginLifecycleService(str(PLUGINS_DIR))

# Create router
router = APIRouter(prefix="/api/plugins", tags=["plugins"])


@router.post("/{plugin_slug}/install")
async def install_plugin(
    plugin_slug: str,
    version: str,
    source_url: str,
    background_tasks: BackgroundTasks,
    auth: AuthContext = Depends(require_user),
    db: AsyncSession = Depends(get_db)
):
    """Install plugin for current user"""
    try:
        result = await plugin_lifecycle_service.install_plugin(
            user_id=auth.user_id,
            plugin_slug=plugin_slug,
            version=version,
            source_url=source_url,
            db=db
        )

        if result['success']:
            # Schedule background cleanup of unused resources
            background_tasks.add_task(plugin_lifecycle_service.cleanup_unused_resources)

        return result

    except Exception as e:
        logger.error(f"Error installing plugin {plugin_slug}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{plugin_slug}/update")
async def update_plugin(
    plugin_slug: str,
    new_version: str,
    background_tasks: BackgroundTasks,
    auth: AuthContext = Depends(require_user),
    db: AsyncSession = Depends(get_db)
):
    """Update plugin to new version for current user"""
    try:
        result = await plugin_lifecycle_service.update_plugin(
            user_id=auth.user_id,
            plugin_slug=plugin_slug,
            new_version=new_version,
            db=db
        )

        if result['success']:
            background_tasks.add_task(plugin_lifecycle_service.cleanup_unused_resources)

        return result

    except Exception as e:
        logger.error(f"Error updating plugin {plugin_slug}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{plugin_slug}")
async def delete_plugin(
    plugin_slug: str,
    background_tasks: BackgroundTasks,
    auth: AuthContext = Depends(require_user),
    db: AsyncSession = Depends(get_db)
):
    """Delete plugin for current user"""
    try:
        result = await plugin_lifecycle_service.delete_plugin(
            user_id=auth.user_id,
            plugin_slug=plugin_slug,
            db=db
        )

        if result['success']:
            background_tasks.add_task(plugin_lifecycle_service.cleanup_unused_resources)

        return result

    except Exception as e:
        logger.error(f"Error deleting plugin {plugin_slug}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{plugin_slug}/status")
async def get_plugin_status(
    plugin_slug: str,
    auth: AuthContext = Depends(require_user),
    db: AsyncSession = Depends(get_db)
):
    """Get plugin status for current user"""
    try:
        return await plugin_lifecycle_service.get_plugin_status(
            user_id=auth.user_id,
            plugin_slug=plugin_slug,
            db=db
        )

    except Exception as e:
        logger.error(f"Error getting plugin status {plugin_slug}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/system/stats")
async def get_system_stats(
    auth: AuthContext = Depends(require_admin)
):
    """Get system-wide plugin statistics (admin only)"""
    try:
        return await plugin_lifecycle_service.get_system_stats()

    except Exception as e:
        logger.error(f"Error getting system stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/public/plugins/{plugin_id}/{path:path}")
async def serve_plugin_files(
    plugin_id: str,
    path: str,
    auth: AuthContext = Depends(require_user),
    db: AsyncSession = Depends(get_db)
):
    """Serve plugin files from shared storage for frontend access"""
    try:
        # Check if user has plugin installed (check JSON file)
        has_plugin = await plugin_lifecycle_service.storage_manager.plugin_exists_for_user(
            auth.user_id, plugin_id
        )

        if not has_plugin:
            raise HTTPException(status_code=404, detail="Plugin not installed for user")

        # Get plugin info from database to get version
        plugin_query = text("""
        SELECT version FROM plugin
        WHERE user_id = :user_id AND plugin_slug = :plugin_slug AND enabled = 1
        """)

        result = await db.execute(plugin_query, {
            'user_id': auth.user_id,
            'plugin_slug': plugin_id
        })

        plugin_row = result.fetchone()
        if not plugin_row:
            raise HTTPException(status_code=404, detail="Plugin not found in database")

        # Construct shared path using database version info
        shared_path = plugin_lifecycle_service.storage_manager.construct_shared_path(
            plugin_id, plugin_row.version
        )

        # Validate the shared path exists and is within allowed directory
        if not shared_path.exists() or not str(shared_path).startswith(str(plugin_lifecycle_service.storage_manager.shared_dir)):
            raise HTTPException(status_code=404, detail="Plugin files not found")

        # Construct the full file path
        file_path = shared_path / path

        # Security check: ensure the requested file is within the plugin directory
        if not str(file_path.resolve()).startswith(str(shared_path.resolve())):
            raise HTTPException(status_code=403, detail="Access denied")

        # Check if file exists
        if not file_path.exists() or not file_path.is_file():
            raise HTTPException(status_code=404, detail="File not found")

        # Determine media type
        media_type = None
        if path.endswith('.js'):
            media_type = "application/javascript"
        elif path.endswith('.css'):
            media_type = "text/css"
        elif path.endswith('.json'):
            media_type = "application/json"
        elif path.endswith('.html'):
            media_type = "text/html"

        # Return the file with appropriate headers
        return FileResponse(
            path=str(file_path),
            media_type=media_type,
            headers={
                "Cache-Control": "public, max-age=3600",  # 1 hour cache
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET",
                "Access-Control-Allow-Headers": "Content-Type"
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error serving plugin file {plugin_id}/{path}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


# Backward compatibility endpoints - these maintain the existing API structure
# while using the new lifecycle service underneath

@router.get("/plugins/manifest")
async def get_plugin_manifest(auth: AuthContext = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Get the manifest of all available plugins for the current user (backward compatibility)"""
    try:
        logger.info(f"Getting plugin manifest for user: {auth.user_id}")

        # Get user's installed plugins from database (primary source of truth)
        plugins_query = select(Plugin).where(Plugin.user_id == auth.user_id)
        result = await db.execute(plugins_query)
        user_plugins = result.scalars().all()

        # Convert to the expected manifest format
        manifest = {}

        for plugin in user_plugins:
            plugin_slug = plugin.plugin_slug

            # Check if user has plugin installed in storage system
            has_installation = await plugin_lifecycle_service.storage_manager.plugin_exists_for_user(
                auth.user_id, plugin_slug
            )

            if not has_installation:
                logger.warning(f"Plugin {plugin_slug} in database but not in storage system")
                continue

            # Construct shared path from database info
            shared_path = plugin_lifecycle_service.storage_manager.construct_shared_path(
                plugin_slug, plugin.version
            )

            # Get modules for this plugin
            modules_query = select(Module).where(Module.plugin_id == plugin.id)
            modules_result = await db.execute(modules_query)
            modules = modules_result.scalars().all()

            # Convert modules to expected format
            module_metadata = []
            for module in modules:
                module_data = {
                    "id": module.id,
                    "name": module.name,
                    "display_name": module.display_name,
                    "description": module.description,
                    "icon": module.icon,
                    "category": module.category,
                    "enabled": module.enabled,
                    "priority": module.priority,
                    "props": json.loads(module.props) if module.props else {},
                    "config_fields": json.loads(module.config_fields) if module.config_fields else {},
                    "messages": json.loads(module.messages) if module.messages else {},
                    "required_services": json.loads(module.required_services) if module.required_services else {},
                    "dependencies": json.loads(module.dependencies) if module.dependencies else [],
                    "layout": json.loads(module.layout) if module.layout else {},
                    "tags": json.loads(module.tags) if module.tags else []
                }
                module_metadata.append(module_data)

            # Create manifest entry using database info
            manifest[plugin_slug] = {
                "id": plugin_slug,
                "name": plugin.name,
                "version": plugin.version,
                "description": plugin.description,
                "bundlelocation": plugin.bundle_location,
                "scope": plugin.scope,
                "type": plugin.type,
                "enabled": plugin.enabled,
                "modules": module_metadata,
                "database_id": plugin.id,
                "user_id": auth.user_id,
                "shared_path": str(shared_path)
            }

        return manifest

    except Exception as e:
        logger.error(f"Error getting plugin manifest: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/plugins/manifest/designer")
async def get_designer_plugin_manifest(auth: AuthContext = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Get plugin manifest for designer (backward compatibility)"""
    # This endpoint returns the same data as the regular manifest
    return await get_plugin_manifest(auth, db)


@router.get("/plugins")
async def get_plugins(auth: AuthContext = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Get all plugins for the current user (backward compatibility)"""
    try:
        # Get plugins from database (for backward compatibility with existing queries)
        query = select(Plugin).where(Plugin.user_id == auth.user_id)
        result = await db.execute(query)
        plugins = result.scalars().all()

        # Convert to expected format
        plugins_data = []
        for plugin in plugins:
            plugin_dict = {
                "id": plugin.id,
                "name": plugin.name,
                "description": plugin.description,
                "version": plugin.version,
                "type": plugin.type,
                "enabled": plugin.enabled,
                "icon": plugin.icon,
                "category": plugin.category,
                "status": plugin.status,
                "official": plugin.official,
                "author": plugin.author,
                "last_updated": plugin.last_updated.isoformat() if plugin.last_updated else None,
                "compatibility": plugin.compatibility,
                "downloads": plugin.downloads,
                "scope": plugin.scope,
                "bundle_method": plugin.bundle_method,
                "bundle_location": plugin.bundle_location,
                "is_local": plugin.is_local,
                "long_description": plugin.long_description,
                "plugin_slug": plugin.plugin_slug,
                "user_id": plugin.user_id
            }
            plugins_data.append(plugin_dict)

        return plugins_data

    except Exception as e:
        logger.error(f"Error getting plugins: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/modules")
async def get_modules(auth: AuthContext = Depends(require_user), db: AsyncSession = Depends(get_db)):
    """Get all modules for the current user (backward compatibility)"""
    try:
        # Get modules from database
        query = select(Module).where(Module.user_id == auth.user_id)
        result = await db.execute(query)
        modules = result.scalars().all()

        # Convert to expected format
        modules_data = []
        for module in modules:
            module_dict = {
                "id": module.id,
                "plugin_id": module.plugin_id,
                "name": module.name,
                "display_name": module.display_name,
                "description": module.description,
                "icon": module.icon,
                "category": module.category,
                "enabled": module.enabled,
                "priority": module.priority,
                "props": json.loads(module.props) if module.props else {},
                "config_fields": json.loads(module.config_fields) if module.config_fields else {},
                "messages": json.loads(module.messages) if module.messages else {},
                "required_services": json.loads(module.required_services) if module.required_services else {},
                "dependencies": json.loads(module.dependencies) if module.dependencies else [],
                "layout": json.loads(module.layout) if module.layout else {},
                "tags": json.loads(module.tags) if module.tags else [],
                "user_id": module.user_id
            }
            modules_data.append(module_dict)

        return modules_data

    except Exception as e:
        logger.error(f"Error getting modules: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Cleanup endpoint for manual resource cleanup
@router.post("/system/cleanup")
async def manual_cleanup(
    background_tasks: BackgroundTasks,
    auth: AuthContext = Depends(require_user)
):
    """Manually trigger cleanup of unused plugin resources (admin only)"""
    if not auth.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")

    try:
        # Run cleanup in background
        background_tasks.add_task(plugin_lifecycle_service.cleanup_unused_resources)

        return {"message": "Cleanup task scheduled"}

    except Exception as e:
        logger.error(f"Error scheduling cleanup: {e}")
        raise HTTPException(status_code=500, detail=str(e))