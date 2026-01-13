from dataclasses import dataclass
from typing import Optional, Set


@dataclass(frozen=True)
class AuthContext:
    #Standardized authentication context for API requests.
    user_id: str
    username: str
    is_admin: bool
    roles: Set[str]  # Future-proof for granular permissions
    tenant_id: Optional[str]  # Placeholder for multi-tenant support

