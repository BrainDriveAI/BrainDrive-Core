"""
Service Authentication for Internal Endpoints

Provides token-based authentication for service-to-service calls.
Services authenticate with static bearer tokens (not user JWTs).
"""
from fastapi import Depends, HTTPException, Request, status
from typing import Optional, Set
import logging

from app.core.config import settings
from app.core.service_context import ServiceContext


logger = logging.getLogger(__name__)


# Service definitions with their scopes
SERVICE_DEFINITIONS = {
    "plugin_runtime": {
        "scopes": {"execute_plugin", "read_plugin_state", "write_plugin_state"}
    },
    "job_worker": {
        "scopes": {"execute_job", "report_progress", "update_job_status"}
    },
    "plugin_lifecycle": {
        "scopes": {"install_plugin", "uninstall_plugin", "update_plugin"}
    },
}


def _extract_service_token(request: Request) -> Optional[str]:
    """
    Extract service bearer token from Authorization header.
    
    Args:
        request: FastAPI Request object
        
    Returns:
        Token string or None if not found
    """
    auth_header = request.headers.get("Authorization", "")
    
    if not auth_header.lower().startswith("bearer "):
        return None
    
    # Extract token after "Bearer "
    token = auth_header[7:].strip()
    return token if token else None


def _validate_service_token(token: str) -> Optional[str]:
    """
    Validate service token and return service name.
    
    Args:
        token: Bearer token to validate
        
    Returns:
        Service name if valid, None otherwise
    """
    # Check against configured service tokens
    # Note: Empty tokens are invalid (not configured)
    if settings.PLUGIN_RUNTIME_TOKEN and token == settings.PLUGIN_RUNTIME_TOKEN:
        return "plugin_runtime"
    elif settings.JOB_WORKER_TOKEN and token == settings.JOB_WORKER_TOKEN:
        return "job_worker"
    elif settings.PLUGIN_LIFECYCLE_TOKEN and token == settings.PLUGIN_LIFECYCLE_TOKEN:
        return "plugin_lifecycle"
    
    return None


async def get_service_context(request: Request) -> ServiceContext:
    """
    Extract and validate service authentication.
    
    Used as a dependency for internal endpoints that require service auth.
    
    Args:
        request: FastAPI Request object
        
    Returns:
        ServiceContext with service name and scopes
        
    Raises:
        HTTPException: 401 if token invalid, 403 if service not recognized
    """
    # Extract token
    token = _extract_service_token(request)
    
    if not token:
        logger.warning(
            "Service auth failed - no token",
            path=request.url.path,
            method=request.method
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Service authentication required. Provide Bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Validate token and get service name
    service_name = _validate_service_token(token)
    
    if not service_name:
        logger.warning(
            "Service auth failed - invalid token",
            path=request.url.path,
            method=request.method
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid service token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Get service definition
    service_def = SERVICE_DEFINITIONS.get(service_name)
    
    if not service_def:
        logger.error(
            "Service auth failed - unknown service",
            service_name=service_name,
            path=request.url.path
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Service '{service_name}' not recognized"
        )
    
    # Create service context
    context = ServiceContext(
        service_name=service_name,
        scopes=service_def["scopes"]
    )
    
    logger.info(
        "Service authenticated",
        service_name=service_name,
        scopes=list(context.scopes),
        path=request.url.path
    )
    
    return context


async def require_service(
    service_context: ServiceContext = Depends(get_service_context)
) -> ServiceContext:
    """
    Dependency that requires valid service authentication.
    
    Use this for internal endpoints that should only be called by services.
    
    Example:
        @router.post("/_internal/job/progress")
        async def report_progress(
            service: ServiceContext = Depends(require_service),
            ...
        ):
            # Only callable by authenticated services
    
    Args:
        service_context: Injected service context from get_service_context
        
    Returns:
        ServiceContext if authenticated
        
    Raises:
        HTTPException: 401/403 if authentication fails
    """
    return service_context


async def require_service_scope(
    required_scope: str,
    service_context: ServiceContext = Depends(get_service_context)
) -> ServiceContext:
    """
    Dependency that requires service with specific scope.
    
    Args:
        required_scope: Scope that service must have
        service_context: Injected service context
        
    Returns:
        ServiceContext if scope present
        
    Raises:
        HTTPException: 403 if scope not present
    """
    if not service_context.has_scope(required_scope):
        logger.warning(
            "Service missing required scope",
            service_name=service_context.service_name,
            required_scope=required_scope,
            has_scopes=list(service_context.scopes)
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Service '{service_context.service_name}' lacks required scope: {required_scope}"
        )
    
    return service_context


def create_scope_dependency(scope: str):
    """
    Factory to create a dependency that checks for specific scope.
    
    Example:
        require_job_execution = create_scope_dependency("execute_job")
        
        @router.post("/_internal/job/start")
        async def start_job(
            service: ServiceContext = Depends(require_job_execution)
        ):
    
    Args:
        scope: Required scope
        
    Returns:
        Dependency function
    """
    async def dependency(
        service_context: ServiceContext = Depends(get_service_context)
    ) -> ServiceContext:
        return await require_service_scope(scope, service_context)
    
    return dependency


# Commonly used scope dependencies
require_plugin_execution = create_scope_dependency("execute_plugin")
require_job_execution = create_scope_dependency("execute_job")
require_plugin_lifecycle = create_scope_dependency("install_plugin")

