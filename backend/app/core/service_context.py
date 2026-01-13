"""
Service Context for Internal Service-to-Service Authentication

Similar to AuthContext (for users), but for internal services like:
- Plugin runtime
- Job worker
- Service orchestration
"""
from dataclasses import dataclass
from typing import Set


@dataclass(frozen=True)
class ServiceContext:
    """
    Immutable context for authenticated services.
    
    Used by internal endpoints that are called by services (not users).
    
    Attributes:
        service_name: Identifier for the service (e.g., "plugin_runtime", "job_worker")
        scopes: Set of permissions/capabilities the service has
    """
    service_name: str
    scopes: Set[str]
    
    def has_scope(self, scope: str) -> bool:
        """Check if service has a specific scope/permission."""
        return scope in self.scopes
    
    def has_any_scope(self, *scopes: str) -> bool:
        """Check if service has any of the specified scopes."""
        return bool(self.scopes.intersection(scopes))
    
    def has_all_scopes(self, *scopes: str) -> bool:
        """Check if service has all of the specified scopes."""
        return set(scopes).issubset(self.scopes)

