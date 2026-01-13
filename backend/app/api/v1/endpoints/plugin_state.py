from fastapi import APIRouter, Depends, HTTPException, status, Query, Request, BackgroundTasks
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, update, func, and_, or_
from typing import List, Optional, Dict, Any
import json
import gzip
import base64
from datetime import datetime, timedelta
import logging

from app.core.database import get_db
from app.core.auth_deps import require_user
from app.core.auth_context import AuthContext
from app.models.user import User
from app.models.plugin_state import PluginState, PluginStateHistory, PluginStateConfig
from app.services.plugin_state_service import get_user_plugin_state
from app.schemas.plugin_state import (
    PluginStateCreate,
    PluginStateUpdate,
    PluginStateResponse,
    PluginStateBulkCreate,
    PluginStateBulkResponse,
    PluginStateHistoryResponse,
    PluginStateConfigCreate,
    PluginStateConfigUpdate,
    PluginStateConfigResponse,
    PluginStateQuery,
    PluginStateStats,
    PluginStateSyncRequest,
    PluginStateSyncResponse,
    PluginStateConflictResolution,
    PluginStateMigrationRequest,
    PluginStateMigrationResponse,
    StateStrategy,
    SyncStatus,
    ChangeType
)

router = APIRouter(prefix="/plugin-state")
logger = logging.getLogger(__name__)

# Utility functions
def compress_state_data(data: Dict[Any, Any]) -> tuple[str, str]:
    """Compress state data if it's large enough."""
    json_str = json.dumps(data)
    if len(json_str) > 1024:  # Compress if larger than 1KB
        compressed = gzip.compress(json_str.encode('utf-8'))
        encoded = base64.b64encode(compressed).decode('utf-8')
        return encoded, "gzip"
    return json_str, None

def decompress_state_data(data: str, compression_type: Optional[str]) -> Dict[Any, Any]:
    """Decompress state data if compressed."""
    if compression_type == "gzip":
        decoded = base64.b64decode(data.encode('utf-8'))
        decompressed = gzip.decompress(decoded).decode('utf-8')
        return json.loads(decompressed)
    return json.loads(data)

async def create_state_history(
    db: AsyncSession,
    plugin_state_id: str,
    state_data: Dict[Any, Any],
    version: int,
    change_type: ChangeType,
    device_id: Optional[str] = None,
    request: Optional[Request] = None
):
    """Create a history record for state changes."""
    history = PluginStateHistory(
        plugin_state_id=plugin_state_id,
        state_data=json.dumps(state_data),
        version=version,
        change_type=change_type.value,
        device_id=device_id,
        user_agent=request.headers.get("user-agent") if request else None,
        ip_address=request.client.host if request else None
    )
    db.add(history)
    await db.flush()
    return history

# Plugin State CRUD endpoints
@router.post("/", response_model=PluginStateResponse)
async def create_plugin_state(
    state_create: PluginStateCreate,
    background_tasks: BackgroundTasks,
    request: Request,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user)
):
    """Create a new plugin state record."""
    try:
        # Check if state already exists
        existing_query = select(PluginState).where(
            and_(
                PluginState.user_id == auth.user_id,
                PluginState.plugin_id == state_create.plugin_id,
                PluginState.page_id == state_create.page_id,
                PluginState.state_key == state_create.state_key
            )
        )
        result = await db.execute(existing_query)
        existing_state = result.scalar_one_or_none()
        
        if existing_state:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Plugin state already exists. Use PUT to update."
            )
        
        # Compress state data if needed
        compressed_data, compression_type = compress_state_data(state_create.state_data)
        
        # Create new state
        plugin_state = PluginState(
            user_id=auth.user_id,
            plugin_id=state_create.plugin_id,
            page_id=state_create.page_id,
            state_key=state_create.state_key,
            state_data=compressed_data,
            state_schema_version=state_create.state_schema_version,
            state_strategy=state_create.state_strategy.value,
            compression_type=compression_type,
            state_size=len(compressed_data),
            device_id=state_create.device_id,
            ttl_expires_at=state_create.ttl_expires_at,
            version=1,
            sync_status=SyncStatus.SYNCED.value
        )
        
        db.add(plugin_state)
        await db.flush()
        
        # Create history record
        background_tasks.add_task(
            create_state_history,
            db, plugin_state.id, state_create.state_data, 1, ChangeType.CREATE,
            state_create.device_id, request
        )
        
        await db.commit()
        
        # Return response with decompressed data
        response_data = plugin_state.__dict__.copy()
        response_data['state_data'] = state_create.state_data
        
        return PluginStateResponse(**response_data)
        
    except Exception as e:
        await db.rollback()
        logger.error(f"Error creating plugin state: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create plugin state: {str(e)}"
        )

@router.get("/", response_model=List[PluginStateResponse])
async def get_plugin_states(
    query: PluginStateQuery = Depends(),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user)
):
    """Get plugin states for the current user with filtering."""
    try:
        # Build query
        stmt = select(PluginState).where(PluginState.user_id == auth.user_id)
        
        # Apply filters
        if query.plugin_id:
            stmt = stmt.where(PluginState.plugin_id == query.plugin_id)
        if query.page_id:
            stmt = stmt.where(PluginState.page_id == query.page_id)
        if query.state_key:
            stmt = stmt.where(PluginState.state_key == query.state_key)
        if query.state_strategy:
            stmt = stmt.where(PluginState.state_strategy == query.state_strategy.value)
        if query.sync_status:
            stmt = stmt.where(PluginState.sync_status == query.sync_status.value)
        if query.is_active is not None:
            stmt = stmt.where(PluginState.is_active == query.is_active)
        if query.device_id:
            stmt = stmt.where(PluginState.device_id == query.device_id)
        
        # Apply pagination
        stmt = stmt.offset(query.offset).limit(query.limit)
        stmt = stmt.order_by(PluginState.last_accessed.desc())
        
        result = await db.execute(stmt)
        states = result.scalars().all()
        
        # Decompress state data for response
        response_states = []
        for state in states:
            state_dict = state.__dict__.copy()
            state_dict['state_data'] = decompress_state_data(
                state.state_data, state.compression_type
            )
            response_states.append(PluginStateResponse(**state_dict))
        
        return response_states
        
    except Exception as e:
        logger.error(f"Error getting plugin states: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get plugin states: {str(e)}"
        )

@router.get("/{state_id}", response_model=PluginStateResponse)
async def get_plugin_state(
    state_id: str,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user)
):
    """Get a specific plugin state by ID."""
    try:
        # Get plugin state and ensure it belongs to current user
        state = await get_user_plugin_state(db, state_id, auth)
        
        # Update access tracking
        state.last_accessed = datetime.utcnow()
        state.access_count += 1
        await db.commit()
        
        # Return response with decompressed data
        state_dict = state.__dict__.copy()
        state_dict['state_data'] = decompress_state_data(
            state.state_data, state.compression_type
        )
        
        return PluginStateResponse(**state_dict)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting plugin state {state_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get plugin state: {str(e)}"
        )

@router.put("/{state_id}", response_model=PluginStateResponse)
async def update_plugin_state(
    state_id: str,
    state_update: PluginStateUpdate,
    background_tasks: BackgroundTasks,
    request: Request,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user)
):
    """Update a plugin state record."""
    try:
        # Get plugin state and ensure it belongs to current user
        state = await get_user_plugin_state(db, state_id, auth)
        
        # Store old data for history
        old_data = decompress_state_data(state.state_data, state.compression_type)
        
        # Update fields
        if state_update.state_data is not None:
            compressed_data, compression_type = compress_state_data(state_update.state_data)
            state.state_data = compressed_data
            state.compression_type = compression_type
            state.state_size = len(compressed_data)
        
        if state_update.state_strategy is not None:
            state.state_strategy = state_update.state_strategy.value
        if state_update.ttl_expires_at is not None:
            state.ttl_expires_at = state_update.ttl_expires_at
        if state_update.device_id is not None:
            state.device_id = state_update.device_id
        if state_update.state_schema_version is not None:
            state.state_schema_version = state_update.state_schema_version
        
        # Update version and sync status
        state.version += 1
        state.sync_status = SyncStatus.SYNCED.value
        state.last_accessed = datetime.utcnow()
        state.access_count += 1
        
        # Create history record
        background_tasks.add_task(
            create_state_history,
            db, state.id, state_update.state_data or old_data, state.version,
            ChangeType.UPDATE, state_update.device_id, request
        )
        
        await db.commit()
        
        # Return response with decompressed data
        state_dict = state.__dict__.copy()
        state_dict['state_data'] = state_update.state_data or old_data
        
        return PluginStateResponse(**state_dict)
        
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"Error updating plugin state {state_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update plugin state: {str(e)}"
        )

@router.delete("/{state_id}")
async def delete_plugin_state(
    state_id: str,
    background_tasks: BackgroundTasks,
    request: Request,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user)
):
    """Delete a plugin state record."""
    try:
        # Get plugin state and ensure it belongs to current user
        state = await get_user_plugin_state(db, state_id, auth)
        
        # Store data for history before deletion
        state_data = decompress_state_data(state.state_data, state.compression_type)
        
        # Create history record
        background_tasks.add_task(
            create_state_history,
            db, state.id, state_data, state.version, ChangeType.DELETE,
            state.device_id, request
        )
        
        # Delete the state
        await db.delete(state)
        await db.commit()
        
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"message": "Plugin state deleted successfully"}
        )
        
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"Error deleting plugin state {state_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete plugin state: {str(e)}"
        )

# Bulk operations
@router.post("/bulk", response_model=PluginStateBulkResponse)
async def create_plugin_states_bulk(
    bulk_create: PluginStateBulkCreate,
    background_tasks: BackgroundTasks,
    request: Request,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user)
):
    """Create multiple plugin states in bulk."""
    created_states = []
    errors = []
    
    try:
        for i, state_create in enumerate(bulk_create.states):
            try:
                # Check if state already exists
                existing_query = select(PluginState).where(
                    and_(
                        PluginState.user_id == auth.user_id,
                        PluginState.plugin_id == state_create.plugin_id,
                        PluginState.page_id == state_create.page_id,
                        PluginState.state_key == state_create.state_key
                    )
                )
                result = await db.execute(existing_query)
                existing_state = result.scalar_one_or_none()
                
                if existing_state:
                    errors.append({
                        "index": i,
                        "plugin_id": state_create.plugin_id,
                        "error": "State already exists"
                    })
                    continue
                
                # Compress state data
                compressed_data, compression_type = compress_state_data(state_create.state_data)
                
                # Create state
                plugin_state = PluginState(
                    user_id=auth.user_id,
                    plugin_id=state_create.plugin_id,
                    page_id=state_create.page_id,
                    state_key=state_create.state_key,
                    state_data=compressed_data,
                    state_schema_version=state_create.state_schema_version,
                    state_strategy=state_create.state_strategy.value,
                    compression_type=compression_type,
                    state_size=len(compressed_data),
                    device_id=state_create.device_id,
                    ttl_expires_at=state_create.ttl_expires_at,
                    version=1,
                    sync_status=SyncStatus.SYNCED.value
                )
                
                db.add(plugin_state)
                await db.flush()
                
                # Create history record
                background_tasks.add_task(
                    create_state_history,
                    db, plugin_state.id, state_create.state_data, 1, ChangeType.CREATE,
                    state_create.device_id, request
                )
                
                # Add to created list
                response_data = plugin_state.__dict__.copy()
                response_data['state_data'] = state_create.state_data
                created_states.append(PluginStateResponse(**response_data))
                
            except Exception as e:
                errors.append({
                    "index": i,
                    "plugin_id": state_create.plugin_id,
                    "error": str(e)
                })
        
        await db.commit()
        
        return PluginStateBulkResponse(created=created_states, errors=errors)
        
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in bulk create: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create plugin states: {str(e)}"
        )

# Statistics endpoint
@router.get("/stats", response_model=PluginStateStats)
async def get_plugin_state_stats(
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user)
):
    """Get plugin state statistics for the current user."""
    try:
        # Get basic counts
        total_query = select(func.count(PluginState.id)).where(PluginState.user_id == auth.user_id)
        active_query = select(func.count(PluginState.id)).where(
            and_(PluginState.user_id == auth.user_id, PluginState.is_active == True)
        )
        size_query = select(func.sum(PluginState.state_size)).where(PluginState.user_id == auth.user_id)
        plugins_query = select(func.count(func.distinct(PluginState.plugin_id))).where(
            PluginState.user_id == auth.user_id
        )
        last_activity_query = select(func.max(PluginState.last_accessed)).where(
            PluginState.user_id == auth.user_id
        )
        
        total_result = await db.execute(total_query)
        active_result = await db.execute(active_query)
        size_result = await db.execute(size_query)
        plugins_result = await db.execute(plugins_query)
        last_activity_result = await db.execute(last_activity_query)
        
        total_states = total_result.scalar() or 0
        active_states = active_result.scalar() or 0
        total_size = size_result.scalar() or 0
        plugins_with_state = plugins_result.scalar() or 0
        last_activity = last_activity_result.scalar()
        
        average_state_size = total_size / total_states if total_states > 0 else 0
        
        return PluginStateStats(
            total_states=total_states,
            active_states=active_states,
            total_size=total_size,
            plugins_with_state=plugins_with_state,
            average_state_size=average_state_size,
            last_activity=last_activity
        )
        
    except Exception as e:
        logger.error(f"Error getting plugin state stats: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get plugin state stats: {str(e)}"
        )

# Cleanup endpoint
@router.delete("/cleanup")
async def cleanup_expired_states(
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user)
):
    """Clean up expired plugin states."""
    try:
        # Delete expired states
        now = datetime.utcnow()
        delete_query = delete(PluginState).where(
            and_(
                PluginState.user_id == auth.user_id,
                PluginState.ttl_expires_at < now
            )
        )
        
        result = await db.execute(delete_query)
        deleted_count = result.rowcount
        
        await db.commit()
        
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "message": f"Cleaned up {deleted_count} expired plugin states",
                "deleted_count": deleted_count
            }
        )
        
    except Exception as e:
        await db.rollback()
        logger.error(f"Error cleaning up expired states: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to cleanup expired states: {str(e)}"
        )