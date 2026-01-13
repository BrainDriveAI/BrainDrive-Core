"""
Tag service with ownership helpers.

Centralizes "tag belongs to current user" checks to avoid repetition across endpoints.
"""
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth_context import AuthContext
from app.models.tag import Tag
from app.models.conversation import Conversation


async def get_user_tag(
    db: AsyncSession,
    tag_id: str,
    auth: AuthContext
) -> Tag:
    """
    Get a tag and ensure it belongs to the current user.
    
    Returns 404 (not 403) if tag doesn't exist or doesn't belong to user,
    to prevent resource enumeration attacks.
    
    Args:
        db: Database session
        tag_id: ID of the tag to retrieve
        auth: Authentication context with current user info
        
    Returns:
        Tag object if found and belongs to user
        
    Raises:
        HTTPException: 404 if tag not found or doesn't belong to user
    """
    tag = await Tag.get_by_id(db, tag_id)
    if not tag:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tag not found"
        )
    
    if str(tag.user_id) != str(auth.user_id):
        # Return 404, not 403, to prevent resource enumeration
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tag not found"
        )
    
    return tag


def ensure_tag_belongs_to_user(tag: Tag, auth: AuthContext) -> None:
    """
    Verify that a tag belongs to the current user.
    
    Args:
        tag: Tag object to check
        auth: Authentication context with current user info
        
    Raises:
        HTTPException: 404 if tag doesn't belong to user
    """
    if str(tag.user_id) != str(auth.user_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tag not found"
        )


async def ensure_conversation_belongs_to_user(
    db: AsyncSession,
    conversation_id: str,
    auth: AuthContext
) -> Conversation:
    """
    Verify that a conversation belongs to the current user.
    
    Used in tag operations to ensure user can only tag their own conversations.
    
    Args:
        db: Database session
        conversation_id: ID of the conversation to check
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
    
    if str(conversation.user_id) != str(auth.user_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found"
        )
    
    return conversation

