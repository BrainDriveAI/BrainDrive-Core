"""
Request size enforcement middleware.

Rejects oversized request bodies early (HTTP 413) to protect memory/CPU.
"""
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from typing import Set
import structlog

logger = structlog.get_logger()


class RequestSizeMiddleware(BaseHTTPMiddleware):
    """
    Middleware to enforce maximum request body size.
    
    Rejects requests with Content-Length exceeding the limit before
    reading the body, protecting against memory exhaustion attacks.
    """
    
    def __init__(
        self,
        app,
        max_size: int = 5 * 1024 * 1024,  # 5MB default for JSON
        excluded_paths: Set[str] = None
    ):
        """
        Initialize request size middleware.
        
        Args:
            app: FastAPI application
            max_size: Maximum request body size in bytes (default 5MB)
            excluded_paths: Set of path prefixes to exclude from size checks
                           (e.g., file upload endpoints with their own limits)
        """
        super().__init__(app)
        self.max_size = max_size
        self.excluded_paths = excluded_paths or {
            "/api/v1/documents/process",  # Has its own 10MB file limit
            "/api/v1/documents/process-multiple",  # Has its own limits
            "/api/v1/plugins/install",  # Plugin uploads
        }
    
    # Hard cap for excluded paths (50 MB)
    EXCLUDED_HARD_CAP = 50 * 1024 * 1024

    async def dispatch(self, request: Request, call_next):
        """
        Check request size before processing.

        Args:
            request: Incoming request
            call_next: Next middleware/handler in chain

        Returns:
            Response from handler or 413 error
        """
        request_path = request.url.path
        content_length = request.headers.get("Content-Length")

        # Determine the applicable limit
        is_excluded = any(request_path.startswith(p) for p in self.excluded_paths)
        effective_limit = self.EXCLUDED_HARD_CAP if is_excluded else self.max_size

        if content_length:
            try:
                size = int(content_length)
                if size > effective_limit:
                    size_mb = size / (1024 * 1024)
                    limit_mb = effective_limit / (1024 * 1024)
                    logger.warning(
                        "Request size exceeded",
                        path=request_path,
                        size_mb=f"{size_mb:.2f}",
                        limit_mb=f"{limit_mb:.2f}",
                        client=request.client.host if request.client else "unknown"
                    )
                    return JSONResponse(
                        status_code=413,
                        content={
                            "detail": f"Request body too large. Maximum size is {limit_mb:.1f}MB, received {size_mb:.2f}MB"
                        }
                    )
            except ValueError:
                pass
        elif request.method in ("POST", "PUT", "PATCH") and not is_excluded:
            # No Content-Length header on mutating requests -- enforce limit via body read
            body = await request.body()
            if len(body) > effective_limit:
                size_mb = len(body) / (1024 * 1024)
                limit_mb = effective_limit / (1024 * 1024)
                logger.warning(
                    "Request body exceeded limit (no Content-Length)",
                    path=request_path,
                    size_mb=f"{size_mb:.2f}",
                    limit_mb=f"{limit_mb:.2f}",
                )
                return JSONResponse(
                    status_code=413,
                    content={
                        "detail": f"Request body too large. Maximum size is {limit_mb:.1f}MB"
                    }
                )

        return await call_next(request)

