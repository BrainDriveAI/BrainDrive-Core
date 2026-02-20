"""
Dynamic loader for plugin-owned API endpoints.

This loader mounts endpoints declared in plugin ``endpoints.py`` modules under:
    /api/v1/plugin-api/{plugin_slug}{route_prefix}{endpoint_path}
"""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Dict, List, Optional, Sequence, Set

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.routing import APIRoute
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import structlog

from app.core.auth_context import AuthContext
from app.core.auth_deps import require_admin, require_user
from app.models.plugin import Plugin
from app.plugins.decorators import PluginEndpointDefinition, PluginRequest, get_plugin_endpoints

logger = structlog.get_logger()

PLUGIN_ROUTE_PREFIX = "/api/v1/plugin-api"
DEFAULT_ENDPOINTS_FILE = "endpoints.py"
PLUGIN_ROUTE_ALIASES: Dict[str, Sequence[str]] = {
    # Canonical Library plugin must also answer historical route slugs.
    "BrainDriveLibraryPlugin": ("braindrive-library", "BrainDriveLibraryService"),
    # Backward-compatibility alias consumed by older plugin builds.
    "BrainDriveLibraryService": ("braindrive-library",),
}


class PluginRouteLoader:
    """Loads and mounts backend endpoints from installed plugins."""

    def __init__(self) -> None:
        self._app: Optional[FastAPI] = None

    def set_app(self, app: FastAPI) -> None:
        self._app = app

    async def reload_routes(self, db: AsyncSession) -> Dict[str, Any]:
        """Remove previously mounted plugin routes and re-mount from DB state."""
        if self._app is None:
            logger.warning("PluginRouteLoader.reload_routes called before app was set.")
            return {"success": False, "reason": "app_not_set"}

        removed = self._unmount_plugin_routes()

        plugins = await self._query_plugins(db)
        loaded_plugins = 0
        mounted_routes = 0
        skipped: List[Dict[str, str]] = []
        seen_slugs: Set[str] = set()

        for plugin in plugins:
            plugin_slug = (plugin.plugin_slug or "").strip()
            if not plugin_slug:
                skipped.append({"plugin_id": plugin.id, "reason": "missing_plugin_slug"})
                continue
            if plugin_slug in seen_slugs:
                continue
            seen_slugs.add(plugin_slug)

            route_prefix = self._normalize_route_prefix(plugin.route_prefix or "/")
            endpoints_file = (plugin.endpoints_file or DEFAULT_ENDPOINTS_FILE).strip()
            endpoints_path = self._resolve_endpoints_path(plugin_slug, plugin.version, endpoints_file)

            if not endpoints_path.exists():
                # UX hardening: support full semver path when a v{major} alias is missing.
                fallback_path = self._resolve_full_version_endpoints_path(plugin_slug, plugin.version, endpoints_file)
                if fallback_path.exists():
                    logger.warning(
                        "Plugin endpoints loaded via full-version fallback; v{major} alias missing",
                        plugin_slug=plugin_slug,
                        expected=str(endpoints_path),
                        fallback=str(fallback_path),
                    )
                    endpoints_path = fallback_path
                else:
                    skipped.append(
                        {
                            "plugin_slug": plugin_slug,
                            "reason": "endpoints_file_missing",
                            "path": str(endpoints_path),
                        }
                    )
                    continue

            try:
                module = self._load_module(plugin_slug, endpoints_path)
                endpoint_defs = get_plugin_endpoints(module)
                if not endpoint_defs:
                    skipped.append({"plugin_slug": plugin_slug, "reason": "no_decorated_endpoints"})
                    continue

                mounted_count = self._mount_endpoints(plugin_slug, route_prefix, endpoint_defs)
                if mounted_count > 0:
                    loaded_plugins += 1
                    mounted_routes += mounted_count
            except Exception as exc:
                logger.exception(
                    "Failed loading plugin endpoints",
                    plugin_slug=plugin_slug,
                    endpoints_path=str(endpoints_path),
                    error=str(exc),
                )
                skipped.append({"plugin_slug": plugin_slug, "reason": str(exc)})

        self._reset_openapi_cache()
        logger.info(
            "Plugin route reload completed",
            removed_routes=removed,
            loaded_plugins=loaded_plugins,
            mounted_routes=mounted_routes,
            skipped=len(skipped),
        )
        return {
            "success": True,
            "removed_routes": removed,
            "loaded_plugins": loaded_plugins,
            "mounted_routes": mounted_routes,
            "skipped": skipped,
        }

    async def _query_plugins(self, db: AsyncSession) -> Sequence[Plugin]:
        stmt = (
            select(Plugin)
            .where(
                Plugin.enabled.is_(True),
                Plugin.type.in_(["backend", "fullstack"]),
            )
            .order_by(Plugin.plugin_slug.asc(), Plugin.updated_at.desc())
        )
        result = await db.execute(stmt)
        return result.scalars().all()

    def _mount_endpoints(
        self,
        plugin_slug: str,
        route_prefix: str,
        endpoint_defs: Sequence[PluginEndpointDefinition],
    ) -> int:
        if self._app is None:
            return 0

        mounted = 0
        for endpoint_def in endpoint_defs:
            wrapped_endpoint = self._build_endpoint_wrapper(plugin_slug, route_prefix, endpoint_def)
            for route_slug in self._route_slug_candidates(plugin_slug):
                route_path = self._build_route_path(route_slug, route_prefix, endpoint_def.path)
                route_name_slug = route_slug if route_slug == plugin_slug else f"{plugin_slug}:{route_slug}"

                before_count = len(self._app.router.routes)
                self._app.add_api_route(
                    route_path,
                    wrapped_endpoint,
                    methods=list(endpoint_def.methods),
                    name=f"plugin:{route_name_slug}:{endpoint_def.endpoint.__name__}",
                    tags=[f"plugin:{plugin_slug}"],
                )
                new_routes = self._app.router.routes[before_count:]
                for route in new_routes:
                    if isinstance(route, APIRoute):
                        setattr(route, "_braindrive_plugin_route", True)
                        setattr(route, "_braindrive_plugin_slug", plugin_slug)
                        setattr(route, "_braindrive_plugin_route_slug", route_slug)
                        mounted += 1

        return mounted

    def _build_endpoint_wrapper(
        self,
        plugin_slug: str,
        route_prefix: str,
        endpoint_def: PluginEndpointDefinition,
    ) -> Callable[..., Any]:
        endpoint_func = endpoint_def.endpoint
        auth_dependency = require_admin if endpoint_def.admin_only else require_user

        async def _handler(request: Request, auth: AuthContext = Depends(auth_dependency)):
            plugin_request = PluginRequest(
                request=request,
                auth=auth,
                plugin_slug=plugin_slug,
                route_prefix=route_prefix,
            )
            try:
                if inspect.iscoroutinefunction(endpoint_func):
                    return await endpoint_func(plugin_request)
                result = endpoint_func(plugin_request)
                if inspect.isawaitable(result):
                    return await result
                return result
            except HTTPException:
                raise
            except Exception as exc:
                logger.exception(
                    "Plugin endpoint execution failed",
                    plugin_slug=plugin_slug,
                    endpoint=endpoint_func.__name__,
                    error=str(exc),
                )
                raise HTTPException(
                    status_code=500,
                    detail=f"Plugin endpoint failed: {plugin_slug}/{endpoint_func.__name__}",
                ) from exc

        safe_slug = plugin_slug.replace("-", "_")
        _handler.__name__ = f"plugin_{safe_slug}_{endpoint_func.__name__}"
        _handler.__doc__ = endpoint_func.__doc__
        return _handler

    def _resolve_endpoints_path(self, plugin_slug: str, version: str, endpoints_file: str) -> Path:
        major = self._extract_major_version(version)
        return self._shared_plugins_root() / plugin_slug / f"v{major}" / endpoints_file

    def _resolve_full_version_endpoints_path(self, plugin_slug: str, version: str, endpoints_file: str) -> Path:
        normalized_version = str(version or "").strip().lstrip("v")
        return self._shared_plugins_root() / plugin_slug / f"v{normalized_version}" / endpoints_file

    @staticmethod
    def _extract_major_version(version: str) -> str:
        normalized = str(version or "").strip().lstrip("v")
        if not normalized:
            return "1"
        major = normalized.split(".", 1)[0]
        return major or "1"

    @staticmethod
    def _normalize_route_prefix(route_prefix: str) -> str:
        normalized = (route_prefix or "").strip()
        if not normalized:
            return "/"
        if not normalized.startswith("/"):
            normalized = f"/{normalized}"
        if normalized != "/" and normalized.endswith("/"):
            normalized = normalized.rstrip("/")
        return normalized

    @staticmethod
    def _build_route_path(plugin_slug: str, route_prefix: str, endpoint_path: str) -> str:
        parts = [PLUGIN_ROUTE_PREFIX.strip("/"), plugin_slug.strip("/")]

        route_part = route_prefix.strip("/")
        if route_part:
            parts.append(route_part)

        endpoint_part = endpoint_path.strip("/")
        if endpoint_part:
            parts.append(endpoint_part)

        return "/" + "/".join(parts)

    @staticmethod
    def _route_slug_candidates(plugin_slug: str) -> List[str]:
        candidates: List[str] = [plugin_slug]
        for alias in PLUGIN_ROUTE_ALIASES.get(plugin_slug, ()):
            normalized = str(alias or "").strip().strip("/")
            if normalized and normalized not in candidates:
                candidates.append(normalized)
        return candidates

    @staticmethod
    def _shared_plugins_root() -> Path:
        return Path(__file__).resolve().parents[2] / "plugins" / "shared"

    @staticmethod
    def _load_module(plugin_slug: str, module_path: Path) -> ModuleType:
        module_name = f"plugin_endpoints_{plugin_slug}_{abs(hash(str(module_path)))}"
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            raise ValueError(f"Unable to create module spec for {module_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _unmount_plugin_routes(self) -> int:
        if self._app is None:
            return 0
        current_routes = self._app.router.routes
        retained_routes = []
        removed = 0
        for route in current_routes:
            if getattr(route, "_braindrive_plugin_route", False):
                removed += 1
                continue
            retained_routes.append(route)
        if removed:
            self._app.router.routes = retained_routes
        return removed

    def _reset_openapi_cache(self) -> None:
        if self._app is not None:
            self._app.openapi_schema = None


_plugin_route_loader: Optional[PluginRouteLoader] = None


def get_plugin_loader() -> PluginRouteLoader:
    global _plugin_route_loader
    if _plugin_route_loader is None:
        _plugin_route_loader = PluginRouteLoader()
    return _plugin_route_loader
