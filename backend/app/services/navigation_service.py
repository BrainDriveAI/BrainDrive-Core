"""
Navigation service with ownership helpers.

Centralizes "navigation route belongs to current user" checks to avoid repetition across endpoints.
"""
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth_context import AuthContext
from app.models.navigation import NavigationRoute


async def get_user_navigation_route(
    db: AsyncSession,
    route_id: str,
    auth: AuthContext
) -> NavigationRoute:
    """
    Get a navigation route and ensure it belongs to the current user.
    
    Returns 404 (not 403) if route doesn't exist or doesn't belong to user,
    to prevent resource enumeration attacks.
    
    Args:
        db: Database session
        route_id: ID of the navigation route to retrieve
        auth: Authentication context with current user info
        
    Returns:
        NavigationRoute object if found and belongs to user
        
    Raises:
        HTTPException: 404 if route not found or doesn't belong to user
    """
    from sqlalchemy import select
    
    stmt = select(NavigationRoute).where(NavigationRoute.id == route_id)
    result = await db.execute(stmt)
    route = result.scalar_one_or_none()
    
    if not route:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Navigation route not found"
        )
    
    # Format IDs for comparison (remove dashes)
    route_creator_id = str(route.creator_id).replace('-', '')
    current_user_id = str(auth.user_id).replace('-', '')
    
    if route_creator_id != current_user_id:
        # Return 404, not 403, to prevent resource enumeration
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Navigation route not found"
        )
    
    return route


def ensure_route_belongs_to_user(route: NavigationRoute, auth: AuthContext) -> None:
    """
    Verify that a navigation route belongs to the current user.
    
    Args:
        route: NavigationRoute object to check
        auth: Authentication context with current user info
        
    Raises:
        HTTPException: 404 if route doesn't belong to user
    """
    route_creator_id = str(route.creator_id).replace('-', '')
    current_user_id = str(auth.user_id).replace('-', '')
    
    if route_creator_id != current_user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Navigation route not found"
        )

