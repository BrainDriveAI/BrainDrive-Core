"""
Internal Plugin Runtime Endpoints

For plugin runtime services to execute hooks and callbacks.
These are NOT user-facing endpoints.
"""
from fastapi import APIRouter, Depends
from typing import Dict, Optional
from datetime import datetime

from app.core.service_auth import require_plugin_execution
from app.core.service_context import ServiceContext


router = APIRouter()


@router.post("/plugin/state")
async def update_plugin_state(
    plugin_id: str,
    state_data: Dict,
    service: ServiceContext = Depends(require_plugin_execution)
):
    """
    Internal endpoint for plugin runtime to update plugin state.
    
    Only accessible by services with 'execute_plugin' scope.
    """
    return {
        "status": "state_updated",
        "plugin_id": plugin_id,
        "updated_by": service.service_name,
        "timestamp": datetime.utcnow().isoformat()
    }


@router.post("/plugin/event")
async def send_plugin_event(
    plugin_id: str,
    event_type: str,
    event_data: Optional[Dict] = None,
    service: ServiceContext = Depends(require_plugin_execution)
):
    """
    Internal endpoint for plugin runtime to send events.
    
    Only accessible by services with 'execute_plugin' scope.
    """
    return {
        "status": "event_received",
        "plugin_id": plugin_id,
        "event_type": event_type,
        "received_by": service.service_name,
        "timestamp": datetime.utcnow().isoformat()
    }


@router.get("/plugin/config")
async def get_plugin_config(
    plugin_id: str,
    service: ServiceContext = Depends(require_plugin_execution)
):
    """
    Internal endpoint for plugin runtime to get configuration.
    
    Only accessible by services with 'execute_plugin' scope.
    """
    return {
        "plugin_id": plugin_id,
        "config": {},
        "retrieved_by": service.service_name,
        "timestamp": datetime.utcnow().isoformat()
    }

