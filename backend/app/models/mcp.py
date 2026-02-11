import json
import uuid
from datetime import datetime, UTC

import sqlalchemy
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.types import TEXT, TypeDecorator

from app.models.base import Base


class JSONType(TypeDecorator):
    """Store JSON values as TEXT for SQLite/Postgres compatibility."""

    impl = TEXT
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None:
            value = json.dumps(value)
        return value

    def process_result_value(self, value, dialect):
        if value is not None:
            return json.loads(value)
        return value


class MCPServerRegistry(Base):
    __tablename__ = "mcp_server_registry"

    id = Column(String(32), primary_key=True, default=lambda: str(uuid.uuid4()).replace("-", ""))
    user_id = Column(String(32), ForeignKey("users.id", name="fk_mcp_server_registry_user_id"), nullable=False, index=True)

    plugin_slug = Column(String, nullable=True, index=True)
    runtime_id = Column(String, ForeignKey("plugin_service_runtime.id", name="fk_mcp_server_registry_runtime_id"), nullable=True, index=True)

    base_url = Column(String, nullable=False)
    tools_url = Column(String, nullable=False)
    healthcheck_url = Column(String, nullable=True)
    tool_call_url_template = Column(String, nullable=False, default="/tool:{name}")

    auth_mode = Column(String, nullable=False, default="none")
    auth_ref = Column(String, nullable=True)
    status = Column(String, nullable=False, default="registered")
    last_sync_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.now(UTC))
    updated_at = Column(DateTime, default=datetime.now(UTC), onupdate=datetime.now(UTC))

    tools = relationship("MCPToolRegistry", back_populates="server", cascade="all, delete-orphan")

    __table_args__ = (
        sqlalchemy.UniqueConstraint("user_id", "runtime_id", name="uq_mcp_server_registry_user_runtime"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "plugin_slug": self.plugin_slug,
            "runtime_id": self.runtime_id,
            "base_url": self.base_url,
            "tools_url": self.tools_url,
            "healthcheck_url": self.healthcheck_url,
            "tool_call_url_template": self.tool_call_url_template,
            "auth_mode": self.auth_mode,
            "auth_ref": self.auth_ref,
            "status": self.status,
            "last_sync_at": self.last_sync_at.isoformat() if self.last_sync_at else None,
            "last_error": self.last_error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class MCPToolRegistry(Base):
    __tablename__ = "mcp_tool_registry"

    id = Column(String(32), primary_key=True, default=lambda: str(uuid.uuid4()).replace("-", ""))
    server_id = Column(String(32), ForeignKey("mcp_server_registry.id", name="fk_mcp_tool_registry_server_id"), nullable=False, index=True)

    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    schema_json = Column(JSONType, nullable=False)

    enabled = Column(Boolean, nullable=False, default=True)
    stale = Column(Boolean, nullable=False, default=False)
    source_hash = Column(String(64), nullable=False)
    version = Column(String, nullable=True)
    safety_class = Column(String, nullable=False, default="read_only")

    created_at = Column(DateTime, default=datetime.now(UTC))
    updated_at = Column(DateTime, default=datetime.now(UTC), onupdate=datetime.now(UTC))

    server = relationship("MCPServerRegistry", back_populates="tools")

    __table_args__ = (
        sqlalchemy.UniqueConstraint("server_id", "name", name="uq_mcp_tool_registry_server_tool"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "server_id": self.server_id,
            "name": self.name,
            "description": self.description,
            "schema_json": self.schema_json,
            "enabled": self.enabled,
            "stale": self.stale,
            "source_hash": self.source_hash,
            "version": self.version,
            "safety_class": self.safety_class,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
