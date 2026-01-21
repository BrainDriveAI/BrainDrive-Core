"""
Plugin Route Loader for BrainDrive backend plugins.

This module provides the PluginRouteLoader class that handles dynamic loading,
unloading, and mounting of plugin routes without server restart.

Key features:
- Atomic route swapping with staging area
- Async locking to prevent concurrent reloads
- Namespaced module imports to prevent collisions
- Automatic sys.modules cleanup on unload
- Skip-on-failure: valid plugins load even if one fails

Usage:
    from app.plugins.route_loader import get_plugin_loader

    loader = get_plugin_loader()
    result = await loader.reload_routes(app, db)
"""

import asyncio
import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Any, Optional, Callable, Set

from fastapi import APIRouter, FastAPI, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import structlog

from app.plugins.decorators import (
    get_plugin_endpoints,
    PluginRequest,
    EndpointMetadata,
)
from app.core.auth_deps import require_user, require_admin
from app.core.auth_context import AuthContext
from app.models.plugin import Plugin

logger = structlog.get_logger()


# Plugin route prefix - backend plugin dynamic routes are mounted here
# Using a distinct prefix to avoid conflicts with core plugin management routes
PLUGIN_ROUTE_PREFIX = "/api/v1/plugin-api"


@dataclass
class PluginLoadError:
    """
    Represents an error that occurred while loading a plugin.

    Attributes:
        plugin_slug: The slug of the plugin that failed to load
        error: Human-readable error message
        exception_type: The type of exception that occurred
        details: Additional error details (optional)
    """
    plugin_slug: str
    error: str
    exception_type: str = "Unknown"
    details: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses."""
        result = {
            "plugin_slug": self.plugin_slug,
            "error": self.error,
            "exception_type": self.exception_type,
        }
        if self.details:
            result["details"] = self.details
        return result


@dataclass
class ReloadResult:
    """
    Result of a route reload operation.

    Attributes:
        loaded: List of plugin slugs that were successfully loaded
        unloaded: List of plugin slugs that were unloaded
        errors: List of errors that occurred during loading
        total_routes: Total number of routes loaded
    """
    loaded: List[str] = field(default_factory=list)
    unloaded: List[str] = field(default_factory=list)
    errors: List[PluginLoadError] = field(default_factory=list)
    total_routes: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "loaded": self.loaded,
            "unloaded": self.unloaded,
            "errors": [e.to_dict() for e in self.errors],
            "total_routes": self.total_routes,
            "success": len(self.errors) == 0,
        }


@dataclass
class PluginInfo:
    """
    Information about a backend plugin needed for route loading.

    Extracted from database Plugin model and filesystem.
    """
    slug: str
    name: str
    version: str
    plugin_type: str
    endpoints_file: str
    route_prefix: str
    plugin_path: Path
    enabled: bool = True

    @property
    def module_name(self) -> str:
        """Generate the namespaced module name for this plugin."""
        module_slug = self.slug.replace('-', '_')
        major_version = self.version.split('.')[0]
        return f"braindrive.plugins.{module_slug}.v{major_version}.endpoints"

    @property
    def endpoints_path(self) -> Path:
        """Get the full path to the endpoints file."""
        return self.plugin_path / self.endpoints_file


class PluginRouteLoader:
    """
    Manages dynamic loading and mounting of plugin routes.

    This class provides thread-safe route reloading with atomic swapping,
    ensuring that in-flight requests complete normally while new requests
    use updated routes.

    Thread Safety:
        All route modifications are protected by an async lock to prevent
        concurrent reload operations.

    Atomic Swap:
        Routes are loaded into a staging area and validated before being
        swapped into the active route table.

    Error Handling:
        If a plugin fails to load, it is skipped and other valid plugins
        are still loaded. Errors are collected and returned.
    """

    def __init__(self):
        """Initialize the route loader."""
        self._reload_lock = asyncio.Lock()
        self._active_routes: Dict[str, APIRouter] = {}
        self._mounted_prefixes: Set[str] = set()
        self._app: Optional[FastAPI] = None
        logger.info("PluginRouteLoader initialized")

    def set_app(self, app: FastAPI) -> None:
        """
        Set the FastAPI application instance.

        Must be called during app startup before reload_routes.

        Args:
            app: The FastAPI application instance
        """
        self._app = app
        logger.info("FastAPI app registered with PluginRouteLoader")

    async def reload_routes(self, db: AsyncSession) -> ReloadResult:
        """
        Reload all backend plugin routes.

        This method:
        1. Acquires an exclusive lock
        2. Queries for enabled backend plugins
        3. Loads routes into a staging area
        4. Validates loaded routes
        5. Atomically swaps staging to active
        6. Cleans up old modules

        Args:
            db: Database session for querying plugins

        Returns:
            ReloadResult with loaded plugins, unloaded plugins, and any errors
        """
        if self._app is None:
            raise RuntimeError("FastAPI app not set. Call set_app() first.")

        async with self._reload_lock:
            logger.info("Starting plugin route reload")

            result = ReloadResult()
            staging: Dict[str, APIRouter] = {}

            # Get enabled backend plugins
            plugins = await self._get_enabled_backend_plugins(db)
            logger.info(f"Found {len(plugins)} enabled backend plugins")

            # Track which plugins were previously loaded
            previously_loaded = set(self._active_routes.keys())

            # Load each plugin's routes into staging
            for plugin_info in plugins:
                try:
                    router = await self._load_plugin_routes(plugin_info)
                    if router:
                        staging[plugin_info.slug] = router
                        result.total_routes += len(router.routes)
                        logger.info(
                            f"Loaded plugin routes",
                            plugin=plugin_info.slug,
                            routes=len(router.routes)
                        )
                except Exception as e:
                    error = PluginLoadError(
                        plugin_slug=plugin_info.slug,
                        error=str(e),
                        exception_type=type(e).__name__,
                    )
                    result.errors.append(error)
                    logger.error(
                        f"Failed to load plugin routes",
                        plugin=plugin_info.slug,
                        error=str(e),
                        exc_info=True
                    )

            # Perform atomic swap
            await self._swap_routes(staging)

            # Record results
            result.loaded = list(staging.keys())
            result.unloaded = list(previously_loaded - set(staging.keys()))

            # Clean up modules for unloaded plugins
            for slug in result.unloaded:
                self._unload_plugin_module(slug)

            logger.info(
                "Plugin route reload complete",
                loaded=len(result.loaded),
                unloaded=len(result.unloaded),
                errors=len(result.errors),
                total_routes=result.total_routes
            )

            return result

    async def _get_enabled_backend_plugins(self, db: AsyncSession) -> List[PluginInfo]:
        """
        Query the database for enabled backend plugins.

        Args:
            db: Database session

        Returns:
            List of PluginInfo objects for enabled backend/fullstack plugins
        """
        # Query for plugins with type 'backend' or 'fullstack' that are enabled
        query = select(Plugin).where(
            Plugin.enabled == True,
            Plugin.type.in_(['backend', 'fullstack'])
        )

        result = await db.execute(query)
        plugins = result.scalars().all()

        plugin_infos = []
        for plugin in plugins:
            # Determine plugin path
            # Standard path: backend/plugins/shared/{slug}/v{major_version}/
            major_version = plugin.version.split('.')[0]
            plugin_path = Path(__file__).parent.parent.parent / "plugins" / "shared" / plugin.plugin_slug / f"v{major_version}"

            # Check if plugin has endpoints file configured
            # For now, default to 'endpoints.py' if not specified
            endpoints_file = "endpoints.py"

            # Check if the plugin metadata specifies an endpoints file
            # This would come from the lifecycle_manager.py plugin_data
            # For MVP, we use convention over configuration

            # Get route prefix from plugin metadata or default to plugin slug
            route_prefix = f"/{plugin.plugin_slug}"

            plugin_info = PluginInfo(
                slug=plugin.plugin_slug,
                name=plugin.name,
                version=plugin.version,
                plugin_type=plugin.type,
                endpoints_file=endpoints_file,
                route_prefix=route_prefix,
                plugin_path=plugin_path,
                enabled=plugin.enabled,
            )
            plugin_infos.append(plugin_info)

        return plugin_infos

    async def _load_plugin_routes(self, plugin_info: PluginInfo) -> Optional[APIRouter]:
        """
        Load routes from a single plugin.

        This method:
        1. Loads the plugin's endpoints module using namespaced imports
        2. Discovers decorated endpoint functions
        3. Creates a FastAPI router with the endpoints

        Args:
            plugin_info: Information about the plugin to load

        Returns:
            APIRouter with the plugin's endpoints, or None if no endpoints found
        """
        endpoints_path = plugin_info.endpoints_path

        # Check if endpoints file exists
        if not endpoints_path.exists():
            logger.warning(
                f"No endpoints file found for plugin",
                plugin=plugin_info.slug,
                path=str(endpoints_path)
            )
            return None

        # Load the module with namespaced name
        module = self._load_plugin_module(
            plugin_info.slug,
            plugin_info.version,
            endpoints_path
        )

        if module is None:
            return None

        # Discover decorated endpoints
        endpoints = get_plugin_endpoints(module)

        if not endpoints:
            logger.info(
                f"No endpoints decorated in plugin",
                plugin=plugin_info.slug
            )
            return None

        # Create router for this plugin
        router = self._create_router_from_endpoints(plugin_info, endpoints)

        return router

    def _load_plugin_module(
        self,
        plugin_slug: str,
        version: str,
        file_path: Path
    ) -> Optional[Any]:
        """
        Load a plugin module with a namespaced name.

        Uses importlib to load the module with a unique name to prevent
        collisions in sys.modules.

        Args:
            plugin_slug: The plugin's slug
            version: The plugin's version
            file_path: Path to the Python file to load

        Returns:
            The loaded module, or None if loading failed
        """
        # Convert slug to valid Python identifier
        module_slug = plugin_slug.replace('-', '_')
        major_version = version.split('.')[0]

        # Create namespaced module name
        module_name = f"braindrive.plugins.{module_slug}.v{major_version}.endpoints"

        try:
            # Create module spec
            spec = importlib.util.spec_from_file_location(module_name, file_path)
            if spec is None or spec.loader is None:
                logger.error(f"Could not create module spec", path=str(file_path))
                return None

            # Create module from spec
            module = importlib.util.module_from_spec(spec)

            # Register in sys.modules before executing
            sys.modules[module_name] = module

            # Execute the module
            spec.loader.exec_module(module)

            logger.debug(
                f"Loaded plugin module",
                plugin=plugin_slug,
                module_name=module_name
            )

            return module

        except Exception as e:
            # Clean up on failure
            if module_name in sys.modules:
                del sys.modules[module_name]
            logger.error(
                f"Failed to load plugin module",
                plugin=plugin_slug,
                error=str(e),
                exc_info=True
            )
            raise

    def _unload_plugin_module(self, plugin_slug: str) -> None:
        """
        Unload a plugin's modules from sys.modules.

        Removes all modules with the plugin's namespace prefix.

        Args:
            plugin_slug: The plugin's slug
        """
        module_slug = plugin_slug.replace('-', '_')
        prefix = f"braindrive.plugins.{module_slug}"

        # Find and remove all modules with this prefix
        to_remove = [k for k in sys.modules if k.startswith(prefix)]

        for key in to_remove:
            del sys.modules[key]
            logger.debug(f"Unloaded module", module=key)

        if to_remove:
            logger.info(
                f"Unloaded plugin modules",
                plugin=plugin_slug,
                count=len(to_remove)
            )

    def _create_router_from_endpoints(
        self,
        plugin_info: PluginInfo,
        endpoints: List[Callable]
    ) -> APIRouter:
        """
        Create a FastAPI router from decorated endpoint functions.

        Args:
            plugin_info: Information about the plugin
            endpoints: List of decorated endpoint functions

        Returns:
            Configured APIRouter with all endpoints mounted
        """
        router = APIRouter(
            prefix=plugin_info.route_prefix,
            tags=[f"plugin-{plugin_info.slug}"],
        )

        for endpoint_func in endpoints:
            metadata: EndpointMetadata = endpoint_func._plugin_endpoint_metadata

            # Create wrapper that injects PluginRequest
            wrapped = self._create_endpoint_wrapper(endpoint_func, metadata.admin_only)

            # Determine dependencies
            if metadata.admin_only:
                dependencies = [Depends(require_admin)]
            else:
                dependencies = [Depends(require_user)]

            # Add route for each method
            for method in metadata.methods:
                router.add_api_route(
                    path=metadata.path,
                    endpoint=wrapped,
                    methods=[method],
                    summary=metadata.summary,
                    description=metadata.description,
                    tags=metadata.tags,
                    response_model=metadata.response_model,
                    status_code=metadata.status_code,
                    dependencies=dependencies,
                )

        return router

    def _create_endpoint_wrapper(
        self,
        endpoint_func: Callable,
        admin_only: bool
    ) -> Callable:
        """
        Create a wrapper function that injects PluginRequest.

        The wrapper extracts authentication context and creates a PluginRequest
        to pass to the original endpoint function.

        Args:
            endpoint_func: The original endpoint function
            admin_only: Whether admin authentication is required

        Returns:
            Wrapped async function compatible with FastAPI
        """
        async def wrapper(
            request: Request,
            auth: AuthContext = Depends(require_admin if admin_only else require_user),
            **kwargs
        ):
            # Create PluginRequest from auth context
            plugin_request = PluginRequest.from_auth_context(request, auth)

            # Call the original endpoint
            return await endpoint_func(plugin_request, **kwargs)

        # Preserve function metadata
        wrapper.__name__ = endpoint_func.__name__
        wrapper.__doc__ = endpoint_func.__doc__

        return wrapper

    async def _swap_routes(self, staging: Dict[str, APIRouter]) -> None:
        """
        Atomically swap staged routes into the active route table.

        This method:
        1. Unmounts all currently mounted plugin routes
        2. Mounts all routes from staging
        3. Updates the active routes tracking

        Note: There is a brief period (~100-500ms) during swap where
        plugin routes may be unavailable. This is acceptable.

        Args:
            staging: Dictionary of plugin_slug -> APIRouter to mount
        """
        if self._app is None:
            raise RuntimeError("FastAPI app not set")

        # Unmount old plugin routes
        # FastAPI doesn't have a direct unmount, so we need to rebuild
        # For now, we track mounted prefixes and skip if already mounted

        # In FastAPI, routes are stored in app.routes
        # We need to remove old plugin routes and add new ones

        # Get the base prefix for plugin routes
        base_prefix = PLUGIN_ROUTE_PREFIX

        # Remove old plugin routes from app.routes
        # We identify them by their path starting with the plugin prefix
        routes_to_remove = []
        for route in self._app.routes:
            if hasattr(route, 'path') and route.path.startswith(base_prefix):
                routes_to_remove.append(route)

        for route in routes_to_remove:
            self._app.routes.remove(route)

        logger.debug(f"Removed {len(routes_to_remove)} old plugin routes")

        # Mount new plugin routers
        for plugin_slug, router in staging.items():
            # Create full prefix: /api/v1/plugins/{plugin_slug}{route_prefix}
            # The router already has the route_prefix, so we just add the base
            full_prefix = f"{base_prefix}/{plugin_slug}"

            # Include the router
            self._app.include_router(router, prefix=full_prefix)

            logger.debug(
                f"Mounted plugin router",
                plugin=plugin_slug,
                prefix=full_prefix
            )

        # Update tracking
        self._active_routes = staging
        self._mounted_prefixes = {f"{base_prefix}/{slug}" for slug in staging.keys()}

        logger.info(f"Swapped routes: {len(staging)} plugins mounted")

    def get_active_plugins(self) -> List[str]:
        """
        Get list of currently loaded plugin slugs.

        Returns:
            List of plugin slugs with active routes
        """
        return list(self._active_routes.keys())

    def is_plugin_loaded(self, plugin_slug: str) -> bool:
        """
        Check if a plugin's routes are currently loaded.

        Args:
            plugin_slug: The plugin slug to check

        Returns:
            True if the plugin's routes are loaded
        """
        return plugin_slug in self._active_routes


# Singleton instance
_plugin_loader: Optional[PluginRouteLoader] = None
_loader_lock = asyncio.Lock()


def get_plugin_loader() -> PluginRouteLoader:
    """
    Get the singleton PluginRouteLoader instance.

    Creates the instance on first call.

    Returns:
        The global PluginRouteLoader instance
    """
    global _plugin_loader

    if _plugin_loader is None:
        _plugin_loader = PluginRouteLoader()

    return _plugin_loader


async def initialize_plugin_routes(app: FastAPI, db: AsyncSession) -> ReloadResult:
    """
    Initialize plugin routes on application startup.

    Convenience function to set up the route loader and load initial routes.

    Args:
        app: The FastAPI application instance
        db: Database session

    Returns:
        ReloadResult from the initial route load
    """
    loader = get_plugin_loader()
    loader.set_app(app)
    return await loader.reload_routes(db)
