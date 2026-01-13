from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional
import asyncio

from app.core.database import get_db
from app.core.auth_context import AuthContext
from app.core.security import decode_access_token
from app.models.user import User
from app.models.tenant_models import UserRole, TenantUser


def extract_access_token(request: Request) -> Optional[str]:
    #Extract bearer token from Authorization header.
    auth_header = request.headers.get("Authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header.split(" ", 1)[1].strip()
    return None


def _log_auth_failure_background(request: Request, reason: str, event_type: str, user_id: Optional[str] = None):
    """Schedule audit log write in background to not block request."""
    async def _write():
        try:
            from app.core.audit import audit_logger, AuditEventType
            await audit_logger.log_auth_failure(
                request=request,
                reason=reason,
                event_type=AuditEventType(event_type),
                user_id=user_id,
            )
        except Exception:
            pass  # Don't fail request if audit logging fails
    
    asyncio.create_task(_write())


async def get_auth_context(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AuthContext:
    #Load user and build authentication context from request token.
    token = extract_access_token(request)
    if not token:
        _log_auth_failure_background(request, "No token provided", "auth.unauthorized")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated"
        )
    
    # Decode token and extract user_id
    try:
        payload = decode_access_token(token)
    except Exception as e:
        _log_auth_failure_background(request, f"Token decode failed: {str(e)}", "auth.token_invalid")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token"
        )
    
    user_id = payload.get("sub")
    if not user_id:
        _log_auth_failure_background(request, "Token missing user ID", "auth.token_invalid")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token"
        )
    
    # Load user from database
    user_id_str = user_id.replace('-', '')
    stmt = select(User).where(User.id == user_id_str)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    
    if not user:
        _log_auth_failure_background(request, "User not found", "auth.user_not_found", user_id_str)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found"
        )
    
    # Check if user has admin role
    admin_stmt = (
        select(UserRole)
        .join(TenantUser)
        .where(
            TenantUser.user_id == user_id_str,
            UserRole.role_name == "admin"
        )
    )
    admin_result = await db.execute(admin_stmt)
    admin_role = admin_result.scalar_one_or_none()
    is_admin = admin_role is not None
    
    # Get all roles for user
    roles_stmt = (
        select(UserRole)
        .join(TenantUser)
        .where(TenantUser.user_id == user_id_str)
    )
    roles_result = await db.execute(roles_stmt)
    user_roles = roles_result.scalars().all()
    role_names = {role.role_name for role in user_roles}
    
    # Build and return AuthContext
    return AuthContext(
        user_id=user.id,
        username=user.username,
        is_admin=is_admin,
        roles=role_names,
        tenant_id=None  # Single-tenant mode for now
    )


async def optional_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Optional[AuthContext]:
    #Optional authentication - returns AuthContext or None if not authenticated.
    try:
        return await get_auth_context(request, db)
    except HTTPException:
        return None


async def require_user(
    auth: AuthContext = Depends(get_auth_context)
) -> AuthContext:
    #Require authenticated user.
    return auth


async def require_admin(
    request: Request,
    auth: AuthContext = Depends(get_auth_context)
) -> AuthContext:
    #Require authenticated admin user.
    if not auth.is_admin:
        # Log authorization failure
        async def _log_forbidden():
            try:
                from app.core.audit import audit_logger
                await audit_logger.log_authorization_failure(
                    request=request,
                    user_id=auth.user_id,
                    reason="Admin privileges required",
                )
            except Exception:
                pass
        asyncio.create_task(_log_forbidden())
        
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin required"
        )
    return auth

