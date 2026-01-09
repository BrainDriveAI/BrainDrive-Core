"""
Audit Log Model for Security Event Tracking.

Stores structured audit events for security-relevant actions including
authentication, authorization, admin actions, and resource lifecycle events.
"""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, JSON, Index, DateTime
from sqlalchemy.sql import func

from app.models.base import Base
from app.models.mixins import TimestampMixin


def _uuid() -> str:
    """Generate a UUID string."""
    return str(uuid.uuid4())


class AuditLog(Base, TimestampMixin):
    """
    Security audit log table for tracking security-relevant events.
    
    This table stores structured audit events that are critical for:
    - Security monitoring and incident response
    - Compliance and audit trails
    - Debugging authentication/authorization issues
    - Tracking administrative actions
    """
    
    __tablename__ = "audit_logs"
    
    # Primary key
    id = Column(String(36), primary_key=True, default=_uuid)
    
    # Event identification
    event_type = Column(String(100), nullable=False)
    """
    Event type identifier using dot notation.
    Examples:
    - auth.login_success, auth.login_failed
    - auth.token_invalid, auth.token_expired
    - auth.forbidden, auth.unauthorized
    - admin.settings_updated, admin.diagnostics_accessed
    - plugin.enabled, plugin.disabled, plugin.installed
    - job.started, job.completed, job.failed
    - service.auth_failed, service.scope_denied
    """
    
    # Actor information (who performed the action)
    actor_type = Column(String(20), nullable=False)
    """Actor type: 'user', 'service', or 'anonymous'"""
    
    actor_id = Column(String(100), nullable=True)
    """User ID, service name, or null for anonymous"""
    
    # Request context
    request_id = Column(String(64), nullable=True)
    """Correlation ID from X-Request-ID header"""
    
    ip = Column(String(45), nullable=True)
    """Client IP address (IPv4 or IPv6)"""
    
    user_agent = Column(String(500), nullable=True)
    """Client User-Agent header (truncated for safety)"""
    
    method = Column(String(10), nullable=True)
    """HTTP method (GET, POST, PUT, DELETE, etc.)"""
    
    path = Column(String(500), nullable=True)
    """Request path (without query string)"""
    
    # Resource affected (if applicable)
    resource_type = Column(String(50), nullable=True)
    """Type of resource affected: 'job', 'plugin', 'setting', 'user', etc."""
    
    resource_id = Column(String(100), nullable=True)
    """ID of the affected resource"""
    
    # Event result
    status = Column(String(20), nullable=False)
    """Event status: 'success' or 'failure'"""
    
    reason = Column(Text, nullable=True)
    """Human-readable reason/description (safe, redacted)"""
    
    # Additional context (JSON, redacted)
    extra_data = Column(JSON, nullable=True)
    """
    Additional curated, redacted metadata.
    NEVER include: tokens, passwords, API keys, cookies, file contents.
    """
    
    # Timestamp (use server default for consistency)
    timestamp = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now()
    )
    """When the event occurred (server time)"""
    
    # Indexes for efficient querying
    __table_args__ = (
        # Query by event type (most common filter)
        Index("idx_audit_event_type", "event_type"),
        
        # Query by actor (who did what)
        Index("idx_audit_actor", "actor_type", "actor_id"),
        
        # Query by time range (compliance, incident response)
        Index("idx_audit_timestamp", "timestamp"),
        
        # Correlate with request logs
        Index("idx_audit_request_id", "request_id"),
        
        # Query by affected resource
        Index("idx_audit_resource", "resource_type", "resource_id"),
        
        # Query by status (find failures)
        Index("idx_audit_status", "status"),
        
        # Composite for common queries (type + time)
        Index("idx_audit_type_time", "event_type", "timestamp"),
    )
    
    def __repr__(self) -> str:
        return (
            f"<AuditLog(id={self.id}, event_type={self.event_type}, "
            f"actor={self.actor_type}:{self.actor_id}, status={self.status})>"
        )
