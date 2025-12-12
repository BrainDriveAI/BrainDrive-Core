import asyncio
from typing import Optional

from app.core.database import db_factory
from app.services.job_manager import JobManager, SleepJobHandler
from app.services.job_handlers import OllamaInstallHandler
from app.services.job_handlers.service_install import ServiceInstallHandler

job_manager: Optional[JobManager] = None
_handlers_registered = False
_initialization_lock = asyncio.Lock()


async def initialize_job_manager() -> None:
    """Initialize and start the job manager singleton."""
    async with _initialization_lock:
        await _ensure_job_manager()


async def shutdown_job_manager() -> None:
    """Shutdown the job manager if it has been started."""
    if job_manager:
        await job_manager.shutdown()


async def get_job_manager() -> JobManager:
    async with _initialization_lock:
        await _ensure_job_manager()
        assert job_manager is not None
        return job_manager


async def _ensure_job_manager() -> None:
    global job_manager, _handlers_registered
    if job_manager is None:
        job_manager = JobManager(db_factory.session_factory)
    if not _handlers_registered:
        await job_manager.register_handler(SleepJobHandler())
        await job_manager.register_handler(OllamaInstallHandler())
        await job_manager.register_handler(ServiceInstallHandler())
        _handlers_registered = True
    await job_manager.start()
