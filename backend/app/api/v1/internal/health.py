"""
Internal Health Check Endpoint

Example internal endpoint for service health monitoring.
Only accessible with valid service authentication.
"""
from fastapi import APIRouter, Depends
from typing import Dict
from datetime import datetime

from app.core.service_auth import require_service
from app.core.service_context import ServiceContext


router = APIRouter()


@router.get("/health")
async def internal_health_check(
    service: ServiceContext = Depends(require_service)
) -> Dict:
    """
    Internal health check endpoint for services.
    
    This endpoint is only accessible with valid service authentication.
    It is NOT exposed to end users and NOT in OpenAPI docs.
    
    Args:
        service: Authenticated service context
        
    Returns:
        Health status and service info
    """
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "service_name": service.service_name,
        "scopes": list(service.scopes),
        "message": "Internal service endpoint working correctly"
    }


@router.post("/heartbeat")
async def service_heartbeat(
    service: ServiceContext = Depends(require_service)
) -> Dict:
    """
    Service heartbeat endpoint.
    
    Services can call this to report they are alive and functioning.
    
    Args:
        service: Authenticated service context
        
    Returns:
        Acknowledgment of heartbeat
    """
    return {
        "acknowledged": True,
        "service_name": service.service_name,
        "timestamp": datetime.utcnow().isoformat()
    }

