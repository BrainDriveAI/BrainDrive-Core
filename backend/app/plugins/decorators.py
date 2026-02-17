"""
Plugin endpoint decorator helpers.

Plugins define backend endpoints in their own ``endpoints.py`` file by
decorating callables with ``@plugin_endpoint``.
"""

from __future__ import annotations

from dataclasses import dataclass
import inspect
from typing import Any, Callable, Iterable, List, Optional, Tuple

from fastapi import Request

from app.core.auth_context import AuthContext

_PLUGIN_ENDPOINT_ATTR = "__plugin_endpoint__"
_RESERVED_PREFIXES = ("/api/", "/admin/", "/_")


@dataclass(frozen=True)
class PluginEndpointDefinition:
    """Metadata captured by ``@plugin_endpoint``."""

    path: str
    methods: Tuple[str, ...]
    admin_only: bool
    endpoint: Callable[..., Any]


@dataclass
class PluginRequest:
    """
    Request context passed to plugin-owned endpoints.

    Plugins receive this object as the single endpoint argument.
    """

    request: Request
    auth: AuthContext
    plugin_slug: str
    route_prefix: str

    @property
    def user_id(self) -> str:
        return self.auth.user_id

    @property
    def username(self) -> str:
        return self.auth.username

    @property
    def is_admin(self) -> bool:
        return self.auth.is_admin

    @property
    def roles(self):
        return self.auth.roles

    @property
    def tenant_id(self) -> Optional[str]:
        return self.auth.tenant_id

    async def json(self) -> Any:
        return await self.request.json()

    async def body(self) -> bytes:
        return await self.request.body()

    async def form(self):
        return await self.request.form()


def _normalize_endpoint_path(path: str) -> str:
    if not isinstance(path, str) or not path:
        raise ValueError("Plugin endpoint path must be a non-empty string.")
    if not path.startswith("/"):
        raise ValueError("Plugin endpoint path must start with '/'.")
    if ".." in path:
        raise ValueError("Plugin endpoint path cannot contain '..'.")
    if "//" in path:
        raise ValueError("Plugin endpoint path cannot contain '//'.")

    lowered = path.lower()
    if lowered in ("/api", "/admin"):
        raise ValueError("Plugin endpoint path cannot use reserved /api or /admin prefixes.")
    if lowered.startswith(_RESERVED_PREFIXES):
        raise ValueError("Plugin endpoint path cannot use reserved prefixes: /api/, /admin/, /_.")

    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return path


def _normalize_methods(methods: Optional[Iterable[str]]) -> Tuple[str, ...]:
    raw_methods = tuple(methods) if methods else ("GET",)
    normalized: List[str] = []
    for method in raw_methods:
        method_name = str(method or "").strip().upper()
        if not method_name:
            continue
        if method_name not in normalized:
            normalized.append(method_name)
    if not normalized:
        raise ValueError("Plugin endpoint must declare at least one HTTP method.")
    return tuple(normalized)


def plugin_endpoint(path: str, methods: Optional[Iterable[str]] = None, admin_only: bool = False):
    """
    Mark a function as a plugin-owned API endpoint.

    Example:
    ``@plugin_endpoint("/health", methods=["GET"])``
    """

    normalized_path = _normalize_endpoint_path(path)
    normalized_methods = _normalize_methods(methods)

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        endpoint_def = PluginEndpointDefinition(
            path=normalized_path,
            methods=normalized_methods,
            admin_only=bool(admin_only),
            endpoint=func,
        )
        setattr(func, _PLUGIN_ENDPOINT_ATTR, endpoint_def)
        return func

    return decorator


def get_plugin_endpoints(module: Any) -> List[PluginEndpointDefinition]:
    """
    Discover all endpoint definitions inside a plugin endpoints module.
    """

    endpoints: List[PluginEndpointDefinition] = []
    for _, member in inspect.getmembers(module):
        endpoint_def = getattr(member, _PLUGIN_ENDPOINT_ATTR, None)
        if isinstance(endpoint_def, PluginEndpointDefinition):
            endpoints.append(endpoint_def)
    endpoints.sort(key=lambda item: (item.path, item.endpoint.__name__))
    return endpoints

