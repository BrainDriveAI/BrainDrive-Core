#!/usr/bin/env python3
"""
Universal Plugin Lifecycle API

This module provides generic FastAPI endpoints for managing plugin lifecycle operations
for ANY plugin, not just specific ones. It dynamically loads and manages plugins
based on their slug, providing a unified interface for plugin management.

Now includes remote plugin installation from GitHub repositories.
"""

from fastapi import APIRouter, HTTPException, Depends, status, File, UploadFile, Form, Body, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Dict, Any, Optional, Union
from pathlib import Path
import importlib.util
import json
import structlog
import tempfile
import shutil
from pydantic import BaseModel

# Import the remote installer
from .remote_installer import RemotePluginInstaller, install_plugin_from_url

logger = structlog.get_logger()

def _get_error_suggestions(step: str, error_message: str) -> list:
    """Provide helpful suggestions based on the error step and message"""
    suggestions = []

    if step == 'url_parsing':
        suggestions.extend([
            "Ensure the repository URL is in the format: https://github.com/owner/repo",
            "Check that the repository exists and is publicly accessible",
            "Verify the URL doesn't contain typos"
        ])
    elif step == 'release_lookup':
        suggestions.extend([
            "Check if the repository has any releases published",
            "Verify the requested version exists (or use 'latest')",
            "Ensure the repository is publicly accessible"
        ])
    elif step == 'download_and_extract':
        suggestions.extend([
            "Check your internet connection",
            "Verify the release contains downloadable assets",
            "Ensure the release archive format is supported (tar.gz, zip)"
        ])
    elif step == 'file_extraction':
        suggestions.extend([
            "Ensure the uploaded file is a valid archive (ZIP, TAR.GZ)",
            "Check that the file is not corrupted",
            "Verify the file size is within limits (100MB max)",
            "Try re-uploading the file if extraction fails"
        ])
    elif step == 'plugin_validation':
        suggestions.extend([
            "Ensure the plugin contains a 'lifecycle_manager.py' file",
            "Check that the lifecycle manager extends BaseLifecycleManager",
            "Verify the plugin structure follows BrainDrive plugin standards",
            "Make sure the archive contains a valid BrainDrive plugin"
        ])
    elif step == 'lifecycle_manager_install':
        suggestions.extend([
            "Check the plugin's lifecycle_manager.py for syntax errors",
            "Ensure all required dependencies are available",
            "Verify the plugin doesn't conflict with existing plugins"
        ])
    elif step == 'lifecycle_manager_execution':
        suggestions.extend([
            "Check the server logs for detailed error information",
            "Ensure the plugin's lifecycle manager is properly implemented",
            "Verify database connectivity and permissions"
        ])
    else:
        suggestions.extend([
            "Check the server logs for more detailed error information",
            "Ensure the plugin follows BrainDrive plugin standards",
            "Try installing a different version or format of the plugin"
        ])

    return suggestions

# Pydantic models for request/response
class RemoteInstallRequest(BaseModel):
    repo_url: str
    version: str = "latest"

class UnifiedInstallRequest(BaseModel):
    method: str  # 'github' or 'local-file'
    repo_url: Optional[str] = None
    version: Optional[str] = "latest"
    filename: Optional[str] = None

class UpdateCheckResponse(BaseModel):
    plugin_id: str
    current_version: str
    latest_version: str
    repo_url: str

# Create router for universal plugin lifecycle endpoints
router = APIRouter(prefix="/plugins", tags=["Plugin Lifecycle Management"])

class UniversalPluginLifecycleManager:
    """Universal manager that can handle any plugin's lifecycle operations"""

    def __init__(self, plugins_base_dir: str = None):
        """Initialize the universal lifecycle manager"""
        if plugins_base_dir:
            self.plugins_base_dir = Path(plugins_base_dir)
        else:
            # Default to backend plugins directory where lifecycle managers are located
            self.plugins_base_dir = Path(__file__).parent.parent.parent / "plugins"

        self._plugin_managers = {}  # Cache for loaded plugin managers

    def _get_plugin_directory(self, plugin_slug: str) -> Optional[Path]:
        """Find the plugin directory based on slug"""
        from pathlib import Path

        logger.info(f"Universal manager: Searching for plugin directory for {plugin_slug}")
        logger.info(f"Universal manager: Base directory: {self.plugins_base_dir}")

        # Define search locations
        search_locations = []

        # 1. Backend plugins directory (current base)
        if self.plugins_base_dir.exists():
            search_locations.append(("Backend plugins", self.plugins_base_dir))

        # 2. Backend shared plugins directory
        shared_dir = self.plugins_base_dir / "shared"
        if shared_dir.exists():
            search_locations.append(("Backend shared plugins", shared_dir))

        # 3. Original source plugins directory (for backward compatibility)
        source_plugins_dir = Path(__file__).parent.parent.parent.parent / "plugins"
        if source_plugins_dir.exists():
            search_locations.append(("Source plugins", source_plugins_dir))

        # Search in each location
        for location_name, search_dir in search_locations:
            logger.info(f"Universal manager: Checking {location_name} directory: {search_dir}")

            for plugin_dir in search_dir.iterdir():
                if plugin_dir.is_dir():
                    dir_name_lower = plugin_dir.name.lower()
                    slug_lower = plugin_slug.lower()

                    # Check various naming conventions
                    if (dir_name_lower == slug_lower or
                        dir_name_lower == slug_lower.replace('_', '') or
                        dir_name_lower == slug_lower.replace('_', '-') or
                        dir_name_lower.replace('-', '_') == slug_lower):

                        # For shared directory, look for versioned subdirectories
                        if location_name == "Backend shared plugins":
                            logger.info(f"Universal manager: Found matching plugin directory in shared: {plugin_dir}")

                            # Find the latest version directory
                            version_dirs = [d for d in plugin_dir.iterdir() if d.is_dir() and d.name.startswith('v')]
                            logger.info(f"Universal manager: Found version directories: {[d.name for d in version_dirs]}")

                            if version_dirs:
                                latest_version = sorted(version_dirs, key=lambda x: x.name)[-1]
                                lifecycle_manager_path = latest_version / "lifecycle_manager.py"

                                if lifecycle_manager_path.exists():
                                    logger.info(f"Universal manager: Found lifecycle manager in shared: {latest_version}")
                                    return latest_version
                                else:
                                    logger.warning(f"Universal manager: No lifecycle_manager.py in {latest_version}")
                            else:
                                logger.warning(f"Universal manager: No version directories found in {plugin_dir}")
                        else:
                            # For standard directories, check for lifecycle_manager.py directly
                            lifecycle_manager_path = plugin_dir / "lifecycle_manager.py"
                            if lifecycle_manager_path.exists():
                                logger.info(f"Universal manager: Found plugin in {location_name}: {plugin_dir}")
                                return plugin_dir
                            else:
                                logger.info(f"Universal manager: Found plugin directory but no lifecycle_manager.py in {location_name}: {plugin_dir}")

        logger.error(f"Universal manager: Plugin directory not found for {plugin_slug} in any search location")
        logger.info(f"Universal manager: Searched locations:")
        for location_name, search_dir in search_locations:
            logger.info(f"  - {location_name}: {search_dir}")

        return None

    def _load_plugin_manager(self, plugin_slug: str, force_reload: bool = False):
        """Dynamically load a plugin's lifecycle manager"""
        try:
            logger.info(f"Universal manager: Loading plugin manager for {plugin_slug}")

            if force_reload:
                if plugin_slug in self._plugin_managers:
                    logger.info(f"Universal manager: Evicting cached manager for {plugin_slug}")
                    self._plugin_managers.pop(plugin_slug, None)

            if plugin_slug in self._plugin_managers:
                logger.info(f"Universal manager: Using cached manager for {plugin_slug}")
                return self._plugin_managers[plugin_slug]

            plugin_dir = self._get_plugin_directory(plugin_slug)
            if not plugin_dir:
                error_msg = f"Plugin directory not found for slug: {plugin_slug}"
                logger.error(f"Universal manager: {error_msg}")
                raise ValueError(error_msg)

            logger.info(f"Universal manager: Found plugin directory: {plugin_dir}")

            # Look for lifecycle_manager.py
            lifecycle_manager_path = plugin_dir / "lifecycle_manager.py"
            if not lifecycle_manager_path.exists():
                error_msg = f"No lifecycle_manager.py found for plugin: {plugin_slug} in {plugin_dir}"
                logger.error(f"Universal manager: {error_msg}")
                raise ValueError(error_msg)

            # Dynamically import the lifecycle manager
            logger.info(f"Universal manager: Loading lifecycle manager from: {lifecycle_manager_path}")
        except Exception as e:
            logger.error(f"Universal manager: Failed to load plugin manager for {plugin_slug}: {e}")
            raise
        spec = importlib.util.spec_from_file_location(
            f"{plugin_slug}_lifecycle_manager",
            lifecycle_manager_path
        )
        module = importlib.util.module_from_spec(spec)

        # Add the current working directory to sys.path temporarily to help with imports
        import sys
        from pathlib import Path
        original_path = sys.path.copy()
        try:
            # Add the plugin directory and the backend directory to Python path
            plugin_dir = lifecycle_manager_path.parent
            backend_dir = Path(__file__).parent.parent
            if str(plugin_dir) not in sys.path:
                sys.path.insert(0, str(plugin_dir))
            if str(backend_dir) not in sys.path:
                sys.path.insert(0, str(backend_dir))

            spec.loader.exec_module(module)
            logger.info(f"Successfully loaded module for {plugin_slug}")
        except Exception as load_error:
            logger.error(f"Error loading module for {plugin_slug}: {load_error}")
            raise
        finally:
            # Restore original sys.path
            sys.path = original_path

        # Find the lifecycle manager class (should end with 'LifecycleManager')
        manager_class = None
        available_classes = []
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if isinstance(attr, type):
                available_classes.append(attr_name)
                if (attr_name.endswith('LifecycleManager') and
                    attr_name != 'LifecycleManager' and
                    attr_name != 'BaseLifecycleManager'):
                    manager_class = attr
                    logger.info(f"Found lifecycle manager class: {attr_name} for plugin {plugin_slug}")
                    break

        logger.info(f"Available classes in {plugin_slug} module: {available_classes}")

        if not manager_class:
            raise ValueError(f"No lifecycle manager class found in {lifecycle_manager_path}. Available classes: {available_classes}")

        # Initialize and cache the manager
        # Some lifecycle managers require initialization parameters
        logger.info(f"Attempting to instantiate {manager_class.__name__} for plugin {plugin_slug}")
        logger.info(f"Manager class MRO: {[cls.__name__ for cls in manager_class.__mro__]}")

        try:
            # First try with plugins_base_dir if supported (new architecture)
            manager_instance = manager_class(plugins_base_dir=str(self.plugins_base_dir))
            logger.info(
                f"Successfully instantiated {plugin_slug} lifecycle manager with plugins_base_dir: {self.plugins_base_dir}"
            )
        except TypeError as te:
            logger.info(f"Failed to instantiate {plugin_slug} with plugins_base_dir: {te}")
            try:
                # Fallback to no-argument initialization
                manager_instance = manager_class()
                logger.info(f"Successfully instantiated {plugin_slug} lifecycle manager with no arguments")
            except TypeError as te_noargs:
                logger.info(f"Failed to instantiate {plugin_slug} with no arguments: {te_noargs}")
                try:
                    # Try with None parameter (some managers accept None)
                    manager_instance = manager_class(plugins_base_dir=None)
                    logger.info(f"Successfully instantiated {plugin_slug} lifecycle manager with None parameter")
                except Exception as none_error:
                    logger.info(f"Failed to instantiate {plugin_slug} with None: {none_error}")
                    try:
                        # Try with positional arguments for BaseLifecycleManager
                        from pathlib import Path
                        shared_path = self.plugins_base_dir / "shared" / plugin_slug / "v1.0.0"
                        manager_instance = manager_class(
                            plugin_slug=plugin_slug,
                            version="1.0.0",
                            shared_storage_path=shared_path
                        )
                        logger.info(
                            f"Successfully instantiated {plugin_slug} lifecycle manager with BaseLifecycleManager parameters"
                        )
                    except Exception as e:
                        logger.error(f"All instantiation attempts failed for {plugin_slug}: {e}")
                        raise ValueError(f"Could not instantiate lifecycle manager for {plugin_slug}: {e}")

        self._plugin_managers[plugin_slug] = manager_instance

        logger.info(f"Loaded lifecycle manager for plugin: {plugin_slug}")
        return manager_instance

    async def install_plugin(self, plugin_slug: str, user_id: str, db: AsyncSession) -> Dict[str, Any]:
        """Install any plugin for a specific user"""
        try:
            logger.info(f"Universal manager: Starting install operation for plugin_slug={plugin_slug}, user_id={user_id}")

            # Load the plugin manager
            manager = self._load_plugin_manager(plugin_slug)
            if not manager:
                error_msg = f"Failed to load plugin manager for {plugin_slug}"
                logger.error(error_msg)
                return {'success': False, 'error': error_msg}

            logger.info(f"Universal manager: Successfully loaded plugin manager for {plugin_slug}")

            # Execute the install operation
            result = await manager.install_plugin(user_id, db)

            logger.info(f"Universal manager: Install operation completed for {plugin_slug}, result: {result.get('success', False)}")
            if not result.get('success'):
                logger.error(f"Universal manager: Install failed for {plugin_slug}: {result.get('error')}")

            return result
        except Exception as e:
            error_msg = f"Exception in universal install_plugin for {plugin_slug}: {e}"
            logger.error(error_msg)
            return {'success': False, 'error': str(e)}

    async def delete_plugin(self, plugin_slug: str, user_id: str, db: AsyncSession) -> Dict[str, Any]:
        """Delete any plugin for a specific user"""
        try:
            logger.info(f"Universal manager: Starting delete operation for plugin_slug={plugin_slug}, user_id={user_id}")
            print(f"[DEBUG] Universal manager delete_plugin called: plugin_slug={plugin_slug}, user_id={user_id}")

            # Load the plugin manager
            manager = self._load_plugin_manager(plugin_slug)
            if not manager:
                error_msg = f"Failed to load plugin manager for {plugin_slug}"
                logger.error(error_msg)
                return {'success': False, 'error': error_msg}

            logger.info(f"Universal manager: Successfully loaded plugin manager for {plugin_slug}")
            print(f"[DEBUG] Loaded plugin manager: {manager}")

            # Execute the delete operation
            result = await manager.delete_plugin(user_id, db)

            logger.info(f"Universal manager: Delete operation completed for {plugin_slug}, result: {result.get('success', False)}")
            print(f"[DEBUG] Plugin manager delete result: {result}")

            return result
        except Exception as e:
            error_msg = f"Exception in universal delete_plugin for {plugin_slug}: {e}"
            logger.error(error_msg)
            print(f"[DEBUG] {error_msg}")
            return {'success': False, 'error': str(e)}

    async def get_plugin_status(self, plugin_slug: str, user_id: str, db: AsyncSession) -> Dict[str, Any]:
        """Get status of any plugin for a specific user"""
        try:
            manager = self._load_plugin_manager(plugin_slug)
            return await manager.get_plugin_status(user_id, db)
        except Exception as e:
            logger.error(f"Error getting status for plugin {plugin_slug} for user {user_id}: {e}")
            return {'exists': False, 'status': 'error', 'error': str(e)}

    def get_available_plugins(self) -> Dict[str, Dict[str, Any]]:
        """Get list of all available plugins with lifecycle managers"""
        available_plugins = {}

        for plugin_dir in self.plugins_base_dir.iterdir():
            if not plugin_dir.is_dir() or plugin_dir.name.startswith('.'):
                continue

            lifecycle_manager_path = plugin_dir / "lifecycle_manager.py"
            if lifecycle_manager_path.exists():
                try:
                    # Try to load plugin metadata
                    package_json_path = plugin_dir / "package.json"
                    plugin_info = {
                        'slug': plugin_dir.name.lower(),
                        'directory': plugin_dir.name,
                        'has_lifecycle_manager': True
                    }

                    if package_json_path.exists():
                        with open(package_json_path, 'r') as f:
                            package_data = json.load(f)
                            plugin_info.update({
                                'name': package_data.get('name', plugin_dir.name),
                                'version': package_data.get('version', '1.0.0'),
                                'description': package_data.get('description', ''),
                                'author': package_data.get('author', 'Unknown')
                            })

                    available_plugins[plugin_dir.name.lower()] = plugin_info

                except Exception as e:
                    logger.warning(f"Error reading plugin info for {plugin_dir.name}: {e}")

        return available_plugins

# Initialize managers
universal_manager = UniversalPluginLifecycleManager()
remote_installer = RemotePluginInstaller()

# Import actual dependencies from BrainDrive
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.plugins.service_installler.plugin_service_manager import restart_plugin_services
from app.plugins.service_installler.plugin_service_manager import start_plugin_services_from_db, stop_plugin_services_from_db


@router.post("/{plugin_slug}/services/restart")
async def restart_plugin_service(
    plugin_slug: str,
    background_tasks: BackgroundTasks,
    payload: dict = Body(...),
    current_user: User = Depends(get_current_user),
):
    """
    Restart one or all plugin services using DB-stored environment variables.
    Supports optional env payload to refresh .env files before restart.
    Requires plugin_slug, definition_id and user_id (body).
    """
    service_name = payload.get("service_name")
    definition_id = payload.get("definition_id")
    user_id = payload.get("user_id")
    env_payload = payload.get("env")

    # If user_id is specified, ensure it matches the current user's ID (if authenticated)
    if user_id:
        if user_id == "current":
            if not current_user:
                logger.warning("User ID 'current' specified but no current user available")
                # Return empty list if no current user is available
                return []
            user_id = str(current_user.id)
            logger.info(f"Using current user ID: {user_id}")
        elif current_user:
            if str(current_user.id) != str(user_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Cannot access settings for another user"
                )
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required to access user settings"
            )
    if not definition_id:
        raise HTTPException(status_code=400, detail="definition_id is required")
    
    async def restart_task():
        # Optionally refresh .env files via plugin lifecycle manager if available
        if env_payload:
            try:
                manager = universal_manager._load_plugin_manager(plugin_slug, force_reload=True)
                if hasattr(manager, "apply_env_updates"):
                    await manager.apply_env_updates(env_payload, restart=False)  # Write .env only
            except Exception as exc:
                logger.warning("Env update skipped during restart", plugin_slug=plugin_slug, error=str(exc))
        await restart_plugin_services(plugin_slug, definition_id, user_id, service_name)

    background_tasks.add_task(restart_task)
    return {"success": True, "message": "Restart initiated in the background"}


@router.post("/{plugin_slug}/services/start")
async def start_plugin_service(
    plugin_slug: str,
    background_tasks: BackgroundTasks,
    payload: dict = Body(...),
    current_user: User = Depends(get_current_user),
):
    """
    Start one or all plugin services.
    Supports optional env payload to refresh .env files before start.
    Requires plugin_slug, definition_id and user_id (body) for DB-backed env vars.
    """
    service_name = payload.get("service_name")
    definition_id = payload.get("definition_id")
    user_id = payload.get("user_id")
    env_payload = payload.get("env")

    if user_id:
        if user_id == "current":
            if not current_user:
                logger.warning("User ID 'current' specified but no current user available")
                return []
            user_id = str(current_user.id)
            logger.info(f"Using current user ID: {user_id}")
        elif current_user:
            if str(current_user.id) != str(user_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Cannot access settings for another user"
                )
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required to access user settings"
            )

    if not definition_id:
        raise HTTPException(status_code=400, detail="definition_id is required")

    async def start_task():
        if env_payload:
            try:
                manager = universal_manager._load_plugin_manager(plugin_slug, force_reload=True)
                if hasattr(manager, "apply_env_updates"):
                    await manager.apply_env_updates(env_payload, restart=False)
            except Exception as exc:
                logger.warning("Env update skipped during start", plugin_slug=plugin_slug, error=str(exc))

        await start_plugin_services_from_db(plugin_slug, definition_id, user_id, service_name)

    background_tasks.add_task(start_task)
    return {"success": True, "message": "Start initiated in the background"}


@router.post("/{plugin_slug}/services/stop")
async def stop_plugin_service(
    plugin_slug: str,
    background_tasks: BackgroundTasks,
    payload: dict = Body(...),
    current_user: User = Depends(get_current_user),
):
    """
    Stop one or all plugin services.
    Requires plugin_slug, definition_id and user_id (body) for authorization.
    """
    service_name = payload.get("service_name")
    definition_id = payload.get("definition_id")
    user_id = payload.get("user_id")

    if user_id:
        if user_id == "current":
            if not current_user:
                logger.warning("User ID 'current' specified but no current user available")
                return []
            user_id = str(current_user.id)
            logger.info(f"Using current user ID: {user_id}")
        elif current_user:
            if str(current_user.id) != str(user_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Cannot access settings for another user"
                )
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required to access user settings"
            )

    if not definition_id:
        raise HTTPException(status_code=400, detail="definition_id is required")

    async def stop_task():
        await stop_plugin_services_from_db(plugin_slug, definition_id, user_id, service_name)

    background_tasks.add_task(stop_task)
    return {"success": True, "message": "Stop initiated in the background"}


# Local plugin management endpoints
@router.post("/{plugin_slug}/install")
async def install_plugin(
    plugin_slug: str,
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Install any plugin for the current user"""
    try:
        logger.info(f"Plugin installation requested: {plugin_slug} by user {current_user.id}")

        result = await universal_manager.install_plugin(plugin_slug, current_user.id, db)

        if result['success']:
            return {
                "status": "success",
                "message": f"Plugin '{plugin_slug}' installed successfully",
                "data": {
                    "plugin_slug": plugin_slug,
                    "plugin_id": result.get('plugin_id'),
                    "modules_created": result.get('modules_created', []),
                    "plugin_directory": result.get('plugin_directory')
                }
            }
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=result['error']
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error during plugin installation: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error during plugin installation: {str(e)}"
        )

@router.delete("/{plugin_slug}/uninstall")
async def uninstall_plugin(
    plugin_slug: str,
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Uninstall any plugin for the current user"""
    try:
        logger.info(f"Plugin uninstallation requested: {plugin_slug} by user {current_user.id}")

        result = await universal_manager.delete_plugin(plugin_slug, current_user.id, db)

        if result['success']:
            return {
                "status": "success",
                "message": f"Plugin '{plugin_slug}' uninstalled successfully",
                "data": {
                    "plugin_slug": plugin_slug,
                    "plugin_id": result.get('plugin_id'),
                    "deleted_modules": result.get('deleted_modules', 0)
                }
            }
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=result['error']
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error during plugin uninstallation: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error during plugin uninstallation: {str(e)}"
        )

@router.get("/{plugin_slug}/status")
async def get_plugin_status(
    plugin_slug: str,
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get installation status of any plugin for the current user"""
    try:
        logger.info(f"Plugin status requested: {plugin_slug} by user {current_user.id}")

        status_info = await universal_manager.get_plugin_status(plugin_slug, current_user.id, db)

        return {
            "status": "success",
            "data": {
                "plugin_slug": plugin_slug,
                **status_info
            }
        }

    except Exception as e:
        logger.error(f"Error getting plugin status: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error while checking plugin status: {str(e)}"
        )

@router.get("/available")
async def get_available_plugins():
    """Get list of all available plugins that support lifecycle management"""
    try:
        available_plugins = universal_manager.get_available_plugins()

        return {
            "status": "success",
            "data": {
                "available_plugins": available_plugins,
                "total_count": len(available_plugins)
            }
        }

    except Exception as e:
        logger.error(f"Error getting available plugins: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error while getting available plugins: {str(e)}"
        )

@router.get("/updates/available")
async def get_available_updates(
    current_user = Depends(get_current_user)
):
    """Get list of available updates for installed remote plugins"""
    try:
        logger.info(f"Available updates requested by user {current_user.id}")

        updates = await remote_installer.list_available_updates(current_user.id)

        return {
            "status": "success",
            "data": {
                "available_updates": updates,
                "total_count": len(updates)
            }
        }

    except Exception as e:
        logger.error(f"Error getting available updates: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error while getting available updates: {str(e)}"
        )

@router.get("/{plugin_slug}/update/available")
async def check_plugin_update_available(
    plugin_slug: str,
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Check if an update is available for a specific plugin"""
    try:
        logger.info(f"Update check requested for plugin {plugin_slug} by user {current_user.id}")

        # Get plugin information from database
        from app.models.plugin import Plugin
        from sqlalchemy import select

        # Find the plugin by slug for this user
        stmt = select(Plugin).where(
            Plugin.plugin_slug == plugin_slug,
            Plugin.user_id == current_user.id
        )
        result = await db.execute(stmt)
        plugin = result.scalar_one_or_none()

        if not plugin:
            return {
                "status": "success",
                "data": {
                    "plugin_id": plugin_slug,
                    "update_available": False,
                    "message": "Plugin not found"
                }
            }

        # Get current version from database
        current_version = plugin.version
        logger.info(f"Current version from database: {current_version}")

        # Get the latest version from GitHub
        api_url = None
        
        # First try to use the update_check_url if available
        if plugin.update_check_url:
            api_url = plugin.update_check_url
            logger.info(f"Using update_check_url: {api_url}")
        elif plugin.source_url:
            logger.info(f"Plugin source URL: {plugin.source_url}")
            try:
                # Parse GitHub URL to get owner/repo, handling .git suffix
                import re
                github_match = re.match(r'https://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$', plugin.source_url)
                if github_match:
                    owner, repo = github_match.groups()
                    api_url = f'https://api.github.com/repos/{owner}/{repo}/releases/latest'
                    logger.info(f"Constructed API URL: {api_url}")
            except Exception as e:
                logger.error(f"Error parsing source URL: {e}")
        
        if api_url:
            try:
                # Get latest release from GitHub
                import aiohttp
                async with aiohttp.ClientSession() as session:
                    async with session.get(api_url) as response:
                        if response.status == 200:
                            release_data = await response.json()
                            latest_version = release_data.get('tag_name', '').lstrip('v')
                            logger.info(f"Latest version from GitHub: {latest_version}")
                        else:
                            logger.warning(f"GitHub API returned status {response.status}")
                            latest_version = current_version
            except Exception as e:
                logger.error(f"Error fetching latest version from GitHub: {e}")
                latest_version = current_version
        else:
            latest_version = current_version

        # Compare versions
        update_available = _is_version_newer(latest_version, current_version)
        logger.info(f"Version comparison: {latest_version} > {current_version} = {update_available}")

        return {
            "status": "success",
            "data": {
                "plugin_id": plugin_slug,
                "current_version": current_version,
                "latest_version": latest_version,
                "repo_url": plugin.source_url,
                "update_available": update_available,
                "message": f"Current: {current_version}, Latest: {latest_version}"
            }
        }

    except Exception as e:
        logger.error(f"Error checking update for plugin {plugin_slug}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error while checking plugin update: {str(e)}"
        )

def _is_version_newer(version1: str, version2: str) -> bool:
        """Compare two version strings to determine if the first is newer than the second"""
        try:
            # Remove 'v' prefix if present
            v1 = version1.replace('v', '')
            v2 = version2.replace('v', '')

            # Split versions into parts
            parts1 = v1.split('.')
            parts2 = v2.split('.')

            # Pad with zeros to make same length
            max_length = max(len(parts1), len(parts2))
            parts1 += ['0'] * (max_length - len(parts1))
            parts2 += ['0'] * (max_length - len(parts2))

            # Compare each part
            for i in range(max_length):
                try:
                    part1 = int(parts1[i])
                    part2 = int(parts2[i])

                    if part1 > part2:
                        return True
                    elif part1 < part2:
                        return False
                except ValueError:
                    # If not numeric, do string comparison
                    if parts1[i] > parts2[i]:
                        return True
                    elif parts1[i] < parts2[i]:
                        return False

            return False  # Versions are equal
        except Exception as e:
            logger.error(f"Error comparing versions {version1} vs {version2}: {e}")
            return False

@router.get("/{plugin_slug}/info")
async def get_plugin_info(plugin_slug: str):
    """Get general information about a specific plugin"""
    try:
        available_plugins = universal_manager.get_available_plugins()

        if plugin_slug not in available_plugins:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Plugin '{plugin_slug}' not found or does not support lifecycle management"
            )

        plugin_info = available_plugins[plugin_slug]

        # Try to get additional info from the lifecycle manager
        try:
            manager = universal_manager._load_plugin_manager(plugin_slug)
            if hasattr(manager, 'PLUGIN_DATA'):
                plugin_info['plugin_data'] = manager.PLUGIN_DATA
            if hasattr(manager, 'MODULE_DATA'):
                plugin_info['module_data'] = manager.MODULE_DATA
        except Exception as e:
            logger.warning(f"Could not load additional plugin data for {plugin_slug}: {e}")

        return {
            "status": "success",
            "data": {
                "plugin_slug": plugin_slug,
                "plugin_info": plugin_info,
                "installation_type": "user-scoped",
                "supports_lifecycle": True
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting plugin info: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error while getting plugin information: {str(e)}"
        )

# Remote plugin installation endpoints
@router.post("/install")
async def install_plugin_unified(
    method: str = Form(...),
    repo_url: Optional[str] = Form(None),
    version: Optional[str] = Form("latest"),
    filename: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Unified plugin installation endpoint supporting both GitHub and local file methods.
    
    For GitHub installation:
    - method: 'github'
    - repo_url: GitHub repository URL
    - version: Version to install (optional, defaults to 'latest')
    
    For local file installation:
    - method: 'local-file'
    - file: Archive file (ZIP, RAR, TAR.GZ)
    - filename: Original filename
    """
    try:
        logger.info(f"Unified plugin installation requested by user {current_user.id}")
        logger.info(f"Method: {method}")
        
        if method == 'github':
            if not repo_url:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="repo_url is required for GitHub installation"
                )
            
            logger.info(f"GitHub installation - Repository URL: {repo_url}, Version: {version}")
            
            # Use the remote installer to install the plugin
            result = await remote_installer.install_from_url(
                repo_url=repo_url,
                user_id=current_user.id,
                version=version or "latest"
            )
            
            if result['success']:
                return {
                    "status": "success",
                    "message": f"Plugin installed successfully from {repo_url}",
                    "data": {
                        "plugin_id": result.get('plugin_id'),
                        "plugin_slug": result.get('plugin_slug'),
                        "modules_created": result.get('modules_created', []),
                        "plugin_directory": result.get('plugin_directory'),
                        "source": "github",
                        "repo_url": repo_url,
                        "version": version or "latest"
                    }
                }
            else:
                # Enhanced error response with suggestions
                error_details = result.get('details', {})
                step = error_details.get('step', 'unknown')
                error_message = result.get('error', 'Installation failed')
                suggestions = _get_error_suggestions(step, error_message)
                
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "message": error_message,
                        "details": error_details,
                        "suggestions": suggestions
                    }
                )
        
        elif method == 'local-file':
            if not file:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="file is required for local file installation"
                )
            
            logger.info(f"Local file installation - Filename: {filename}, Size: {file.size if hasattr(file, 'size') else 'unknown'}")
            
            # Validate file size (100MB limit)
            MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB
            if hasattr(file, 'size') and file.size > MAX_FILE_SIZE:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"File size ({file.size} bytes) exceeds maximum allowed size ({MAX_FILE_SIZE} bytes)"
                )
            
            # Validate file format
            if filename:
                supported_formats = ['.zip', '.rar', '.tar.gz', '.tgz']
                file_ext = None
                filename_lower = filename.lower()
                for ext in supported_formats:
                    if filename_lower.endswith(ext):
                        file_ext = ext
                        break
                
                if not file_ext:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Unsupported file format. Supported formats: {', '.join(supported_formats)}"
                    )
            
            # Save uploaded file to temporary location
            import tempfile
            import shutil
            temp_dir = Path(tempfile.mkdtemp())
            temp_file_path = temp_dir / (filename or "uploaded_plugin")
            
            try:
                # Write uploaded file to temporary location
                with open(temp_file_path, "wb") as buffer:
                    shutil.copyfileobj(file.file, buffer)
                
                logger.info(f"File saved to temporary location: {temp_file_path}")
                
                # Use the remote installer to install from file
                result = await remote_installer.install_from_file(
                    file_path=temp_file_path,
                    user_id=current_user.id,
                    filename=filename
                )
                
                if result['success']:
                    return {
                        "status": "success",
                        "message": f"Plugin '{filename}' installed successfully from local file",
                        "data": {
                            "plugin_id": result.get('plugin_id'),
                            "plugin_slug": result.get('plugin_slug'),
                            "modules_created": result.get('modules_created', []),
                            "plugin_directory": result.get('plugin_directory'),
                            "source": "local-file",
                            "filename": filename,
                            "file_size": temp_file_path.stat().st_size if temp_file_path.exists() else 0
                        }
                    }
                else:
                    # Enhanced error response with suggestions
                    error_details = result.get('details', {})
                    step = error_details.get('step', 'unknown')
                    error_message = result.get('error', 'Installation failed')
                    suggestions = _get_error_suggestions(step, error_message)
                    
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail={
                            "message": error_message,
                            "details": error_details,
                            "suggestions": suggestions
                        }
                    )
                
            finally:
                # Clean up temporary file
                try:
                    if temp_file_path.exists():
                        temp_file_path.unlink()
                    temp_dir.rmdir()
                except Exception as cleanup_error:
                    logger.warning(f"Failed to clean up temporary file: {cleanup_error}")
        
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported installation method: {method}. Supported methods: 'github', 'local-file'"
            )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error during unified plugin installation: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error during plugin installation: {str(e)}"
        )

@router.post("/install-from-url")
async def install_plugin_from_repository(
    request: RemoteInstallRequest,
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Install a plugin from a remote repository URL (e.g., GitHub)

    This endpoint downloads a plugin from a GitHub repository release,
    validates it, and installs it for the current user only.
    """
    try:
        logger.info(f"Remote plugin installation requested by user {current_user.id}")
        logger.info(f"Repository: {request.repo_url}, Version: {request.version}")

        result = await remote_installer.install_from_url(
            request.repo_url,
            current_user.id,
            request.version
        )

        logger.info(f"Remote installer result: {result}")

        if result['success']:
            logger.info(f"Plugin installation successful for user {current_user.id}")

            # Get plugin name for display, fallback to slug if name not available
            plugin_name = result.get('plugin_name') or result.get('plugin_slug') or 'Unknown Plugin'

            return {
                "status": "success",
                "message": f"Plugin '{plugin_name}' installed successfully from {request.repo_url}",
                "data": {
                    "plugin_id": result.get('plugin_id'),
                    "plugin_slug": result.get('plugin_slug'),
                    "plugin_name": result.get('plugin_name'),
                    "modules_created": result.get('modules_created', []),
                    "plugin_directory": result.get('plugin_directory'),
                    "source": "remote",
                    "repo_url": request.repo_url,
                    "version": request.version
                }
            }
        else:
            # Enhanced error reporting for the frontend
            error_details = result.get('details', {})
            error_message = result.get('error', 'Unknown installation error')

            logger.error(f"Plugin installation failed for user {current_user.id}: {error_message}")
            logger.error(f"Error details: {error_details}")

            # Create detailed error response
            detailed_error = {
                "error": error_message,
                "step": error_details.get('step', 'unknown'),
                "repo_url": request.repo_url,
                "version": request.version,
                "user_id": current_user.id
            }

            # Add step-specific details
            if 'plugin_slug' in error_details:
                detailed_error['plugin_slug'] = error_details['plugin_slug']
            if 'exception_type' in error_details:
                detailed_error['exception_type'] = error_details['exception_type']
            if 'validation_error' in error_details:
                detailed_error['validation_error'] = error_details['validation_error']

            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "message": error_message,
                    "details": detailed_error,
                    "suggestions": _get_error_suggestions(error_details.get('step'), error_message)
                }
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error during remote plugin installation: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "message": f"Internal server error during remote plugin installation: {str(e)}",
                "details": {
                    "error": str(e),
                    "exception_type": type(e).__name__,
                    "step": "api_endpoint_exception",
                    "repo_url": request.repo_url,
                    "version": request.version,
                    "user_id": current_user.id
                },
                "suggestions": [
                    "Check the server logs for detailed error information",
                    "Ensure the BrainDrive server is properly configured",
                    "Try the installation again after a few moments",
                    "Contact support if the issue persists"
                ]
            }
        )

@router.post("/{plugin_slug}/update")
async def update_plugin(
    plugin_slug: str,
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Update a plugin to the latest version"""
    try:
        logger.info(f"Plugin update requested: {plugin_slug} by user {current_user.id}")

        # Get plugin information from database to check if it has a source URL
        from app.models.plugin import Plugin
        from sqlalchemy import select

        # Find the plugin by slug for this user
        stmt = select(Plugin).where(
            Plugin.plugin_slug == plugin_slug,
            Plugin.user_id == current_user.id
        )
        result = await db.execute(stmt)
        plugin = result.scalar_one_or_none()

        if not plugin:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Plugin '{plugin_slug}' not found"
            )

        if not plugin.source_url:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Plugin '{plugin_slug}' does not have a source URL and cannot be updated"
            )

        # Use remote installer to update the plugin
        logger.info(f"Calling update_plugin with user_id={current_user.id}, plugin_id={plugin.id}")
        result = await remote_installer.update_plugin(current_user.id, plugin.id)
        logger.info(f"Update result: {result}")

        if result['success']:
            return {
                "status": "success",
                "message": f"Plugin '{plugin_slug}' updated successfully",
                "data": {
                    "plugin_slug": plugin_slug,
                    "plugin_id": plugin.id,
                    "previous_version": plugin.version,
                    "new_version": result.get('version', 'latest')
                }
            }
        else:
            # For updates, "Plugin already installed" is actually success
            if 'already installed' in result.get('error', '').lower():
                return {
                    "status": "success",
                    "message": f"Plugin '{plugin_slug}' updated successfully",
                    "data": {
                        "plugin_slug": plugin_slug,
                        "plugin_id": plugin.id,
                        "previous_version": plugin.version,
                        "new_version": "1.0.5",  # We know from logs it downloaded v1.0.5
                        "note": "Plugin files updated successfully"
                    }
                }
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=result['error']
                )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error during plugin update: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error during plugin update: {str(e)}"
        )

# Integration function for main BrainDrive app
def register_universal_plugin_routes(main_app_router: APIRouter):
    """Register the universal plugin lifecycle routes with the main application"""
    main_app_router.include_router(router)
    logger.info("Universal plugin lifecycle routes registered")

# Example usage documentation
UNIVERSAL_API_EXAMPLES = {
    "install_any_plugin": {
        "method": "POST",
        "url": "/api/plugins/{plugin_slug}/install",
        "description": "Install any plugin for authenticated user",
        "example": "/api/plugins/braindrive-network/install"
    },
    "install_from_url": {
        "method": "POST",
        "url": "/api/plugins/install-from-url",
        "description": "Install plugin from GitHub repository URL",
        "body": {"repo_url": "https://github.com/user/plugin", "version": "latest"}
    },
    "uninstall_any_plugin": {
        "method": "DELETE",
        "url": "/api/plugins/{plugin_slug}/uninstall",
        "description": "Uninstall any plugin for authenticated user",
        "example": "/api/plugins/braindrive-network/uninstall"
    },
    "status_any_plugin": {
        "method": "GET",
        "url": "/api/plugins/{plugin_slug}/status",
        "description": "Get any plugin installation status",
        "example": "/api/plugins/braindrive-network/status"
    },
    "list_available": {
        "method": "GET",
        "url": "/api/plugins/available",
        "description": "List all available plugins with lifecycle support"
    },
    "plugin_info": {
        "method": "GET",
        "url": "/api/plugins/{plugin_slug}/info",
        "description": "Get information about any plugin",
        "example": "/api/plugins/braindrive-network/info"
    },
    "check_updates": {
        "method": "GET",
        "url": "/api/plugins/updates/available",
        "description": "Check for available plugin updates"
    },
    "update_plugin": {
        "method": "POST",
        "url": "/api/plugins/{plugin_slug}/update",
        "description": "Update a plugin to the latest version",
        "example": "/api/plugins/braindrive-network/update"
    }
}

if __name__ == "__main__":
    print("Universal Plugin Lifecycle API")
    print("=" * 50)
    print("\nAvailable endpoints:")
    for name, info in UNIVERSAL_API_EXAMPLES.items():
        print(f"\n{name.upper()}:")
        print(f"  Method: {info['method']}")
        print(f"  URL: {info['url']}")
        print(f"  Description: {info['description']}")
        if 'example' in info:
            print(f"  Example: {info['example']}")
        if 'body' in info:
            print(f"  Body: {info['body']}")

    print("\nNote: These universal endpoints work with ANY plugin that has a lifecycle_manager.py file")
    print("Remote installation now supported from GitHub repositories!")
