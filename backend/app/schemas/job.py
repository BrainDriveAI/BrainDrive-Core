from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.job import JobStatus


class JobCreateRequest(BaseModel):
    job_type: str = Field(..., min_length=1, max_length=50)
    payload: Dict[str, Any] = Field(..., description="Job-specific payload data")
    priority: int = Field(0, ge=-100, le=100)
    scheduled_for: Optional[datetime] = Field(default=None)
    idempotency_key: Optional[str] = Field(default=None, max_length=100)
    max_retries: int = Field(default=3, ge=0, le=10)
    depends_on: Optional[List[str]] = None


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    job_type: str
    status: JobStatus
    priority: int
    progress_percent: int
    current_stage: Optional[str]
    message: Optional[str]
    created_at: Optional[datetime]
    updated_at: Optional[datetime]
    scheduled_for: Optional[datetime]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    user_id: str
    workspace_id: Optional[str]
    result: Optional[Dict[str, Any]]
    error_message: Optional[str]
    error_code: Optional[str]
    retry_count: int
    max_retries: int


class JobListResponse(BaseModel):
    jobs: List[JobResponse]
    total: int
    page: int
    page_size: int
    has_next: bool


class JobProgressEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    job_id: str
    event_type: str
    timestamp: datetime
    data: Dict[str, Any]
    sequence_number: int
