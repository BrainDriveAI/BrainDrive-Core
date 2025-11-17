import asyncio
import json
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from app.core.job_manager_provider import get_job_manager
from app.core.security import get_current_user
from app.models.job import Job, JobStatus
from app.schemas.job import (
    JobCreateRequest,
    JobListResponse,
    JobProgressEventResponse,
    JobResponse,
)
from app.services.job_manager import JobManager
from app.models.user import User

router = APIRouter(prefix="/jobs", tags=["jobs"])
TERMINAL_JOB_STATUSES = {
    JobStatus.COMPLETED.value,
    JobStatus.FAILED.value,
    JobStatus.CANCELED.value,
}


def _ensure_job_access(job: Job, user: User) -> None:
    if job.user_id != str(user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")


def _to_job_response(job: Job) -> JobResponse:
    return JobResponse.model_validate(job)


def _serialize_job_snapshot(job: Job, event_type: str = "snapshot") -> dict:
    timestamp = job.updated_at or job.created_at or datetime.now(timezone.utc)
    data = {
        "stage": job.current_stage,
        "progress_percent": job.progress_percent,
        "message": job.message,
    }
    return {
        "job_id": job.id,
        "job_type": job.job_type,
        "status": job.status,
        "current_stage": job.current_stage,
        "message": job.message,
        "progress_percent": job.progress_percent,
        "event_type": event_type,
        "sequence_number": 0,
        "timestamp": timestamp.isoformat(),
        "data": data,
    }


def _serialize_job_event_payload(job: Job, event) -> dict:
    timestamp = getattr(event, "timestamp", None) or datetime.now(timezone.utc)
    payload = {
        "job_id": job.id,
        "job_type": job.job_type,
        "status": job.status,
        "current_stage": job.current_stage,
        "message": job.message,
        "progress_percent": job.progress_percent,
        "event_type": event.event_type,
        "sequence_number": event.sequence_number,
        "timestamp": timestamp.isoformat(),
        "data": getattr(event, "data", {}) or {},
    }
    data = payload["data"]
    if "stage" in data:
        payload["current_stage"] = data["stage"]
        payload["stage"] = data["stage"]
    if "message" in data:
        payload["message"] = data["message"]
    if "progress_percent" in data:
        payload["progress_percent"] = data["progress_percent"]
    if "progress_bucket" in data:
        payload["progress_bucket"] = data["progress_bucket"]
    if "completed_bytes" in data:
        payload["completed_bytes"] = data["completed_bytes"]
    if "total_bytes" in data:
        payload["total_bytes"] = data["total_bytes"]
    return payload


@router.post("", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_job(
    request: JobCreateRequest,
    current_user: User = Depends(get_current_user),
    job_manager: JobManager = Depends(get_job_manager),
) -> JobResponse:
    job, _ = await job_manager.enqueue_job(
        job_type=request.job_type,
        payload=request.payload,
        user_id=str(current_user.id),
        priority=request.priority,
        scheduled_for=request.scheduled_for,
        idempotency_key=request.idempotency_key,
        max_retries=request.max_retries,
        depends_on=request.depends_on,
    )
    return _to_job_response(job)


@router.get("", response_model=JobListResponse)
async def list_jobs(
    status_filter: Optional[JobStatus] = Query(default=None, alias="status"),
    job_type: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    job_manager: JobManager = Depends(get_job_manager),
) -> JobListResponse:
    result = await job_manager.list_jobs(
        user_id=str(current_user.id),
        status=status_filter.value if status_filter else None,
        job_type=job_type,
        page=page,
        page_size=page_size,
    )
    jobs = [_to_job_response(job) for job in result["jobs"]]
    return JobListResponse(
        jobs=jobs,
        total=result["total"],
        page=result["page"],
        page_size=result["page_size"],
        has_next=result["has_next"],
    )


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: str,
    current_user: User = Depends(get_current_user),
    job_manager: JobManager = Depends(get_job_manager),
) -> JobResponse:
    job = await job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    _ensure_job_access(job, current_user)
    return _to_job_response(job)


@router.post("/{job_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
async def cancel_job(
    job_id: str,
    current_user: User = Depends(get_current_user),
    job_manager: JobManager = Depends(get_job_manager),
) -> dict:
    job = await job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    _ensure_job_access(job, current_user)

    canceled = await job_manager.cancel_job(job_id)
    if not canceled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unable to cancel job")
    return {"status": "cancellation_requested"}


@router.post("/{job_id}/retry", response_model=JobResponse)
async def retry_job(
    job_id: str,
    current_user: User = Depends(get_current_user),
    job_manager: JobManager = Depends(get_job_manager),
) -> JobResponse:
    job = await job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    _ensure_job_access(job, current_user)

    retried = await job_manager.retry_job(job_id)
    if not retried:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unable to retry job")
    return _to_job_response(retried)


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_job(
    job_id: str,
    current_user: User = Depends(get_current_user),
    job_manager: JobManager = Depends(get_job_manager),
) -> None:
    job = await job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    _ensure_job_access(job, current_user)
    deleted = await job_manager.delete_job(job_id, str(current_user.id))
    if not deleted:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unable to delete job")
    return None


@router.get("/{job_id}/events", response_model=List[JobProgressEventResponse])
async def get_job_events(
    job_id: str,
    since_sequence: Optional[int] = Query(default=None, ge=0),
    current_user: User = Depends(get_current_user),
    job_manager: JobManager = Depends(get_job_manager),
) -> List[JobProgressEventResponse]:
    job = await job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    _ensure_job_access(job, current_user)

    events = await job_manager.get_progress_events(job_id, since=since_sequence)
    return [JobProgressEventResponse.model_validate(event) for event in events]


@router.get("/{job_id}/events/stream")
async def stream_job_events(
    job_id: str,
    current_user: User = Depends(get_current_user),
    job_manager: JobManager = Depends(get_job_manager),
):
    job = await job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    _ensure_job_access(job, current_user)

    async def event_generator():
        last_sequence: Optional[int] = None
        try:
            yield f"data: {json.dumps(_serialize_job_snapshot(job))}\n\n".encode()

            while True:
                events = await job_manager.get_progress_events(job_id, since=last_sequence)
                if events:
                    job_snapshot = await job_manager.get_job(job_id)
                    if not job_snapshot or job_snapshot.user_id != str(current_user.id):
                        break
                    for event in events:
                        last_sequence = event.sequence_number
                        payload = _serialize_job_event_payload(job_snapshot, event)
                        yield f"data: {json.dumps(payload)}\n\n".encode()

                job_snapshot = await job_manager.get_job(job_id)
                if not job_snapshot or job_snapshot.user_id != str(current_user.id):
                    break
                if job_snapshot.status in TERMINAL_JOB_STATUSES:
                    yield f"data: {json.dumps(_serialize_job_snapshot(job_snapshot, event_type='terminal'))}\n\n".encode()
                    break
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            return

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/{job_id}/logs", response_model=List[JobProgressEventResponse])
async def get_job_logs(
    job_id: str,
    current_user: User = Depends(get_current_user),
    job_manager: JobManager = Depends(get_job_manager),
) -> List[JobProgressEventResponse]:
    job = await job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    _ensure_job_access(job, current_user)

    events = await job_manager.get_progress_events(job_id)
    log_events = [event for event in events if event.event_type in {"log", "error"}]
    return [JobProgressEventResponse.model_validate(event) for event in log_events]
