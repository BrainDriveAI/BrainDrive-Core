"""
API endpoints for personas.
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional
import math

from app.core.database import get_db
from app.core.auth_deps import require_user
from app.core.auth_context import AuthContext
from app.services.persona_service import PersonaService
from app.schemas.persona import (
    PersonaResponse,
    PersonaCreate,
    PersonaUpdate,
    PersonaListResponse
)

router = APIRouter()


@router.get("/personas", response_model=PersonaListResponse)
async def get_personas(
    skip: int = Query(0, ge=0, description="Number of personas to skip"),
    limit: int = Query(20, ge=1, le=100, description="Number of personas to return"),
    search: Optional[str] = Query(None, description="Search term for name, description, or system prompt"),
    tags: Optional[str] = Query(None, description="Comma-separated list of tags to filter by"),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user)
):
    """Get paginated list of user's personas with optional filtering."""
    try:
        # Parse tags if provided
        tag_list = None
        if tags:
            tag_list = [tag.strip() for tag in tags.split(",") if tag.strip()]
        
        # Get personas from service
        personas, total_count = await PersonaService.get_user_personas(
            db=db,
            user_id=str(auth.user_id),
            skip=skip,
            limit=limit,
            search=search,
            tags=tag_list,
            is_active=is_active
        )
        
        # Parse personas for response
        persona_responses = []
        for persona in personas:
            persona_dict = PersonaService.parse_persona_response(persona)
            persona_responses.append(PersonaResponse(**persona_dict))
        
        # Calculate pagination info
        total_pages = math.ceil(total_count / limit) if total_count > 0 else 0
        current_page = (skip // limit) + 1
        
        return PersonaListResponse(
            personas=persona_responses,
            total_items=total_count,
            page=current_page,
            page_size=limit,
            total_pages=total_pages
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error retrieving personas: {str(e)}")


@router.post("/personas", response_model=PersonaResponse)
async def create_persona(
    persona: PersonaCreate,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user)
):
    """Create a new persona."""
    try:
        db_persona = await PersonaService.create_persona(
            db=db,
            persona_data=persona,
            user_id=str(auth.user_id)
        )
        
        # Parse persona for response
        persona_dict = PersonaService.parse_persona_response(db_persona)
        return PersonaResponse(**persona_dict)
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating persona: {str(e)}")


@router.get("/personas/{persona_id}", response_model=PersonaResponse)
async def get_persona(
    persona_id: str,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user)
):
    """Get a specific persona by ID."""
    try:
        # Get persona and ensure it belongs to current user
        db_persona = await PersonaService.get_user_persona(db, persona_id, auth)
        
        # Parse persona for response
        persona_dict = PersonaService.parse_persona_response(db_persona)
        return PersonaResponse(**persona_dict)
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error retrieving persona: {str(e)}")


@router.put("/personas/{persona_id}", response_model=PersonaResponse)
async def update_persona(
    persona_id: str,
    persona_update: PersonaUpdate,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user)
):
    """Update a persona."""
    try:
        db_persona = await PersonaService.update_persona(
            db=db,
            persona_id=persona_id,
            user_id=str(auth.user_id),
            persona_update=persona_update
        )
        
        if not db_persona:
            raise HTTPException(status_code=404, detail="Persona not found")
        
        # Parse persona for response
        persona_dict = PersonaService.parse_persona_response(db_persona)
        return PersonaResponse(**persona_dict)
        
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating persona: {str(e)}")


@router.delete("/personas/{persona_id}", status_code=204)
async def delete_persona(
    persona_id: str,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user)
):
    """Delete a persona."""
    try:
        success = await PersonaService.delete_persona(
            db=db,
            persona_id=persona_id,
            user_id=str(auth.user_id)
        )
        
        if not success:
            raise HTTPException(status_code=404, detail="Persona not found")
        
        return None
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting persona: {str(e)}")


@router.get("/personas/tags", response_model=List[str])
async def get_available_tags(
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user)
):
    """Get all unique tags used by user's personas."""
    try:
        tags = await PersonaService.get_available_tags(
            db=db,
            user_id=str(auth.user_id)
        )
        
        return tags
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error retrieving tags: {str(e)}")
