from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.api import api_router
from app.core.config import settings
from app.core.database import db_factory
from app.core.job_manager_provider import (
    initialize_job_manager,
    shutdown_job_manager,
)
from app.plugins.route_loader import get_plugin_loader
from app.plugins.service_installler.start_stop_plugin_services import (
    start_plugin_services_on_startup,
    stop_all_plugin_services_on_shutdown,
)
from app.routers.plugins import initialize_plugin_manager_on_startup
from app.middleware.request_size import RequestSizeMiddleware
import logging
import time
import structlog

# Configure structlog: JSON in production, console in dev
if settings.APP_ENV.lower() != "dev":
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
    )
else:
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
    )

logger = structlog.get_logger()


def _validate_production_config() -> None:
    """Validate configuration is safe for production deployment."""
    if settings.APP_ENV.lower() == "dev":
        return

    known_defaults = {"your-secret-key-here", "change-me", "secret", ""}
    if settings.SECRET_KEY in known_defaults:
        message = (
            "SECRET_KEY is not configured for production. "
            "Set a strong, unique SECRET_KEY environment variable before deploying."
        )
        logger.critical(message)
        raise RuntimeError(message)

    key = settings.ENCRYPTION_MASTER_KEY.strip() if settings.ENCRYPTION_MASTER_KEY else ""
    known_placeholder_keys = {"your-encryption-master-key-here", "change-me", "encryption-key", ""}
    if key.lower() in known_placeholder_keys:
        message = (
            "ENCRYPTION_MASTER_KEY is not configured for production. "
            "Set a strong, unique ENCRYPTION_MASTER_KEY (32+ chars) before deploying."
        )
        logger.critical(message)
        raise RuntimeError(message)


_validate_production_config()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize services on startup and stop them gracefully on shutdown."""
    logger.info("Initializing application settings...")
    from app.init_settings import init_ollama_settings

    await init_ollama_settings()
    await initialize_job_manager()
    await initialize_plugin_manager_on_startup()
    await start_plugin_services_on_startup()

    # Load plugin-owned API routes on startup.
    plugin_loader = get_plugin_loader()
    plugin_loader.set_app(app)
    async with db_factory.session_factory() as session:
        try:
            await plugin_loader.reload_routes(session)
        except Exception as loader_error:
            logger.warning(
                "Plugin endpoint route reload failed during startup",
                error=str(loader_error),
            )

    logger.info("Settings initialization completed")
    try:
        yield
    finally:
        logger.info(
            "Shutting down application and stopping plugin services..."
        )
        await stop_all_plugin_services_on_shutdown()
        await shutdown_job_manager()
        logger.info("Application shutdown completed.")


app = FastAPI(
    title=settings.APP_NAME,
    lifespan=lifespan,
    docs_url="/api/v1/docs" if settings.ENABLE_API_DOCS else None,
    redoc_url="/api/v1/redoc" if settings.ENABLE_API_DOCS else None,
    openapi_url="/api/v1/openapi.json" if settings.ENABLE_API_DOCS else None,
)

# Configure CORS using settings from environment
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
    allow_methods=settings.CORS_METHODS,
    allow_headers=settings.CORS_HEADERS,
    expose_headers=settings.CORS_EXPOSE_HEADERS,
    max_age=settings.CORS_MAX_AGE,
)

# Request size enforcement
app.add_middleware(
    RequestSizeMiddleware,
    max_size=settings.MAX_REQUEST_SIZE,
)

# Add middleware to log all requests
@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log all incoming requests (headers redacted)."""
    start_time = time.time()

    logger.info(
        "Request received",
        method=request.method,
        path=request.url.path,
        client=request.client.host if request.client else None,
    )

    try:
        response = await call_next(request)
        process_time = time.time() - start_time

        logger.info(
            "Request completed",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            process_time_ms=round(process_time * 1000, 2),
        )

        return response
    except Exception as e:
        logger.error(
            "Request failed",
            method=request.method,
            path=request.url.path,
            error=str(e),
            exception_type=type(e).__name__,
        )
        raise

# Add exception handler for validation errors
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Handle validation errors (body content redacted)."""
    import logging
    _logger = logging.getLogger(__name__)
    _logger.warning("Validation error on %s %s", request.method, request.url.path)
    # Return error locations/types without echoing back the raw body
    safe_errors = [
        {"loc": e.get("loc"), "msg": e.get("msg"), "type": e.get("type")}
        for e in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={"detail": safe_errors},
    )

# Include API routers
app.include_router(api_router)
