"""
Settings service with ownership helpers.

Centralizes "setting instance belongs to current user" checks to avoid repetition across endpoints.
"""
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from typing import Optional, Dict, Any

from app.core.auth_context import AuthContext


async def get_user_setting_instance(
    db: AsyncSession,
    instance_id: str,
    auth: AuthContext
) -> Dict[str, Any]:
    """
    Get a setting instance and ensure it belongs to the current user.
    
    Returns 404 (not 403) if instance doesn't exist or doesn't belong to user,
    to prevent resource enumeration attacks.
    
    Note: This uses raw SQL queries as settings endpoints currently use text() queries.
    
    Args:
        db: Database session
        instance_id: ID of the setting instance to retrieve
        auth: Authentication context with current user info
        
    Returns:
        Setting instance dictionary if found and belongs to user
        
    Raises:
        HTTPException: 404 if instance not found or doesn't belong to user
    """
    query = text("""
        SELECT * FROM settings_instances 
        WHERE id = :instance_id
    """)
    result = await db.execute(query, {"instance_id": instance_id})
    instance = result.fetchone()
    
    if not instance:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Setting instance not found"
        )
    
    # Convert row to dict
    instance_dict = dict(instance._mapping)
    
    # Check ownership if instance has user_id
    if instance_dict.get("user_id"):
        instance_user_id = str(instance_dict["user_id"])
        current_user_id = str(auth.user_id)
        
        if instance_user_id != current_user_id:
            # Return 404, not 403, to prevent resource enumeration
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Setting instance not found"
            )
    
    return instance_dict


def ensure_setting_instance_belongs_to_user(
    instance: Dict[str, Any],
    auth: AuthContext
) -> None:
    """
    Verify that a setting instance belongs to the current user.
    
    Args:
        instance: Setting instance dictionary to check
        auth: Authentication context with current user info
        
    Raises:
        HTTPException: 404 if instance doesn't belong to user
    """
    if instance.get("user_id"):
        instance_user_id = str(instance["user_id"])
        current_user_id = str(auth.user_id)
        
        if instance_user_id != current_user_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Setting instance not found"
            )

