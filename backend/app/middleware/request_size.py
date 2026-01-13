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
    
    async def dispatch(self, request: Request, call_next):
        """
        Check request size before processing.
        
        Args:
            request: Incoming request
            call_next: Next middleware/handler in chain
            
        Returns:
            Response from handler or 413 error
        """
        # Skip check for excluded paths
        request_path = request.url.path
        for excluded in self.excluded_paths:
            if request_path.startswith(excluded):
                return await call_next(request)
        
        # Check Content-Length header
        content_length = request.headers.get("Content-Length")
        
        if content_length:
            try:
                size = int(content_length)
                
                if size > self.max_size:
                    size_mb = size / (1024 * 1024)
                    limit_mb = self.max_size / (1024 * 1024)
                    
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
                # Invalid Content-Length header - let it through, will fail downstream
                pass
        
        # Request is within size limit, continue processing
        return await call_next(request)

