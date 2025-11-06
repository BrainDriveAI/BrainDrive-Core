import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional, Sequence, Tuple

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker

from app.models.job import Job, JobAttempt, JobDependency, JobProgressEvent, JobStatus, JobTypeDefinition

logger = logging.getLogger(__name__)

TERMINAL_STATES: Sequence[str] = (
    JobStatus.COMPLETED.value,
    JobStatus.FAILED.value,
    JobStatus.CANCELED.value,
)


class JobCanceledError(Exception):
    """Raised when a job is canceled during execution."""


class HandlerRegistrationError(Exception):
    """Raised when attempting to register an invalid handler."""


@dataclass
class JobRuntimeState:
    cancel_event: asyncio.Event
    handler_name: str


class BaseJobHandler:
    """Abstract base class for background job handlers."""

    job_type: str = "base"
    display_name: str = "Base Job"
    description: str = ""
    default_sandbox_profile: str = "standard"
    default_config: Optional[Dict[str, Any]] = None
    payload_schema: Optional[Dict[str, Any]] = None
    required_permissions: Optional[List[str]] = None

    async def validate_payload(self, payload: Dict[str, Any]) -> None:
        """Validate the payload before job creation."""
        return None

    async def execute(
        self,
        context: "JobExecutionContext",
    ) -> Dict[str, Any]:
        """Execute the job and return the result payload."""
        raise NotImplementedError("Handlers must implement execute()")

    async def cleanup(self, context: "JobExecutionContext") -> None:
        """Cleanup resources after job completes or fails."""
        return None

    @property
    def handler_class_path(self) -> str:
        """Return module-qualified handler path."""
        return f"{self.__class__.__module__}.{self.__class__.__name__}"


class JobExecutionContext:
    """Execution context provided to job handlers."""

    def __init__(
        self,
        manager: "JobManager",
        job_id: str,
        payload: Dict[str, Any],
        user_id: str,
        workspace_id: Optional[str],
        cancel_event: asyncio.Event,
    ):
        self._manager = manager
        self.job_id = job_id
        self.payload = payload
        self.user_id = user_id
        self.workspace_id = workspace_id
        self._cancel_event = cancel_event

    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    async def check_for_cancel(self) -> None:
        if self.is_cancelled():
            raise JobCanceledError("Job canceled by user request")

    async def report_progress(
        self,
        *,
        percent: Optional[int] = None,
        stage: Optional[str] = None,
        message: Optional[str] = None,
        event_type: str = "progress",
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Report progress or other runtime events."""
        payload = dict(data or {})
        if percent is not None:
            payload.setdefault("progress_percent", percent)
        if stage is not None:
            payload.setdefault("stage", stage)
        if message is not None:
            payload.setdefault("message", message)
        await self._manager.record_progress_event(
            job_id=self.job_id,
            event_type=event_type,
            percent=percent,
            stage=stage,
            message=message,
            data=payload,
        )

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Provide a managed database session for handler use."""
        async with self._manager.session() as session:
            yield session


class JobManager:
    """Coordinated background job manager with in-process workers."""

    def __init__(
        self,
        session_factory: sessionmaker,
        *,
        poll_interval: float = 1.0,
    ) -> None:
        self._session_factory = session_factory
        self._poll_interval = poll_interval
        self._handlers: Dict[str, BaseJobHandler] = {}
        self._worker_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._active_jobs: Dict[str, JobRuntimeState] = {}
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Yield a managed async session."""
        session: AsyncSession = self._session_factory()
        try:
            yield session
        finally:
            await session.close()

    async def start(self) -> None:
        """Start the background worker loop."""
        async with self._lock:
            if self._worker_task and not self._worker_task.done():
                return
            await self._recover_stale_jobs()
            logger.info("Starting job manager worker loop")
            self._stop_event.clear()
            self._worker_task = asyncio.create_task(self._worker_loop(), name="job-manager-worker")

    async def shutdown(self) -> None:
        """Stop the background worker loop."""
        async with self._lock:
            if not self._worker_task:
                return
            logger.info("Stopping job manager worker loop")
            self._stop_event.set()
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            finally:
                self._worker_task = None

    async def _recover_stale_jobs(self) -> None:
        """Mark running jobs from previous sessions as failed so they can be retried."""
        now = datetime.now(timezone.utc)
        async with self.session() as session:
            result = await session.execute(
                sa.select(Job).where(Job.status == JobStatus.RUNNING.value)
            )
            stale_jobs: List[Job] = result.scalars().all()
            if not stale_jobs:
                return

            logger.warning("Recovering %d stale running jobs", len(stale_jobs))
            for job in stale_jobs:
                job.status = JobStatus.FAILED.value
                job.error_message = job.error_message or "Job interrupted during restart; please retry"
                job.completed_at = now
                job.updated_at = now
                job.message = job.message or "Job interrupted"
                job.retry_count = job.retry_count or 0

            await session.execute(
                sa.update(JobAttempt)
                .where(JobAttempt.job_id.in_([job.id for job in stale_jobs]), JobAttempt.status == JobStatus.RUNNING.value)
                .values(status=JobStatus.FAILED.value, completed_at=now, updated_at=now, error_message="Worker interrupted")
            )

            await session.commit()

    async def register_handler(self, handler: BaseJobHandler) -> None:
        """Register a job handler for a job type."""
        if not handler.job_type or handler.job_type in ("", "base"):
            raise HandlerRegistrationError("Handlers must define a non-empty job_type")
        self._handlers[handler.job_type] = handler
        await self._persist_job_type_definition(handler)
        logger.info("Registered job handler %s", handler.job_type)

    async def _persist_job_type_definition(self, handler: BaseJobHandler) -> None:
        async with self.session() as session:
            existing = await session.get(JobTypeDefinition, handler.job_type)
            if existing:
                existing.name = handler.display_name
                existing.description = handler.description
                existing.handler_class = handler.handler_class_path
                existing.default_config = handler.default_config
                existing.payload_schema = handler.payload_schema
                existing.required_permissions = handler.required_permissions
                existing.sandbox_profile = handler.default_sandbox_profile
            else:
                session.add(
                    JobTypeDefinition(
                        job_type=handler.job_type,
                        name=handler.display_name,
                        description=handler.description,
                        handler_class=handler.handler_class_path,
                        default_config=handler.default_config,
                        payload_schema=handler.payload_schema,
                        required_permissions=handler.required_permissions,
                        sandbox_profile=handler.default_sandbox_profile,
                    )
                )
            await session.commit()

    async def enqueue_job(
        self,
        *,
        job_type: str,
        payload: Dict[str, Any],
        user_id: str,
        workspace_id: Optional[str] = None,
        priority: int = 0,
        scheduled_for: Optional[datetime] = None,
        idempotency_key: Optional[str] = None,
        max_retries: int = 3,
        depends_on: Optional[Sequence[str]] = None,
    ) -> tuple[Job, bool]:
        """Persist a job and return the record."""
        if job_type not in self._handlers:
            raise HandlerRegistrationError(f"No handler registered for job_type={job_type}")

        handler = self._handlers[job_type]
        await handler.validate_payload(payload)

        scheduled_at = scheduled_for or datetime.now(timezone.utc)
        async with self.session() as session:
            existing: Optional[Job] = None
            created = False
            if idempotency_key:
                result = await session.execute(
                    sa.select(Job).where(
                        Job.idempotency_key == idempotency_key,
                        Job.user_id == user_id,
                        Job.job_type == job_type,
                    )
                )
                existing = result.scalars().first()
                if existing:
                    if existing.status not in TERMINAL_STATES:
                        logger.info("Returning existing job %s for idempotency key %s", existing.id, idempotency_key)
                        session.expunge(existing)
                        return existing, False
                    if existing.status == JobStatus.COMPLETED.value:
                        logger.info("Reusing completed job %s for idempotency key %s", existing.id, idempotency_key)
                        session.expunge(existing)
                        return existing, False

                    now = scheduled_at
                    logger.info("Resetting job %s for retry via enqueue_job", existing.id)
                    existing.payload = payload
                    existing.priority = priority
                    existing.scheduled_for = scheduled_at
                    existing.status = JobStatus.QUEUED.value
                    existing.message = "Queued"
                    existing.progress_percent = 0
                    existing.current_stage = None
                    existing.result = None
                    existing.error_message = None
                    existing.error_code = None
                    existing.started_at = None
                    existing.completed_at = None
                    existing.updated_at = now
                    existing.workspace_id = workspace_id
                    existing.max_retries = max_retries
                    existing.retry_count = (existing.retry_count or 0) + 1
                    await session.execute(
                        sa.update(JobAttempt)
                        .where(
                            JobAttempt.job_id == existing.id,
                            JobAttempt.status == JobStatus.RUNNING.value,
                        )
                        .values(
                            status=JobStatus.FAILED.value,
                            completed_at=now,
                            updated_at=now,
                            error_message="Superseded by retry",
                        )
                    )
                    await session.commit()
                    await session.refresh(existing)
                    session.expunge(existing)
                    created = True
                    return existing, created

            job = Job(
                job_type=job_type,
                status=JobStatus.QUEUED.value,
                priority=priority,
                payload=payload,
                scheduled_for=scheduled_at,
                user_id=user_id,
                workspace_id=workspace_id,
                idempotency_key=idempotency_key,
                max_retries=max_retries,
            )
            session.add(job)
            await session.flush()

            if depends_on:
                for depends_on_job_id in depends_on:
                    session.add(
                        JobDependency(
                            job_id=job.id,
                            depends_on_job_id=depends_on_job_id,
                            dependency_type="success",
                        )
                    )

            await session.commit()
            await session.refresh(job)
            session.expunge(job)
            created = True

        logger.info("Enqueued job %s of type %s", job.id, job_type)
        return job, created

    async def get_job(self, job_id: str) -> Optional[Job]:
        async with self.session() as session:
            job = await session.get(Job, job_id)
            if job:
                await session.refresh(job)
                session.expunge(job)
            return job

    async def list_jobs(
        self,
        *,
        user_id: str,
        status: Optional[str] = None,
        job_type: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        """Return paginated jobs for a user."""
        page = max(page, 1)
        page_size = max(min(page_size, 100), 1)
        offset = (page - 1) * page_size

        async with self.session() as session:
            query = sa.select(Job).where(Job.user_id == user_id)
            count_query = sa.select(sa.func.count()).select_from(Job).where(Job.user_id == user_id)

            if status:
                query = query.where(Job.status == status)
                count_query = count_query.where(Job.status == status)
            if job_type:
                query = query.where(Job.job_type == job_type)
                count_query = count_query.where(Job.job_type == job_type)

            query = query.order_by(Job.created_at.desc()).offset(offset).limit(page_size)

            result = await session.execute(query)
            jobs = result.scalars().all()
            for job in jobs:
                session.expunge(job)

            total_result = await session.execute(count_query)
            total = total_result.scalar_one()

        return {
            "jobs": jobs,
            "total": total,
            "page": page,
            "page_size": page_size,
            "has_next": total > offset + len(jobs),
        }

    async def cancel_job(self, job_id: str) -> bool:
        """Request cancellation of a job."""
        async with self.session() as session:
            job = await session.get(Job, job_id)
            if not job:
                return False
            if job.status in TERMINAL_STATES:
                return False

            if job.status == JobStatus.QUEUED.value:
                job.mark_canceled("Canceled before execution")
                await session.commit()
                return True

            if job.status == JobStatus.RUNNING.value:
                job.message = "Cancellation requested"
                job.updated_at = datetime.now(timezone.utc)
                await session.commit()

        state = self._active_jobs.get(job_id)
        if state:
            state.cancel_event.set()
            await self.record_progress_event(
                job_id=job_id,
                event_type="cancel_requested",
                message="Cancellation requested",
            )
            return True
        return False

    async def retry_job(self, job_id: str) -> Optional[Job]:
        """Requeue a failed or canceled job."""
        async with self.session() as session:
            job = await session.get(Job, job_id)
            if not job:
                return None
            if job.status not in (JobStatus.FAILED.value, JobStatus.CANCELED.value):
                return job

            job.status = JobStatus.QUEUED.value
            job.message = "Queued for retry"
            job.progress_percent = 0
            job.started_at = None
            job.completed_at = None
            job.error_message = None
            job.error_code = None
            job.updated_at = datetime.now(timezone.utc)
            await session.commit()
            await session.refresh(job)
            logger.info("Requeued job %s for retry", job.id)
            return job

    async def delete_job(self, job_id: str, user_id: str) -> bool:
        """Delete a terminal job and associated records."""
        async with self.session() as session:
            job = await session.get(Job, job_id)
            if not job or job.user_id != user_id:
                return False
            if job.status not in TERMINAL_STATES:
                return False
            await session.delete(job)
            await session.commit()
            return True

    async def record_progress_event(
        self,
        *,
        job_id: str,
        event_type: str,
        percent: Optional[int] = None,
        stage: Optional[str] = None,
        message: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Persist a progress event and update job fields."""
        data = data or {}
        if percent is not None and "progress_percent" not in data:
            data["progress_percent"] = percent
        if stage and "stage" not in data:
            data["stage"] = stage
        if message and "message" not in data:
            data["message"] = message
        logger.debug(
            "Recording progress",
            extra={
                "job_id": job_id,
                "event_type": event_type,
                "percent": percent,
                "stage": stage,
                "message": message,
            },
        )
        now = datetime.now(timezone.utc)
        async with self.session() as session:
            job = await session.get(Job, job_id)
            if not job:
                return

            if percent is not None:
                job.progress_percent = percent
            if stage is not None:
                job.current_stage = stage
            if message is not None:
                job.message = message
            job.updated_at = now

            result = await session.execute(
                sa.select(sa.func.max(JobProgressEvent.sequence_number)).where(JobProgressEvent.job_id == job_id)
            )
            next_sequence = (result.scalar_one_or_none() or 0) + 1

            event = JobProgressEvent(
                job_id=job_id,
                event_type=event_type,
                data=data,
                sequence_number=next_sequence,
            )
            session.add(event)
            await session.commit()

    async def get_progress_events(self, job_id: str, since: Optional[int] = None) -> List[JobProgressEvent]:
        """Return job progress events optionally filtered by sequence number."""
        async with self.session() as session:
            query = sa.select(JobProgressEvent).where(JobProgressEvent.job_id == job_id)
            if since is not None:
                query = query.where(JobProgressEvent.sequence_number > since)
            query = query.order_by(JobProgressEvent.sequence_number.asc())
            result = await session.execute(query)
            events = result.scalars().all()
            for event in events:
                session.expunge(event)
            return events

    async def _worker_loop(self) -> None:
        """Continuously fetch and execute jobs."""
        try:
            while not self._stop_event.is_set():
                job = await self._claim_next_job()
                if not job:
                    await asyncio.sleep(self._poll_interval)
                    continue
                await self._execute_job(job)
        except asyncio.CancelledError:
            logger.info("Job worker task cancelled")
            raise
        except Exception as exc:  # pragma: no cover
            logger.exception("Job worker loop crashed: %s", exc)
            raise

    async def _claim_next_job(self) -> Optional[Job]:
        """Claim the next queued job for execution."""
        now = datetime.now(timezone.utc)
        async with self.session() as session:
            result = await session.execute(
                sa.select(Job)
                .where(
                    Job.status == JobStatus.QUEUED.value,
                    Job.scheduled_for <= now,
                )
                .order_by(Job.priority.desc(), Job.created_at.asc())
                .limit(1)
            )
            job = result.scalars().first()
            if not job:
                return None

            update_result = await session.execute(
                sa.update(Job)
                .where(Job.id == job.id, Job.status == JobStatus.QUEUED.value)
                .values(
                    status=JobStatus.RUNNING.value,
                    started_at=now,
                    updated_at=now,
                    message="Starting execution",
                )
            )
            if update_result.rowcount == 0:
                await session.rollback()
                return None

            await session.commit()
            await session.refresh(job)
            session.expunge(job)
            return job

    async def _execute_job(self, job: Job) -> None:
        """Execute the claimed job using the registered handler."""
        handler = self._handlers.get(job.job_type)
        if not handler:
            logger.error("No handler registered for job type %s", job.job_type)
            await self._mark_job_failed(job.id, "Handler not registered")
            return

        cancel_event = asyncio.Event()
        self._active_jobs[job.id] = JobRuntimeState(cancel_event=cancel_event, handler_name=handler.job_type)

        attempt_number = await self._create_attempt(job.id)
        context = JobExecutionContext(
            manager=self,
            job_id=job.id,
            payload=job.payload or {},
            user_id=job.user_id,
            workspace_id=job.workspace_id,
            cancel_event=cancel_event,
        )

        try:
            await self.record_progress_event(
                job_id=job.id,
                event_type="stage",
                stage="started",
                message="Job started",
                data={"attempt_number": attempt_number},
            )
            result_payload = await handler.execute(context)
        except JobCanceledError:
            logger.info("Job %s canceled during execution", job.id)
            await self._complete_attempt(
                job_id=job.id,
                attempt_number=attempt_number,
                status=JobStatus.CANCELED.value,
            )
            await self._mark_job_canceled(job.id)
        except Exception as exc:  # pragma: no cover - execution failure path
            logger.exception("Job %s failed: %s", job.id, exc)
            await self._complete_attempt(
                job_id=job.id,
                attempt_number=attempt_number,
                status=JobStatus.FAILED.value,
                error_message=str(exc),
            )
            await self._mark_job_failed(job.id, str(exc))
        else:
            await handler.cleanup(context)
            await self._complete_attempt(
                job_id=job.id,
                attempt_number=attempt_number,
                status=JobStatus.COMPLETED.value,
            )
            await self._mark_job_completed(job.id, result_payload)
        finally:
            self._active_jobs.pop(job.id, None)

    async def _create_attempt(self, job_id: str) -> int:
        now = datetime.now(timezone.utc)
        async with self.session() as session:
            job = await session.get(Job, job_id)
            if not job:
                return 1
            result = await session.execute(
                sa.select(sa.func.max(JobAttempt.attempt_number)).where(JobAttempt.job_id == job_id)
            )
            max_attempt = result.scalar_one_or_none() or 0
            attempt_number = max_attempt + 1
            job.retry_count = max(job.retry_count or 0, max_attempt)
            attempt = JobAttempt(
                job_id=job_id,
                attempt_number=attempt_number,
                status=JobStatus.RUNNING.value,
                started_at=now,
            )
            session.add(attempt)
            job.message = f"Running attempt {attempt_number}"
            job.updated_at = now
            await session.commit()
            return attempt_number

    async def _complete_attempt(
        self,
        *,
        job_id: str,
        attempt_number: int,
        status: str,
        error_message: Optional[str] = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        async with self.session() as session:
            result = await session.execute(
                sa.select(JobAttempt).where(
                    JobAttempt.job_id == job_id,
                    JobAttempt.attempt_number == attempt_number,
                )
            )
            attempt = result.scalars().first()
            if not attempt:
                return
            attempt.status = status
            attempt.completed_at = now
            attempt.updated_at = now
            attempt.error_message = error_message
            await session.commit()

    async def _mark_job_completed(self, job_id: str, result_payload: Optional[Dict[str, Any]]) -> None:
        now = datetime.now(timezone.utc)
        async with self.session() as session:
            job = await session.get(Job, job_id)
            if not job:
                return
            job.status = JobStatus.COMPLETED.value
            job.result = result_payload or {}
            job.message = "Completed successfully"
            job.progress_percent = 100
            job.completed_at = now
            job.updated_at = now
            await session.commit()

    async def _mark_job_failed(self, job_id: str, error_message: str) -> None:
        now = datetime.now(timezone.utc)
        async with self.session() as session:
            job = await session.get(Job, job_id)
            if not job:
                return
            job.status = JobStatus.FAILED.value
            job.error_message = error_message
            job.retry_count = (job.retry_count or 0) + 1
            job.completed_at = now
            job.updated_at = now
            await session.commit()

    async def _mark_job_canceled(self, job_id: str) -> None:
        now = datetime.now(timezone.utc)
        async with self.session() as session:
            job = await session.get(Job, job_id)
            if not job:
                return
            job.status = JobStatus.CANCELED.value
            job.completed_at = now
            job.updated_at = now
            if job.message is None:
                job.message = "Canceled"
            await session.commit()


class SleepJobHandler(BaseJobHandler):
    """Simple handler useful for testing the job pipeline."""

    job_type = "system.sleep"
    display_name = "Sleep Job"
    description = "Sleeps for a requested number of seconds, emitting progress updates."

    async def execute(self, context: JobExecutionContext) -> Dict[str, Any]:
        seconds = max(int(context.payload.get("seconds", 1)), 0)
        if seconds == 0:
            await context.report_progress(
                percent=100, stage="completed", message="No sleep requested", data={"slept_seconds": 0}
            )
            return {"slept_seconds": 0}

        for i in range(seconds):
            await context.check_for_cancel()
            await asyncio.sleep(1)
            percent = int(((i + 1) / seconds) * 100)
            await context.report_progress(
                percent=percent,
                stage="sleeping",
                message=f"Slept {i + 1} of {seconds} seconds",
                data={"slept_seconds": i + 1},
            )
        return {"slept_seconds": seconds}
