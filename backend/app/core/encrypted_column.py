"""
SQLAlchemy encrypted column type for automatic field encryption/decryption
"""
import hashlib
import logging
from typing import Any, Optional, Iterable
from itertools import islice
from sqlalchemy import TypeDecorator, Text
from sqlalchemy.engine import Dialect

from .encryption import encryption_service, EncryptionError

logger = logging.getLogger(__name__)


def _summarize_value(value: Any) -> str:
    """Return a sanitised summary of a value for debug logging."""
    try:
        if value is None:
            return "None"
        if isinstance(value, str):
            preview = value
            if len(value) > 12:
                preview = f"{value[:4]}…{value[-4:]}"
            digest = hashlib.sha256(value.encode('utf-8')).hexdigest()[:8]
            return f"str(len={len(value)},preview='{preview}',hash={digest})"
        if isinstance(value, dict):
            keys = list(value.keys())[:5]
            return f"dict(keys={keys},len={len(value)})"
        if isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray, str)):
            iterator = iter(value)
            sample = list(islice(iterator, 3))
            length = getattr(value, "__len__", None)
            length_repr = length() if callable(length) else (len(value) if hasattr(value, "__len__") else "unknown")
            return f"iterable(sample={sample},len={length_repr},type={type(value).__name__})"
        return f"{type(value).__name__}"
    except Exception as exc:  # pragma: no cover - defensive logging helper
        return f"unprintable({type(value).__name__}):{exc}"

class EncryptedType(TypeDecorator):
    """
    SQLAlchemy column type that automatically encrypts/decrypts field values
    based on configuration
    """
    
    # Use Text as the underlying SQL type to store base64-encoded encrypted data
    impl = Text
    cache_ok = True  # Enable SQLAlchemy query caching
    
    def __init__(self, table_name: str, field_name: str, *args, **kwargs):
        """
        Initialize encrypted column type
        
        Args:
            table_name: Name of the database table
            field_name: Name of the field being encrypted
        """
        self.table_name = table_name
        self.field_name = field_name
        super().__init__(*args, **kwargs)
    
    def process_bind_param(self, value: Any, dialect: Dialect) -> Optional[str]:
        """
        Process value when binding to database (encrypt on save)
        
        Args:
            value: The original value to be stored
            dialect: SQLAlchemy dialect (not used)
            
        Returns:
            Encrypted string or None
        """
        if value is None:
            return None
            
        # Check if field should be encrypted
        if not encryption_service.should_encrypt_field(self.table_name, self.field_name):
            # If encryption is disabled for this field, store as JSON string
            import json
            if isinstance(value, (dict, list)):
                return json.dumps(value, ensure_ascii=False, separators=(',', ':'))
            return str(value)
        
        try:
            # Encrypt the value
            encrypted_value = encryption_service.encrypt_field(
                self.table_name, 
                self.field_name, 
                value
            )
            
            logger.debug(f"Encrypted field {self.table_name}.{self.field_name}")
            return encrypted_value
            
        except EncryptionError as e:
            import os
            app_env = os.getenv("APP_ENV", "production")
            if app_env.lower() != "dev":
                logger.error(
                    "Encryption failed for %s.%s in production -- refusing to store plaintext. Reason: %s",
                    self.table_name,
                    self.field_name,
                    e,
                )
                raise
            logger.exception(
                "Failed to encrypt %s.%s (storing plaintext fallback in dev mode). Reason: %s | value=%s",
                self.table_name,
                self.field_name,
                e,
                _summarize_value(value),
            )
            import json
            if isinstance(value, (dict, list)):
                return json.dumps(value, ensure_ascii=False, separators=(',', ':'))
            return str(value)
    
    def process_result_value(self, value: Optional[str], dialect: Dialect) -> Any:
        """
        Process value when loading from database (decrypt on load)
        
        Args:
            value: The encrypted string from database
            dialect: SQLAlchemy dialect (not used)
            
        Returns:
            Decrypted original value or None
        """
        if value is None:
            return None
            
        # Check if field should be encrypted
        if not encryption_service.should_encrypt_field(self.table_name, self.field_name):
            # If encryption is disabled, try to parse as JSON
            try:
                import json
                return json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return value
        
        try:
            # Check if the value appears to be encrypted
            if encryption_service.is_encrypted_value(value):
                # Decrypt the value
                decrypted_value = encryption_service.decrypt_field(
                    self.table_name,
                    self.field_name,
                    value
                )
                
                logger.debug(f"Decrypted field {self.table_name}.{self.field_name}")
                return decrypted_value
            else:
                # Value is not encrypted (legacy data or encryption disabled)
                logger.warning(
                    "Expected encrypted value for %s.%s but received plaintext. value=%s",
                    self.table_name,
                    self.field_name,
                    _summarize_value(value),
                )
                try:
                    import json
                    return json.loads(value)
                except (json.JSONDecodeError, TypeError):
                    try:
                        stripped = str(value).strip()
                        if stripped.startswith('{') or stripped.startswith('['):
                            return json.loads(stripped)
                    except Exception:
                        pass
                    return value
                    
        except EncryptionError as e:
            import os
            app_env = os.getenv("APP_ENV", "production")
            if app_env.lower() != "dev":
                logger.error(
                    "Decryption failed for %s.%s in production -- refusing plaintext fallback. Reason: %s",
                    self.table_name,
                    self.field_name,
                    e,
                )
                raise
            logger.exception(
                "Failed to decrypt %s.%s (returning raw value in dev mode). Reason: %s | value=%s",
                self.table_name,
                self.field_name,
                e,
                _summarize_value(value),
            )
            try:
                import json
                return json.loads(value)
            except (json.JSONDecodeError, TypeError):
                try:
                    stripped = str(value).strip()
                    if stripped.startswith('{') or stripped.startswith('['):
                        return json.loads(stripped)
                except Exception:
                    pass
                return value

class EncryptedJSON(EncryptedType):
    """
    Specialized encrypted type for JSON fields
    Provides better type hints and validation
    """
    
    def __init__(self, table_name: str, field_name: str, *args, **kwargs):
        super().__init__(table_name, field_name, *args, **kwargs)
    
    def process_bind_param(self, value: Any, dialect: Dialect) -> Optional[str]:
        """Ensure value is JSON-serializable before encryption"""
        if value is None:
            return None
            
        # Validate that the value is JSON-serializable
        try:
            import json
            json.dumps(value)  # Test serialization
        except (TypeError, ValueError) as e:
            logger.error(f"Value for {self.table_name}.{self.field_name} is not JSON-serializable: {e}")
            raise ValueError(f"Value must be JSON-serializable: {e}")
        
        return super().process_bind_param(value, dialect)

def create_encrypted_column(table_name: str, field_name: str, json_type: bool = True):
    """
    Factory function to create encrypted column types
    
    Args:
        table_name: Name of the database table
        field_name: Name of the field
        json_type: Whether to use EncryptedJSON (True) or EncryptedType (False)
        
    Returns:
        Configured encrypted column type
    """
    if json_type:
        return EncryptedJSON(table_name, field_name)
    else:
        return EncryptedType(table_name, field_name)
