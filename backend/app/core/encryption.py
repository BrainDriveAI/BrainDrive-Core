"""
Universal encryption service for BrainDrive
Provides AES-256-GCM encryption with automatic compression and key derivation
"""
import os
import json
import gzip
import base64
import logging
from typing import Any, Optional, Union
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.backends import default_backend
from cryptography.exceptions import InvalidTag

from .config import settings

from .encryption_config import encryption_config

logger = logging.getLogger(__name__)

class EncryptionError(Exception):
    """Custom exception for encryption-related errors"""
    pass

class UniversalEncryptionService:
    """Universal encryption service with configuration-driven field encryption"""
    
    def __init__(self):
        self.backend = default_backend()
        self._master_key = None
        self._salt = self._derive_salt()

    @staticmethod
    def _derive_salt() -> bytes:
        """Derive salt from master key via HMAC-SHA256."""
        import hmac
        import hashlib
        master_key_str = settings.ENCRYPTION_MASTER_KEY or os.getenv('ENCRYPTION_MASTER_KEY', '')
        if not master_key_str:
            raise EncryptionError(
                "ENCRYPTION_MASTER_KEY is not set. Cannot derive encryption salt. "
                "Please set ENCRYPTION_MASTER_KEY to a secure random string."
            )
        return hmac.new(
            master_key_str.encode(), b'BrainDrive-Salt-Derivation', hashlib.sha256
        ).digest()[:16]
        
    def _get_master_key(self) -> bytes:
        """Get or derive the master encryption key"""
        if self._master_key is None:
            # Get master key from environment
            master_key_str = settings.ENCRYPTION_MASTER_KEY or os.getenv('ENCRYPTION_MASTER_KEY')
            if not master_key_str:
                raise EncryptionError(
                    "ENCRYPTION_MASTER_KEY environment variable not set. "
                    "Please set it to a secure random string."
                )
            
            # Derive a 32-byte key using PBKDF2
            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=32,
                salt=self._salt,
                iterations=100000,
                backend=self.backend
            )
            self._master_key = kdf.derive(master_key_str.encode('utf-8'))
            
        return self._master_key
    
    def _compress_data(self, data: bytes) -> bytes:
        """Compress data using gzip"""
        return gzip.compress(data)
    
    def _decompress_data(self, data: bytes) -> bytes:
        """Decompress data using gzip"""
        return gzip.decompress(data)
    
    def _serialize_value(self, value: Any) -> bytes:
        """Serialize a value to bytes for encryption"""
        if value is None:
            return b''
        
        # Convert to JSON string then to bytes
        json_str = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
        return json_str.encode('utf-8')
    
    def _deserialize_value(self, data: bytes) -> Any:
        """Deserialize bytes back to original value"""
        if not data:
            return None
        
        # Convert bytes to JSON string then parse
        json_str = data.decode('utf-8')
        return json.loads(json_str)
    
    def encrypt_field(self, table_name: str, field_name: str, value: Any) -> Optional[str]:
        """
        Encrypt a field value based on its configuration
        
        Args:
            table_name: Name of the database table
            field_name: Name of the field
            value: Value to encrypt
            
        Returns:
            Base64-encoded encrypted string or None if value is None
        """
        if value is None:
            return None
            
        try:
            # Get field settings
            settings = encryption_config.get_field_settings(table_name, field_name)
            
            # Serialize the value
            data = self._serialize_value(value)
            
            # Compress if configured
            if settings.get('compress', False):
                data = self._compress_data(data)
            
            # Generate a random 96-bit (12-byte) IV for GCM
            iv = os.urandom(12)
            
            # Create cipher
            cipher = Cipher(
                algorithms.AES(self._get_master_key()),
                modes.GCM(iv),
                backend=self.backend
            )
            encryptor = cipher.encryptor()
            
            # Encrypt the data
            ciphertext = encryptor.update(data) + encryptor.finalize()
            
            # Get the authentication tag
            tag = encryptor.tag
            
            # Combine IV + ciphertext + tag
            encrypted_data = iv + ciphertext + tag
            
            # Encode based on settings
            encoding = settings.get('encoding', 'base64')
            if encoding == 'base64':
                return base64.b64encode(encrypted_data).decode('ascii')
            else:
                raise EncryptionError(f"Unsupported encoding: {encoding}")
                
        except Exception as e:
            logger.error(f"Error encrypting field {table_name}.{field_name}: {e}")
            raise EncryptionError(f"Failed to encrypt field: {e}")
    
    def decrypt_field(self, table_name: str, field_name: str, encrypted_value: Optional[str]) -> Any:
        """
        Decrypt a field value based on its configuration
        
        Args:
            table_name: Name of the database table
            field_name: Name of the field
            encrypted_value: Base64-encoded encrypted string
            
        Returns:
            Decrypted original value or None if encrypted_value is None
        """
        if encrypted_value is None:
            return None
            
        try:
            # Get field settings
            settings = encryption_config.get_field_settings(table_name, field_name)
            
            # Decode based on settings
            encoding = settings.get('encoding', 'base64')
            if encoding == 'base64':
                encrypted_data = base64.b64decode(encrypted_value.encode('ascii'))
            else:
                raise EncryptionError(f"Unsupported encoding: {encoding}")
            
            # Extract IV (12 bytes), ciphertext, and tag (16 bytes)
            if len(encrypted_data) < 28:  # 12 + 16 = minimum size
                raise EncryptionError("Invalid encrypted data: too short")
                
            iv = encrypted_data[:12]
            tag = encrypted_data[-16:]
            ciphertext = encrypted_data[12:-16]
            
            # Create cipher
            cipher = Cipher(
                algorithms.AES(self._get_master_key()),
                modes.GCM(iv, tag),
                backend=self.backend
            )
            decryptor = cipher.decryptor()
            
            # Decrypt the data
            data = decryptor.update(ciphertext) + decryptor.finalize()
            
            # Decompress if configured
            if settings.get('compress', False):
                data = self._decompress_data(data)
            
            # Deserialize the value
            return self._deserialize_value(data)
            
        except InvalidTag:
            logger.error(f"Authentication failed for field {table_name}.{field_name}")
            raise EncryptionError("Decryption failed: data may have been tampered with")
        except Exception as e:
            logger.error(f"Error decrypting field {table_name}.{field_name}: {e}")
            raise EncryptionError(f"Failed to decrypt field: {e}")
    
    def should_encrypt_field(self, table_name: str, field_name: str) -> bool:
        """Check if a field should be encrypted"""
        return encryption_config.should_encrypt_field(table_name, field_name)
    
    def is_encrypted_value(self, value: str) -> bool:
        """
        Check if a value appears to be encrypted (basic heuristic)
        This is useful for migration scenarios
        """
        if not isinstance(value, str):
            return False
            
        try:
            # Strip whitespace that may legitimately surround encoded data
            candidate = value.strip()
            # Strict validation so JSON/plaintext does not masquerade as ciphertext
            decoded = base64.b64decode(candidate.encode('ascii'), validate=True)
            # Encrypted values should be at least 28 bytes (12 IV + 16 tag)
            return len(decoded) >= 28
        except Exception:
            return False

# Global instance
encryption_service = UniversalEncryptionService()
