"""
Redaction utilities for audit logging.

Ensures sensitive data is NEVER logged or stored in audit events.

CRITICAL: This module is security-sensitive. Changes require careful review.
"""
import re
from typing import Any, Dict, List, Optional, Set

# Headers that should NEVER be logged
SENSITIVE_HEADERS: Set[str] = {
    "authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "x-auth-token",
    "proxy-authorization",
}

# Keys in JSON payloads that should be redacted
SENSITIVE_KEYS: Set[str] = {
    "password",
    "old_password",
    "new_password",
    "current_password",
    "confirm_password",
    "secret",
    "token",
    "access_token",
    "refresh_token",
    "api_key",
    "apikey",
    "api-key",
    "private_key",
    "privatekey",
    "secret_key",
    "secretkey",
    "credentials",
    "auth",
    "authentication",
    "authorization",
    "cookie",
    "session",
    "ssn",
    "social_security",
    "credit_card",
    "card_number",
    "cvv",
    "pin",
    "encryption_key",
    "master_key",
}

# Patterns for sensitive data in strings
SENSITIVE_PATTERNS = [
    # JWT tokens
    (re.compile(r'eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+'), '[REDACTED_JWT]'),
    # API keys (common patterns)
    (re.compile(r'sk-[A-Za-z0-9]{20,}'), '[REDACTED_API_KEY]'),
    (re.compile(r'sk-or-[A-Za-z0-9]{20,}'), '[REDACTED_API_KEY]'),
    (re.compile(r'sk-ant-[A-Za-z0-9]{20,}'), '[REDACTED_API_KEY]'),
    (re.compile(r'gsk_[A-Za-z0-9]{20,}'), '[REDACTED_API_KEY]'),
    # Bearer tokens in strings
    (re.compile(r'Bearer\s+[A-Za-z0-9_\-\.]+', re.IGNORECASE), 'Bearer [REDACTED]'),
    # Basic auth
    (re.compile(r'Basic\s+[A-Za-z0-9+/=]+', re.IGNORECASE), 'Basic [REDACTED]'),
]


def redact_string(value: str) -> str:
    """
    Redact sensitive patterns from a string.
    
    Args:
        value: String to redact
        
    Returns:
        Redacted string
    """
    if not value:
        return value
    
    result = value
    for pattern, replacement in SENSITIVE_PATTERNS:
        result = pattern.sub(replacement, result)
    
    return result


def is_sensitive_key(key: str) -> bool:
    """
    Check if a key name indicates sensitive data.
    
    Args:
        key: Key name to check
        
    Returns:
        True if the key is sensitive
    """
    key_lower = key.lower().replace('-', '_')
    return key_lower in SENSITIVE_KEYS or any(
        sensitive in key_lower 
        for sensitive in ['password', 'secret', 'token', 'key', 'auth', 'credential']
    )


def redact_dict(data: Dict[str, Any], max_depth: int = 5) -> Dict[str, Any]:
    """
    Recursively redact sensitive values from a dictionary.
    
    Args:
        data: Dictionary to redact
        max_depth: Maximum recursion depth (prevent infinite loops)
        
    Returns:
        Redacted copy of the dictionary
    """
    if max_depth <= 0:
        return {"_truncated": "max_depth_exceeded"}
    
    result = {}
    for key, value in data.items():
        if is_sensitive_key(key):
            result[key] = "[REDACTED]"
        elif isinstance(value, dict):
            result[key] = redact_dict(value, max_depth - 1)
        elif isinstance(value, list):
            result[key] = redact_list(value, max_depth - 1)
        elif isinstance(value, str):
            result[key] = redact_string(value)
        else:
            result[key] = value
    
    return result


def redact_list(data: List[Any], max_depth: int = 5) -> List[Any]:
    """
    Recursively redact sensitive values from a list.
    
    Args:
        data: List to redact
        max_depth: Maximum recursion depth
        
    Returns:
        Redacted copy of the list
    """
    if max_depth <= 0:
        return ["_truncated"]
    
    result = []
    for item in data:
        if isinstance(item, dict):
            result.append(redact_dict(item, max_depth - 1))
        elif isinstance(item, list):
            result.append(redact_list(item, max_depth - 1))
        elif isinstance(item, str):
            result.append(redact_string(item))
        else:
            result.append(item)
    
    return result


def redact_sensitive_data(data: Any) -> Any:
    """
    Main entry point for redacting sensitive data.
    
    Handles dictionaries, lists, and strings.
    
    Args:
        data: Data to redact (dict, list, str, or other)
        
    Returns:
        Redacted copy of the data
    """
    if data is None:
        return None
    
    if isinstance(data, dict):
        return redact_dict(data)
    elif isinstance(data, list):
        return redact_list(data)
    elif isinstance(data, str):
        return redact_string(data)
    else:
        return data


def truncate_user_agent(user_agent: Optional[str], max_length: int = 500) -> Optional[str]:
    """
    Truncate user agent string to safe length.
    
    Args:
        user_agent: User agent string
        max_length: Maximum allowed length
        
    Returns:
        Truncated user agent or None
    """
    if not user_agent:
        return None
    
    if len(user_agent) <= max_length:
        return user_agent
    
    return user_agent[:max_length - 3] + "..."


def get_client_ip(request) -> Optional[str]:
    """
    Extract client IP address from request, handling proxies.
    
    Args:
        request: FastAPI/Starlette request object
        
    Returns:
        Client IP address or None
    """
    # Check X-Forwarded-For (set by proxies/load balancers)
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        # Take the first IP (original client)
        ip = forwarded_for.split(",")[0].strip()
        return ip
    
    # Check X-Real-IP (nginx)
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    
    # Fall back to direct client
    if request.client:
        return request.client.host
    
    return None


def safe_path(path: str, max_length: int = 500) -> str:
    """
    Sanitize and truncate request path.
    
    Removes query strings and truncates to safe length.
    
    Args:
        path: Request path
        max_length: Maximum allowed length
        
    Returns:
        Sanitized path
    """
    if not path:
        return ""
    
    # Remove query string
    path_only = path.split("?")[0]
    
    # Truncate if needed
    if len(path_only) > max_length:
        return path_only[:max_length - 3] + "..."
    
    return path_only
