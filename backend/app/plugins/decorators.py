"""
Plugin endpoint decorators for BrainDrive backend plugins.

This module provides the @plugin_endpoint decorator for marking functions
as plugin REST API endpoints, along with supporting classes and utilities.

Usage:
    from app.plugins.decorators import plugin_endpoint, PluginRequest

    @plugin_endpoint('/projects', methods=['GET'])
    async def list_projects(request: PluginRequest):
        user_id = request.user_id
        return {"projects": [...]}

    @plugin_endpoint('/admin/config', methods=['POST'], admin_only=True)
    async def update_config(request: PluginRequest):
        # Only admin users can access
        return {"status": "updated"}
"""

import re
from dataclasses import dataclass
from functools import wraps
from typing import List, Optional, Set, Callable, Any

from fastapi import Request

import structlog

logger = structlog.get_logger()


# Regex pattern for valid plugin slugs
SLUG_PATTERN = re.compile(r'^[a-z0-9]+(-[a-z0-9]+)*$')

# Reserved path prefixes that plugins cannot use
RESERVED_PREFIXES = ('/api/', '/admin/', '/_')


class PathValidationError(ValueError):
    """Raised when an endpoint path fails validation."""
    pass


def validate_endpoint_path(path: str) -> str:
    """
    Validate and normalize a plugin endpoint path.

    Ensures the path:
    - Starts with '/'
    - Does not use reserved prefixes (/api/, /admin/)
    - Does not contain path traversal sequences (..)
    - Does not contain double slashes (//)
    - Is properly normalized (trailing slash removed)

    Args:
        path: The endpoint path to validate

    Returns:
        The normalized path

    Raises:
        PathValidationError: If the path is invalid
    """
    if not isinstance(path, str):
        raise PathValidationError(f"Path must be a string, got {type(path).__name__}")

    if not path:
        raise PathValidationError("Path cannot be empty")

    # Must start with /
    if not path.startswith('/'):
        raise PathValidationError(f"Path must start with '/': {path}")

    # Cannot use reserved prefixes
    for prefix in RESERVED_PREFIXES:
        if path.startswith(prefix) or path.lower().startswith(prefix):
            raise PathValidationError(f"Path cannot use reserved prefix '{prefix}': {path}")

    # Cannot contain path traversal
    if '..' in path:
        raise PathValidationError(f"Path cannot contain '..': {path}")

    # Cannot contain double slashes (except potentially at start, which we check above)
    if '//' in path:
        raise PathValidationError(f"Path cannot contain '//': {path}")

    # Normalize: remove trailing slash (but keep single /)
    normalized = path.rstrip('/') if path != '/' else '/'

    return normalized


def validate_slug(slug: str) -> str:
    """
    Validate a plugin slug.

    Slugs must be lowercase alphanumeric with hyphens only.
    Pattern: ^[a-z0-9]+(-[a-z0-9]+)*$

    Args:
        slug: The plugin slug to validate

    Returns:
        The validated slug

    Raises:
        ValueError: If the slug is invalid
    """
    if not isinstance(slug, str):
        raise ValueError(f"Slug must be a string, got {type(slug).__name__}")

    if not slug:
        raise ValueError("Slug cannot be empty")

    if not SLUG_PATTERN.match(slug):
        raise ValueError(
            f"Invalid slug '{slug}'. Slugs must be lowercase alphanumeric "
            "with hyphens only (e.g., 'my-plugin', 'braindrive-library')"
        )

    return slug


@dataclass
class PluginRequest:
    """
    Wrapper around FastAPI Request providing convenient access to plugin context.

    This class wraps a FastAPI Request and provides easy access to:
    - User authentication context (user_id, username, is_admin, roles)
    - The underlying FastAPI Request for advanced use cases

    Attributes:
        request: The underlying FastAPI Request object
        user_id: The authenticated user's ID
        username: The authenticated user's username
        is_admin: Whether the user has admin privileges
        roles: Set of role names assigned to the user
        tenant_id: Optional tenant ID for multi-tenant deployments
    """
    request: Request
    user_id: str
    username: str
    is_admin: bool
    roles: Set[str]
    tenant_id: Optional[str] = None

    @classmethod
    def from_auth_context(cls, request: Request, auth_context: Any) -> 'PluginRequest':
        """
        Create a PluginRequest from a FastAPI Request and AuthContext.

        Args:
            request: The FastAPI Request object
            auth_context: The AuthContext from authentication

        Returns:
            A new PluginRequest instance
        """
        return cls(
            request=request,
            user_id=auth_context.user_id,
            username=auth_context.username,
            is_admin=auth_context.is_admin,
            roles=auth_context.roles,
            tenant_id=auth_context.tenant_id,
        )

    @property
    def headers(self):
        """Access request headers."""
        return self.request.headers

    @property
    def query_params(self):
        """Access query parameters."""
        return self.request.query_params

    @property
    def path_params(self):
        """Access path parameters."""
        return self.request.path_params

    @property
    def cookies(self):
        """Access cookies."""
        return self.request.cookies

    @property
    def client(self):
        """Access client information."""
        return self.request.client

    async def json(self):
        """Parse request body as JSON."""
        return await self.request.json()

    async def body(self):
        """Get raw request body."""
        return await self.request.body()

    async def form(self):
        """Parse request body as form data."""
        return await self.request.form()


@dataclass
class EndpointMetadata:
    """
    Metadata stored on decorated plugin endpoint functions.

    This dataclass holds all the configuration for a plugin endpoint,
    used by the route loader to properly mount the endpoint.
    """
    path: str
    methods: List[str]
    admin_only: bool
    summary: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[List[str]] = None
    response_model: Optional[Any] = None
    status_code: int = 200


def plugin_endpoint(
    path: str,
    methods: List[str] = None,
    admin_only: bool = False,
    summary: Optional[str] = None,
    description: Optional[str] = None,
    tags: Optional[List[str]] = None,
    response_model: Optional[Any] = None,
    status_code: int = 200,
) -> Callable:
    """
    Decorator to mark a function as a plugin endpoint.

    This decorator validates the endpoint path and stores metadata on the
    function for later use by the plugin route loader.

    Args:
        path: The endpoint path (e.g., '/projects', '/projects/{id}')
            Must start with '/' and cannot use reserved prefixes.
        methods: HTTP methods to support (default: ['GET'])
        admin_only: If True, only admin users can access (default: False)
        summary: Short summary for OpenAPI docs
        description: Detailed description for OpenAPI docs
        tags: Tags for OpenAPI docs grouping
        response_model: Pydantic model for response validation
        status_code: Default status code (default: 200)

    Returns:
        Decorated function with _plugin_endpoint_metadata attribute

    Raises:
        PathValidationError: If the path is invalid

    Example:
        @plugin_endpoint('/projects', methods=['GET'])
        async def list_projects(request: PluginRequest):
            return {"projects": [...]}

        @plugin_endpoint('/projects', methods=['POST'], admin_only=True)
        async def create_project(request: PluginRequest):
            data = await request.json()
            return {"id": "new-project"}
    """
    # Default to GET if no methods specified
    if methods is None:
        methods = ['GET']

    # Normalize methods to uppercase
    methods = [m.upper() for m in methods]

    # Validate methods
    valid_methods = {'GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS'}
    invalid_methods = set(methods) - valid_methods
    if invalid_methods:
        raise ValueError(f"Invalid HTTP methods: {invalid_methods}")

    # Validate path at decoration time
    validated_path = validate_endpoint_path(path)

    def decorator(func: Callable) -> Callable:
        # Create metadata
        metadata = EndpointMetadata(
            path=validated_path,
            methods=methods,
            admin_only=admin_only,
            summary=summary or func.__doc__,
            description=description,
            tags=tags,
            response_model=response_model,
            status_code=status_code,
        )

        # Store metadata on function
        func._plugin_endpoint = True
        func._plugin_endpoint_metadata = metadata

        # Also store individual attributes for backward compatibility
        func._path = validated_path
        func._methods = methods
        func._admin_only = admin_only

        @wraps(func)
        async def wrapper(*args, **kwargs):
            return await func(*args, **kwargs)

        # Copy metadata to wrapper
        wrapper._plugin_endpoint = True
        wrapper._plugin_endpoint_metadata = metadata
        wrapper._path = validated_path
        wrapper._methods = methods
        wrapper._admin_only = admin_only

        return wrapper

    return decorator


def get_plugin_endpoints(module) -> List[Callable]:
    """
    Discover all plugin endpoints in a module.

    Scans a module for functions decorated with @plugin_endpoint
    and returns them as a list.

    Args:
        module: The Python module to scan

    Returns:
        List of decorated endpoint functions
    """
    endpoints = []

    for name in dir(module):
        obj = getattr(module, name)
        if callable(obj) and getattr(obj, '_plugin_endpoint', False):
            endpoints.append(obj)
            logger.debug(f"Discovered plugin endpoint: {name}", path=obj._path)

    return endpoints
