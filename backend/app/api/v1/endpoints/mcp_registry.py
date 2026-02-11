"""MCP registry and tool sync endpoints."""

from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth_context import AuthContext
from app.core.auth_deps import require_user
from app.core.database import get_db
from app.services.mcp_registry_service import MCPRegistryService


router = APIRouter(prefix="/mcp", tags=["mcp"])


@router.post("/sync")
async def sync_mcp_tools(
    plugin_slug: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user),
) -> dict[str, Any]:
    service = MCPRegistryService(db)
    summary = await service.sync_user_servers(auth.user_id, plugin_slug_filter=plugin_slug)
    return {"success": True, "data": summary}


@router.get("/servers")
async def list_mcp_servers(
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user),
) -> dict[str, Any]:
    service = MCPRegistryService(db)
    servers = await service.list_servers(auth.user_id)
    return {"success": True, "data": {"servers": [server.to_dict() for server in servers]}}


@router.get("/tools")
async def list_mcp_tools(
    enabled_only: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user),
) -> dict[str, Any]:
    service = MCPRegistryService(db)
    tools = await service.list_tools(auth.user_id, enabled_only=enabled_only)
    return {"success": True, "data": {"tools": [tool.to_dict() for tool in tools]}}

