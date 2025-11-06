import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.models.base import Base
from app.models.mixins import TimestampMixin


class JobStatus(str, enum.Enum):
    """Enumeration of job lifecycle states."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
    WAITING = "waiting"


def _uuid() -> str:
    return str(uuid.uuid4())


class Job(Base, TimestampMixin):
    """Primary persisted job record."""

    __tablename__ = "jobs"

    id = Column(String(36), primary_key=True, default=_uuid)
    job_type = Column(
        String(50),
        ForeignKey("job_type_definitions.job_type", ondelete="RESTRICT"),
        nullable=False,
    )
    status = Column(String(20), nullable=False, default=JobStatus.QUEUED.value)
    priority = Column(Integer, nullable=False, default=0)

    payload = Column(JSON, nullable=False)
    config = Column(JSON, nullable=True)

    scheduled_for = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))

    progress_percent = Column(Integer, nullable=False, default=0)
    current_stage = Column(String(100))
    message = Column(Text)

    result = Column(JSON)
    error_message = Column(Text)
    error_code = Column(String(50))

    user_id = Column(String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    workspace_id = Column(String(32), ForeignKey("tenants.id", ondelete="SET NULL"))
    parent_job_id = Column(String(36), ForeignKey("jobs.id", ondelete="SET NULL"))

    idempotency_key = Column(String(100))
    retry_count = Column(Integer, nullable=False, default=0)
    max_retries = Column(Integer, nullable=False, default=3)
    expires_at = Column(DateTime(timezone=True))

    user = relationship("User", back_populates="jobs", lazy="selectin")
    workspace = relationship("Tenant", back_populates="jobs", lazy="selectin")
    parent_job = relationship("Job", remote_side=[id], back_populates="child_jobs", lazy="selectin")

    attempts = relationship(
        "JobAttempt",
        back_populates="job",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="JobAttempt.attempt_number",
    )
    progress_events = relationship(
        "JobProgressEvent",
        back_populates="job",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="JobProgressEvent.sequence_number",
    )
    dependencies = relationship(
        "JobDependency",
        back_populates="job",
        cascade="all, delete-orphan",
        lazy="selectin",
        foreign_keys="JobDependency.job_id",
    )
    dependents = relationship(
        "JobDependency",
        back_populates="depends_on_job",
        lazy="selectin",
        foreign_keys="JobDependency.depends_on_job_id",
    )
    subscriptions = relationship(
        "JobSubscription",
        back_populates="job",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    job_type_definition = relationship("JobTypeDefinition", back_populates="jobs", lazy="selectin")
    child_jobs = relationship("Job", back_populates="parent_job", lazy="selectin")

    __table_args__ = (
        UniqueConstraint("idempotency_key", "user_id", name="uq_jobs_idempotency_user"),
        Index("idx_jobs_status_priority", "status", "priority", "created_at"),
        Index("idx_jobs_user_status", "user_id", "status", "created_at"),
        Index("idx_jobs_type_status", "job_type", "status", "scheduled_for"),
        Index("idx_jobs_scheduled", "status", "scheduled_for"),
        Index("idx_jobs_cleanup", "expires_at"),
    )

    def mark_running(self, now: Optional[datetime] = None) -> None:
        """Update job metadata when execution starts."""
        now = now or datetime.utcnow()
        self.status = JobStatus.RUNNING.value
        self.started_at = now
        self.updated_at = now

    def mark_completed(self, result: Optional[dict] = None, now: Optional[datetime] = None) -> None:
        """Mark job as completed with optional result payload."""
        now = now or datetime.utcnow()
        self.status = JobStatus.COMPLETED.value
        self.completed_at = now
        self.updated_at = now
        if result is not None:
            self.result = result

    def mark_failed(
        self,
        message: str,
        error_code: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> None:
        """Mark job as failed with error metadata."""
        now = now or datetime.utcnow()
        self.status = JobStatus.FAILED.value
        self.error_message = message
        self.error_code = error_code
        self.completed_at = now
        self.updated_at = now

    def mark_canceled(self, message: str = "Canceled by user", now: Optional[datetime] = None) -> None:
        """Mark job as canceled."""
        now = now or datetime.utcnow()
        self.status = JobStatus.CANCELED.value
        self.message = message
        self.completed_at = now
        self.updated_at = now


class JobAttempt(Base, TimestampMixin):
    """Execution attempt record for a job."""

    __tablename__ = "job_attempts"

    id = Column(String(36), primary_key=True, default=_uuid)
    job_id = Column(String(36), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    attempt_number = Column(Integer, nullable=False)
    status = Column(String(20), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=False)
    completed_at = Column(DateTime(timezone=True))
    worker_id = Column(String(100))
    error_message = Column(Text)
    error_code = Column(String(50))

    job = relationship("Job", back_populates="attempts", lazy="selectin")

    __table_args__ = (
        UniqueConstraint("job_id", "attempt_number", name="uq_job_attempt_number"),
        Index("idx_job_attempt_job", "job_id"),
    )


class JobProgressEvent(Base):
    """Discrete progress events emitted while a job executes."""

    __tablename__ = "job_progress_events"

    id = Column(String(36), primary_key=True, default=_uuid)
    job_id = Column(String(36), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    event_type = Column(String(50), nullable=False)
    timestamp = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    data = Column(JSON, nullable=False)
    sequence_number = Column(Integer, nullable=False)

    job = relationship("Job", back_populates="progress_events", lazy="selectin")

    __table_args__ = (
        UniqueConstraint("job_id", "sequence_number", name="uq_job_progress_sequence"),
        Index("idx_job_progress_events_job_sequence", "job_id", "sequence_number"),
        Index("idx_job_progress_events_timestamp", "timestamp"),
    )


class JobDependency(Base, TimestampMixin):
    """Dependency relationship between jobs."""

    __tablename__ = "job_dependencies"

    id = Column(String(36), primary_key=True, default=_uuid)
    job_id = Column(String(36), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    depends_on_job_id = Column(String(36), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    dependency_type = Column(String(20), nullable=False, default="success")

    job = relationship(
        "Job",
        foreign_keys=[job_id],
        back_populates="dependencies",
        lazy="selectin",
    )
    depends_on_job = relationship(
        "Job",
        foreign_keys=[depends_on_job_id],
        back_populates="dependents",
        lazy="selectin",
    )

    __table_args__ = (
        UniqueConstraint("job_id", "depends_on_job_id", name="uq_job_dependency"),
        Index("idx_job_deps_depends_on", "depends_on_job_id"),
        Index("idx_job_deps_job", "job_id"),
    )


class JobSubscription(Base, TimestampMixin):
    """Subscriptions for job progress notifications."""

    __tablename__ = "job_subscriptions"

    id = Column(String(36), primary_key=True, default=_uuid)
    job_id = Column(String(36), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    subscriber_id = Column(String(100), nullable=False)
    subscription_type = Column(String(20), nullable=False)
    expires_at = Column(DateTime(timezone=True))

    job = relationship("Job", back_populates="subscriptions", lazy="selectin")

    __table_args__ = (
        UniqueConstraint("job_id", "subscriber_id", "subscription_type", name="uq_job_subscription"),
        Index("idx_job_subscriptions_job", "job_id"),
    )


class JobTypeDefinition(Base, TimestampMixin):
    """Registry of supported job types and their handlers."""

    __tablename__ = "job_type_definitions"

    job_type = Column(String(50), primary_key=True)
    name = Column(String(100), nullable=False)
    description = Column(Text)
    handler_class = Column(String(200), nullable=False)
    default_config = Column(JSON)
    payload_schema = Column(JSON)
    required_permissions = Column(JSON)
    sandbox_profile = Column(String(50))

    worker_capabilities = relationship(
        "WorkerCapability",
        back_populates="job_type_definition",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    jobs = relationship("Job", back_populates="job_type_definition", lazy="selectin")


class WorkerCapability(Base, TimestampMixin):
    """Capabilities advertised by worker runtimes."""

    __tablename__ = "worker_capabilities"

    id = Column(String(36), primary_key=True, default=_uuid)
    worker_id = Column(String(100), nullable=False)
    job_type = Column(String(50), ForeignKey("job_type_definitions.job_type", ondelete="CASCADE"), nullable=False)
    max_concurrent = Column(Integer, nullable=False, default=1)
    sandbox_profiles = Column(JSON)
    metadata_json = Column("metadata", JSON)
    last_heartbeat = Column(DateTime(timezone=True))
    status = Column(String(20), nullable=False, default="active")

    job_type_definition = relationship("JobTypeDefinition", back_populates="worker_capabilities", lazy="selectin")

    __table_args__ = (
        UniqueConstraint("worker_id", "job_type", name="uq_worker_job_type"),
        Index("idx_worker_capabilities_job_type", "job_type"),
    )
