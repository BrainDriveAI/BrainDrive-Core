"""
Plugin State service with ownership helpers.

Centralizes "plugin state belongs to current user" checks to avoid repetition across endpoints.
"""
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from typing import Optional

from app.core.auth_context import AuthContext
from app.models.plugin_state import PluginState


async def get_user_plugin_state(
    db: AsyncSession,
    state_id: str,
    auth: AuthContext
) -> PluginState:
    """
    Get a plugin state and ensure it belongs to the current user.
    
    Returns 404 (not 403) if state doesn't exist or doesn't belong to user,
    to prevent resource enumeration attacks.
    
    Args:
        db: Database session
        state_id: ID of the plugin state to retrieve
        auth: Authentication context with current user info
        
    Returns:
        PluginState object if found and belongs to user
        
    Raises:
        HTTPException: 404 if state not found or doesn't belong to user
    """
    stmt = select(PluginState).where(
        and_(
            PluginState.id == state_id,
            PluginState.user_id == auth.user_id
        )
    )
    result = await db.execute(stmt)
    state = result.scalar_one_or_none()
    
    if not state:
        # Return 404, not 403, to prevent resource enumeration
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plugin state not found"
        )
    
    return state


def ensure_plugin_state_belongs_to_user(state: PluginState, auth: AuthContext) -> None:
    """
    Verify that a plugin state belongs to the current user.
    
    Args:
        state: PluginState object to check
        auth: Authentication context with current user info
        
    Raises:
        HTTPException: 404 if state doesn't belong to user
    """
    if state.user_id != auth.user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plugin state not found"
        )

