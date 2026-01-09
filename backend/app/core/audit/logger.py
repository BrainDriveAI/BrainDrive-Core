"""
Audit Logger Service.

Provides high-level methods to log security events to the database.
"""
import logging
from datetime import datetime
from typing import Any, Dict, Optional
from fastapi import Request

from app.core.audit.models import (
    AuditEventType,
    ActorType,
    EventStatus,
    AuditEvent,
)
from app.core.audit.redaction import (
    redact_sensitive_data,
    truncate_user_agent,
    get_client_ip,
    safe_path,
)

logger = logging.getLogger(__name__)


class AuditLogger:
    """
    Audit logger service for recording security events.
    
    Usage:
        from app.core.audit import audit_logger
        
        # Log an authentication failure
        await audit_logger.log_auth_failure(
            request=request,
            reason="Invalid credentials",
            event_type=AuditEventType.AUTH_LOGIN_FAILED
        )
        
        # Log an admin action
        await audit_logger.log_admin_action(
            request=request,
            user_id="...",
            event_type=AuditEventType.ADMIN_SETTINGS_UPDATED,
            resource_type="setting",
            resource_id="ollama_settings",
            metadata={"category": "servers"}
        )
    """
    
    def _get_request_context(self, request: Request) -> Dict[str, Any]:
        """
        Extract common request context for audit events.
        
        Args:
            request: FastAPI request object
            
        Returns:
            Dictionary of request context
        """
        return {
            "request_id": getattr(request.state, 'request_id', None),
            "ip": get_client_ip(request),
            "user_agent": truncate_user_agent(request.headers.get("User-Agent")),
            "method": request.method,
            "path": safe_path(str(request.url.path)),
        }
    
    async def _write_to_db(self, event: AuditEvent) -> None:
        """
        Write an audit event to the database.
        
        Args:
            event: The audit event to write
        """
        # Import here to avoid circular imports
        from app.core.database import db_factory
        from app.models.audit_log import AuditLog
        
        try:
            async with db_factory.session_factory() as session:
                audit_log = AuditLog(
                    event_type=event.event_type,
                    actor_type=event.actor_type,
                    actor_id=event.actor_id,
                    request_id=event.request_id,
                    ip=event.ip,
                    user_agent=event.user_agent,
                    method=event.method,
                    path=event.path,
                    resource_type=event.resource_type,
                    resource_id=event.resource_id,
                    status=event.status,
                    reason=event.reason,
                    extra_data=event.metadata,
                    timestamp=event.timestamp,
                )
                session.add(audit_log)
                await session.commit()
        except Exception as e:
            # Log error but don't fail the request
            logger.error(f"Failed to write audit log: {e}", exc_info=True)
    
    async def log_event(
        self,
        event_type: AuditEventType,
        actor_type: ActorType,
        status: EventStatus,
        request: Optional[Request] = None,
        actor_id: Optional[str] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        reason: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Log a generic audit event.
        
        Args:
            event_type: Type of event
            actor_type: Type of actor
            status: Event outcome
            request: Optional FastAPI request for context
            actor_id: Optional user/service ID
            resource_type: Optional resource type
            resource_id: Optional resource ID
            reason: Optional reason string
            metadata: Optional additional metadata (will be redacted)
        """
        # Build event
        event_data = {
            "event_type": event_type,
            "actor_type": actor_type,
            "actor_id": actor_id,
            "status": status,
            "reason": reason,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "metadata": redact_sensitive_data(metadata) if metadata else None,
            "timestamp": datetime.utcnow(),
        }
        
        # Add request context if available
        if request:
            event_data.update(self._get_request_context(request))
        
        event = AuditEvent(**event_data)
        
        # Write to database
        await self._write_to_db(event)
    
    # === Authentication Events ===
    
    async def log_auth_success(
        self,
        request: Request,
        user_id: str,
        event_type: AuditEventType = AuditEventType.AUTH_LOGIN_SUCCESS,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Log a successful authentication event."""
        await self.log_event(
            event_type=event_type,
            actor_type=ActorType.USER,
            actor_id=user_id,
            status=EventStatus.SUCCESS,
            request=request,
            metadata=metadata,
        )
    
    async def log_auth_failure(
        self,
        request: Request,
        reason: str,
        event_type: AuditEventType = AuditEventType.AUTH_LOGIN_FAILED,
        user_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Log an authentication failure event."""
        await self.log_event(
            event_type=event_type,
            actor_type=ActorType.ANONYMOUS if not user_id else ActorType.USER,
            actor_id=user_id,
            status=EventStatus.FAILURE,
            request=request,
            reason=reason,
            metadata=metadata,
        )
    
    async def log_authorization_failure(
        self,
        request: Request,
        user_id: str,
        reason: str,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Log an authorization (403) failure."""
        await self.log_event(
            event_type=AuditEventType.AUTH_FORBIDDEN,
            actor_type=ActorType.USER,
            actor_id=user_id,
            status=EventStatus.FAILURE,
            request=request,
            reason=reason,
            resource_type=resource_type,
            resource_id=resource_id,
            metadata=metadata,
        )
    
    # === Service Authentication Events ===
    
    async def log_service_auth_success(
        self,
        request: Request,
        service_name: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Log a successful service authentication."""
        await self.log_event(
            event_type=AuditEventType.SERVICE_AUTH_SUCCESS,
            actor_type=ActorType.SERVICE,
            actor_id=service_name,
            status=EventStatus.SUCCESS,
            request=request,
            metadata=metadata,
        )
    
    async def log_service_auth_failure(
        self,
        request: Request,
        reason: str,
        service_name: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Log a service authentication failure."""
        await self.log_event(
            event_type=AuditEventType.SERVICE_AUTH_FAILED,
            actor_type=ActorType.SERVICE,
            actor_id=service_name,
            status=EventStatus.FAILURE,
            request=request,
            reason=reason,
            metadata=metadata,
        )
    
    # === Admin Events ===
    
    async def log_admin_action(
        self,
        request: Request,
        user_id: str,
        event_type: AuditEventType,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        reason: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Log an administrative action."""
        await self.log_event(
            event_type=event_type,
            actor_type=ActorType.USER,
            actor_id=user_id,
            status=EventStatus.SUCCESS,
            request=request,
            resource_type=resource_type,
            resource_id=resource_id,
            reason=reason,
            metadata=metadata,
        )
    
    # === Plugin Events ===
    
    async def log_plugin_action(
        self,
        request: Request,
        user_id: str,
        event_type: AuditEventType,
        plugin_id: str,
        plugin_name: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Log a plugin lifecycle action."""
        event_metadata = {"plugin_name": plugin_name} if plugin_name else {}
        if metadata:
            event_metadata.update(metadata)
        
        await self.log_event(
            event_type=event_type,
            actor_type=ActorType.USER,
            actor_id=user_id,
            status=EventStatus.SUCCESS,
            request=request,
            resource_type="plugin",
            resource_id=plugin_id,
            metadata=event_metadata if event_metadata else None,
        )
    
    # === Job Events ===
    
    async def log_job_event(
        self,
        event_type: AuditEventType,
        job_id: str,
        user_id: str,
        job_type: Optional[str] = None,
        status: EventStatus = EventStatus.SUCCESS,
        reason: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Log a job lifecycle event.
        
        Note: Job events may not have a request context (background processing).
        """
        event_metadata = {"job_type": job_type} if job_type else {}
        if metadata:
            event_metadata.update(metadata)
        
        await self.log_event(
            event_type=event_type,
            actor_type=ActorType.USER,
            actor_id=user_id,
            status=status,
            resource_type="job",
            resource_id=job_id,
            reason=reason,
            metadata=event_metadata if event_metadata else None,
        )


# Global singleton instance
audit_logger = AuditLogger()
