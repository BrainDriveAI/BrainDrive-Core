"""
Demo API endpoints for ServiceExample_API plugin demonstration.

This module provides simple CRUD operations for demonstration purposes.
Uses in-memory storage (no database required) and requires user authentication.
"""

from fastapi import APIRouter, Depends, HTTPException
from typing import List, Dict, Any, Optional
from datetime import datetime
from pydantic import BaseModel, Field
from app.core.auth_deps import require_user
from app.core.auth_context import AuthContext
from app.models.user import User

router = APIRouter()

# In-memory storage for demo purposes
demo_items: Dict[str, Dict[str, Any]] = {}
item_counter = 0

class CreateItemRequest(BaseModel):
    """Request model for creating a new demo item."""
    name: str = Field(..., min_length=1, max_length=100, description="Item name")
    description: str = Field("", max_length=500, description="Item description")

class UpdateItemRequest(BaseModel):
    """Request model for updating an existing demo item."""
    name: Optional[str] = Field(None, min_length=1, max_length=100, description="Item name")
    description: Optional[str] = Field(None, max_length=500, description="Item description")

class DemoItemResponse(BaseModel):
    """Response model for demo items."""
    id: str
    name: str
    description: str
    user_id: str
    created_at: str
    updated_at: str

@router.get("/demo/items", response_model=Dict[str, Any])
async def get_demo_items(auth: AuthContext = Depends(require_user)):
    """
    Get all demo items for the current user.
    
    Returns:
        Dict containing data array and count of items
    """
    try:
        user_items = {k: v for k, v in demo_items.items() if v.get("user_id") == auth.user_id}
        items_list = list(user_items.values())
        
        return {
            "data": items_list,
            "count": len(items_list),
            "message": f"Retrieved {len(items_list)} items successfully"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve items: {str(e)}")

@router.post("/demo/items", response_model=Dict[str, Any])
async def create_demo_item(
    item_data: CreateItemRequest, 
    auth: AuthContext = Depends(require_user)
):
    """
    Create a new demo item.
    
    Args:
        item_data: The item data to create
        current_user: The authenticated user
        
    Returns:
        Dict containing the created item and success message
    """
    global item_counter
    
    try:
        item_counter += 1
        
        item = {
            "id": str(item_counter),
            "name": item_data.name.strip(),
            "description": item_data.description.strip(),
            "user_id": auth.user_id,
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat()
        }
        
        demo_items[str(item_counter)] = item
        
        return {
            "data": item,
            "message": "Item created successfully"
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create item: {str(e)}")

@router.put("/demo/items/{item_id}", response_model=Dict[str, Any])
async def update_demo_item(
    item_id: str,
    item_data: UpdateItemRequest,
    auth: AuthContext = Depends(require_user)
):
    """
    Update an existing demo item.
    
    Args:
        item_id: The ID of the item to update
        item_data: The updated item data
        current_user: The authenticated user
        
    Returns:
        Dict containing the updated item and success message
    """
    try:
        if item_id not in demo_items:
            raise HTTPException(status_code=404, detail="Item not found")
        
        item = demo_items[item_id]
        if item.get("user_id") != auth.user_id:
            raise HTTPException(status_code=403, detail="Not authorized to update this item")
        
        # Update only provided fields
        if item_data.name is not None:
            if not item_data.name.strip():
                raise HTTPException(status_code=400, detail="Item name cannot be empty")
            item["name"] = item_data.name.strip()
            
        if item_data.description is not None:
            item["description"] = item_data.description.strip()
            
        item["updated_at"] = datetime.utcnow().isoformat()
        
        return {
            "data": item,
            "message": "Item updated successfully"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update item: {str(e)}")

@router.delete("/demo/items/{item_id}", response_model=Dict[str, Any])
async def delete_demo_item(
    item_id: str,
    auth: AuthContext = Depends(require_user)
):
    """
    Delete a demo item.
    
    Args:
        item_id: The ID of the item to delete
        current_user: The authenticated user
        
    Returns:
        Dict containing success message
    """
    try:
        if item_id not in demo_items:
            raise HTTPException(status_code=404, detail="Item not found")
        
        item = demo_items[item_id]
        if item.get("user_id") != auth.user_id:
            raise HTTPException(status_code=403, detail="Not authorized to delete this item")
        
        del demo_items[item_id]
        
        return {
            "message": "Item deleted successfully",
            "deleted_item_id": item_id
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete item: {str(e)}")

@router.get("/demo/status", response_model=Dict[str, Any])
async def get_demo_status(auth: AuthContext = Depends(require_user)):
    """
    Get demo API status and statistics.
    
    Args:
        current_user: The authenticated user
        
    Returns:
        Dict containing status information and statistics
    """
    try:
        user_items = {k: v for k, v in demo_items.items() if v.get("user_id") == auth.user_id}
        
        return {
            "status": "active",
            "user_item_count": len(user_items),
            "total_items": len(demo_items),
            "server_time": datetime.utcnow().isoformat(),
            "user_id": auth.user_id,
            "message": "Demo API is running successfully"
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get status: {str(e)}")

@router.get("/demo/health", response_model=Dict[str, Any])
async def health_check():
    """
    Simple health check endpoint (no authentication required).
    
    Returns:
        Dict containing health status
    """
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "service": "demo-api",
        "version": "1.0.0"
    }