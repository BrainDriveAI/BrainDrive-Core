"""
Page service with ownership helpers.

Centralizes "page belongs to current user" checks to avoid repetition across endpoints.
"""
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.core.auth_context import AuthContext
from app.models.page import Page


async def get_user_page(
    db: AsyncSession,
    page_id: UUID,
    auth: AuthContext,
    allow_published: bool = False
) -> Page:
    """
    Get a page and ensure the user has access to it.
    
    Users can access:
    - Their own pages (always)
    - Published pages from other users (if allow_published=True)
    
    Returns 404 (not 403) if page doesn't exist or user doesn't have access,
    to prevent resource enumeration attacks.
    
    Args:
        db: Database session
        page_id: ID of the page to retrieve
        auth: Authentication context with current user info
        allow_published: If True, allow access to published pages from other users
        
    Returns:
        Page object if found and user has access
        
    Raises:
        HTTPException: 404 if page not found or user doesn't have access
    """
    page = await Page.get_by_id(db, page_id)
    if not page:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Page not found"
        )
    
    # Format IDs for comparison (remove dashes)
    creator_id_str = str(page.creator_id).replace('-', '')
    auth_id_str = str(auth.user_id).replace('-', '')
    
    # User owns the page - always allow
    if creator_id_str == auth_id_str:
        return page
    
    # Page is published and we allow published pages - allow
    if allow_published and page.is_published:
        return page
    
    # Otherwise, deny access
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Page not found"
    )


def ensure_page_belongs_to_user(page: Page, auth: AuthContext) -> None:
    """
    Verify that a page belongs to the current user (strict ownership check).
    
    Args:
        page: Page object to check
        auth: Authentication context with current user info
        
    Raises:
        HTTPException: 404 if page doesn't belong to user
    """
    creator_id_str = str(page.creator_id).replace('-', '')
    auth_id_str = str(auth.user_id).replace('-', '')
    
    if creator_id_str != auth_id_str:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Page not found"
        )

