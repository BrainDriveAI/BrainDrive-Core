"""
Request ID middleware for request correlation and tracing.

Generates or propagates a unique request ID for each request,
enabling correlation of logs and audit events across services.
"""
import uuid
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
import structlog

logger = structlog.get_logger()

# Header name for request ID (standard convention)
REQUEST_ID_HEADER = "X-Request-ID"


class RequestIdMiddleware(BaseHTTPMiddleware):
    """
    Middleware that ensures every request has a unique request ID.
    
    Behavior:
    - If X-Request-ID header is present, use that value
    - Otherwise, generate a new UUID
    - Store the ID on request.state.request_id
    - Return the ID in the X-Request-ID response header
    """
    
    async def dispatch(self, request: Request, call_next) -> Response:
        """
        Process request and add request ID.
        
        Args:
            request: Incoming request
            call_next: Next middleware/handler in chain
            
        Returns:
            Response with X-Request-ID header
        """
        # Get existing request ID or generate new one
        request_id = request.headers.get(REQUEST_ID_HEADER)
        
        if not request_id:
            request_id = str(uuid.uuid4())
        
        # Validate request ID format (prevent injection attacks)
        # Allow UUIDs and alphanumeric strings up to 64 chars
        if len(request_id) > 64 or not self._is_valid_request_id(request_id):
            request_id = str(uuid.uuid4())
        
        # Store on request state for access in endpoints and audit logger
        request.state.request_id = request_id
        
        # Process the request
        response = await call_next(request)
        
        # Add request ID to response headers
        response.headers[REQUEST_ID_HEADER] = request_id
        
        return response
    
    @staticmethod
    def _is_valid_request_id(request_id: str) -> bool:
        """
        Validate request ID format.
        
        Args:
            request_id: The request ID to validate
            
        Returns:
            True if valid, False otherwise
        """
        # Allow alphanumeric, hyphens, and underscores
        return all(c.isalnum() or c in '-_' for c in request_id)


def get_request_id(request: Request) -> str:
    """
    Helper to get request ID from request state.
    
    Args:
        request: The FastAPI request object
        
    Returns:
        The request ID string, or "unknown" if not set
    """
    return getattr(request.state, 'request_id', 'unknown')
