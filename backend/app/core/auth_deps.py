from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional

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


async def get_auth_context(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AuthContext:
    #Load user and build authentication context from request token.
    token = extract_access_token(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated"
        )
    
    # Decode token and extract user_id
    payload = decode_access_token(token)
    user_id = payload.get("sub")
    if not user_id:
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
    auth: AuthContext = Depends(get_auth_context)
) -> AuthContext:
    #Require authenticated admin user.
    if not auth.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin required"
        )
    return auth

