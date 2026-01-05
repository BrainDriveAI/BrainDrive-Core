"""
Robust JSON parsing utilities with encryption-aware error handling.
Specifically designed to handle the Ollama provider JSON parsing issue.
"""

import json
import logging
from typing import Any, Dict, Optional, Union

logger = logging.getLogger(__name__)


def safe_encrypted_json_parse(
    value: Any, 
    context: str = "",
    setting_id: str = "",
    definition_id: str = ""
) -> Any:
    """
    Safely parse JSON from potentially encrypted settings with multiple fallback strategies.
    
    This function is specifically designed to handle the Ollama provider JSON parsing issue
    where encrypted settings values may not decrypt properly, leading to malformed JSON.
    
    Args:
        value: The value to parse (could be encrypted, JSON string, or already parsed)
        context: Context description for logging
        setting_id: The setting instance ID for detailed error reporting
        definition_id: The setting definition ID for detailed error reporting
    
    Returns:
        Parsed JSON object or the original value if parsing fails
        
    Raises:
        ValueError: If all parsing strategies fail and the value is critical
    """
    
    # If value is already a dict or list, return as-is
    if isinstance(value, (dict, list)):
        logger.debug(f"Value already parsed for {context}")
        return value
    
    # If value is None or empty, return as-is
    if not value:
        logger.debug(f"Empty value for {context}")
        return value
    
    # If value is not a string, convert to string and try parsing
    if not isinstance(value, str):
        logger.debug(f"Converting non-string value to string for {context}")
        value = str(value)
    
    logger.debug(f"Parsing encrypted settings value for {context}")
    logger.debug(f"Setting ID: {setting_id}, Definition ID: {definition_id}")
    logger.debug(f"Value length: {len(value)}, starts with: {value[:50]}...")
    
    # Strategy 1: Direct JSON parsing (for properly decrypted values)
    try:
        result = json.loads(value)
        logger.debug(f"✅ Direct JSON parse successful for {context}")
        return result
    except json.JSONDecodeError as e:
        logger.debug(f"❌ Direct JSON parse failed for {context}: {e}")
        logger.debug(f"Failed at position {e.pos}: {value[max(0, e.pos-10):e.pos+10]}")
    except Exception as e:
        logger.debug(f"❌ Direct JSON parse failed with unexpected error for {context}: {e}")
    
    # Strategy 2: Handle double-encoded JSON (JSON string containing JSON)
    if value.startswith('"') and value.endswith('"'):
        try:
            # Remove outer quotes and unescape
            unquoted = value[1:-1]
            unescaped = unquoted.replace('\\"', '"').replace('\\\\', '\\')
            result = json.loads(unescaped)
            logger.info(f"✅ Double-encoded JSON detected and parsed for {context}")
            return result
        except json.JSONDecodeError as e:
            logger.debug(f"❌ Double-encoded JSON parse failed for {context}: {e}")
        except Exception as e:
            logger.debug(f"❌ Double-encoded JSON parse failed with unexpected error for {context}: {e}")
    
    # Strategy 3: Try parsing as nested JSON string
    try:
        first_parse = json.loads(value)
        if isinstance(first_parse, str):
            result = json.loads(first_parse)
            logger.info(f"✅ Nested JSON string detected and parsed for {context}")
            return result
    except json.JSONDecodeError:
        logger.debug(f"❌ Nested JSON string parse failed for {context}")
    except Exception as e:
        logger.debug(f"❌ Nested JSON string parse failed with unexpected error for {context}: {e}")
    
    # Strategy 4: Clean and retry (remove extra whitespace, quotes, etc.)
    try:
        cleaned = value.strip().strip('"').strip("'").strip()
        if cleaned != value:
            result = json.loads(cleaned)
            logger.info(f"✅ JSON parsed after cleaning for {context}")
            return result
    except json.JSONDecodeError:
        logger.debug(f"❌ Cleaned JSON parse failed for {context}")
    except Exception as e:
        logger.debug(f"❌ Cleaned JSON parse failed with unexpected error for {context}: {e}")
    
    # Strategy 5: Check if it looks like encrypted data that failed to decrypt
    if _looks_like_encrypted_data(value):
        error_msg = (
            f"Value appears to be encrypted data that failed to decrypt for {context}. "
            f"This suggests an encryption key issue. Setting ID: {setting_id}, "
            f"Definition ID: {definition_id}"
        )
        logger.error(error_msg)
        logger.error(f"Encrypted value preview: {value[:100]}...")
        
        # For Ollama settings, try alternative encryption keys before giving up
        if 'ollama' in definition_id.lower():
            logger.info("🔑 Attempting alternative encryption keys for Ollama settings...")
            
            # Try some common encryption keys that might have been used
            # Note: This is a fallback for migration scenarios only
            alternative_keys = []
            
            import os
            original_key = os.environ.get('ENCRYPTION_MASTER_KEY')
            
            for alt_key in alternative_keys:
                try:
                    logger.info(f"🔑 Trying alternative key: {alt_key[:15]}...")
                    os.environ['ENCRYPTION_MASTER_KEY'] = alt_key
                    
                    # Reload encryption service with new key
                    from importlib import reload
                    from app.core import encryption
                    reload(encryption)
                    from app.core.encryption import encryption_service as alt_encryption_service
                    
                    decrypted_value = alt_encryption_service.decrypt_field('settings_instances', 'value', value)
                    
                    if isinstance(decrypted_value, str):
                        try:
                            parsed_data = json.loads(decrypted_value)
                            logger.info(f"🎉 SUCCESS! Decrypted Ollama settings with key: {alt_key[:15]}...")
                            logger.info(f"🔧 IMPORTANT: Update your ENCRYPTION_MASTER_KEY to: {alt_key}")
                            return parsed_data
                        except json.JSONDecodeError:
                            continue
                    elif isinstance(decrypted_value, dict):
                        logger.info(f"🎉 SUCCESS! Decrypted Ollama settings with key: {alt_key[:15]}...")
                        logger.info(f"🔧 IMPORTANT: Update your ENCRYPTION_MASTER_KEY to: {alt_key}")
                        return decrypted_value
                        
                except Exception as key_error:
                    logger.debug(f"Alternative key {alt_key[:15]}... failed: {key_error}")
                    continue
                finally:
                    # Restore original key
                    if original_key:
                        os.environ['ENCRYPTION_MASTER_KEY'] = original_key
                    else:
                        os.environ.pop('ENCRYPTION_MASTER_KEY', None)
                    
                    # Reload encryption service back to original
                    try:
                        reload(encryption)
                    except:
                        pass
            
            # If all alternative keys failed, raise the original error
            raise ValueError(
                f"Failed to decrypt Ollama settings with all available keys. "
                f"The encryption key is incorrect or the data is corrupted. "
                f"Setting ID: {setting_id}"
            )
        else:
            raise ValueError(error_msg)
    
    # Strategy 6: Check if it looks like corrupted JSON
    if _looks_like_corrupted_json(value):
        error_msg = (
            f"Value appears to be corrupted JSON for {context}. "
            f"Setting ID: {setting_id}, Definition ID: {definition_id}"
        )
        logger.error(error_msg)
        logger.error(f"Corrupted JSON preview: {value[:200]}...")
        
        raise ValueError(
            f"Corrupted JSON detected in settings. The stored value appears to be "
            f"malformed. Please check the settings configuration or consider "
            f"recreating the setting. Setting ID: {setting_id}"
        )
    
    # Final fallback - log detailed error and raise exception
    logger.error(f"❌ All JSON parsing strategies failed for {context}")
    logger.error(f"Setting ID: {setting_id}, Definition ID: {definition_id}")
    logger.error(f"Value type: {type(value)}, length: {len(value)}")
    logger.error(f"Value preview: {repr(value[:200])}")
    
    # Provide specific error message for Ollama settings
    if 'ollama' in definition_id.lower():
        raise ValueError(
            f"Failed to parse Ollama settings JSON. This could be due to:\n"
            f"1. Missing or incorrect ENCRYPTION_MASTER_KEY environment variable\n"
            f"2. Corrupted settings data in the database\n"
            f"3. Settings created with a different encryption key\n"
            f"Setting ID: {setting_id}\n"
            f"Please check your encryption configuration or recreate the Ollama settings."
        )
    else:
        raise ValueError(
            f"Failed to parse settings JSON for {context}. "
            f"The stored value could not be parsed with any available strategy. "
            f"Setting ID: {setting_id}, Definition ID: {definition_id}"
        )


def _looks_like_encrypted_data(value: str) -> bool:
    """Check if a value looks like encrypted data (base64-like)"""
    if len(value) < 20:
        return False
    
    # Check if it's mostly base64 characters
    base64_chars = set('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=')
    value_chars = set(value)
    
    # If more than 80% of characters are base64 characters, it's likely encrypted
    if len(value_chars.intersection(base64_chars)) / len(value_chars) > 0.8:
        return True
    
    return False


def _looks_like_corrupted_json(value: str) -> bool:
    """Check if a value looks like corrupted JSON"""
    # Look for JSON-like patterns that are malformed
    json_indicators = ['{', '}', '[', ']', '":', ',"', ':{', ':[']
    
    has_json_chars = any(indicator in value for indicator in json_indicators)
    
    if has_json_chars:
        # If it has JSON characters but failed all parsing attempts, it's likely corrupted
        return True
    
    return False


def validate_ollama_settings_format(parsed_data: Any) -> bool:
    """
    Validate that parsed Ollama settings have the expected format.
    
    Args:
        parsed_data: The parsed JSON data
        
    Returns:
        True if the format is valid, False otherwise
    """
    if not isinstance(parsed_data, dict):
        logger.warning("Ollama settings is not a dictionary")
        return False
    
    # Check for expected Ollama settings structure
    if 'servers' not in parsed_data:
        logger.warning("Ollama settings missing 'servers' key")
        return False
    
    if not isinstance(parsed_data['servers'], list):
        logger.warning("Ollama settings 'servers' is not a list")
        return False
    
    # Validate each server entry
    for i, server in enumerate(parsed_data['servers']):
        if not isinstance(server, dict):
            logger.warning(f"Ollama server {i} is not a dictionary")
            return False
        
        required_fields = ['id', 'serverName', 'serverAddress']
        for field in required_fields:
            if field not in server:
                logger.warning(f"Ollama server {i} missing required field: {field}")
                return False
    
    logger.debug("Ollama settings format validation passed")
    return True


def create_default_ollama_settings() -> Dict[str, Any]:
    """
    Create minimal default Ollama settings structure.
    This should only be used when no settings exist at all.
    
    Returns:
        Minimal default Ollama settings dictionary
    """
    return {
        "servers": []
    }