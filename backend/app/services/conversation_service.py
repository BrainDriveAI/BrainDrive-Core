"""
Conversation service with ownership helpers.

Centralizes "conversation belongs to current user" checks to avoid repetition across endpoints.
"""
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from app.core.auth_context import AuthContext
from app.models.conversation import Conversation


async def get_user_conversation(
    db: AsyncSession,
    conversation_id: str,
    auth: AuthContext
) -> Conversation:
    """
    Get a conversation and ensure it belongs to the current user.
    
    Returns 404 (not 403) if conversation doesn't exist or doesn't belong to user,
    to prevent resource enumeration attacks.
    
    Args:
        db: Database session
        conversation_id: ID of the conversation to retrieve
        auth: Authentication context with current user info
        
    Returns:
        Conversation object if found and belongs to user
        
    Raises:
        HTTPException: 404 if conversation not found or doesn't belong to user
    """
    conversation = await Conversation.get_by_id(db, conversation_id)
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found"
        )
    
    # Format IDs for comparison (remove dashes)
    conversation_user_id = str(conversation.user_id).replace('-', '')
    current_user_id = str(auth.user_id).replace('-', '')
    
    if conversation_user_id != current_user_id:
        # Return 404, not 403, to prevent resource enumeration
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found"
        )
    
    return conversation


def ensure_user_id_matches(
    user_id_param: str,
    auth: AuthContext
) -> str:
    """
    Ensure the user_id parameter matches the authenticated user.
    
    Returns the formatted user_id if valid.
    
    Args:
        user_id_param: User ID from request parameter
        auth: Authentication context
        
    Returns:
        Formatted user_id (without dashes)
        
    Raises:
        HTTPException: 403 if user_id doesn't match authenticated user
    """
    formatted_user_id = user_id_param.replace('-', '')
    current_user_id = str(auth.user_id).replace('-', '')
    
    if formatted_user_id != current_user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access these conversations"
        )
    
    return formatted_user_id

