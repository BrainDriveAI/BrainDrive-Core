from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, UTC
from time import perf_counter
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models.mcp import MCPServerRegistry, MCPToolRegistry
from app.models.plugin import PluginServiceRuntime

try:
    from jsonschema import Draft7Validator
except Exception:  # pragma: no cover - fallback path if jsonschema is unavailable
    Draft7Validator = None

logger = logging.getLogger(__name__)

READ_ONLY_PREFIXES = (
    "get",
    "list",
    "read",
    "search",
    "preview",
    "project_exists",
    "digest",
    "summarize",
)

MUTATING_PREFIXES = (
    "create",
    "write",
    "edit",
    "delete",
    "move",
    "copy",
    "rename",
    "update",
    "set",
    "append",
    "prepend",
    "complete",
    "reopen",
)


def _normalize_user_id(user_id: str) -> str:
    return str(user_id).replace("-", "")


def derive_base_url(healthcheck_url: Optional[str]) -> Optional[str]:
    if not healthcheck_url:
        return None
    parsed = urlparse(healthcheck_url.strip())
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def normalize_tools_payload(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, dict):
        if isinstance(payload.get("data"), dict) and isinstance(payload["data"].get("tools"), list):
            tools = payload["data"]["tools"]
        elif isinstance(payload.get("tools"), list):
            tools = payload["tools"]
        else:
            tools = []
    elif isinstance(payload, list):
        tools = payload
    else:
        tools = []

    normalized: List[Dict[str, Any]] = []
    for entry in tools:
        if not isinstance(entry, dict):
            continue

        # Accept both OpenAI-style {"type":"function","function":...} and direct function shape.
        function_obj = entry.get("function") if isinstance(entry.get("function"), dict) else entry
        if not isinstance(function_obj, dict):
            continue

        name = function_obj.get("name")
        if not isinstance(name, str) or not name.strip():
            continue

        description = function_obj.get("description")
        if not isinstance(description, str):
            description = ""

        parameters = function_obj.get("parameters")
        if not isinstance(parameters, dict):
            parameters = {"type": "object", "properties": {}}

        normalized.append(
            {
                "type": "function",
                "function": {
                    "name": name.strip(),
                    "description": description,
                    "parameters": parameters,
                },
            }
        )

    return normalized


def infer_safety_class(tool_name: str) -> str:
    lowered = (tool_name or "").strip().lower()
    if not lowered:
        return "read_only"
    if lowered.startswith(MUTATING_PREFIXES):
        return "mutating"
    if lowered.startswith(READ_ONLY_PREFIXES):
        return "read_only"
    return "read_only"


def compute_tool_hash(tool_schema: Dict[str, Any]) -> str:
    canonical = json.dumps(tool_schema, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_tool_call_url(server: MCPServerRegistry, tool_name: str) -> str:
    template = (server.tool_call_url_template or "/tool:{name}").strip()
    if "{name}" not in template:
        if template.endswith("/"):
            template = f"{template}tool:{{name}}"
        else:
            template = f"{template}/tool:{{name}}"

    rendered = template.replace("{name}", tool_name)
    if rendered.startswith("http://") or rendered.startswith("https://"):
        return rendered

    return f"{server.base_url.rstrip('/')}/{rendered.lstrip('/')}"


def _validate_tool_arguments(tool_schema: Dict[str, Any], arguments: Dict[str, Any]) -> Tuple[bool, List[str]]:
    if not isinstance(arguments, dict):
        return False, ["Tool arguments must be a JSON object."]

    parameters = (
        tool_schema.get("function", {}).get("parameters")
        if isinstance(tool_schema.get("function"), dict)
        else None
    )
    if not isinstance(parameters, dict):
        return True, []

    if Draft7Validator is None:
        # Fallback to a minimal required-field check when jsonschema is unavailable.
        required = parameters.get("required") if isinstance(parameters.get("required"), list) else []
        missing = [key for key in required if key not in arguments]
        if missing:
            return False, [f"Missing required argument: {field}" for field in missing]
        return True, []

    validator = Draft7Validator(parameters)
    errors = sorted(validator.iter_errors(arguments), key=lambda err: err.path)
    if not errors:
        return True, []
    return False, [error.message for error in errors]


def _build_tool_call_headers(user_id: str, request_id: Optional[str] = None) -> Dict[str, str]:
    headers: Dict[str, str] = {
        "X-BrainDrive-User-Id": _normalize_user_id(user_id),
        "X-BrainDrive-Request-Id": request_id or str(uuid.uuid4()),
    }
    service_token = os.environ.get("BRAINDRIVE_LIBRARY_SERVICE_TOKEN", "").strip()
    if service_token:
        headers["X-BrainDrive-Service-Token"] = service_token
    return headers


class MCPRegistryService:
    def __init__(self, db: AsyncSession, tools_timeout_seconds: float = 15.0, call_timeout_seconds: float = 15.0):
        self.db = db
        self.tools_timeout_seconds = tools_timeout_seconds
        self.call_timeout_seconds = call_timeout_seconds

    async def _list_runtime_candidates(
        self,
        user_id: str,
        plugin_slug_filter: Optional[str] = None,
    ) -> List[PluginServiceRuntime]:
        normalized_user_id = _normalize_user_id(user_id)
        stmt: Select = select(PluginServiceRuntime).where(
            PluginServiceRuntime.user_id == normalized_user_id,
            PluginServiceRuntime.healthcheck_url.isnot(None),
        )
        if plugin_slug_filter:
            stmt = stmt.where(PluginServiceRuntime.plugin_slug == plugin_slug_filter)

        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def auto_register_managed_servers(
        self,
        user_id: str,
        plugin_slug_filter: Optional[str] = None,
    ) -> List[MCPServerRegistry]:
        normalized_user_id = _normalize_user_id(user_id)
        runtimes = await self._list_runtime_candidates(
            normalized_user_id, plugin_slug_filter=plugin_slug_filter
        )
        if not runtimes:
            return []

        runtime_ids = [runtime.id for runtime in runtimes]
        existing_result = await self.db.execute(
            select(MCPServerRegistry).where(
                MCPServerRegistry.user_id == normalized_user_id,
                MCPServerRegistry.runtime_id.in_(runtime_ids),
            )
        )
        existing_by_runtime = {
            server.runtime_id: server
            for server in existing_result.scalars().all()
            if server.runtime_id
        }

        registered: List[MCPServerRegistry] = []
        for runtime in runtimes:
            base_url = derive_base_url(runtime.healthcheck_url)
            if not base_url:
                continue

            server = existing_by_runtime.get(runtime.id)
            if server is None:
                server = MCPServerRegistry(
                    user_id=normalized_user_id,
                    plugin_slug=runtime.plugin_slug,
                    runtime_id=runtime.id,
                    base_url=base_url,
                    tools_url=f"{base_url}/tools",
                    healthcheck_url=runtime.healthcheck_url,
                    tool_call_url_template="/tool:{name}",
                    status="registered",
                )
                self.db.add(server)
            else:
                server.plugin_slug = runtime.plugin_slug
                server.base_url = base_url
                server.tools_url = f"{base_url}/tools"
                server.healthcheck_url = runtime.healthcheck_url
                if not server.tool_call_url_template:
                    server.tool_call_url_template = "/tool:{name}"
                if server.status == "error":
                    server.status = "registered"

            registered.append(server)

        await self.db.flush()
        return registered

    async def sync_server_tools(self, server: MCPServerRegistry) -> Dict[str, Any]:
        started_at = perf_counter()
        summary: Dict[str, Any] = {
            "server_id": server.id,
            "plugin_slug": server.plugin_slug,
            "base_url": server.base_url,
            "tools_url": server.tools_url,
            "fetched_count": 0,
            "upserted_count": 0,
            "stale_disabled_count": 0,
            "status": "error",
            "error": None,
            "duration_ms": 0,
        }

        try:
            async with httpx.AsyncClient(timeout=self.tools_timeout_seconds) as client:
                response = await client.get(server.tools_url)
                response.raise_for_status()
                payload = response.json()

            tools = normalize_tools_payload(payload)
            summary["fetched_count"] = len(tools)

            existing_result = await self.db.execute(
                select(MCPToolRegistry).where(MCPToolRegistry.server_id == server.id)
            )
            existing_tools = list(existing_result.scalars().all())
            existing_by_name = {tool.name: tool for tool in existing_tools}

            active_tool_names: set[str] = set()
            upserted_count = 0
            for tool_schema in tools:
                function = tool_schema.get("function", {})
                name = function.get("name")
                if not isinstance(name, str) or not name:
                    continue

                description = function.get("description")
                if not isinstance(description, str):
                    description = ""

                source_hash = compute_tool_hash(tool_schema)
                safety_class = infer_safety_class(name)
                version = source_hash[:12]

                record = existing_by_name.get(name)
                if record is None:
                    record = MCPToolRegistry(
                        server_id=server.id,
                        name=name,
                        description=description,
                        schema_json=tool_schema,
                        enabled=True,
                        stale=False,
                        source_hash=source_hash,
                        version=version,
                        safety_class=safety_class,
                    )
                    self.db.add(record)
                else:
                    record.description = description
                    record.schema_json = tool_schema
                    record.source_hash = source_hash
                    record.version = version
                    record.safety_class = safety_class
                    record.stale = False
                upserted_count += 1
                active_tool_names.add(name)

            stale_disabled_count = 0
            for existing in existing_tools:
                if existing.name in active_tool_names:
                    continue
                if not existing.stale or existing.enabled:
                    stale_disabled_count += 1
                existing.stale = True
                existing.enabled = False

            server.status = "healthy"
            server.last_sync_at = datetime.now(UTC)
            server.last_error = None

            summary.update(
                {
                    "upserted_count": upserted_count,
                    "stale_disabled_count": stale_disabled_count,
                    "status": "healthy",
                }
            )
        except Exception as exc:
            logger.warning(
                "MCP tools sync failed for server=%s tools_url=%s error=%s",
                server.id,
                server.tools_url,
                exc,
            )
            server.status = "error"
            server.last_error = str(exc)
            summary["error"] = str(exc)
        finally:
            summary["duration_ms"] = int((perf_counter() - started_at) * 1000)
            await self.db.flush()

        return summary

    async def sync_user_servers(
        self,
        user_id: str,
        plugin_slug_filter: Optional[str] = None,
    ) -> Dict[str, Any]:
        normalized_user_id = _normalize_user_id(user_id)

        await self.auto_register_managed_servers(
            normalized_user_id, plugin_slug_filter=plugin_slug_filter
        )

        stmt = select(MCPServerRegistry).where(MCPServerRegistry.user_id == normalized_user_id)
        if plugin_slug_filter:
            stmt = stmt.where(MCPServerRegistry.plugin_slug == plugin_slug_filter)
        result = await self.db.execute(stmt)
        servers = list(result.scalars().all())

        server_summaries: List[Dict[str, Any]] = []
        for server in servers:
            summary = await self.sync_server_tools(server)
            server_summaries.append(summary)

        await self.db.commit()

        total_tools_synced = sum(item.get("upserted_count", 0) for item in server_summaries)
        total_errors = sum(1 for item in server_summaries if item.get("status") != "healthy")
        return {
            "user_id": normalized_user_id,
            "server_count": len(servers),
            "tool_upserts": total_tools_synced,
            "error_count": total_errors,
            "servers": server_summaries,
        }

    async def list_servers(self, user_id: str) -> List[MCPServerRegistry]:
        normalized_user_id = _normalize_user_id(user_id)
        result = await self.db.execute(
            select(MCPServerRegistry).where(MCPServerRegistry.user_id == normalized_user_id)
        )
        return list(result.scalars().all())

    async def list_tools(self, user_id: str, enabled_only: bool = False) -> List[MCPToolRegistry]:
        normalized_user_id = _normalize_user_id(user_id)
        stmt = (
            select(MCPToolRegistry)
            .join(MCPServerRegistry, MCPServerRegistry.id == MCPToolRegistry.server_id)
            .where(MCPServerRegistry.user_id == normalized_user_id)
        )
        if enabled_only:
            stmt = stmt.where(MCPToolRegistry.enabled.is_(True), MCPToolRegistry.stale.is_(False))
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def resolve_tools_for_request(
        self,
        user_id: str,
        *,
        mcp_tools_enabled: bool,
        mcp_scope_mode: str,
        mcp_project_slug: Optional[str],
        plugin_slug: Optional[str] = None,
        max_tools: int = 32,
        max_schema_bytes: int = 128_000,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        scope_mode = (mcp_scope_mode or "none").strip().lower()
        if not mcp_tools_enabled or scope_mode != "project" or not mcp_project_slug:
            return [], {
                "enabled": False,
                "scope_mode": scope_mode,
                "project_slug": mcp_project_slug,
                "available_count": 0,
                "selected_count": 0,
                "reason": "scope_disabled",
            }

        normalized_user_id = _normalize_user_id(user_id)
        stmt = (
            select(MCPToolRegistry)
            .join(MCPServerRegistry, MCPServerRegistry.id == MCPToolRegistry.server_id)
            .where(
                MCPServerRegistry.user_id == normalized_user_id,
                MCPToolRegistry.enabled.is_(True),
                MCPToolRegistry.stale.is_(False),
            )
        )
        if plugin_slug:
            stmt = stmt.where(MCPServerRegistry.plugin_slug == plugin_slug)

        result = await self.db.execute(stmt)
        all_tools = list(result.scalars().all())

        selected: List[Dict[str, Any]] = []
        total_schema_bytes = 0
        for tool in sorted(all_tools, key=lambda item: item.name):
            if len(selected) >= max_tools:
                break
            schema = tool.schema_json
            if not isinstance(schema, dict):
                continue
            serialized = json.dumps(schema, separators=(",", ":"))
            encoded_size = len(serialized.encode("utf-8"))
            if total_schema_bytes + encoded_size > max_schema_bytes:
                break
            selected.append(schema)
            total_schema_bytes += encoded_size

        return selected, {
            "enabled": bool(selected),
            "scope_mode": scope_mode,
            "project_slug": mcp_project_slug,
            "available_count": len(all_tools),
            "selected_count": len(selected),
            "total_schema_bytes": total_schema_bytes,
        }

    async def get_enabled_tool(
        self,
        user_id: str,
        tool_name: str,
    ) -> Optional[MCPToolRegistry]:
        normalized_user_id = _normalize_user_id(user_id)
        result = await self.db.execute(
            select(MCPToolRegistry)
            .options(joinedload(MCPToolRegistry.server))
            .join(MCPServerRegistry, MCPServerRegistry.id == MCPToolRegistry.server_id)
            .where(
                MCPServerRegistry.user_id == normalized_user_id,
                MCPToolRegistry.name == tool_name,
                MCPToolRegistry.enabled.is_(True),
                MCPToolRegistry.stale.is_(False),
            )
        )
        return result.scalars().first()

    async def execute_tool_call(
        self,
        user_id: str,
        tool_name: str,
        arguments: Dict[str, Any],
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        tool = await self.get_enabled_tool(user_id, tool_name)
        if tool is None:
            return {
                "ok": False,
                "error": {
                    "code": "TOOL_NOT_ALLOWED",
                    "message": f"Tool '{tool_name}' is not enabled for this user.",
                },
            }

        schema = tool.schema_json if isinstance(tool.schema_json, dict) else {}
        is_valid, validation_errors = _validate_tool_arguments(schema, arguments)
        if not is_valid:
            return {
                "ok": False,
                "error": {
                    "code": "TOOL_ARGUMENTS_INVALID",
                    "message": "Tool arguments failed schema validation.",
                    "details": validation_errors,
                },
            }

        call_url = build_tool_call_url(tool.server, tool_name)
        headers = _build_tool_call_headers(user_id, request_id=request_id)
        started_at = perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self.call_timeout_seconds) as client:
                response = await client.post(call_url, json=arguments, headers=headers)

            elapsed_ms = int((perf_counter() - started_at) * 1000)
            try:
                payload = response.json()
            except ValueError:
                payload = {"raw_text": response.text}

            if response.status_code >= 400:
                return {
                    "ok": False,
                    "latency_ms": elapsed_ms,
                    "http_status": response.status_code,
                    "error": {
                        "code": "TOOL_HTTP_ERROR",
                        "message": f"MCP tool call failed with status {response.status_code}.",
                        "details": payload,
                    },
                }

            return {
                "ok": True,
                "latency_ms": elapsed_ms,
                "http_status": response.status_code,
                "data": payload,
            }
        except Exception as exc:
            return {
                "ok": False,
                "error": {
                    "code": "TOOL_EXECUTION_ERROR",
                    "message": str(exc),
                },
            }
