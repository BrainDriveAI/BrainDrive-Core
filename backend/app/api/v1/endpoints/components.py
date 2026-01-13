from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.core.auth_deps import require_user, require_admin
from app.core.auth_context import AuthContext
from app.models.component import Component
from app.models.user import User
from app.schemas.component import (
    ComponentCreate,
    ComponentResponse,
    ComponentUpdate
)

router = APIRouter()

@router.get("", response_model=List[ComponentResponse])
async def get_components(
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user)
):
    """
    Get all components.
    """
    try:
        components = await Component.get_all_components(db)
        return components
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch components: {str(e)}"
        )

@router.post("", response_model=ComponentResponse, status_code=status.HTTP_201_CREATED)
async def create_component(
    component_data: ComponentCreate,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_admin)  # Only admins can create components
):
    """
    Create a new component.
    """
    try:
        # Check if component already exists
        existing_component = await Component.get_by_component_id(db, component_data.component_id)
        if existing_component:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Component with ID '{component_data.component_id}' already exists"
            )
        
        # Create new component
        new_component = Component(**component_data.dict())
        db.add(new_component)
        await db.commit()
        await db.refresh(new_component)
        
        return new_component
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create component: {str(e)}"
        )

@router.get("/{component_id}", response_model=ComponentResponse)
async def get_component(
    component_id: str,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user)
):
    """
    Get a specific component by ID.
    """
    try:
        component = await Component.get_by_component_id(db, component_id)
        if not component:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Component with ID {component_id} not found"
            )
        
        return component
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch component: {str(e)}"
        )

@router.put("/{component_id}", response_model=ComponentResponse)
async def update_component(
    component_id: str,
    component_data: ComponentUpdate,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_admin)  # Only admins can update components
):
    """
    Update a component.
    """
    try:
        # Get existing component
        component = await Component.get_by_component_id(db, component_id)
        if not component:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Component with ID {component_id} not found"
            )
        
        # Update fields
        for key, value in component_data.dict(exclude_unset=True).items():
            setattr(component, key, value)
        
        db.add(component)
        await db.commit()
        await db.refresh(component)
        
        return component
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update component: {str(e)}"
        )

@router.delete("/{component_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_component(
    component_id: str,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_admin)  # Only admins can delete components
):
    """
    Delete a component.
    """
    try:
        # Get existing component
        component = await Component.get_by_component_id(db, component_id)
        if not component:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Component with ID {component_id} not found"
            )
        
        # Prevent deletion of system components
        if component.is_system:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot delete system components"
            )
        
        # Delete component
        await db.delete(component)
        await db.commit()
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete component: {str(e)}"
        )
