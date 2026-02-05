from fastapi import APIRouter, HTTPException, Body, Depends, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, text
from ..plugins import PluginManager
from ..plugins.repository import PluginRepository
from ..core.database import get_db
from ..core.config import settings
from ..models.plugin import Plugin, Module
from ..models.user import User
from pathlib import Path
from typing import Dict, Any, List, Optional
import structlog
import json

# Import the lifecycle API router
from ..plugins.lifecycle_api import router as lifecycle_router

logger = structlog.get_logger()

# Initialize plugin manager with the correct plugins directory
PLUGINS_DIR = Path(__file__).parent.parent.parent / "plugins"
plugin_manager = PluginManager(str(PLUGINS_DIR))


def _safe_join(base_dir: Path, relative_path: str) -> Optional[Path]:
    if not relative_path:
        return None
    if Path(relative_path).is_absolute():
        return None
    base_dir = base_dir.resolve()
    candidate = (base_dir / relative_path).resolve()
    try:
        candidate.relative_to(base_dir)
    except ValueError:
        return None
    return candidate

# Import new auth dependencies
from ..core.auth_deps import require_user, optional_user
from ..core.auth_context import AuthContext

# Create a router for plugin management endpoints WITHOUT a prefix
router = APIRouter(tags=["plugins"])

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

# Initialize plugin manager on startup
@router.on_event("startup")
async def startup_event():
    """Initialize plugin manager on startup."""
    await plugin_manager.initialize()
    
    # Discover plugins for all users
    async for db in get_db():
        # Get all users
        result = await db.execute(select(User))
        users = result.scalars().all()
        
        # Discover plugins for each user
        for user in users:
            await plugin_manager._discover_plugins(user_id=user.id)
        
        break  # Only need one session

@router.get("/plugins/manifest")
async def get_plugin_manifest(auth: AuthContext = Depends(require_user)):
    """Get the manifest of all available plugins for the current user."""
    logger.info(f"Getting plugin manifest for user: {auth.user_id}")
    
    if not plugin_manager._initialized:
        await plugin_manager.initialize()
    
    plugins = await plugin_manager.get_all_plugins(user_id=auth.user_id)
    
    # Log the number of plugins returned
    logger.info(f"Found {len(plugins)} plugins for user {auth.user_id}")
    
    # Log the plugin IDs
    plugin_ids = list(plugins.keys())
    logger.info(f"Plugin IDs: {plugin_ids}")
    
    # Transform the response to maintain backward compatibility
    transformed_plugins = {}
    for plugin_id, plugin_data in plugins.items():
        # Use plugin_slug as the key in the response
        plugin_slug = plugin_data.get("plugin_slug")
        if plugin_slug:
            # Create a copy of the plugin data
            plugin_copy = dict(plugin_data)
            
            # Ensure the id field in the response is the plugin_slug for frontend compatibility
            plugin_copy["id"] = plugin_slug
            
            # Store the actual database ID as a separate field if needed
            plugin_copy["database_id"] = plugin_id
            
            # Make sure bundlelocation is using the public endpoint
            if "bundlelocation" in plugin_copy and plugin_copy["bundlelocation"]:
                # Extract the path part from the bundlelocation
                bundle_path = plugin_copy["bundlelocation"]
                if bundle_path.startswith("/"):
                    bundle_path = bundle_path[1:]
                
                # Update to use the public endpoint
                plugin_copy["bundlelocation"] = bundle_path
            
            transformed_plugins[plugin_slug] = plugin_copy
        else:
            # Fallback for plugins without a slug
            transformed_plugins[plugin_id] = plugin_data
    
    # Log the transformed plugin slugs
    transformed_slugs = list(transformed_plugins.keys())
    logger.info(f"Transformed plugin slugs: {transformed_slugs}")
    
    return transformed_plugins

@router.get("/plugins/manifest/designer")
async def get_plugin_manifest_for_designer(auth: AuthContext = Depends(require_user)):
    """Get the manifest of all available plugins with layout information for the page designer."""
    logger.info(f"Getting plugin manifest for user: {auth.user_id}")
    
    if not plugin_manager._initialized:
        await plugin_manager.initialize()
    
    plugins = await plugin_manager.get_all_plugins_for_designer(user_id=auth.user_id)
    
    logger.info(f"Found {len(plugins)} plugins for user {auth.user_id}")
    
    # Log the plugin IDs
    plugin_ids = list(plugins.keys())
    logger.info(f"Plugin IDs: {plugin_ids}")
    
    # Transform the response to maintain backward compatibility
    transformed_plugins = {}
    for plugin_id, plugin_data in plugins.items():
        # Use plugin_slug as the key in the response
        plugin_slug = plugin_data.get("plugin_slug")
        if plugin_slug:
            # Create a copy of the plugin data
            plugin_copy = dict(plugin_data)
            
            # Ensure the id field in the response is the plugin_slug for frontend compatibility
            plugin_copy["id"] = plugin_slug
            
            # Store the actual database ID as a separate field if needed
            plugin_copy["database_id"] = plugin_id
            
            # Transform bundle_location to bundlelocation for frontend compatibility
            if "bundle_location" in plugin_copy and plugin_copy["bundle_location"]:
                # Extract the path part from the bundle_location
                bundle_path = plugin_copy["bundle_location"]
                if bundle_path.startswith("/"):
                    bundle_path = bundle_path[1:]

                # Set bundlelocation field for frontend
                plugin_copy["bundlelocation"] = bundle_path
                # Remove the old field
                del plugin_copy["bundle_location"]
            elif "bundlelocation" in plugin_copy and plugin_copy["bundlelocation"]:
                # Handle case where bundlelocation is already set
                bundle_path = plugin_copy["bundlelocation"]
                if bundle_path.startswith("/"):
                    bundle_path = bundle_path[1:]
                
                # Update to use the public endpoint
                plugin_copy["bundlelocation"] = bundle_path
            
            # Update module references if needed
            if "modules" in plugin_copy:
                for module in plugin_copy["modules"]:
                    # Ensure pluginId references use the slug
                    if "pluginId" in module:
                        module["pluginId"] = plugin_slug
            
            transformed_plugins[plugin_slug] = plugin_copy
        else:
            # Fallback for plugins without a slug
            transformed_plugins[plugin_id] = plugin_data
    
    # Log the transformed plugin slugs
    transformed_slugs = list(transformed_plugins.keys())
    logger.info(f"Transformed plugin slugs: {transformed_slugs}")
    
    return transformed_plugins

@router.get("/plugins/{plugin_id}/info")
async def get_plugin_info(plugin_id: str, auth: AuthContext = Depends(require_user)):
    """Get information about a specific plugin."""
    # Log the request
    logger.info(f"Getting plugin info for plugin {plugin_id} and user {auth.user_id}")
    
    try:
        # Ensure plugin manager is initialized
        if not plugin_manager._initialized:
            await plugin_manager.initialize()
        
        # Get plugin info
        plugin_info = await plugin_manager.get_plugin_info(plugin_id, user_id=auth.user_id)
        
        # Log success
        logger.info(f"Successfully retrieved plugin info for plugin {plugin_id} and user {auth.user_id}")
        
        return plugin_info
    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        # Log the error
        logger.error("Error getting plugin info", plugin_id=plugin_id, user_id=auth.user_id, error=str(e))
        raise HTTPException(status_code=404, detail=str(e))

@router.post("/plugins/{plugin_slug}/register")
async def register_plugin(
    plugin_slug: str,
    plugin_info: Dict[str, Any] = Body(...),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user)
):
    """Register a new plugin."""
    try:
        if not plugin_manager._initialized:
            await plugin_manager.initialize()
            
        plugin_id = f"{auth.user_id}_{plugin_slug}"
        
        # Set plugin ID and slug in the info
        plugin_info["id"] = plugin_id
        plugin_info["plugin_slug"] = plugin_slug
        plugin_info["user_id"] = auth.user_id
        
        # Check if plugin with this slug already exists for this user
        existing_plugin = await db.execute(
            select(Plugin).where(
                Plugin.plugin_slug == plugin_slug,
                Plugin.user_id == auth.user_id
            )
        )
        if existing_plugin.scalars().first():
            raise HTTPException(
                status_code=400,
                detail=f"Plugin with slug '{plugin_slug}' already exists for this user"
            )
        
        # Insert plugin into database
        repo = PluginRepository(db)
        await repo.insert_plugin(plugin_info)
        
        # Create user-specific plugin directory if it doesn't exist
        user_plugin_dir = PLUGINS_DIR / auth.user_id / plugin_slug
        user_plugin_dir.mkdir(parents=True, exist_ok=True)
        
        # Reload plugin in manager
        await plugin_manager.reload_plugin(plugin_id, user_id=auth.user_id)
        
        return {"status": "success", "message": f"Plugin {plugin_slug} registered successfully"}
    except Exception as e:
        logger.error("Error registering plugin", plugin_slug=plugin_slug, error=str(e))
        raise HTTPException(status_code=400, detail=str(e))

@router.delete("/plugins/{plugin_id}")
async def unregister_plugin(
    plugin_id: str,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user)
):
    """Unregister a plugin."""
    try:
        # Ensure plugin manager is initialized
        if not plugin_manager._initialized:
            await plugin_manager.initialize()
            
        # Check if plugin belongs to current user
        plugin = await db.execute(
            select(Plugin).where(
                Plugin.id == plugin_id,
                Plugin.user_id == auth.user_id
            )
        )
        if not plugin.scalars().first():
            raise HTTPException(status_code=404, detail=f"Plugin {plugin_id} not found or you don't have permission")
            
        # Delete plugin from database
        repo = PluginRepository(db)
        success = await repo.delete_plugin(plugin_id)
        
        if not success:
            raise HTTPException(status_code=404, detail=f"Plugin {plugin_id} not found")
        
        return {"status": "success", "message": f"Plugin {plugin_id} unregistered successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error unregistering plugin", plugin_id=plugin_id, error=str(e))
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/plugins/refresh-cache")
async def refresh_plugin_cache(auth: AuthContext = Depends(require_user)):
    """Refresh the plugin cache by reloading all plugin configurations."""
    try:
        # Ensure plugin manager is initialized
        if not plugin_manager._initialized:
            await plugin_manager.initialize()
            
        result = await plugin_manager.refresh_plugin_cache()
        return result
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to refresh plugin cache: {str(e)}"
        )

# Plugin Manager API Endpoints

@router.get("/plugins/manager")
async def get_plugins_for_manager(
    search: Optional[str] = None,
    category: Optional[str] = None,
    tags: Optional[str] = None,
    page: int = Query(1, ge=1),
    pageSize: int = Query(16, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user)
):
    """
    Get all modules with optional filtering for the plugin manager.
    
    Args:
        search: Optional search term to filter modules by name, display name, or description
        category: Optional category to filter modules by
        tags: Optional comma-separated list of tags to filter modules by
        page: Page number for pagination (1-based)
        pageSize: Number of items per page
    """
    try:
        # Parse tags if provided
        tag_list = tags.split(',') if tags else []
        
        # Build query for modules
        query = select(Module).where(Module.user_id == auth.user_id)
        
        # Apply filters
        if search:
            search_lower = f"%{search.lower()}%"
            query = query.filter(
                (func.lower(Module.name).like(search_lower)) |
                (func.lower(Module.display_name).like(search_lower)) |
                (func.lower(Module.description).like(search_lower))
            )
        
        if category:
            query = query.filter(Module.category == category)
        
        # Execute query to get all matching modules
        result = await db.execute(query)
        all_modules = result.scalars().all()
        
        # Filter by tags if needed (this needs to be done in Python since tags are stored as JSON)
        if tag_list:
            filtered_modules = []
            for module in all_modules:
                if module.tags:
                    module_tags = json.loads(module.tags) if isinstance(module.tags, str) else module.tags
                    if any(tag in module_tags for tag in tag_list):
                        filtered_modules.append(module)
            all_modules = filtered_modules
        
        # Calculate total count
        total_items = len(all_modules)
        
        # Apply pagination
        start_idx = (page - 1) * pageSize
        end_idx = start_idx + pageSize
        paginated_modules = all_modules[start_idx:end_idx]
        
        # Convert to dictionaries
        module_dicts = []
        for module in paginated_modules:
            module_dict = module.to_dict()
            
            # Parse tags from JSON string
            if module_dict.get('tags') and isinstance(module_dict['tags'], str):
                try:
                    module_dict['tags'] = json.loads(module_dict['tags'])
                except json.JSONDecodeError:
                    module_dict['tags'] = []
            
            module_dicts.append(module_dict)
        
        return {
            "modules": module_dicts,
            "totalItems": total_items
        }
    except Exception as e:
        logger.error("Error getting modules for manager", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/plugins/categories")
async def get_categories(
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user)
):
    """Get all available module categories."""
    try:
        # Query distinct categories from modules for the current user
        result = await db.execute(
            select(Module.category).distinct().where(Module.user_id == auth.user_id)
        )
        categories = [row[0] for row in result.all() if row[0]]
        
        return {"categories": categories}
    except Exception as e:
        logger.error("Error getting categories", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/plugins/tags")
async def get_tags(
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user)
):
    """Get all available module tags."""
    try:
        # Query all modules to extract tags for the current user
        result = await db.execute(select(Module.tags).where(Module.user_id == auth.user_id))
        all_tags = []
        
        # Extract tags from each module
        for row in result.all():
            if row[0]:
                try:
                    tags = json.loads(row[0]) if isinstance(row[0], str) else row[0]
                    if isinstance(tags, list):
                        all_tags.extend(tags)
                except json.JSONDecodeError:
                    pass
        
        # Get unique tags
        unique_tags = list(set(all_tags))
        
        return {"tags": unique_tags}
    except Exception as e:
        logger.error("Error getting tags", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/plugins/{plugin_id}/modules")
async def get_plugin_modules(
    plugin_id: str,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user)
):
    """Get all modules for a specific plugin."""
    try:
        plugin = await db.execute(
            select(Plugin).where(
                Plugin.id == plugin_id,
                Plugin.user_id == auth.user_id
            )
        )
        if not plugin.scalars().first():
            raise HTTPException(status_code=404, detail=f"Plugin {plugin_id} not found or you don't have permission")
            
        repo = PluginRepository(db)
        modules = await repo.get_plugin_modules(plugin_id, user_id=auth.user_id)
        
        # Parse tags from JSON string for each module
        for module in modules:
            if module.get('tags') and isinstance(module['tags'], str):
                try:
                    module['tags'] = json.loads(module['tags'])
                except json.JSONDecodeError:
                    module['tags'] = []
        
        return {"modules": modules}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error getting plugin modules", plugin_id=plugin_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/plugins/{plugin_id}/modules/{module_id}")
async def get_module_detail(
    plugin_id: str,
    module_id: str,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user)
):
    """Get details for a specific module."""
    try:
        logger.info("Fetching module detail", plugin_id=plugin_id, module_id=module_id)
        
        # Use raw SQL query to bypass ORM issues
        # Get module data
        from sqlalchemy import text
        module_query = text("""
        SELECT * FROM module
        WHERE plugin_id = :plugin_id AND id = :module_id AND user_id = :user_id
        """)
        module_result = await db.execute(
            module_query,
            {"plugin_id": plugin_id, "module_id": module_id, "user_id": auth.user_id}
        )
        module_row = module_result.fetchone()
        
        if not module_row:
            logger.error("Module not found", plugin_id=plugin_id, module_id=module_id)
            raise HTTPException(status_code=404, detail=f"Module {module_id} not found in plugin {plugin_id}")
        
        logger.info("Module found", plugin_id=plugin_id, module_id=module_id)
        
        # Get plugin data
        plugin_query = text("""
        SELECT * FROM plugin
        WHERE id = :plugin_id AND user_id = :user_id
        """)
        plugin_result = await db.execute(
            plugin_query,
            {"plugin_id": plugin_id, "user_id": auth.user_id}
        )
        plugin_row = plugin_result.fetchone()
        
        if not plugin_row:
            logger.error("Plugin not found", plugin_id=plugin_id)
            raise HTTPException(status_code=404, detail=f"Plugin {plugin_id} not found")
        
        # Convert rows to dictionaries
        module = {
            "id": module_row.id,
            "pluginId": module_row.plugin_id,
            "name": module_row.name,
            "displayName": module_row.display_name,
            "description": module_row.description,
            "icon": module_row.icon,
            "category": module_row.category,
            "enabled": bool(module_row.enabled),
            "priority": module_row.priority
        }
        
        # Parse JSON fields
        for field, attr in [
            ("props", module_row.props),
            ("configFields", module_row.config_fields),
            ("messages", module_row.messages),
            ("requiredServices", module_row.required_services),
            ("layout", module_row.layout)
        ]:
            if attr:
                try:
                    module[field] = json.loads(attr)
                except json.JSONDecodeError:
                    module[field] = {}
        
        # Parse tags
        if module_row.tags:
            try:
                module["tags"] = json.loads(module_row.tags)
            except json.JSONDecodeError:
                module["tags"] = []
        else:
            module["tags"] = []
        
        # Parse dependencies
        if module_row.dependencies:
            try:
                module["dependencies"] = json.loads(module_row.dependencies)
            except json.JSONDecodeError:
                module["dependencies"] = []
        else:
            module["dependencies"] = []
        
        # Convert plugin row to dictionary
        plugin = {
            "id": plugin_row.id,
            "name": plugin_row.name,
            "description": plugin_row.description,
            "version": plugin_row.version,
            "type": plugin_row.type,
            "enabled": bool(plugin_row.enabled),
            "icon": plugin_row.icon,
            "category": plugin_row.category,
            "status": plugin_row.status,
            "official": bool(plugin_row.official),
            "author": plugin_row.author,
            "lastUpdated": plugin_row.last_updated,
            "compatibility": plugin_row.compatibility,
            "downloads": plugin_row.downloads,
            "scope": plugin_row.scope,
            "bundleMethod": plugin_row.bundle_method,
            "bundleLocation": plugin_row.bundle_location,
            "isLocal": bool(plugin_row.is_local),
            # Add source tracking fields for update/delete functionality
            "sourceType": plugin_row.source_type,
            "sourceUrl": plugin_row.source_url,
            "updateCheckUrl": plugin_row.update_check_url,
            "lastUpdateCheck": plugin_row.last_update_check.isoformat() if plugin_row.last_update_check else None,
            "updateAvailable": bool(plugin_row.update_available) if plugin_row.update_available is not None else False,
            "latestVersion": plugin_row.latest_version,
            "installationType": plugin_row.installation_type
        }
        
        # Parse JSON fields for plugin
        for field, attr in [
            ("configFields", plugin_row.config_fields),
            ("messages", plugin_row.messages),
            ("dependencies", plugin_row.dependencies)
        ]:
            if attr:
                try:
                    plugin[field] = json.loads(attr)
                except json.JSONDecodeError:
                    plugin[field] = {} if field != "dependencies" else []
            else:
                plugin[field] = {} if field != "dependencies" else []
        
        # Parse permissions field
        if plugin_row.permissions:
            try:
                plugin["permissions"] = json.loads(plugin_row.permissions)
            except json.JSONDecodeError:
                plugin["permissions"] = []
        else:
            plugin["permissions"] = []

        # Check for updates if plugin has a source URL
        if plugin.get("sourceUrl"):
            try:
                # Import here to avoid circular imports
                import aiohttp
                import re

                # Parse GitHub URL to get owner/repo
                github_match = re.match(r'https://github\.com/([^/]+)/([^/]+)', plugin["sourceUrl"])
                if github_match:
                    owner, repo = github_match.groups()

                    # Get latest release from GitHub
                    async with aiohttp.ClientSession() as session:
                        async with session.get(f'https://api.github.com/repos/{owner}/{repo}/releases/latest') as response:
                            if response.status == 200:
                                release_data = await response.json()
                                latest_version = release_data.get('tag_name', '').lstrip('v')

                                # Compare versions
                                current_version = plugin["version"]
                                update_available = _is_version_newer(latest_version, current_version)

                                # Update plugin data
                                plugin["updateAvailable"] = update_available
                                plugin["latestVersion"] = latest_version

                                # Update database
                                if update_available:
                                    from sqlalchemy import text
                                    update_query = text("""
                                    UPDATE plugin
                                    SET update_available = :update_available,
                                        latest_version = :latest_version,
                                        last_update_check = CURRENT_TIMESTAMP
                                    WHERE id = :plugin_id
                                    """)
                                    await db.execute(update_query, {
                                        "update_available": update_available,
                                        "latest_version": latest_version,
                                        "plugin_id": plugin_id
                                    })
                                    await db.commit()

                                logger.info(f"Update check completed: {current_version} -> {latest_version}, available: {update_available}")
                            else:
                                logger.warning(f"GitHub API returned status {response.status}")
            except Exception as e:
                logger.error(f"Error checking for updates: {e}")
                # Don't fail the request if update check fails
                pass

        logger.info("Returning module and plugin details", plugin_id=plugin_id, module_id=module_id)
        return {
            "module": module,
            "plugin": plugin
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error getting module detail", 
                    plugin_id=plugin_id, 
                    module_id=module_id, 
                    error=str(e))
        raise HTTPException(status_code=500, detail=str(e))

# Add a direct endpoint for the frontend to use
@router.get("/plugins/direct/{plugin_id}/modules/{module_id}")
async def get_module_detail_direct(
    plugin_id: str,
    module_id: str,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user),
):
    """Direct endpoint for module details that matches the frontend path."""
    try:
        logger.info("Direct endpoint called", plugin_id=plugin_id, module_id=module_id)
        
        # Use raw SQL query to bypass ORM issues
        # Get module data
        from sqlalchemy import text
        module_query = text("""
        SELECT * FROM module 
        WHERE plugin_id = :plugin_id AND id = :module_id
        """)
        module_result = await db.execute(module_query, {"plugin_id": plugin_id, "module_id": module_id})
        module_row = module_result.fetchone()
        
        if not module_row:
            logger.error("Module not found", plugin_id=plugin_id, module_id=module_id)
            raise HTTPException(status_code=404, detail=f"Module {module_id} not found in plugin {plugin_id}")
        
        logger.info("Module found", plugin_id=plugin_id, module_id=module_id)
        
        # Get plugin data
        plugin_query = text("""
        SELECT * FROM plugin 
        WHERE id = :plugin_id
        """)
        plugin_result = await db.execute(plugin_query, {"plugin_id": plugin_id})
        plugin_row = plugin_result.fetchone()
        
        if not plugin_row:
            logger.error("Plugin not found", plugin_id=plugin_id)
            raise HTTPException(status_code=404, detail=f"Plugin {plugin_id} not found")
        
        # Convert rows to dictionaries
        module = {
            "id": module_row.id,
            "pluginId": module_row.plugin_id,
            "name": module_row.name,
            "displayName": module_row.display_name,
            "description": module_row.description,
            "icon": module_row.icon,
            "category": module_row.category,
            "enabled": bool(module_row.enabled),
            "priority": module_row.priority
        }
        
        # Parse JSON fields
        for field, attr in [
            ("props", module_row.props),
            ("configFields", module_row.config_fields),
            ("messages", module_row.messages),
            ("requiredServices", module_row.required_services),
            ("layout", module_row.layout)
        ]:
            if attr:
                try:
                    module[field] = json.loads(attr)
                except json.JSONDecodeError:
                    module[field] = {}
        
        # Parse tags
        if module_row.tags:
            try:
                module["tags"] = json.loads(module_row.tags)
            except json.JSONDecodeError:
                module["tags"] = []
        else:
            module["tags"] = []
        
        # Parse dependencies
        if module_row.dependencies:
            try:
                module["dependencies"] = json.loads(module_row.dependencies)
            except json.JSONDecodeError:
                module["dependencies"] = []
        else:
            module["dependencies"] = []
        
        # Convert plugin row to dictionary
        plugin = {
            "id": plugin_row.id,
            "name": plugin_row.name,
            "description": plugin_row.description,
            "version": plugin_row.version,
            "type": plugin_row.type,
            "enabled": bool(plugin_row.enabled),
            "icon": plugin_row.icon,
            "category": plugin_row.category,
            "status": plugin_row.status,
            "official": bool(plugin_row.official),
            "author": plugin_row.author,
            "lastUpdated": plugin_row.last_updated,
            "compatibility": plugin_row.compatibility,
            "downloads": plugin_row.downloads,
            "scope": plugin_row.scope,
            "bundleMethod": plugin_row.bundle_method,
            "bundleLocation": plugin_row.bundle_location,
            "isLocal": bool(plugin_row.is_local),
            # Add source tracking fields for update/delete functionality
            "sourceType": plugin_row.source_type,
            "sourceUrl": plugin_row.source_url,
            "updateCheckUrl": plugin_row.update_check_url,
            "lastUpdateCheck": plugin_row.last_update_check.isoformat() if plugin_row.last_update_check else None,
            "updateAvailable": bool(plugin_row.update_available) if plugin_row.update_available is not None else False,
            "latestVersion": plugin_row.latest_version,
            "installationType": plugin_row.installation_type
        }
        
        # Parse JSON fields for plugin
        for field, attr in [
            ("configFields", plugin_row.config_fields),
            ("messages", plugin_row.messages),
            ("dependencies", plugin_row.dependencies)
        ]:
            if attr:
                try:
                    plugin[field] = json.loads(attr)
                except json.JSONDecodeError:
                    plugin[field] = {} if field != "dependencies" else []
            else:
                plugin[field] = {} if field != "dependencies" else []
        
        # Parse permissions field
        if plugin_row.permissions:
            try:
                plugin["permissions"] = json.loads(plugin_row.permissions)
            except json.JSONDecodeError:
                plugin["permissions"] = []
        else:
            plugin["permissions"] = []

        logger.info("Direct endpoint successful", plugin_id=plugin_id, module_id=module_id)
        return {
            "module": module,
            "plugin": plugin
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Direct endpoint error", plugin_id=plugin_id, module_id=module_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))

# Add a simple test endpoint
@router.get("/plugins/test")
async def test_endpoint(auth: AuthContext = Depends(require_user)):
    """Simple test endpoint to verify the router is working."""
    logger.info("Test endpoint called by user", user_id=auth.user_id)
    return {"message": "Plugin router is working!", "user_id": auth.user_id}

# Add a simple module data endpoint
@router.get("/plugins/simple/{plugin_id}/modules/{module_id}")
async def get_simple_module_detail(
    plugin_id: str,
    module_id: str,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user)
):
    """Simple endpoint that directly returns module data from the database."""
    logger.info("Simple module endpoint called", plugin_id=plugin_id, module_id=module_id)
    try:
        # Use raw SQL query to bypass ORM issues
        from sqlalchemy import text
        module_query = text("""
        SELECT * FROM module
        WHERE plugin_id = :plugin_id AND id = :module_id AND user_id = :user_id
        """)
        module_result = await db.execute(
            module_query,
            {"plugin_id": plugin_id, "module_id": module_id, "user_id": auth.user_id}
        )
        module_row = module_result.fetchone()
        
        if not module_row:
            logger.error("Module not found", plugin_id=plugin_id, module_id=module_id)
            raise HTTPException(status_code=404, detail=f"Module {module_id} not found in plugin {plugin_id}")
        
        # Get plugin data
        plugin_query = text("""
        SELECT * FROM plugin
        WHERE id = :plugin_id AND user_id = :user_id
        """)
        plugin_result = await db.execute(
            plugin_query,
            {"plugin_id": plugin_id, "user_id": auth.user_id}
        )
        plugin_row = plugin_result.fetchone()
        
        if not plugin_row:
            logger.error("Plugin not found", plugin_id=plugin_id)
            raise HTTPException(status_code=404, detail=f"Plugin {plugin_id} not found")
        
        # Return simplified data
        return {
            "module": {
                "id": module_row.id,
                "pluginId": module_row.plugin_id,
                "name": module_row.name,
                "displayName": module_row.display_name,
                "description": module_row.description,
            },
            "plugin": {
                "id": plugin_row.id,
                "name": plugin_row.name,
                "description": plugin_row.description,
                "version": plugin_row.version,
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Simple module endpoint error", plugin_id=plugin_id, module_id=module_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))

@router.patch("/plugins/{plugin_id}")
async def update_plugin_status(
    plugin_id: str,
    data: Dict[str, Any] = Body(...),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user)
):
    """Enable or disable a plugin."""
    try:
        if "enabled" not in data:
            raise HTTPException(status_code=400, detail="Missing 'enabled' field in request body")
        
        enabled = bool(data["enabled"])
        
        # Check if plugin belongs to current user
        plugin = await db.execute(
            select(Plugin).where(
                Plugin.id == plugin_id,
                Plugin.user_id == auth.user_id
            )
        )
        if not plugin.scalars().first():
            raise HTTPException(status_code=404, detail=f"Plugin {plugin_id} not found or you don't have permission")
        
        repo = PluginRepository(db)
        success = await repo.update_plugin_status(plugin_id, enabled)
        
        if not success:
            raise HTTPException(status_code=404, detail=f"Plugin {plugin_id} not found")
        
        return {"status": "success", "message": f"Plugin {plugin_id} {'enabled' if enabled else 'disabled'} successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error updating plugin status", plugin_id=plugin_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))

@router.patch("/plugins/{plugin_id}/modules/{module_id}")
async def update_module_status(
    plugin_id: str,
    module_id: str,
    data: Dict[str, Any] = Body(...),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user)
):
    """Enable or disable a module."""
    try:
        if "enabled" not in data:
            raise HTTPException(status_code=400, detail="Missing 'enabled' field in request body")
        
        enabled = bool(data["enabled"])
        
        # Check if module belongs to current user
        module = await db.execute(
            select(Module).where(
                Module.plugin_id == plugin_id,
                Module.id == module_id,
                Module.user_id == auth.user_id
            )
        )
        if not module.scalars().first():
            raise HTTPException(status_code=404, detail=f"Module {module_id} not found in plugin {plugin_id} or you don't have permission")
        
        repo = PluginRepository(db)
        success = await repo.update_module_status(plugin_id, module_id, enabled)
        
        if not success:
            raise HTTPException(status_code=404, detail=f"Module {module_id} not found in plugin {plugin_id}")
        
        return {"status": "success", "message": f"Module {module_id} {'enabled' if enabled else 'disabled'} successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error updating module status", 
                    plugin_id=plugin_id, 
                    module_id=module_id, 
                    error=str(e))

# Add a helper endpoint to look up a plugin by slug
@router.get("/plugins/by-slug/{plugin_slug}")
async def get_plugin_by_slug(
    plugin_slug: str,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user)
):
    """Get a plugin by its slug."""
    repo = PluginRepository(db)
    plugin = await repo.get_plugin_by_slug(plugin_slug, auth.user_id)
    
    if not plugin:
        raise HTTPException(status_code=404, detail=f"Plugin {plugin_slug} not found")

    return plugin

# Include the lifecycle API router for plugin lifecycle management
router.include_router(lifecycle_router, tags=["Plugin Lifecycle Management"])


# Add this route at the end so it doesn't catch other routes
@router.get("/plugins/{plugin_id}/{path:path}")
async def serve_plugin_static(
    plugin_id: str,
    path: str,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user)
):
    """Serve static files from plugin directory."""
    # Skip if the path starts with "modules/" to avoid catching module endpoints
    if path.startswith("modules/"):
        raise HTTPException(status_code=404, detail="File not found")
    
    # Skip if the path starts with "update/" to avoid catching update endpoints
    if path.startswith("update/"):
        raise HTTPException(status_code=404, detail="File not found")

    logger.debug(f"Serving static file for plugin_id: {plugin_id}, path: {path}")
    
    # First try to find the plugin by ID
    plugin_query = await db.execute(
        select(Plugin).where(
            Plugin.id == plugin_id,
            Plugin.user_id == auth.user_id
        )
    )
    plugin = plugin_query.scalars().first()
    
    # If not found by ID, try to find by plugin_slug
    if not plugin:
        logger.debug(f"Plugin not found by ID, trying plugin_slug: {plugin_id}")
        plugin_query = await db.execute(
            select(Plugin).where(
                Plugin.plugin_slug == plugin_id,
                Plugin.user_id == auth.user_id
            )
        )
        plugin = plugin_query.scalars().first()
    
    if not plugin:
        # For backward compatibility, try without user_id check
        logger.debug(f"Plugin not found for current user, trying without user check")
        plugin_query = await db.execute(
            select(Plugin).where(Plugin.id == plugin_id)
        )
        plugin = plugin_query.scalars().first()
        
        if not plugin:
            plugin_query = await db.execute(
                select(Plugin).where(Plugin.plugin_slug == plugin_id)
            )
            plugin = plugin_query.scalars().first()
    
    if not plugin:
        logger.error(f"Plugin not found: {plugin_id}")
        raise HTTPException(status_code=404, detail="Plugin not found")
    
    logger.debug(f"Found plugin: {plugin.id}, plugin_slug: {plugin.plugin_slug}, user_id: {plugin.user_id}")
    
    # Try multiple possible locations for the file
    possible_paths = []

    def add_candidate(base_dir: Path) -> None:
        candidate = _safe_join(base_dir, path)
        if candidate:
            possible_paths.append(candidate)
    
    # 1. New architecture: Shared storage with version
    if plugin.plugin_slug and plugin.version:
        # The actual path is backend/backend/plugins/shared/ relative to project root
        # PLUGINS_DIR is already /path/to/project/plugins, so PLUGINS_DIR.parent is /path/to/project
        # We need to go to /path/to/project/backend/backend/plugins/shared/
        shared_plugin_dir = PLUGINS_DIR.parent / "backend" / "plugins" / "shared" / plugin.plugin_slug / f"v{plugin.version}"
        add_candidate(shared_plugin_dir)

    # 2. New architecture: Shared storage without version (fallback)
    if plugin.plugin_slug:
        shared_plugin_dir = PLUGINS_DIR.parent / "backend" / "plugins" / "shared" / plugin.plugin_slug
        add_candidate(shared_plugin_dir)

    # 3. Backend plugins directory (where webpack builds to)
    if plugin.plugin_slug:
        backend_plugin_dir = PLUGINS_DIR.parent / "backend" / "plugins" / plugin.plugin_slug
        add_candidate(backend_plugin_dir)

    # 4. User-specific directory with plugin_slug
    if plugin.user_id and plugin.plugin_slug:
        user_plugin_dir = PLUGINS_DIR / plugin.user_id / plugin.plugin_slug
        add_candidate(user_plugin_dir)
    
    # 5. User-specific directory with plugin ID
    if plugin.user_id:
        user_plugin_dir = PLUGINS_DIR / plugin.user_id / plugin.id
        add_candidate(user_plugin_dir)
    
    # 6. Legacy path directly under plugins directory with plugin_slug
    if plugin.plugin_slug:
        add_candidate(PLUGINS_DIR / plugin.plugin_slug)
    
    # 7. Legacy path directly under plugins directory with plugin ID
    add_candidate(PLUGINS_DIR / plugin.id)
    
    # Try each path
    for plugin_path in possible_paths:
        logger.debug(f"Trying path: {plugin_path}")
        if plugin_path.exists():
            logger.debug(f"Found file at: {plugin_path}")
            return FileResponse(plugin_path)
    
    # If we get here, the file wasn't found in any of the possible locations
    logger.error(f"File not found in any location. Tried: {[str(p) for p in possible_paths]}")
    raise HTTPException(status_code=404, detail="File not found")

@router.get("/public/plugins/{plugin_id}/{path:path}")
async def serve_plugin_static_public(
    plugin_id: str,
    path: str,
    db: AsyncSession = Depends(get_db),
    auth: Optional[AuthContext] = Depends(optional_user),
):
    """Serve static files from plugin directory without authentication.
    This endpoint is specifically for serving JavaScript bundles and other
    static assets needed for the frontend to load plugins.
    """
    # Only allow specific file extensions for security
    allowed_extensions = ['.js', '.css', '.map', '.json', '.woff', '.woff2', '.ttf', '.eot', '.svg', '.png', '.jpg', '.jpeg', '.gif']
    file_ext = Path(path).suffix.lower()
    
    if file_ext not in allowed_extensions:
        logger.warning(f"Attempted to access disallowed file type: {file_ext}")
        raise HTTPException(status_code=403, detail="File type not allowed")

    if settings.APP_ENV.lower() not in {"dev", "development", "test", "local"} and auth is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    # Skip if the path starts with "modules/" to avoid catching module endpoints
    if path.startswith("modules/"):
        raise HTTPException(status_code=404, detail="File not found")
    
    logger.debug(f"Serving public static file for plugin_id: {plugin_id}, path: {path}")
    
    # Try to find the plugin by ID first
    plugin_filters = [Plugin.id == plugin_id]
    if auth:
        plugin_filters.append(Plugin.user_id == auth.user_id)
    plugin_query = await db.execute(select(Plugin).where(*plugin_filters))
    plugin = plugin_query.scalars().first()
    
    # If not found by ID, try to find by plugin_slug
    if not plugin:
        plugin_filters = [Plugin.plugin_slug == plugin_id]
        if auth:
            plugin_filters.append(Plugin.user_id == auth.user_id)
        plugin_query = await db.execute(select(Plugin).where(*plugin_filters))
        plugin = plugin_query.scalars().first()
    
    if not plugin:
        logger.error(f"Plugin not found: {plugin_id}")
        raise HTTPException(status_code=404, detail="Plugin not found")
    
    logger.debug(f"Found plugin: {plugin.id}, plugin_slug: {plugin.plugin_slug}, user_id: {plugin.user_id}")
    
    # Try multiple possible locations for the file
    possible_paths = []

    def add_candidate(base_dir: Path) -> None:
        candidate = _safe_join(base_dir, path)
        if candidate:
            possible_paths.append(candidate)
    
    # 1. New architecture: Shared storage with version
    if plugin.plugin_slug and plugin.version:
        # The actual path is backend/backend/plugins/shared/ relative to project root
        # PLUGINS_DIR is already /path/to/project/plugins, so PLUGINS_DIR.parent is /path/to/project
        # We need to go to /path/to/project/backend/backend/plugins/shared/
        shared_plugin_dir = PLUGINS_DIR.parent / "plugins" / "shared" / plugin.plugin_slug / f"v{plugin.version}"
        add_candidate(shared_plugin_dir)
        logger.debug(f"Added new architecture path with version: {shared_plugin_dir / path}")

    # 2. New architecture: Shared storage without version (fallback)
    if plugin.plugin_slug:
        shared_plugin_dir = PLUGINS_DIR.parent / "plugins" / "shared" / plugin.plugin_slug
        add_candidate(shared_plugin_dir)
        logger.debug(f"Added new architecture path without version: {shared_plugin_dir / path}")

    # 3. Backend plugins directory (where webpack builds to)
    if plugin.plugin_slug:
        backend_plugin_dir = PLUGINS_DIR.parent / "backend" / "plugins" / plugin.plugin_slug
        add_candidate(backend_plugin_dir)
        logger.debug(f"Added backend plugins path: {backend_plugin_dir / path}")

    # 4. User-specific directory with plugin_slug
    if plugin.user_id and plugin.plugin_slug:
        user_plugin_dir = PLUGINS_DIR / plugin.user_id / plugin.plugin_slug
        add_candidate(user_plugin_dir)
    
    # 5. User-specific directory with plugin ID
    if plugin.user_id:
        user_plugin_dir = PLUGINS_DIR / plugin.user_id / plugin.id
        add_candidate(user_plugin_dir)
    
    # 6. Legacy path directly under plugins directory with plugin_slug
    if plugin.plugin_slug:
        add_candidate(PLUGINS_DIR / plugin.plugin_slug)
    
    # 7. Legacy path directly under plugins directory with plugin ID
    add_candidate(PLUGINS_DIR / plugin.id)
    
    # Try each path
    for plugin_path in possible_paths:
        logger.debug(f"Trying path: {plugin_path}")
        if plugin_path.exists():
            logger.debug(f"Found file at: {plugin_path}")
            return FileResponse(plugin_path)
    
    # If we get here, the file wasn't found in any of the possible locations
    logger.error(f"File not found in any location. Tried: {[str(p) for p in possible_paths]}")
    raise HTTPException(status_code=404, detail="File not found")
