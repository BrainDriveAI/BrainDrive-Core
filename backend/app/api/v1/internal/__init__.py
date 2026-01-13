"""
Internal API Endpoints

These endpoints are for service-to-service communication only.
They are NOT exposed to end users and are NOT included in OpenAPI docs.

Internal endpoints:
- Require service authentication (Bearer tokens, not user JWTs)
- Are prefixed with /_internal
- Are excluded from Swagger/OpenAPI schema
- Must not accept cookies or user authentication
"""
from fastapi import APIRouter

# Internal router - excluded from OpenAPI schema
internal_router = APIRouter(
    prefix="/_internal",
    include_in_schema=False,  # Hide from Swagger/OpenAPI docs
    tags=["internal"]
)

# Import internal endpoint modules
from app.api.v1.internal import health, jobs, plugins

# Register internal routers
internal_router.include_router(health.router, tags=["internal-health"])
internal_router.include_router(jobs.router, tags=["internal-jobs"])
internal_router.include_router(plugins.router, tags=["internal-plugins"])

__all__ = ["internal_router"]

