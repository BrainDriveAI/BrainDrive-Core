from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from app.api.v1.api import api_router
from app.core.config import settings
from app.routers.plugins import plugin_manager
from app.plugins.service_installler.start_stop_plugin_services import start_plugin_services_on_startup, stop_all_plugin_services_on_shutdown
from app.core.job_manager_provider import initialize_job_manager, shutdown_job_manager
from app.middleware.request_size import RequestSizeMiddleware
import logging
import time
import structlog

app = FastAPI(title=settings.APP_NAME)

# Configure CORS using settings from environment
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
    allow_methods=settings.cors_methods_list,
    allow_headers=settings.cors_headers_list,
    expose_headers=settings.cors_expose_headers_list,
    max_age=settings.CORS_MAX_AGE,
)

app.add_middleware(
    RequestSizeMiddleware,
    max_size=settings.MAX_REQUEST_SIZE
)

# Add startup event to initialize settings
@app.on_event("startup")
async def startup_event():
    """Initialize required settings on application startup."""
    logger.info("Initializing application settings...")
    from app.init_settings import init_ollama_settings
    await init_ollama_settings()
    await initialize_job_manager()
    # Start plugin services
    await start_plugin_services_on_startup()
    logger.info("Settings initialization completed")


# Add shutdown event to gracefully stop services
@app.on_event("shutdown")
async def shutdown_event():
    """Gracefully stop all plugin services on application shutdown."""
    logger.info("Shutting down application and stopping plugin services...")
    # Stop all plugin services gracefully
    await stop_all_plugin_services_on_shutdown()
    await shutdown_job_manager()
    logger.info("Application shutdown completed.")


# Add middleware to log all requests
logger = structlog.get_logger()

@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log all incoming requests."""
    start_time = time.time()
    
    # Log the request with full details
    logger.info(
        "Request received",
        method=request.method,
        url=str(request.url),
        path=request.url.path,
        query_params=str(request.query_params),
        client=request.client.host if request.client else None,
        headers=dict(request.headers),
    )
    
    try:
        # Process the request
        response = await call_next(request)
        
        # Calculate processing time
        process_time = time.time() - start_time
        
        # Log the response with full details
        logger.info(
            "Request completed",
            method=request.method,
            url=str(request.url),
            path=request.url.path,
            status_code=response.status_code,
            process_time_ms=round(process_time * 1000, 2),
        )
        
        return response
    except Exception as e:
        # Log any exceptions
        logger.error(
            "Request failed",
            method=request.method,
            url=str(request.url),
            path=request.url.path,
            error=str(e),
            exception_type=type(e).__name__,
        )
        raise

# Add exception handler for validation errors
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Handle validation errors."""
    import logging
    logger = logging.getLogger(__name__)
    logger.error(f"Validation error: {exc.errors()}")
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors()},
    )

# Include API routers
app.include_router(api_router)
