"""
Internal Job Worker Endpoints

For background job workers to report progress and status.
These are NOT user-facing endpoints.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from typing import Dict, Optional
from datetime import datetime

from app.core.service_auth import require_job_execution
from app.core.service_context import ServiceContext


router = APIRouter()


@router.post("/job/progress")
async def report_job_progress(
    job_id: str,
    progress_percent: Optional[int] = None,
    stage: Optional[str] = None,
    message: Optional[str] = None,
    service: ServiceContext = Depends(require_job_execution)
):
    """
    Internal endpoint for job workers to report progress.
    
    Only accessible by services with 'execute_job' scope.
    """
    return {
        "status": "progress_recorded",
        "job_id": job_id,
        "reported_by": service.service_name,
        "timestamp": datetime.utcnow().isoformat()
    }


@router.post("/job/complete")
async def mark_job_complete(
    job_id: str,
    result: Dict,
    service: ServiceContext = Depends(require_job_execution)
):
    """
    Internal endpoint for job workers to mark job as complete.
    
    Only accessible by services with 'execute_job' scope.
    """
    return {
        "status": "job_completed",
        "job_id": job_id,
        "completed_by": service.service_name,
        "timestamp": datetime.utcnow().isoformat()
    }


@router.post("/job/fail")
async def mark_job_failed(
    job_id: str,
    error: str,
    service: ServiceContext = Depends(require_job_execution)
):
    """
    Internal endpoint for job workers to mark job as failed.
    
    Only accessible by services with 'execute_job' scope.
    """
    return {
        "status": "job_failed",
        "job_id": job_id,
        "failed_by": service.service_name,
        "error": error,
        "timestamp": datetime.utcnow().isoformat()
    }

