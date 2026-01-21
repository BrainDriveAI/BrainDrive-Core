"""
Admin API Router.

Provides administrative endpoints for system management.
All endpoints require admin authentication.
"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
import structlog

from app.core.database import get_db
from app.core.auth_deps import require_admin
from app.core.auth_context import AuthContext
from app.core.audit import audit_logger, AuditEventType
from app.plugins.route_loader import get_plugin_loader

logger = structlog.get_logger()

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/plugins/reload-routes")
async def reload_plugin_routes(
    request: Request,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_admin),
):
    """
    Reload all backend plugin routes.

    This endpoint triggers a full reload of all enabled backend plugin routes.
    It uses an atomic swap pattern to minimize disruption to in-flight requests.

    Requires admin authentication.

    Returns:
        JSON object with:
        - success: bool - True if reload completed without errors
        - loaded: list[str] - Plugin slugs that were successfully loaded
        - unloaded: list[str] - Plugin slugs that were unloaded
        - errors: list[dict] - Error details for any failed plugins
        - total_routes: int - Total number of routes loaded
    """
    logger.info(
        "Admin route reload requested",
        user_id=auth.user_id,
        username=auth.username,
    )

    # Get the plugin route loader
    loader = get_plugin_loader()

    # Perform the reload
    result = await loader.reload_routes(db)

    # Log the result
    if result.errors:
        logger.warning(
            "Plugin route reload completed with errors",
            user_id=auth.user_id,
            loaded=result.loaded,
            unloaded=result.unloaded,
            error_count=len(result.errors),
            total_routes=result.total_routes,
        )
    else:
        logger.info(
            "Plugin route reload completed successfully",
            user_id=auth.user_id,
            loaded=result.loaded,
            unloaded=result.unloaded,
            total_routes=result.total_routes,
        )

    # Audit log the reload
    await audit_logger.log_admin_action(
        request=request,
        user_id=auth.user_id,
        event_type=AuditEventType.ADMIN_PLUGIN_ROUTES_RELOADED,
        resource_type="plugin_routes",
        metadata={
            "loaded_count": len(result.loaded),
            "unloaded_count": len(result.unloaded),
            "error_count": len(result.errors),
            "total_routes": result.total_routes,
            "loaded_plugins": result.loaded,
        },
    )

    return result.to_dict()


@router.get("/plugins/routes")
async def get_active_plugin_routes(
    auth: AuthContext = Depends(require_admin),
):
    """
    Get information about currently active plugin routes.

    Returns a list of plugin slugs that have routes currently mounted.

    Requires admin authentication.

    Returns:
        JSON object with:
        - plugins: list[str] - Slugs of plugins with active routes
    """
    loader = get_plugin_loader()
    active_plugins = loader.get_active_plugins()

    return {
        "plugins": active_plugins,
    }
