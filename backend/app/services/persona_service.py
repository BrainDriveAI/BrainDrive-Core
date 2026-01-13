"""
Service layer for persona operations.
Handles CRUD operations, validation, and business logic for personas.
"""
import json
import logging
from typing import List, Optional, Dict, Any
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload

from app.models.persona import Persona
from app.schemas.persona import PersonaCreate, PersonaUpdate, ModelSettings
from app.core.database import get_db
from app.core.auth_context import AuthContext

logger = logging.getLogger(__name__)


class PersonaService:
    """Service class for persona operations."""
    
    @staticmethod
    async def create_persona(
        db: AsyncSession,
        persona_data: PersonaCreate,
        user_id: str
    ) -> Persona:
        """Create a new persona for a user."""
        try:
            # Validate model settings if provided
            if persona_data.model_settings:
                PersonaService._validate_model_settings(persona_data.model_settings)
            
            # Create persona instance
            db_persona = Persona(
                name=persona_data.name,
                description=persona_data.description,
                system_prompt=persona_data.system_prompt,
                model_settings=json.dumps(persona_data.model_settings) if persona_data.model_settings else None,
                avatar=persona_data.avatar,
                tags=json.dumps(persona_data.tags) if persona_data.tags else None,
                sample_greeting=persona_data.sample_greeting,
                is_active=persona_data.is_active,
                user_id=user_id
            )
            
            db.add(db_persona)
            await db.commit()
            await db.refresh(db_persona)
            
            logger.info(f"Created persona {db_persona.id} for user {user_id}")
            return db_persona
            
        except Exception as e:
            logger.error(f"Error creating persona: {e}")
            await db.rollback()
            raise
    
    @staticmethod
    async def get_persona_by_id(
        db: AsyncSession,
        persona_id: str,
        user_id: str
    ) -> Optional[Persona]:
        """Get a persona by ID, ensuring it belongs to the user."""
        try:
            query = select(Persona).where(
                Persona.id == persona_id,
                Persona.user_id == user_id
            )
            result = await db.execute(query)
            return result.scalars().first()
        except Exception as e:
            logger.error(f"Error getting persona {persona_id}: {e}")
            return None
    
    @staticmethod
    async def get_user_persona(
        db: AsyncSession,
        persona_id: str,
        auth: AuthContext
    ) -> Persona:
        """
        Get a persona and ensure it belongs to the current user.
        
        Returns 404 (not 403) if persona doesn't exist or doesn't belong to user,
        to prevent resource enumeration attacks.
        
        Args:
            db: Database session
            persona_id: ID of the persona to retrieve
            auth: Authentication context with current user info
            
        Returns:
            Persona object if found and belongs to user
            
        Raises:
            HTTPException: 404 if persona not found or doesn't belong to user
        """
        persona = await PersonaService.get_persona_by_id(db, persona_id, str(auth.user_id))
        if not persona:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Persona not found"
            )
        return persona
    
    @staticmethod
    async def get_user_personas(
        db: AsyncSession,
        user_id: str,
        skip: int = 0,
        limit: int = 20,
        search: Optional[str] = None,
        tags: Optional[List[str]] = None,
        is_active: Optional[bool] = None
    ) -> tuple[List[Persona], int]:
        """Get paginated list of user's personas with optional filtering."""
        try:
            # Build base query
            query = select(Persona).where(Persona.user_id == user_id)
            
            # Apply filters
            if search:
                search_term = f"%{search}%"
                query = query.where(
                    Persona.name.ilike(search_term) |
                    Persona.description.ilike(search_term) |
                    Persona.system_prompt.ilike(search_term)
                )
            
            if tags:
                # Filter by tags (JSON contains any of the specified tags)
                for tag in tags:
                    query = query.where(Persona.tags.like(f'%"{tag}"%'))
            
            if is_active is not None:
                query = query.where(Persona.is_active == is_active)
            
            # Get total count
            count_query = select(func.count()).select_from(query.subquery())
            total_result = await db.execute(count_query)
            total_count = total_result.scalar()
            
            # Apply pagination and ordering
            query = query.order_by(Persona.updated_at.desc()).offset(skip).limit(limit)
            
            result = await db.execute(query)
            personas = result.scalars().all()
            
            return personas, total_count
            
        except Exception as e:
            logger.error(f"Error getting user personas: {e}")
            return [], 0
    
    @staticmethod
    async def update_persona(
        db: AsyncSession,
        persona_id: str,
        user_id: str,
        persona_update: PersonaUpdate
    ) -> Optional[Persona]:
        """Update a persona."""
        try:
            # Get existing persona
            db_persona = await PersonaService.get_persona_by_id(db, persona_id, user_id)
            if not db_persona:
                return None
            
            # Update fields
            update_data = persona_update.dict(exclude_unset=True)
            
            for field, value in update_data.items():
                if field == "model_settings" and value is not None:
                    PersonaService._validate_model_settings(value)
                    setattr(db_persona, field, json.dumps(value))
                elif field == "tags" and value is not None:
                    setattr(db_persona, field, json.dumps(value))
                else:
                    setattr(db_persona, field, value)
            
            await db.commit()
            await db.refresh(db_persona)
            
            logger.info(f"Updated persona {persona_id}")
            return db_persona
            
        except Exception as e:
            logger.error(f"Error updating persona {persona_id}: {e}")
            await db.rollback()
            raise
    
    @staticmethod
    async def delete_persona(
        db: AsyncSession,
        persona_id: str,
        user_id: str
    ) -> bool:
        """Delete a persona."""
        try:
            db_persona = await PersonaService.get_persona_by_id(db, persona_id, user_id)
            if not db_persona:
                return False
            
            await db.delete(db_persona)
            await db.commit()
            
            logger.info(f"Deleted persona {persona_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error deleting persona {persona_id}: {e}")
            await db.rollback()
            raise
    
    @staticmethod
    async def get_available_tags(
        db: AsyncSession,
        user_id: str
    ) -> List[str]:
        """Get all unique tags used by user's personas."""
        try:
            query = select(Persona.tags).where(
                Persona.user_id == user_id,
                Persona.tags.isnot(None)
            )
            result = await db.execute(query)
            tag_strings = result.scalars().all()
            
            # Parse JSON tags and collect unique values
            all_tags = set()
            for tag_string in tag_strings:
                if tag_string:
                    try:
                        tags = json.loads(tag_string)
                        if isinstance(tags, list):
                            all_tags.update(tags)
                    except json.JSONDecodeError:
                        continue
            
            return sorted(list(all_tags))
            
        except Exception as e:
            logger.error(f"Error getting available tags: {e}")
            return []
    
    @staticmethod
    def _validate_model_settings(settings: Dict[str, Any]) -> None:
        """Validate model settings against schema."""
        try:
            ModelSettings(**settings)
        except Exception as e:
            raise ValueError(f"Invalid model settings: {e}")
    
    @staticmethod
    def parse_persona_response(persona: Persona) -> Dict[str, Any]:
        """Parse persona for API response, handling JSON fields."""
        persona_dict = {
            "id": persona.id,
            "name": persona.name,
            "description": persona.description,
            "system_prompt": persona.system_prompt,
            "avatar": persona.avatar,
            "sample_greeting": persona.sample_greeting,
            "is_active": persona.is_active,
            "user_id": persona.user_id,
            "created_at": persona.created_at,
            "updated_at": persona.updated_at
        }
        
        # Parse JSON fields
        try:
            persona_dict["model_settings"] = json.loads(persona.model_settings) if persona.model_settings else None
        except (json.JSONDecodeError, TypeError):
            persona_dict["model_settings"] = None
        
        try:
            persona_dict["tags"] = json.loads(persona.tags) if persona.tags else None
        except (json.JSONDecodeError, TypeError):
            persona_dict["tags"] = None
        
        return persona_dict
