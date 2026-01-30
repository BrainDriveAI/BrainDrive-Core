"""
Audit Event Models (Pydantic).

Defines the structure of audit events and their types.
"""
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field


class AuditEventType(str, Enum):
    """
    Enumeration of security-relevant event types.
    
    Uses dot notation: category.action
    """
    # Authentication events
    AUTH_LOGIN_SUCCESS = "auth.login_success"
    AUTH_LOGIN_FAILED = "auth.login_failed"
    AUTH_LOGOUT = "auth.logout"
    AUTH_TOKEN_INVALID = "auth.token_invalid"
    AUTH_TOKEN_EXPIRED = "auth.token_expired"
    AUTH_TOKEN_REFRESH = "auth.token_refresh"
    AUTH_UNAUTHORIZED = "auth.unauthorized"
    AUTH_FORBIDDEN = "auth.forbidden"
    AUTH_USER_NOT_FOUND = "auth.user_not_found"
    AUTH_REGISTER_SUCCESS = "auth.register_success"
    AUTH_REGISTER_FAILED = "auth.register_failed"
    
    # Service authentication events
    SERVICE_AUTH_SUCCESS = "service.auth_success"
    SERVICE_AUTH_FAILED = "service.auth_failed"
    SERVICE_SCOPE_DENIED = "service.scope_denied"
    
    # Admin events
    ADMIN_SETTINGS_CREATED = "admin.settings_created"
    ADMIN_SETTINGS_UPDATED = "admin.settings_updated"
    ADMIN_SETTINGS_DELETED = "admin.settings_deleted"
    ADMIN_DIAGNOSTICS_ACCESSED = "admin.diagnostics_accessed"
    ADMIN_PLUGIN_ROUTES_RELOADED = "admin.plugin_routes_reloaded"
    
    # Plugin lifecycle events
    PLUGIN_ENABLED = "plugin.enabled"
    PLUGIN_DISABLED = "plugin.disabled"
    PLUGIN_INSTALLED = "plugin.installed"
    PLUGIN_UNINSTALLED = "plugin.uninstalled"
    PLUGIN_UPDATED = "plugin.updated"
    
    # Job lifecycle events
    JOB_STARTED = "job.started"
    JOB_COMPLETED = "job.completed"
    JOB_FAILED = "job.failed"
    JOB_CANCELED = "job.canceled"
    JOB_CREATED = "job.created"


class ActorType(str, Enum):
    """Type of actor that triggered the event."""
    USER = "user"
    SERVICE = "service"
    ANONYMOUS = "anonymous"
    SYSTEM = "system"


class EventStatus(str, Enum):
    """Status/outcome of the event."""
    SUCCESS = "success"
    FAILURE = "failure"


class AuditEvent(BaseModel):
    """
    Pydantic model for audit events.
    
    Used for validation and serialization before storing to database.
    """
    # Event identification
    event_type: AuditEventType = Field(..., description="Type of security event")
    
    # Actor information
    actor_type: ActorType = Field(..., description="Type of actor")
    actor_id: Optional[str] = Field(None, description="User ID or service name")
    
    # Request context
    request_id: Optional[str] = Field(None, description="X-Request-ID for correlation")
    ip: Optional[str] = Field(None, description="Client IP address")
    user_agent: Optional[str] = Field(None, max_length=500, description="User agent (truncated)")
    method: Optional[str] = Field(None, max_length=10, description="HTTP method")
    path: Optional[str] = Field(None, max_length=500, description="Request path")
    
    # Resource affected
    resource_type: Optional[str] = Field(None, max_length=50, description="Type of resource")
    resource_id: Optional[str] = Field(None, max_length=100, description="Resource ID")
    
    # Event result
    status: EventStatus = Field(..., description="Event outcome")
    reason: Optional[str] = Field(None, description="Human-readable reason")
    
    # Additional metadata
    metadata: Optional[Dict[str, Any]] = Field(None, description="Curated, redacted metadata")
    
    # Timestamp
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="Event timestamp")
    
    class Config:
        use_enum_values = True
