"""
Auth-aware rate limiting dependencies for FastAPI endpoints.

Provides reusable dependencies that apply rate limiting based on:
- user_id (for authenticated endpoints)
- IP address (for unauthenticated endpoints like login)
"""
from fastapi import Request, Depends
from typing import Optional, Callable, Iterable
import functools
import ipaddress

from app.core.rate_limit import rate_limiter
from app.core.auth_context import AuthContext
from app.core.auth_deps import optional_user
from app.core.config import settings


def _parse_forwarded_allow_ips(value: str) -> Iterable[str]:
    if not value:
        return []
    if value.strip() == "*":
        return ["*"]
    return [part.strip() for part in value.split(",") if part.strip()]


def _is_trusted_proxy(client_host: Optional[str]) -> bool:
    if not client_host:
        return False

    allow_ips = _parse_forwarded_allow_ips(settings.FORWARDED_ALLOW_IPS)
    if not allow_ips:
        return False
    if "*" in allow_ips:
        return True

    try:
        client_ip = ipaddress.ip_address(client_host)
    except ValueError:
        return False

    for entry in allow_ips:
        try:
            if "/" in entry:
                if client_ip in ipaddress.ip_network(entry, strict=False):
                    return True
            elif client_ip == ipaddress.ip_address(entry):
                return True
        except ValueError:
            continue
    return False


def _get_client_ip(request: Request) -> str:
    """
    Extract client IP from request, handling proxies.
    
    Checks X-Forwarded-For header first (for proxied requests),
    then falls back to direct client host.
    
    Args:
        request: FastAPI Request object
        
    Returns:
        Client IP address as string
    """
    if _is_trusted_proxy(request.client.host if request.client else None):
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()

        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip.strip()
    
    # Fall back to direct client host
    if request.client:
        return request.client.host
    
    # Last resort fallback
    return "unknown"


def rate_limit_ip(limit: int, window_seconds: int) -> Callable:
    """
    Rate limit by IP address (for unauthenticated endpoints).
    
    Use this for public endpoints like login, register, refresh.
    
    Args:
        limit: Maximum requests allowed
        window_seconds: Time window in seconds
        
    Returns:
        FastAPI dependency function
        
    Example:
        @router.post("/login")
        async def login(
            _: None = Depends(rate_limit_ip(limit=5, window_seconds=300)),
            ...
        ):
    """
    async def dependency(request: Request) -> None:
        client_ip = _get_client_ip(request)
        key = f"ip:{client_ip}"
        rate_limiter.check(key, limit, window_seconds)
        return None
    
    return dependency


def rate_limit_user(limit: int, window_seconds: int) -> Callable:
    """
    Rate limit by user_id (for authenticated endpoints).
    
    Use this for endpoints that require authentication.
    
    Args:
        limit: Maximum requests allowed
        window_seconds: Time window in seconds
        
    Returns:
        FastAPI dependency function
        
    Example:
        @router.post("/generate")
        async def generate(
            _: None = Depends(rate_limit_user(limit=50, window_seconds=60)),
            auth: AuthContext = Depends(require_user)
        ):
    """
    async def dependency(
        request: Request,
        auth: Optional[AuthContext] = Depends(optional_user)
    ) -> None:
        if auth:
            # Authenticated - use user_id
            key = f"user:{auth.user_id}"
        else:
            # Not authenticated - fall back to IP
            # This handles edge cases where require_user is added after rate_limit_user
            client_ip = _get_client_ip(request)
            key = f"ip:{client_ip}"
        
        rate_limiter.check(key, limit, window_seconds)
        return None
    
    return dependency


def rate_limit_auto(limit: int, window_seconds: int) -> Callable:
    """
    Smart rate limiting - uses user_id if authenticated, IP if not.
    
    Use this for endpoints that support both authenticated and unauthenticated access.
    
    Args:
        limit: Maximum requests allowed
        window_seconds: Time window in seconds
        
    Returns:
        FastAPI dependency function
        
    Example:
        @router.get("/public-or-private")
        async def endpoint(
            _: None = Depends(rate_limit_auto(limit=100, window_seconds=60)),
            auth: Optional[AuthContext] = Depends(optional_user)
        ):
    """
    async def dependency(
        request: Request,
        auth: Optional[AuthContext] = Depends(optional_user)
    ) -> None:
        if auth:
            key = f"user:{auth.user_id}"
        else:
            client_ip = _get_client_ip(request)
            key = f"ip:{client_ip}"
        
        rate_limiter.check(key, limit, window_seconds)
        return None
    
    return dependency
