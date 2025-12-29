"""
API endpoints for tags.
"""
from fastapi import APIRouter, HTTPException, Depends, Query, Path
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Optional
from uuid import UUID

from app.core.database import get_db
from app.core.auth_deps import require_user
from app.core.auth_context import AuthContext
from app.models.tag import Tag, ConversationTag
from app.models.conversation import Conversation
from app.schemas.tag_schemas import (
    Tag as TagSchema,
    TagCreate,
    TagUpdate,
    ConversationTagCreate,
    ConversationWithTags
)

router = APIRouter()


@router.get("/users/{user_id}/tags", response_model=List[TagSchema])
async def get_user_tags(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user)
):
    """Get all tags for a specific user."""
    # Ensure the current user can only access their own tags
    if str(auth.user_id) != str(user_id):
        raise HTTPException(status_code=403, detail="Not authorized to access these tags")
    
    tags = await Tag.get_by_user_id(db, user_id)
    return tags


@router.post("/tags", response_model=TagSchema)
async def create_tag(
    tag: TagCreate,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user)
):
    """Create a new tag."""
    # Ensure the current user can only create tags for themselves
    if str(auth.user_id) != str(tag.user_id):
        raise HTTPException(status_code=403, detail="Not authorized to create tags for this user")
    
    db_tag = Tag(
        user_id=tag.user_id,
        name=tag.name,
        color=tag.color
    )
    db.add(db_tag)
    await db.commit()
    await db.refresh(db_tag)
    return db_tag


@router.put("/tags/{tag_id}", response_model=TagSchema)
async def update_tag(
    tag_id: str,
    tag_update: TagUpdate,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user)
):
    """Update a tag."""
    db_tag = await Tag.get_by_id(db, tag_id)
    if not db_tag:
        raise HTTPException(status_code=404, detail="Tag not found")
    
    # Ensure the current user can only update their own tags
    if str(db_tag.user_id) != str(auth.user_id):
        raise HTTPException(status_code=403, detail="Not authorized to update this tag")
    
    # Update tag fields
    if tag_update.name is not None:
        db_tag.name = tag_update.name
    if tag_update.color is not None:
        db_tag.color = tag_update.color
    
    await db.commit()
    await db.refresh(db_tag)
    return db_tag


@router.delete("/tags/{tag_id}", status_code=204)
async def delete_tag(
    tag_id: str,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user)
):
    """Delete a tag."""
    db_tag = await Tag.get_by_id(db, tag_id)
    if not db_tag:
        raise HTTPException(status_code=404, detail="Tag not found")
    
    # Ensure the current user can only delete their own tags
    if str(db_tag.user_id) != str(auth.user_id):
        raise HTTPException(status_code=403, detail="Not authorized to delete this tag")
    
    await db.delete(db_tag)
    await db.commit()
    return None


@router.post("/conversations/{conversation_id}/tags", response_model=ConversationWithTags)
async def add_tag_to_conversation(
    conversation_id: str,
    tag_data: ConversationTagCreate,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user)
):
    """Add a tag to a conversation."""
    # Get the conversation
    conversation = await Conversation.get_by_id(db, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    # Ensure the current user owns the conversation
    if str(conversation.user_id) != str(auth.user_id):
        raise HTTPException(status_code=403, detail="Not authorized to modify this conversation")
    
    # Get the tag
    tag = await Tag.get_by_id(db, tag_data.tag_id)
    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")
    
    # Ensure the tag belongs to the current user
    if str(tag.user_id) != str(auth.user_id):
        raise HTTPException(status_code=403, detail="Not authorized to use this tag")
    
    # Add the tag to the conversation
    await conversation.add_tag(db, tag_data.tag_id)
    
    # Get all tags for the conversation
    tags = await conversation.get_tags(db)
    
    # Return the conversation with tags
    return {
        **conversation.__dict__,
        "tags": tags
    }


@router.delete("/conversations/{conversation_id}/tags/{tag_id}", response_model=ConversationWithTags)
async def remove_tag_from_conversation(
    conversation_id: str,
    tag_id: str,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user)
):
    """Remove a tag from a conversation."""
    # Get the conversation
    conversation = await Conversation.get_by_id(db, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    # Ensure the current user owns the conversation
    if str(conversation.user_id) != str(auth.user_id):
        raise HTTPException(status_code=403, detail="Not authorized to modify this conversation")
    
    # Remove the tag from the conversation
    await conversation.remove_tag(db, tag_id)
    
    # Get all tags for the conversation
    tags = await conversation.get_tags(db)
    
    # Return the conversation with tags
    return {
        **conversation.__dict__,
        "tags": tags
    }


@router.get("/tags/{tag_id}/conversations", response_model=List[ConversationWithTags])
async def get_conversations_by_tag(
    tag_id: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user)
):
    """Get all conversations with a specific tag."""
    # Get the tag
    tag = await Tag.get_by_id(db, tag_id)
    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")
    
    # Ensure the current user owns the tag
    if str(tag.user_id) != str(auth.user_id):
        raise HTTPException(status_code=403, detail="Not authorized to access this tag")
    
    # Get conversations with this tag
    query = select(Conversation).join(
        ConversationTag, 
        ConversationTag.conversation_id == Conversation.id
    ).where(
        ConversationTag.tag_id == tag_id,
        Conversation.user_id == auth.user_id
    ).offset(skip).limit(limit)
    
    result = await db.execute(query)
    conversations = result.scalars().all()
    
    # Get tags for each conversation
    conversation_with_tags = []
    for conversation in conversations:
        tags = await conversation.get_tags(db)
        conversation_with_tags.append({
            **conversation.__dict__,
            "tags": tags
        })
    
    return conversation_with_tags
