"""
API endpoints for AI providers.
"""
import os
import json
import time
import asyncio
import logging
import traceback
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, HTTPException, Depends, Body, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text, select
from app.core.database import get_db
from app.core.auth_deps import require_user, optional_user
from app.core.auth_context import AuthContext
from app.core.rate_limit_deps import rate_limit_user
from app.models.settings import SettingDefinition, SettingScope, SettingInstance
from app.models.user import User
from app.ai_providers.registry import provider_registry
from app.ai_providers.ollama import OllamaProvider
from app.utils.json_parsing import safe_encrypted_json_parse, validate_ollama_settings_format, create_default_ollama_settings
from app.core.encryption import encryption_service, EncryptionError
from app.schemas.ai_providers import (
    TextGenerationRequest,
    ChatCompletionRequest,
    ValidationRequest,
)
from app.utils.persona_utils import apply_persona_prompt_and_params
from app.services.mcp_registry_service import MCPRegistryService

# Flag to enable/disable test routes (set to False in production)
TEST_ROUTES_ENABLED = os.getenv("ENABLE_TEST_ROUTES", "True").lower() == "true"

router = APIRouter()

DEFAULT_AUTO_CONTINUE_PROMPT = "Continue exactly where you left off. Do not repeat prior text."
DEFAULT_AUTO_CONTINUE_MAX_PASSES = 2
DEFAULT_AUTO_CONTINUE_MIN_PROGRESS_CHARS = 1


def extract_finish_reason(chunk: Dict[str, Any]) -> Optional[str]:
    """Extract finish_reason from provider chunk variants."""
    if not isinstance(chunk, dict):
        return None

    finish_reason = chunk.get("finish_reason")
    if isinstance(finish_reason, str) and finish_reason.strip():
        return finish_reason.strip()

    choices = chunk.get("choices")
    if isinstance(choices, list) and choices:
        choice = choices[0] if isinstance(choices[0], dict) else {}
        finish_reason = choice.get("finish_reason")
        if isinstance(finish_reason, str) and finish_reason.strip():
            return finish_reason.strip()

    metadata = chunk.get("metadata")
    if isinstance(metadata, dict):
        done_reason = metadata.get("done_reason")
        if isinstance(done_reason, str) and done_reason.strip():
            return done_reason.strip()

    return None


def extract_chunk_content(chunk: Dict[str, Any]) -> str:
    """Extract text content from provider chunk variants."""
    if not isinstance(chunk, dict):
        return ""

    text = chunk.get("text")
    if isinstance(text, str):
        return text

    choices = chunk.get("choices")
    if isinstance(choices, list) and choices:
        choice = choices[0] if isinstance(choices[0], dict) else {}
        delta = choice.get("delta")
        if isinstance(delta, dict):
            content = delta.get("content")
            if isinstance(content, str):
                return content

        choice_text = choice.get("text")
        if isinstance(choice_text, str):
            return choice_text

    return ""


def extract_response_content(result: Dict[str, Any]) -> str:
    """Extract assistant text from a non-stream provider response."""
    if not isinstance(result, dict):
        return ""

    choices = result.get("choices")
    if isinstance(choices, list) and choices:
        first_choice = choices[0] if isinstance(choices[0], dict) else {}
        message = first_choice.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                return content
        text_value = first_choice.get("text")
        if isinstance(text_value, str):
            return text_value

    direct_content = result.get("content")
    if isinstance(direct_content, str):
        return direct_content

    direct_text = result.get("text")
    if isinstance(direct_text, str):
        return direct_text

    message = result.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content

    return ""


def _decode_tool_arguments(raw_arguments: Any) -> Dict[str, Any]:
    if isinstance(raw_arguments, dict):
        return raw_arguments
    if isinstance(raw_arguments, str):
        raw = raw_arguments.strip()
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def extract_response_tool_calls(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Normalize tool call payloads from provider responses."""
    if not isinstance(result, dict):
        return []

    candidates: List[Dict[str, Any]] = []

    top_level = result.get("tool_calls")
    if isinstance(top_level, list):
        candidates.extend([item for item in top_level if isinstance(item, dict)])

    message = result.get("message")
    if isinstance(message, dict) and isinstance(message.get("tool_calls"), list):
        candidates.extend(
            [item for item in message.get("tool_calls", []) if isinstance(item, dict)]
        )

    choices = result.get("choices")
    if isinstance(choices, list) and choices:
        first_choice = choices[0] if isinstance(choices[0], dict) else {}
        choice_message = first_choice.get("message")
        if isinstance(choice_message, dict) and isinstance(choice_message.get("tool_calls"), list):
            candidates.extend(
                [item for item in choice_message.get("tool_calls", []) if isinstance(item, dict)]
            )

    normalized_calls: List[Dict[str, Any]] = []
    for call in candidates:
        function = call.get("function") if isinstance(call.get("function"), dict) else {}
        name = function.get("name") or call.get("name")
        if not isinstance(name, str) or not name.strip():
            continue

        raw_arguments = function.get("arguments")
        if raw_arguments is None:
            raw_arguments = call.get("arguments")
        arguments = _decode_tool_arguments(raw_arguments)
        normalized_calls.append(
            {
                "id": call.get("id"),
                "name": name.strip(),
                "arguments": arguments,
                "raw_arguments": raw_arguments,
            }
        )

    return normalized_calls


def extract_chunk_tool_call_deltas(chunk: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract tool-call deltas from a streaming chunk."""
    if not isinstance(chunk, dict):
        return []

    candidates: List[Dict[str, Any]] = []

    top_level = chunk.get("tool_calls")
    if isinstance(top_level, list):
        candidates.extend([item for item in top_level if isinstance(item, dict)])

    choices = chunk.get("choices")
    if isinstance(choices, list) and choices:
        first_choice = choices[0] if isinstance(choices[0], dict) else {}
        delta = first_choice.get("delta")
        if isinstance(delta, dict) and isinstance(delta.get("tool_calls"), list):
            candidates.extend(
                [item for item in delta.get("tool_calls", []) if isinstance(item, dict)]
            )
        message = first_choice.get("message")
        if isinstance(message, dict) and isinstance(message.get("tool_calls"), list):
            candidates.extend(
                [item for item in message.get("tool_calls", []) if isinstance(item, dict)]
            )

    normalized: List[Dict[str, Any]] = []
    for item in candidates:
        function = item.get("function") if isinstance(item.get("function"), dict) else {}
        normalized.append(
            {
                "id": item.get("id"),
                "index": item.get("index"),
                "name": function.get("name") or item.get("name"),
                "arguments": function.get("arguments", item.get("arguments")),
            }
        )
    return normalized


def update_stream_tool_call_buffer(
    buffer: Dict[str, Dict[str, Any]],
    deltas: List[Dict[str, Any]],
) -> None:
    for delta in deltas:
        raw_key = delta.get("id")
        if not isinstance(raw_key, str) or not raw_key.strip():
            index_value = delta.get("index")
            if isinstance(index_value, int):
                raw_key = f"idx:{index_value}"
            else:
                raw_key = f"idx:{len(buffer)}"

        entry = buffer.get(raw_key)
        if entry is None:
            entry = {
                "id": delta.get("id"),
                "index": delta.get("index"),
                "name": None,
                "arguments_buffer": "",
                "arguments_object": None,
            }
            buffer[raw_key] = entry

        name = delta.get("name")
        if isinstance(name, str) and name.strip():
            entry["name"] = name.strip()

        arguments = delta.get("arguments")
        if isinstance(arguments, str):
            entry["arguments_buffer"] += arguments
        elif isinstance(arguments, dict):
            existing_object = entry.get("arguments_object")
            if isinstance(existing_object, dict):
                existing_object.update(arguments)
                entry["arguments_object"] = existing_object
            else:
                entry["arguments_object"] = dict(arguments)


def finalize_stream_tool_calls(buffer: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    records = list(buffer.values())
    records.sort(
        key=lambda item: (
            item.get("index") if isinstance(item.get("index"), int) else 10_000,
            item.get("id") or "",
        )
    )

    finalized: List[Dict[str, Any]] = []
    for item in records:
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            continue

        arguments: Dict[str, Any] = {}
        buffer_text = item.get("arguments_buffer")
        if isinstance(buffer_text, str) and buffer_text.strip():
            parsed = _decode_tool_arguments(buffer_text)
            if isinstance(parsed, dict):
                arguments = parsed

        if not arguments and isinstance(item.get("arguments_object"), dict):
            arguments = dict(item["arguments_object"])

        finalized.append(
            {
                "id": item.get("id"),
                "name": name.strip(),
                "arguments": arguments,
                "raw_arguments": buffer_text or item.get("arguments_object"),
            }
        )

    return finalized


def is_truncation_finish_reason(
    reason: Optional[str],
    provider: Optional[str] = None,
    chunk: Optional[Dict[str, Any]] = None,
) -> bool:
    """Return True when finish_reason indicates token-limit truncation."""
    if not reason:
        if not isinstance(chunk, dict):
            return False
        metadata = chunk.get("metadata")
        done_reason = metadata.get("done_reason") if isinstance(metadata, dict) else None
        reason = done_reason if isinstance(done_reason, str) else None
        if not reason:
            return False

    normalized = reason.strip().lower()
    provider_name = (provider or "").strip().lower()

    truncation_reasons = {
        "length",
        "max_tokens",
        "max_token",
        "max_output_tokens",
        "token_limit",
    }
    if provider_name == "claude":
        truncation_reasons.update({"max_tokens", "length"})
    if provider_name == "ollama":
        truncation_reasons.update({"length", "max_tokens"})

    if normalized in truncation_reasons:
        return True

    return "length" in normalized or "max_tokens" in normalized


def build_continuation_messages(
    base_messages: List[Dict[str, Any]],
    full_response: str,
    continuation_prompt: str = DEFAULT_AUTO_CONTINUE_PROMPT,
) -> List[Dict[str, Any]]:
    """Build a continuation prompt using the merged assistant response so far."""
    return [
        *base_messages,
        {"role": "assistant", "content": full_response},
        {"role": "user", "content": continuation_prompt},
    ]


def _as_bool(value: Any, default: bool) -> bool:
    """Parse permissive bool values from request params."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"false", "0", "no", "off", ""}:
            return False
        if lowered in {"true", "1", "yes", "on"}:
            return True
    return bool(value)


def _as_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    """Parse bounded int values from request params."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _normalize_scope_mode(value: Any) -> str:
    if not isinstance(value, str):
        return "none"
    normalized = value.strip().lower()
    if normalized in {"none", "project"}:
        return normalized
    return "none"


def _extract_mcp_scope_params(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract MCP request-contract fields from provider params.
    These values are local orchestration controls and are not sent to model providers directly.
    """
    extracted: Dict[str, Any] = {
        "mcp_tools_enabled": _as_bool(params.pop("mcp_tools_enabled", False), False),
        "mcp_scope_mode": _normalize_scope_mode(params.pop("mcp_scope_mode", "none")),
        "mcp_project_slug": params.pop("mcp_project_slug", None),
        "mcp_project_name": params.pop("mcp_project_name", None),
        "mcp_project_lifecycle": params.pop("mcp_project_lifecycle", None),
        "mcp_project_source": params.pop("mcp_project_source", "ui"),
        "mcp_plugin_slug": params.pop("mcp_plugin_slug", None),
        "mcp_sync_on_request": _as_bool(params.pop("mcp_sync_on_request", True), True),
        "mcp_max_tools": _as_int(params.pop("mcp_max_tools", 32), default=32, minimum=1, maximum=128),
        "mcp_max_schema_bytes": _as_int(
            params.pop("mcp_max_schema_bytes", 128_000),
            default=128_000,
            minimum=2_048,
            maximum=500_000,
        ),
    }
    return extracted


def _normalize_approval_action(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if normalized in {"approve", "approved", "allow", "yes"}:
        return "approve"
    if normalized in {"reject", "rejected", "deny", "denied", "no"}:
        return "reject"
    return None


def _extract_mcp_approval_params(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract explicit approval-resume controls from params.
    Supports both flat keys and nested `mcp_approval` object.
    """
    nested_raw = params.pop("mcp_approval", None)
    nested = nested_raw if isinstance(nested_raw, dict) else {}

    action = _normalize_approval_action(
        nested.get("action", params.pop("mcp_approval_action", None))
    )
    request_id = nested.get("request_id", params.pop("mcp_approval_request_id", None))
    tool = nested.get("tool", params.pop("mcp_approval_tool", None))
    arguments = nested.get("arguments", params.pop("mcp_approval_arguments", None))
    if not isinstance(arguments, dict):
        arguments = None

    if not isinstance(request_id, str) or not request_id.strip():
        request_id = None
    if not isinstance(tool, str) or not tool.strip():
        tool = None

    return {
        "action": action,
        "request_id": request_id.strip() if isinstance(request_id, str) else None,
        "tool": tool.strip() if isinstance(tool, str) else None,
        "arguments": arguments,
    }


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _build_approval_request_payload(
    *,
    tool: str,
    safety_class: str,
    arguments: Dict[str, Any],
    summary: Optional[str] = None,
    request_id: Optional[str] = None,
    scope: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "type": "approval_request",
        "request_id": request_id or f"apr_{int(time.time() * 1000)}",
        "tool": tool,
        "safety_class": safety_class,
        "summary": summary or f"Approval required to run mutating tool '{tool}'.",
        "arguments": arguments,
        "status": "pending",
        "created_at": _utc_timestamp(),
    }
    if isinstance(scope, dict) and scope:
        payload["scope"] = {
            "mcp_scope_mode": scope.get("mcp_scope_mode"),
            "mcp_project_slug": scope.get("mcp_project_slug"),
            "mcp_project_name": scope.get("mcp_project_name"),
            "mcp_project_lifecycle": scope.get("mcp_project_lifecycle"),
            "mcp_project_source": scope.get("mcp_project_source"),
            "mcp_plugin_slug": scope.get("mcp_plugin_slug"),
        }
    return payload


# Helper function to get provider instance from request
async def get_provider_instance_from_request(request, db):
    """Helper function to get provider instance from request."""
    # Use current user if not specified
    user_id = request.user_id or "current"
    
    # Normalize user_id by removing hyphens if present
    if user_id != "current":
        user_id = user_id.replace("-", "")
    
    logger = logging.getLogger(__name__)
    print(f"🚀 PROVIDER REQUEST RECEIVED")
    print(f"📊 Provider: {request.provider}")
    print(f"📊 Settings ID: {request.settings_id}")
    print(f"📊 Server ID: {request.server_id}")
    print(f"📊 Model: {getattr(request, 'model', 'N/A')}")
    print(f"📊 User ID: {user_id}")
    logger.info(f"Getting provider instance for: settings_id={request.settings_id}, user_id={user_id}")
    logger.info(f"Original user_id from request: {request.user_id}")
    
    # Helper to map provider to API key env var
    def _get_env_api_key(provider_name: str) -> Optional[str]:
        env_map = {
            "openrouter": "OPENROUTER_API_KEY",
            "openai": "OPENAI_API_KEY",
            "claude": "ANTHROPIC_API_KEY",
            "groq": "GROQ_API_KEY",
        }
        env_var = env_map.get(provider_name.lower())
        if env_var:
            return os.getenv(env_var)
        return None

    api_key_providers = {"openrouter", "openai", "claude", "groq"}

    try:
        # Get settings for the specified user
        logger.info(f"Fetching settings with definition_id={request.settings_id}, user_id={user_id}")
        settings = await SettingInstance.get_all_parameterized(
            db,
            definition_id=request.settings_id,
            scope=SettingScope.USER.value,
            user_id=user_id
        )
        # Fallback to legacy direct SQL if ORM returns none (compat with legacy enum storage)
        if not settings or len(settings) == 0:
            logger.info("ORM returned no settings; falling back to direct SQL query for settings")
            settings = await SettingInstance.get_all(
                db,
                definition_id=request.settings_id,
                scope=SettingScope.USER.value,
                user_id=user_id
            )
        
        logger.info(f"Found {len(settings)} settings for user_id={user_id}")
        
        if not settings or len(settings) == 0:
            logger.error(f"No settings found for definition_id={request.settings_id}, user_id={user_id}")
            # For testing purposes, use a default configuration if settings are not found
            if request.settings_id == "ollama_settings" and request.provider == "ollama":
                logger.warning(f"Using default Ollama configuration for testing. settings_id={request.settings_id}, user_id={user_id}")
                server = {
                    "id": request.server_id,
                    "serverName": "Test Ollama Server",
                    "serverAddress": "http://localhost:11434",
                    "apiKey": ""
                }
                config = {
                    "server_url": server["serverAddress"],
                    "api_key": server["apiKey"],
                    "server_name": server["serverName"]
                }
                
                # Get provider instance
                logger.info(f"Getting provider instance for: {request.provider}, {request.server_id}")
                provider_instance = await provider_registry.get_provider(
                    request.provider,
                    request.server_id,
                    config
                )
                
                logger.info(f"Got provider instance: {provider_instance.provider_name}")
                
                return provider_instance
            elif request.provider in api_key_providers:
                # Try environment fallback for API-key providers
                env_key = _get_env_api_key(request.provider)
                if env_key:
                    logger.warning(f"Using environment API key for provider '{request.provider}' due to missing settings")
                    if request.provider == "openai":
                        config = {"api_key": env_key, "server_url": "https://api.openai.com/v1", "server_name": "OpenAI API"}
                    elif request.provider == "openrouter":
                        config = {"api_key": env_key, "server_url": "https://openrouter.ai/api/v1", "server_name": "OpenRouter API"}
                    elif request.provider == "claude":
                        config = {"api_key": env_key, "server_url": "https://api.anthropic.com", "server_name": "Claude API"}
                    elif request.provider == "groq":
                        config = {"api_key": env_key, "server_url": "https://api.groq.com", "server_name": "Groq API"}
                    else:
                        config = {"api_key": env_key}

                    provider_instance = await provider_registry.get_provider(
                        request.provider,
                        request.server_id,
                        config
                    )
                    logger.info(f"Got provider instance with env key: {provider_instance.provider_name}")
                    return provider_instance

                # No settings and no env fallback
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"{request.provider.capitalize()} API key is not configured. "
                        f"Please add your API key in Settings (definition_id={request.settings_id}) "
                        f"or set the environment variable {('OPENROUTER_API_KEY' if request.provider=='openrouter' else 'OPENAI_API_KEY' if request.provider=='openai' else 'ANTHROPIC_API_KEY' if request.provider=='claude' else 'GROQ_API_KEY')} and restart."
                    ),
                )
            else:
                # For other providers, raise an error
                raise HTTPException(
                    status_code=404,
                    detail=f"Provider settings not found for settings_id={request.settings_id}, user_id={user_id}. "
                           f"Please ensure the settings are properly configured."
                )
        
        # Use the first setting found
        setting = settings[0]
        logger.debug(f"Using setting with ID: {setting['id'] if isinstance(setting, dict) else setting.id}")
        
        # Extract configuration from settings value using robust parsing
        setting_value = setting['value'] if isinstance(setting, dict) else setting.value
        setting_id = setting['id'] if isinstance(setting, dict) else setting.id
        
        # Use our robust JSON parsing utility that handles encryption issues
        try:
            # If the value appears encrypted (when using direct SQL dict path), try decrypting first
            if isinstance(setting_value, str):
                try:
                    from app.core.encryption import encryption_service as _enc, EncryptionError as _EncErr
                    if _enc.is_encrypted_value(setting_value):
                        logger.info("Attempting to decrypt settings value via encryption_service")
                        decrypted = _enc.decrypt_field('settings_instances', 'value', setting_value)
                        setting_value = decrypted
                except Exception as dec_err:
                    logger.debug(f"Settings decryption attempt failed or not needed: {dec_err}")

            value_dict = safe_encrypted_json_parse(
                setting_value,
                context=f"settings_id={request.settings_id}, user_id={user_id}",
                setting_id=setting_id,
                definition_id=request.settings_id
            )
            
            # Ensure we have a dictionary
            if not isinstance(value_dict, dict):
                logger.error(f"Parsed value is not a dictionary: {type(value_dict)}")
                # For Ollama settings, provide a default structure
                if 'ollama' in request.settings_id.lower():
                    logger.warning("Creating default Ollama settings structure")
                    value_dict = create_default_ollama_settings()
                elif request.provider in api_key_providers:
                    # Try environment fallback for API-key providers
                    env_key = _get_env_api_key(request.provider)
                    if env_key:
                        logger.warning(f"Using environment API key for provider '{request.provider}' due to non-dict settings value")
                        value_dict = {"api_key": env_key}
                    else:
                        raise HTTPException(
                            status_code=400,
                            detail=(
                                f"{request.provider.capitalize()} API key could not be read from settings. "
                                f"Please re-enter your key in Settings (definition_id={request.settings_id}) "
                                f"or set the appropriate environment variable."
                            )
                        )
                else:
                    raise HTTPException(
                        status_code=500,
                        detail=f"Settings value must be a dictionary, got {type(value_dict)}. "
                               f"Setting ID: {setting_id}"
                    )
            
            logger.debug(f"Successfully parsed settings value for {request.settings_id}")
            
        except ValueError as e:
            logger.error(f"Failed to parse encrypted settings: {e}")
            # For Ollama settings, provide helpful error message and fallback
            if 'ollama' in request.settings_id.lower():
                logger.info("Ollama settings parsing failed, using fallback configuration")
                value_dict = create_default_ollama_settings()
            elif request.provider in api_key_providers:
                # Try environment fallback for API-key providers
                env_key = _get_env_api_key(request.provider)
                if env_key:
                    logger.warning(f"Using environment API key for provider '{request.provider}' due to settings parse failure")
                    value_dict = {"api_key": env_key}
                else:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"{request.provider.capitalize()} API key could not be decrypted or parsed. "
                            f"Please re-enter your key in Settings (definition_id={request.settings_id}) "
                            f"or set the appropriate environment variable and restart."
                        )
                    )
            else:
                raise HTTPException(
                    status_code=500,
                    detail=str(e)
                )
        except Exception as e:
            logger.error(f"Unexpected error parsing settings: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Unexpected error parsing settings: {str(e)}. Setting ID: {setting_id}"
            )
        
        # Add specific validation for Ollama settings
        if 'ollama' in request.settings_id.lower():
            logger.info("Validating Ollama settings format")
            if not validate_ollama_settings_format(value_dict):
                logger.warning("Ollama settings format validation failed, using default structure")
                value_dict = create_default_ollama_settings()
            else:
                logger.info("Ollama settings format validation passed")
        
        # Handle different provider configurations
        if request.provider == "openai":
            # OpenAI uses simple api_key structure
            logger.info("Processing OpenAI provider configuration")
            api_key = value_dict.get("api_key") or value_dict.get("apiKey") or _get_env_api_key("openai") or ""
            if not api_key:
                logger.error("OpenAI API key is missing")
                raise HTTPException(
                    status_code=400,
                    detail="OpenAI API key is required. Please configure your OpenAI API key in settings."
                )
            
            # For OpenAI, we create a virtual server configuration
            config = {
                "api_key": api_key,
                "server_url": "https://api.openai.com/v1",  # Default OpenAI API URL
                "server_name": "OpenAI API"
            }
            logger.info(f"Created OpenAI config with API key")
        elif request.provider == "openrouter":
            # OpenRouter uses simple api_key structure (similar to OpenAI)
            logger.info("Processing OpenRouter provider configuration")
            api_key = value_dict.get("api_key") or value_dict.get("apiKey") or _get_env_api_key("openrouter") or ""
            if not api_key:
                logger.error("OpenRouter API key is missing")
                raise HTTPException(
                    status_code=400,
                    detail="OpenRouter API key is required. Please configure your OpenRouter API key in settings."
                )
            
            # For OpenRouter, we create a virtual server configuration
            config = {
                "api_key": api_key,
                "server_url": "https://openrouter.ai/api/v1",  # OpenRouter API URL
                "server_name": "OpenRouter API"
            }
            logger.info(f"Created OpenRouter config with API key")
        elif request.provider == "claude":
            # Claude uses simple api_key structure (similar to OpenAI)
            logger.info("Processing Claude provider configuration")
            api_key = value_dict.get("api_key") or value_dict.get("apiKey") or _get_env_api_key("claude") or ""
            if not api_key:
                logger.error("Claude API key is missing")
                raise HTTPException(
                    status_code=400,
                    detail="Claude API key is required. Please configure your Claude API key in settings."
                )
            
            # For Claude, we create a virtual server configuration
            config = {
                "api_key": api_key,
                "server_url": "https://api.anthropic.com",  # Claude API URL
                "server_name": "Claude API"
            }
            logger.info(f"Created Claude config with API key")
        elif request.provider == "groq":
            # Groq uses simple api_key structure (similar to OpenAI)
            logger.info("Processing Groq provider configuration")
            api_key = value_dict.get("api_key") or value_dict.get("apiKey") or _get_env_api_key("groq") or ""
            if not api_key:
                logger.error("Groq API key is missing")
                raise HTTPException(
                    status_code=400,
                    detail="Groq API key is required. Please configure your Groq API key in settings."
                )
            
            # For Groq, we create a virtual server configuration
            config = {
                "api_key": api_key,
                "server_url": "https://api.groq.com",  # Groq API URL
                "server_name": "Groq API"
            }
            logger.info(f"Created Groq config with API key")
        else:
            # Other providers (like Ollama) use servers array
            logger.debug("Processing server-based provider configuration")
            servers = value_dict.get("servers", [])
            logger.debug(f"Found {len(servers)} servers in settings")
            
            logger.debug("Processing server-based provider configuration")
            servers = value_dict.get("servers", [])
            logger.debug(f"Found {len(servers)} servers in settings")
            
            # Find the specific server by ID
            logger.debug(f"Looking for server with ID: '{request.server_id}'")
            server = next((s for s in servers if s.get("id") == request.server_id), None)
            
            if not server:
                # Provide detailed error message about available servers
                if servers:
                    available_servers = [f"{s.get('serverName', 'Unknown')} (ID: {s.get('id', 'Unknown')})" for s in servers]
                    available_list = ", ".join(available_servers)
                    logger.error(f"❌ Server with ID '{request.server_id}' not found")
                    logger.error(f"📋 Available servers: {available_list}")
                    raise HTTPException(
                        status_code=404,
                        detail=f"Ollama server '{request.server_id}' not found. "
                               f"Available servers: {available_list}. "
                               f"Please select a valid server from your Ollama settings."
                    )
                else:
                    logger.error(f"❌ No Ollama servers configured")
                    raise HTTPException(
                        status_code=404,
                        detail="No Ollama servers are configured. "
                               "Please add at least one Ollama server in your settings before using this provider."
                    )
            
            logger.debug(f"Found server: {server.get('serverName')} (ID: {server.get('id')})")
            
            # Create provider configuration from server details
            server_url = server.get("serverAddress")
            logger.debug(f"Server URL from settings: '{server_url}'")
            
            if not server_url:
                logger.error(f"Server URL is missing for server: {server.get('id')}")
                raise HTTPException(
                    status_code=400,
                    detail=f"Server URL is missing for server: {server.get('id')}. "
                           f"Please update your server configuration with a valid URL."
                )
                
            config = {
                "server_url": server_url,
                "api_key": server.get("apiKey", ""),
                "server_name": server.get("serverName", "Unknown Server")
            }
            
            logger.debug(f"Created server config: {config.get('server_name')} -> {config.get('server_url')}")
        
        # Get provider instance
        logger.debug(f"Getting provider instance for: {request.provider}, {request.server_id}")
        provider_instance = await provider_registry.get_provider(
            request.provider,
            request.server_id,
            config
        )
        
        logger.info(f"Got provider instance: {provider_instance.provider_name}")
        
        return provider_instance
    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        logger.error(f"Error in get_provider_instance_from_request: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(
            status_code=500,
            detail=f"Error getting provider instance: {str(e)}. "
                   f"Please check your configuration and try again."
        )


@router.get("/providers")
async def get_providers():
    """Get list of available AI providers."""
    return {
        "providers": provider_registry.get_available_providers()
    }


@router.get("/catalog")
async def get_provider_catalog(
    user_id: Optional[str] = Query("current", description="User ID"),
    db: AsyncSession = Depends(get_db),
    auth: Optional[AuthContext] = Depends(optional_user)
):
    """
    Return provider metadata for UI selection (provider -> settings_id, server strategy, etc)
    plus per-user configured/enabled signals (no secrets).
    """
    # Resolve user_id from authentication if "current" is specified
    if user_id == "current":
        if not auth:
            raise HTTPException(status_code=401, detail="Authentication required")
        user_id = str(auth.user_id)

    # Normalize user_id by removing hyphens if present
    user_id = user_id.replace("-", "")

    provider_meta: Dict[str, Dict[str, Any]] = {
        "ollama": {
            "settings_id": "ollama_servers_settings",
            "server_strategy": "settings_servers",
            "default_server_id": None,
        },
        "openai": {
            "settings_id": "openai_api_keys_settings",
            "server_strategy": "single",
            "default_server_id": "openai_default_server",
        },
        "openrouter": {
            "settings_id": "openrouter_api_keys_settings",
            "server_strategy": "single",
            "default_server_id": "openrouter_default_server",
        },
        "claude": {
            "settings_id": "claude_api_keys_settings",
            "server_strategy": "single",
            "default_server_id": "claude_default_server",
        },
        "groq": {
            "settings_id": "groq_api_keys_settings",
            "server_strategy": "single",
            "default_server_id": "groq_default_server",
        },
    }

    def _get_env_api_key(provider_name: str) -> Optional[str]:
        env_map = {
            "openrouter": "OPENROUTER_API_KEY",
            "openai": "OPENAI_API_KEY",
            "claude": "ANTHROPIC_API_KEY",
            "groq": "GROQ_API_KEY",
        }
        env_var = env_map.get(provider_name.lower())
        if env_var:
            return os.getenv(env_var)
        return None

    async def _load_settings_value(settings_id: str) -> Optional[Dict[str, Any]]:
        if not settings_id:
            return None
        settings = await SettingInstance.get_all_parameterized(
            db,
            definition_id=settings_id,
            scope=SettingScope.USER.value,
            user_id=user_id
        )
        if not settings or len(settings) == 0:
            settings = await SettingInstance.get_all(
                db,
                definition_id=settings_id,
                scope=SettingScope.USER.value,
                user_id=user_id
            )
        if not settings or len(settings) == 0:
            return None

        setting = settings[0]
        setting_value = setting['value'] if isinstance(setting, dict) else getattr(setting, 'value', None)
        setting_instance_id = setting['id'] if isinstance(setting, dict) else getattr(setting, 'id', '')

        if isinstance(setting_value, dict):
            return setting_value

        raw_value = setting_value
        try:
            if isinstance(raw_value, str) and encryption_service.is_encrypted_value(raw_value):
                raw_value = encryption_service.decrypt_field('settings_instances', 'value', raw_value)

            value_dict = safe_encrypted_json_parse(
                raw_value,
                context=f"catalog settings_id={settings_id}, user_id={user_id}",
                setting_id=setting_instance_id,
                definition_id=settings_id,
            )

            if isinstance(value_dict, str):
                # Some legacy rows stored the API key directly as a string
                value_dict = {"api_key": value_dict}

            return value_dict if isinstance(value_dict, dict) else None
        except Exception:
            return None

    providers: List[Dict[str, Any]] = []
    for provider in provider_registry.get_available_providers():
        meta = provider_meta.get(provider, {})
        settings_id = meta.get("settings_id")
        server_strategy = meta.get("server_strategy", "unknown")
        default_server_id = meta.get("default_server_id")

        settings_value = await _load_settings_value(settings_id) if settings_id else None

        enabled = True
        if isinstance(settings_value, dict) and isinstance(settings_value.get("enabled"), bool):
            enabled = bool(settings_value.get("enabled"))

        configured = False
        configured_via = None
        server_count = 0

        if provider == "ollama":
            servers = []
            if isinstance(settings_value, dict):
                servers = settings_value.get("servers", []) or []
            servers = servers if isinstance(servers, list) else []
            server_count = len(servers)
            configured = any(bool(s.get("serverAddress")) for s in servers if isinstance(s, dict))
            configured_via = "settings" if configured else None
        else:
            api_key = None
            if isinstance(settings_value, dict):
                api_key = settings_value.get("api_key") or settings_value.get("apiKey")
            if isinstance(api_key, str) and api_key.strip():
                configured = True
                configured_via = "settings"
            else:
                env_key = _get_env_api_key(provider)
                if isinstance(env_key, str) and env_key.strip():
                    configured = True
                    configured_via = "env"

        providers.append({
            "id": provider,
            "label": provider.replace("_", " ").title(),
            "settings_id": settings_id,
            "server_strategy": server_strategy,
            "default_server_id": default_server_id,
            "configured": configured,
            "configured_via": configured_via,
            "enabled": enabled,
            "server_count": server_count,
        })

    return {
        "user_id": user_id,
        "providers": providers,
    }


@router.post("/validate")
async def validate_provider(request: ValidationRequest):
    """Validate connection to a provider."""
    try:
        provider_name = request.provider
        if provider_name not in provider_registry.get_available_providers():
            raise HTTPException(status_code=404, detail=f"Provider '{provider_name}' not found")
        
        # Create a temporary provider instance for validation
        provider_class = provider_registry._providers.get(provider_name)
        provider = provider_class()
        result = await provider.validate_connection(request.config)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/models")
async def get_models(
    provider: str = Query(..., description="Provider name"),
    settings_id: str = Query(..., description="Settings ID"),
    server_id: str = Query(..., description="Server ID"),
    user_id: Optional[str] = Query("current", description="User ID"),
    db: AsyncSession = Depends(get_db),
    auth: Optional[AuthContext] = Depends(optional_user),
    _: None = Depends(rate_limit_user(limit=100, window_seconds=60))
):
    """Get available models from a provider."""
    try:
        # Resolve user_id from authentication if "current" is specified
        if user_id == "current":
            if not auth:
                raise HTTPException(status_code=401, detail="Authentication required")
            user_id = str(auth.user_id)
        
        # Normalize user_id by removing hyphens if present
        user_id = user_id.replace("-", "")
            
        print(f"Getting models for: provider={provider}, settings_id={settings_id}, server_id={server_id}, user_id={user_id}")
        
        # Get settings for the specified user
        settings = await SettingInstance.get_all_parameterized(
            db, 
            definition_id=settings_id,
            scope=SettingScope.USER.value, 
            user_id=user_id
        )
        
        print(f"Found {len(settings)} settings for user_id={user_id}")
        
        if not settings or len(settings) == 0:
            print("ORM returned no settings; falling back to direct SQL query")
            settings = await SettingInstance.get_all(
                db,
                definition_id=settings_id,
                scope=SettingScope.USER.value,
                user_id=user_id
            )

        if not settings or len(settings) == 0:
            raise HTTPException(status_code=404, detail=f"Provider settings not found for user_id={user_id}")

        # Use the first setting found
        setting = settings[0]

        # Extract configuration from settings value
        setting_id = setting['id'] if isinstance(setting, dict) else getattr(setting, 'id', '')
        setting_value = setting['value'] if isinstance(setting, dict) else getattr(setting, 'value', None)

        if isinstance(setting_value, (dict, list)):
            value_dict = setting_value
        else:
            raw_value = setting_value
            try:
                if isinstance(raw_value, str) and encryption_service.is_encrypted_value(raw_value):
                    raw_value = encryption_service.decrypt_field('settings_instances', 'value', raw_value)

                value_dict = safe_encrypted_json_parse(
                    raw_value,
                    context=f"provider={provider}, settings_id={settings_id}, user_id={user_id}",
                    setting_id=setting_id,
                    definition_id=settings_id,
                )

                if isinstance(value_dict, str):
                    # Some legacy rows stored the API key directly as a string
                    value_dict = {"api_key": value_dict}

            except Exception as parse_err:
                raise HTTPException(status_code=400, detail="Invalid settings value format") from parse_err

        print(f"Parsed settings value: {value_dict}")
        
        # Handle different provider configurations
        if provider == "openai":
            # OpenAI uses simple api_key structure
            api_key = value_dict.get("api_key", "")
            if not api_key:
                raise HTTPException(status_code=400, detail="OpenAI API key is required")
            
            config = {
                "api_key": api_key,
                "server_url": "https://api.openai.com/v1",  # Default OpenAI API URL
                "server_name": "OpenAI API"
            }
            print(f"Created OpenAI config with API key")
        elif provider == "openrouter":
            # OpenRouter uses simple api_key structure
            api_key = value_dict.get("api_key") or value_dict.get("apiKey") or ""
            if not api_key:
                raise HTTPException(status_code=400, detail="OpenRouter API key is required")
            
            config = {
                "api_key": api_key,
                "server_url": "https://openrouter.ai/api/v1",  # OpenRouter API URL
                "server_name": "OpenRouter API"
            }
            print(f"Created OpenRouter config with API key")
        elif provider == "claude":
            # Claude uses simple api_key structure (similar to OpenAI)
            api_key = value_dict.get("api_key", "")
            if not api_key:
                raise HTTPException(status_code=400, detail="Claude API key is required")
            
            # For Claude, we create a virtual server configuration
            config = {
                "api_key": api_key,
                "server_url": "https://api.anthropic.com",  # Claude API URL
                "server_name": "Claude API"
            }
            print(f"Created Claude config with API key")
        elif provider == "groq":
            # Groq uses simple api_key structure (similar to OpenAI)
            api_key = value_dict.get("api_key") or value_dict.get("apiKey") or ""
            if not api_key:
                raise HTTPException(status_code=400, detail="Groq API key is required")
            
            # For Groq, we create a virtual server configuration
            config = {
                "api_key": api_key,
                "server_url": "https://api.groq.com",  # Groq API URL
                "server_name": "Groq API"
            }
            print(f"Created Groq config with API key")
        else:
            # Other providers (like Ollama) use servers array
            servers = value_dict.get("servers", [])
            print(f"Found {len(servers)} servers in settings")
            
            # Find the specific server by ID
            server = next((s for s in servers if s.get("id") == server_id), None)
            if not server and servers:
                # If the requested server ID is not found but there are servers available,
                # use the first server as a fallback
                print(f"Server with ID {server_id} not found, using first available server as fallback")
                server = servers[0]
                print(f"Using fallback server: {server.get('serverName')} ({server.get('id')})")
            
            if not server:
                raise HTTPException(status_code=404, detail=f"Server not found with ID: {server_id}")
            
            print(f"Found server: {server.get('serverName')}")
            
            # Create provider configuration from server details
            config = {
                "server_url": server.get("serverAddress"),
                "api_key": server.get("apiKey", ""),
                "server_name": server.get("serverName")
            }
        
        print(f"Created config with server_url: {config['server_url']}")
        
        # Get provider instance
        provider_instance = await provider_registry.get_provider(
            provider, 
            server_id,
            config
        )
        
        print(f"Got provider instance: {provider_instance.provider_name}")
        
        # Get models
        models = await provider_instance.get_models()
        print(f"Got {len(models)} models")
        
        return {
            "models": models
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error in get_models: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/all-models")
async def get_all_models(
    user_id: Optional[str] = Query("current", description="User ID"),
    db: AsyncSession = Depends(get_db),
    auth: Optional[AuthContext] = Depends(optional_user),
    _: None = Depends(rate_limit_user(limit=100, window_seconds=60))
):
    """Get models from ALL connected providers for a user."""
    try:
        # Resolve user_id from authentication if "current" is specified
        if user_id == "current":
            if not auth:
                raise HTTPException(status_code=401, detail="Authentication required")
            user_id = str(auth.user_id)
        
        # Normalize user_id by removing hyphens if present
        user_id = user_id.replace("-", "")
        
        print(f"Getting all models for user: {user_id}")
        
        # Define all possible provider settings
        provider_settings = [
            {
                "provider": "openai",
                "settings_id": "openai_api_keys_settings",
                "server_id": "openai_default_server"
            },
            {
                "provider": "openrouter", 
                "settings_id": "openrouter_api_keys_settings",
                "server_id": "openrouter_default_server"
            },
            {
                "provider": "claude",
                "settings_id": "claude_api_keys_settings", 
                "server_id": "claude_default_server"
            },
            {
                "provider": "groq",
                "settings_id": "groq_api_keys_settings",
                "server_id": "groq_default_server"
            },
            {
                "provider": "ollama",
                "settings_id": "ollama_servers_settings",
                "server_id": None  # Ollama uses dynamic server IDs
            }
        ]
        
        all_models = []
        errors = []
        successful_providers = 0
        
        # Helper to map provider to API key env var
        def _get_env_api_key(provider_name: str) -> Optional[str]:
            env_map = {
                "openrouter": "OPENROUTER_API_KEY",
                "openai": "OPENAI_API_KEY",
                "claude": "ANTHROPIC_API_KEY",
                "groq": "GROQ_API_KEY",
            }
            env_var = env_map.get(provider_name.lower())
            if env_var:
                return os.getenv(env_var)
            return None

        # Process each provider
        for provider_config in provider_settings:
            try:
                provider = provider_config["provider"]
                settings_id = provider_config["settings_id"]
                server_id = provider_config["server_id"]
                
                print(f"Processing provider: {provider}")
                
                # Get settings for this provider (prefer ORM; fallback to direct SQL if none)
                settings = await SettingInstance.get_all_parameterized(
                    db,
                    definition_id=settings_id,
                    scope=SettingScope.USER.value,
                    user_id=user_id
                )
                if not settings or len(settings) == 0:
                    print(f"ORM returned no settings for {provider}; falling back to direct SQL")
                    settings = await SettingInstance.get_all(
                        db,
                        definition_id=settings_id,
                        scope=SettingScope.USER.value,
                        user_id=user_id
                    )
                
                if not settings or len(settings) == 0:
                    print(f"No settings found for {provider}")
                    # For API-key providers, try env fallback
                    if provider in {"openai", "openrouter", "claude", "groq"}:
                        env_key = _get_env_api_key(provider)
                        if env_key:
                            try:
                                if provider == "openai":
                                    config = {"api_key": env_key, "server_url": "https://api.openai.com/v1", "server_name": "OpenAI API"}
                                elif provider == "openrouter":
                                    config = {"api_key": env_key, "server_url": "https://openrouter.ai/api/v1", "server_name": "OpenRouter API"}
                                elif provider == "claude":
                                    config = {"api_key": env_key, "server_url": "https://api.anthropic.com", "server_name": "Claude API"}
                                elif provider == "groq":
                                    config = {"api_key": env_key, "server_url": "https://api.groq.com", "server_name": "Groq API"}
                                else:
                                    config = {"api_key": env_key}

                                provider_instance = await provider_registry.get_provider(provider, server_id or f"{provider}_default_server", config)
                                models = await provider_instance.get_models()
                                for model in models:
                                    model["provider"] = provider
                                    model["server_id"] = server_id or f"{provider}_default_server"
                                    model["server_name"] = config["server_name"]
                                    all_models.append(model)
                                successful_providers += 1
                                print(f"Successfully loaded {len(models)} models from {provider} via env key")
                            except Exception as e:
                                error_msg = f"Failed to load models from {provider} via env key: {str(e)}"
                                errors.append(error_msg)
                                print(f"Error: {error_msg}")
                        else:
                            print(f"No env key for {provider}; skipping")
                    # Continue to next provider
                    continue
                
                # Use the first setting found
                setting = settings[0]
                setting_value = setting['value'] if isinstance(setting, dict) else setting.value
                setting_instance_id = setting['id'] if isinstance(setting, dict) else getattr(setting, 'id', '')

                # Robust parse to handle encrypted or malformed values
                try:
                    value_dict = None
                    if isinstance(setting_value, str) and encryption_service.is_encrypted_value(setting_value):
                        # Decrypt first, then use the decrypted JSON
                        try:
                            value_dict = encryption_service.decrypt_field('settings_instances', 'value', setting_value)
                        except EncryptionError as ee:
                            raise ValueError(f"Decryption failed for {settings_id}: {str(ee)}")
                    else:
                        # Use safe parser which handles multiple JSON edge cases
                        value_dict = safe_encrypted_json_parse(
                            setting_value,
                            context=f"all-models settings_id={settings_id}, user_id={user_id}",
                            setting_id=setting_instance_id,
                            definition_id=settings_id,
                        )
                except Exception as e:
                    err = f"Failed to parse settings for {provider}: {str(e)}"
                    print(err)
                    errors.append(err)
                    # For Ollama, attempt a safe default structure; otherwise skip
                    if provider == "ollama":
                        value_dict = create_default_ollama_settings()
                    elif provider in {"openai", "openrouter", "claude", "groq"}:
                        # Try env fallback for API-key providers
                        env_key = _get_env_api_key(provider)
                        if env_key:
                            value_dict = {"api_key": env_key}
                        else:
                            continue
                
                # Check if provider has valid configuration
                if provider == "ollama":
                    # Ollama needs servers array
                    if not isinstance(value_dict, dict) or not value_dict.get("servers") or len(value_dict["servers"]) == 0:
                        print(f"No servers configured for {provider}, skipping")
                        continue
                else:
                    # Other providers need API key (accept both api_key and apiKey)
                    if not isinstance(value_dict, dict):
                        print(f"Invalid settings format for {provider}, skipping")
                        continue
                    api_key = value_dict.get("api_key") or value_dict.get("apiKey")
                    if not api_key:
                        print(f"No API key for {provider}, skipping")
                        continue
                
                # Get provider instance and models
                if provider == "ollama":
                    # Handle Ollama servers dynamically
                    for server in value_dict["servers"]:
                        try:
                            config = {
                                "server_url": server.get("serverAddress"),
                                "api_key": server.get("apiKey", ""),
                                "server_name": server.get("serverName", "Unknown Server")
                            }
                            
                            provider_instance = await provider_registry.get_provider(
                                provider,
                                server["id"],
                                config
                            )
                            
                            models = await provider_instance.get_models()
                            for model in models:
                                model["provider"] = provider
                                model["server_id"] = server["id"]
                                model["server_name"] = server.get("serverName", "Unknown Server")
                                all_models.append(model)
                            
                            successful_providers += 1
                            print(f"Successfully loaded {len(models)} models from {provider} server: {server['id']}")
                            
                        except Exception as e:
                            error_msg = f"Failed to load models from {provider} server {server.get('id', 'unknown')}: {str(e)}"
                            errors.append(error_msg)
                            print(f"Error: {error_msg}")
                else:
                    # Handle API key-based providers
                    try:
                        if provider == "openai":
                            config = {
                                "api_key": value_dict.get("api_key") or value_dict.get("apiKey"),
                                "server_url": "https://api.openai.com/v1",
                                "server_name": "OpenAI API"
                            }
                        elif provider == "openrouter":
                            config = {
                                "api_key": value_dict.get("api_key") or value_dict.get("apiKey") or _get_env_api_key("openrouter"),
                                "server_url": "https://openrouter.ai/api/v1",
                                "server_name": "OpenRouter API"
                            }
                        elif provider == "claude":
                            config = {
                                "api_key": value_dict.get("api_key") or value_dict.get("apiKey") or _get_env_api_key("claude"),
                                "server_url": "https://api.anthropic.com",
                                "server_name": "Claude API"
                            }
                        elif provider == "groq":
                            config = {
                                "api_key": value_dict.get("api_key") or value_dict.get("apiKey") or _get_env_api_key("groq"),
                                "server_url": "https://api.groq.com",
                                "server_name": "Groq API"
                            }
                        
                        provider_instance = await provider_registry.get_provider(
                            provider,
                            server_id,
                            config
                        )
                        
                        models = await provider_instance.get_models()
                        for model in models:
                            model["provider"] = provider
                            model["server_id"] = server_id
                            model["server_name"] = config["server_name"]
                            all_models.append(model)
                        
                        successful_providers += 1
                        print(f"Successfully loaded {len(models)} models from {provider}")
                        
                    except Exception as e:
                        error_msg = f"Failed to load models from {provider}: {str(e)}"
                        errors.append(error_msg)
                        print(f"Error: {error_msg}")
                
            except Exception as e:
                error_msg = f"Error processing {provider}: {str(e)}"
                errors.append(error_msg)
                print(f"Error: {error_msg}")
        
        print(f"Total models loaded: {len(all_models)} from {successful_providers} providers")
        
        return {
            "models": all_models,
            "total_count": len(all_models),
            "successful_providers": successful_providers,
            "errors": errors,
            "summary": {
                "total_providers_checked": len(provider_settings),
                "successful_providers": successful_providers,
                "failed_providers": len(errors),
                "total_models": len(all_models)
            }
        }
        
    except Exception as e:
        print(f"Error in get_all_models: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/generate")
async def generate_text(request: TextGenerationRequest, db: AsyncSession = Depends(get_db)):
    """Generate text from a prompt.
    
    Uses the 'stream' parameter to determine whether to return a streaming or batch response.
    """
    try:
        # Get provider instance using the helper function
        provider_instance = await get_provider_instance_from_request(request, db)
        
        # Handle streaming
        if request.stream:
            async def stream_generator():
                async for chunk in provider_instance.generate_stream(
                    request.prompt, 
                    request.model, 
                    request.params
                ):
                    # Yield each chunk and flush immediately
                    yield f"data: {json.dumps(chunk)}\n\n"
                    # Add an explicit flush marker
                    yield ""
                yield "data: [DONE]\n\n"
            
            # Add headers to prevent buffering
            headers = {
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",  # Disable Nginx buffering
                "Connection": "keep-alive",
                "Content-Type": "text/event-stream"
            }
            
            return StreamingResponse(
                stream_generator(),
                media_type="text/event-stream",
                headers=headers
            )
        
        # Handle non-streaming
        result = await provider_instance.generate_text(
            request.prompt, 
            request.model, 
            request.params
        )
        
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cancel")
async def cancel_generation(request: dict = Body(...)):
    """Cancel ongoing generation for a conversation."""
    # TODO: Complete backend cancellation logic here
    return {
            "status": "success",
            "message": "Generation cancellation requested (basic mode)",
            # "conversation_id": conversation_id,
            "cancelled": True
        }

@router.post("/chat")
async def chat_completion(request: ChatCompletionRequest, db: AsyncSession = Depends(get_db)):
    """Generate a chat completion.
    
    Uses the 'stream' parameter to determine whether to return a streaming or batch response.
    Also stores the conversation history in the database and uses it for context.
    """
    logger = logging.getLogger(__name__)
    try:
        print(f"🎯 CHAT COMPLETION ENDPOINT CALLED")
        print(f"📊 Provider: {request.provider}")
        print(f"📊 Settings ID: {request.settings_id}")
        print(f"📊 Server ID: {request.server_id}")
        print(f"📊 Model: {request.model}")
        print(f"📊 User ID: {request.user_id}")
        print(f"📊 Stream: {request.stream}")
        logger.info(f"Production chat endpoint called with: provider={request.provider}, settings_id={request.settings_id}, server_id={request.server_id}, model={request.model}")
        logger.debug(f"Messages: {request.messages}")
        logger.debug(f"Params: {request.params}")
        
        # Validate persona data if provided
        if request.persona_id or request.persona_system_prompt or request.persona_model_settings:
            logger.info(f"Persona data provided - persona_id: {request.persona_id}")
            
            # Basic validation: if persona_id is provided, persona_system_prompt should also be provided
            if request.persona_id and not request.persona_system_prompt:
                logger.error(f"Invalid persona data: persona_id provided but persona_system_prompt is missing")
                raise HTTPException(
                    status_code=400,
                    detail="Invalid persona data: persona_system_prompt is required when persona_id is provided"
                )
            
            # Validate persona model settings if provided
            if request.persona_model_settings:
                try:
                    # Import here to avoid circular imports
                    from app.schemas.persona import ModelSettings
                    ModelSettings(**request.persona_model_settings)
                    logger.debug(f"Persona model settings validated successfully")
                except Exception as validation_error:
                    logger.error(f"Invalid persona model settings: {validation_error}")
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid persona model settings: {str(validation_error)}"
                    )
        
        # Get provider instance using the helper function
        logger.info("Getting provider instance from request")
        provider_instance = await get_provider_instance_from_request(request, db)
        logger.info(f"Provider instance created successfully: {provider_instance.provider_name}")
        
        # Convert messages to the format expected by the provider
        current_messages = [message.model_dump() for message in request.messages]
        print(f"Current messages: {current_messages}")
        
        combined_messages = current_messages.copy()

        # Get or create a conversation
        from app.models.conversation import Conversation
        from app.models.message import Message
        import uuid
        
        # Extract user_id from the request
        user_id = request.user_id
        # The conversation_id is defined in the ChatCompletionRequest schema, so we can access it directly
        conversation_id = request.conversation_id
        print(f"Conversation ID from request: {conversation_id}")
        print(f"USER ID from request: {user_id} - THIS SHOULD BE THE CURRENT USER'S ID, NOT HARDCODED")
        
        # Debug: Print the entire request for inspection
        print(f"Request details:")
        print(f"  provider: {request.provider}")
        print(f"  settings_id: {request.settings_id}")
        print(f"  server_id: {request.server_id}")
        print(f"  model: {request.model}")
        print(f"  user_id: {user_id}")
        print(f"  conversation_id: {conversation_id}")
        print(f"  messages count: {len(request.messages)}")
        for i, msg in enumerate(request.messages):
            print(f"    Message {i+1}: role={msg.role}, content={msg.content[:50]}...")
            
            # If conversation_id is provided, get the existing conversation
            if conversation_id:
                print(f"Attempting to retrieve conversation with ID: {conversation_id}")
                conversation = await Conversation.get_by_id(db, conversation_id)
                if not conversation:
                    print(f"ERROR: Conversation with ID {conversation_id} not found in database")
                    raise HTTPException(status_code=404, detail="Conversation not found")
                
                print(f"Found conversation: {conversation.id}, user_id: {conversation.user_id}")
                
                # Ensure the user owns the conversation
                if str(conversation.user_id) != str(user_id):
                    print(f"ERROR: User {user_id} is not authorized to access conversation {conversation_id}")
                    print(f"Conversation owner: {conversation.user_id}, Request user: {user_id}, Original request user_id: {request.user_id}")
                    raise HTTPException(status_code=403, detail="Not authorized to access this conversation")
                
                # Update conversation with persona_id if provided and different from current
                if request.persona_id and conversation.persona_id != request.persona_id:
                    logger.info(f"Updating conversation {conversation_id} with persona_id: {request.persona_id}")
                    conversation.persona_id = request.persona_id
                    await db.commit()
                    await db.refresh(conversation)
                
                # Get previous messages for this conversation
                print(f"Retrieving previous messages for conversation {conversation_id}")
                previous_messages = await conversation.get_messages(db)
                print(f"Retrieved {len(previous_messages)} previous messages")
                
                # Convert previous messages to the format expected by the provider
                if previous_messages and len(previous_messages) > 0:
                    # Sort messages by created_at to ensure correct order
                    previous_messages.sort(key=lambda x: x.created_at)
                    print(f"Sorted {len(previous_messages)} messages by timestamp")
                    
                    # Print all previous messages for debugging
                    for i, msg in enumerate(previous_messages):
                        print(f"  Previous message {i+1}: sender={msg.sender}, created_at={msg.created_at}, content={msg.message[:50]}...")
                    
                    # Convert to the format expected by the provider
                    # We'll skip the last message if it's from the user, as it's likely duplicated in the current request
                    skip_last = previous_messages[-1].sender == "user" and len(current_messages) > 0
                    print(f"Skip last message: {skip_last} (last message sender: {previous_messages[-1].sender}, current messages: {len(current_messages)})")
                    
                    history_messages = []
                    for i, msg in enumerate(previous_messages):
                        # Skip the last message if it's from the user and we have current messages
                        if skip_last and i == len(previous_messages) - 1:
                            print(f"  Skipping last message (index {i})")
                            continue
                        
                        # Prefer explicit role stored in metadata, fall back to sender
                        role = None
                        if msg.message_metadata and isinstance(msg.message_metadata, dict):
                            role = msg.message_metadata.get("role")
                        if role not in {"assistant", "user", "system"}:
                            role = "assistant" if msg.sender == "llm" else "user"
                        
                        history_messages.append({
                            "role": role,
                            "content": msg.message
                        })
                        print(f"  Added message to history: role={role}, content={msg.message[:50]}...")
                    
                    # Replace combined_messages with history followed by current
                    combined_messages = history_messages + current_messages
                    
                    print(f"Using {len(history_messages)} previous messages + {len(current_messages)} current messages = {len(combined_messages)} total messages")
                    print(f"Final combined messages:")
                    for i, msg in enumerate(combined_messages):
                        print(f"  Combined message {i+1}: role={msg.get('role', 'unknown')}, content={msg.get('content', '')[:50]}...")
                    
                    logger.info(f"Using {len(history_messages)} previous messages for context")
                    logger.debug(f"Combined messages: {combined_messages}")
            else:
                # Create a new conversation
                conversation = Conversation(
                    id=str(uuid.uuid4()),
                    user_id=user_id,
                    title=f"Conversation with {request.model}",
                    page_context=request.page_context,  # This is already defined in the schema with a default of None
                    page_id=request.page_id,  # NEW FIELD - ID of the page this conversation belongs to
                    model=request.model,
                    server=provider_instance.server_name,
                    conversation_type=request.conversation_type or "chat",  # New field with default
                    persona_id=request.persona_id  # Store persona_id when creating conversation
                )
                db.add(conversation)
                await db.commit()
                await db.refresh(conversation)
                print(f"Created new conversation with ID: {conversation.id}")
                
                # If persona has a sample greeting, add it as the first assistant message
                if request.persona_sample_greeting:
                    logger.info(f"Adding persona sample greeting for persona_id: {request.persona_id}")
                    greeting_message = Message(
                        id=str(uuid.uuid4()),
                        conversation_id=conversation.id,
                        sender="llm",
                        message=request.persona_sample_greeting,
                        message_metadata={
                            "persona_id": request.persona_id,
                            "persona_greeting": True,
                            "model": request.model,
                            "temperature": 0.0  # Greeting is static, not generated
                        }
                    )
                    db.add(greeting_message)
                    await db.commit()
                    print(f"Added persona sample greeting: {request.persona_sample_greeting[:50]}...")
            
            document_context_mode = (request.params or {}).get("document_context_mode")

            # Apply persona prompt/model settings after history merge (preserve new persona changes)
            combined_messages, enhanced_params = apply_persona_prompt_and_params(
                combined_messages,
                request.params or {},
                request.persona_system_prompt,
                request.persona_model_settings,
                max_history=100  # trim oldest history messages if needed
            )

            # Remove local-only params before sending to provider
            provider_params = enhanced_params.copy()
            provider_params.pop("document_context_mode", None)

            # Extract explicit approval-resume controls before provider/tool params are finalized.
            approval_controls = _extract_mcp_approval_params(provider_params)

            # Extract MCP scope/tool flags from provider params (server-side orchestration contract).
            mcp_scope = _extract_mcp_scope_params(provider_params)
            mcp_tooling_metadata: Dict[str, Any] = {
                "mcp_tools_enabled": bool(mcp_scope.get("mcp_tools_enabled")),
                "mcp_scope_mode": mcp_scope.get("mcp_scope_mode"),
                "mcp_project_slug": mcp_scope.get("mcp_project_slug"),
                "mcp_project_name": mcp_scope.get("mcp_project_name"),
                "mcp_project_lifecycle": mcp_scope.get("mcp_project_lifecycle"),
                "mcp_project_source": mcp_scope.get("mcp_project_source"),
                "available_count": 0,
                "selected_count": 0,
            }

            approval_resume_context: Optional[Dict[str, Any]] = None
            approval_action = approval_controls.get("action")
            if approval_action:
                if not conversation_id:
                    raise HTTPException(
                        status_code=400,
                        detail="mcp_approval requires an existing conversation_id",
                    )

                pending_query = (
                    select(Message)
                    .where(
                        Message.conversation_id == conversation.id,
                        Message.sender == "llm",
                    )
                    .order_by(Message.created_at.desc())
                    .limit(50)
                )
                pending_result = await db.execute(pending_query)
                pending_messages = pending_result.scalars().all()
                pending_message: Optional[Any] = None
                pending_request: Optional[Dict[str, Any]] = None

                requested_id = approval_controls.get("request_id")
                requested_tool = approval_controls.get("tool")

                for candidate in pending_messages:
                    candidate_meta = (
                        candidate.message_metadata
                        if isinstance(candidate.message_metadata, dict)
                        else {}
                    )
                    candidate_mcp = (
                        candidate_meta.get("mcp")
                        if isinstance(candidate_meta.get("mcp"), dict)
                        else {}
                    )
                    candidate_request = candidate_mcp.get("approval_request")
                    if not isinstance(candidate_request, dict):
                        continue

                    candidate_status = str(
                        candidate_request.get("status") or "pending"
                    ).lower()
                    if candidate_status in {
                        "approved",
                        "rejected",
                        "denied",
                        "cancelled",
                        "canceled",
                    }:
                        continue

                    candidate_request_id = candidate_request.get("request_id")
                    if not isinstance(candidate_request_id, str) or not candidate_request_id.strip():
                        candidate_request_id = f"apr_{candidate.id}"
                        candidate_request["request_id"] = candidate_request_id

                    if requested_id and candidate_request_id != requested_id:
                        continue
                    if requested_tool and candidate_request.get("tool") != requested_tool:
                        continue

                    pending_message = candidate
                    pending_request = candidate_request
                    break

                if pending_message is None or pending_request is None:
                    raise HTTPException(
                        status_code=409,
                        detail="No pending approval request found for this conversation.",
                    )

                pending_tool = pending_request.get("tool")
                if not isinstance(pending_tool, str) or not pending_tool.strip():
                    raise HTTPException(
                        status_code=409,
                        detail="Pending approval request is missing tool metadata.",
                    )

                pending_arguments = pending_request.get("arguments")
                if not isinstance(pending_arguments, dict):
                    pending_arguments = {}

                override_arguments = approval_controls.get("arguments")
                effective_arguments = (
                    override_arguments if isinstance(override_arguments, dict) else pending_arguments
                )

                resolved_request_id = str(pending_request.get("request_id"))
                resolution_status = "approved" if approval_action == "approve" else "rejected"
                pending_request.update(
                    {
                        "status": resolution_status,
                        "resolved_at": _utc_timestamp(),
                        "resolution_source": "client",
                        "resolution_message": (
                            current_messages[-1].get("content")
                            if current_messages and isinstance(current_messages[-1], dict)
                            else None
                        ),
                    }
                )

                pending_meta = (
                    pending_message.message_metadata
                    if isinstance(pending_message.message_metadata, dict)
                    else {}
                )
                pending_mcp = (
                    pending_meta.get("mcp")
                    if isinstance(pending_meta.get("mcp"), dict)
                    else {}
                )
                pending_mcp["approval_request"] = pending_request
                pending_meta["mcp"] = pending_mcp
                pending_message.message_metadata = pending_meta
                await db.commit()

                approval_resume_context = {
                    "action": approval_action,
                    "request_id": resolved_request_id,
                    "tool": pending_tool.strip(),
                    "arguments": effective_arguments,
                    "safety_class": pending_request.get("safety_class") or "mutating",
                    "summary": pending_request.get("summary"),
                    "scope": pending_request.get("scope")
                    if isinstance(pending_request.get("scope"), dict)
                    else {},
                }
                mcp_tooling_metadata.update(
                    {
                        "approval_resume_action": approval_action,
                        "approval_resume_request_id": resolved_request_id,
                        "approval_resume_tool": pending_tool.strip(),
                    }
                )

                # If client omitted scope in resume request, reuse stored scope from original approval.
                pending_scope = approval_resume_context.get("scope") or {}
                if (
                    approval_action == "approve"
                    and str(mcp_scope.get("mcp_scope_mode")) != "project"
                    and isinstance(pending_scope, dict)
                    and pending_scope.get("mcp_project_slug")
                ):
                    mcp_scope.update(
                        {
                            "mcp_tools_enabled": True,
                            "mcp_scope_mode": "project",
                            "mcp_project_slug": pending_scope.get("mcp_project_slug"),
                            "mcp_project_name": pending_scope.get("mcp_project_name"),
                            "mcp_project_lifecycle": pending_scope.get("mcp_project_lifecycle"),
                            "mcp_project_source": pending_scope.get("mcp_project_source") or "ui",
                            "mcp_plugin_slug": pending_scope.get("mcp_plugin_slug"),
                        }
                    )
                    mcp_tooling_metadata.update(
                        {
                            "mcp_tools_enabled": True,
                            "mcp_scope_mode": "project",
                            "mcp_project_slug": pending_scope.get("mcp_project_slug"),
                            "mcp_project_name": pending_scope.get("mcp_project_name"),
                            "mcp_project_lifecycle": pending_scope.get("mcp_project_lifecycle"),
                            "mcp_project_source": pending_scope.get("mcp_project_source") or "ui",
                        }
                    )

            resolved_tools: List[Dict[str, Any]] = []
            if bool(mcp_scope.get("mcp_tools_enabled")) and str(mcp_scope.get("mcp_scope_mode")) == "project":
                mcp_service = MCPRegistryService(db)
                mcp_user_id = user_id or "current"

                if bool(mcp_scope.get("mcp_sync_on_request")) and mcp_user_id != "current":
                    try:
                        await mcp_service.sync_user_servers(
                            mcp_user_id,
                            plugin_slug_filter=mcp_scope.get("mcp_plugin_slug"),
                        )
                    except Exception as sync_error:
                        logger.warning(
                            "mcp_sync_on_request_failed user_id=%s error=%s",
                            mcp_user_id,
                            sync_error,
                        )

                resolved_tools, resolve_meta = await mcp_service.resolve_tools_for_request(
                    mcp_user_id,
                    mcp_tools_enabled=bool(mcp_scope.get("mcp_tools_enabled")),
                    mcp_scope_mode=str(mcp_scope.get("mcp_scope_mode") or "none"),
                    mcp_project_slug=mcp_scope.get("mcp_project_slug"),
                    plugin_slug=mcp_scope.get("mcp_plugin_slug"),
                    max_tools=int(mcp_scope.get("mcp_max_tools") or 32),
                    max_schema_bytes=int(mcp_scope.get("mcp_max_schema_bytes") or 128_000),
                )
                mcp_tooling_metadata.update(resolve_meta)

            if resolved_tools:
                provider_params["tools"] = resolved_tools
                provider_params["tool_choice"] = provider_params.get("tool_choice", "auto")
            else:
                provider_params.pop("tools", None)
                if "tool_choice" not in provider_params and bool(mcp_scope.get("mcp_tools_enabled")):
                    provider_params["tool_choice"] = "none"

            mcp_max_tool_iterations = _as_int(
                provider_params.pop("mcp_max_tool_iterations", 5),
                default=5,
                minimum=1,
                maximum=10,
            )
            mcp_tool_timeout_seconds = float(
                provider_params.pop("mcp_tool_timeout_seconds", 15.0)
                or 15.0
            )
            if mcp_tool_timeout_seconds < 1:
                mcp_tool_timeout_seconds = 1.0
            if mcp_tool_timeout_seconds > 120:
                mcp_tool_timeout_seconds = 120.0

            mcp_auto_approve_mutating = _as_bool(
                provider_params.pop("mcp_auto_approve_mutating", False),
                False,
            )
            approved_mutating_raw = provider_params.pop("mcp_approved_mutating_tools", [])
            if isinstance(approved_mutating_raw, str):
                approved_mutating_tools = {
                    item.strip()
                    for item in approved_mutating_raw.split(",")
                    if item.strip()
                }
            elif isinstance(approved_mutating_raw, list):
                approved_mutating_tools = {
                    str(item).strip()
                    for item in approved_mutating_raw
                    if str(item).strip()
                }
            else:
                approved_mutating_tools = set()
            if (
                approval_resume_context
                and approval_resume_context.get("action") == "approve"
                and isinstance(approval_resume_context.get("tool"), str)
            ):
                approved_mutating_tools.add(str(approval_resume_context["tool"]))

            auto_continue_enabled = _as_bool(provider_params.pop("auto_continue", True), True)
            auto_continue_max_passes = _as_int(
                provider_params.pop("auto_continue_max_passes", DEFAULT_AUTO_CONTINUE_MAX_PASSES),
                default=DEFAULT_AUTO_CONTINUE_MAX_PASSES,
                minimum=0,
                maximum=10,
            )
            auto_continue_min_progress_chars = _as_int(
                provider_params.pop("auto_continue_min_progress_chars", DEFAULT_AUTO_CONTINUE_MIN_PROGRESS_CHARS),
                default=DEFAULT_AUTO_CONTINUE_MIN_PROGRESS_CHARS,
                minimum=0,
                maximum=200,
            )
            auto_continue_prompt = provider_params.pop("auto_continue_prompt", DEFAULT_AUTO_CONTINUE_PROMPT)
            if not isinstance(auto_continue_prompt, str) or not auto_continue_prompt.strip():
                auto_continue_prompt = DEFAULT_AUTO_CONTINUE_PROMPT

            # Store incoming messages (user/system) in the database
            for msg in request.messages:
                if msg.role == "system" and document_context_mode == "one-shot":
                    print("Skipping persistence of one-shot document context system message.")
                    continue

                if msg.role in {"user", "system"}:
                    sender = "user" if msg.role == "user" else "system"
                    db_message = Message(
                        id=str(uuid.uuid4()),
                        conversation_id=conversation.id,
                        sender=sender,
                        message=msg.content,
                        message_metadata={"role": msg.role}
                    )
                    db.add(db_message)
                    print(f"Added {msg.role} message to database: {msg.content[:50]}...")
            
            # Handle streaming
            if request.stream:
                async def stream_generator():
                    try:
                        print(f"Starting streaming with model: {request.model}")
                        full_response = ""
                        token_count = 0
                        start_time = time.time()
                        finish_reason_history: List[str] = []
                        finish_reason_final: Optional[str] = None
                        auto_continue_attempts = 0
                        truncated_at_least_once = False
                        stopped_by_guardrail: Optional[str] = None

                        # Emit the conversation_id early so clients can persist context
                        try:
                            initial_evt = {
                                "type": "conversation",
                                "conversation_id": conversation.id,
                            }
                            yield f"data: {json.dumps(initial_evt)}\n\n"
                        except Exception as init_evt_error:
                            # Don't fail the stream if the initial event fails
                            print(f"Warning: failed to emit initial conversation_id event: {init_evt_error}")

                        try:
                            tooling_evt = {
                                "type": "tooling_state",
                                **mcp_tooling_metadata,
                                "tools_passed_count": len(resolved_tools),
                            }
                            yield f"data: {json.dumps(tooling_evt)}\n\n"
                        except Exception as tooling_evt_error:
                            print(f"Warning: failed to emit tooling_state event: {tooling_evt_error}")

                        if (
                            str(mcp_scope.get("mcp_scope_mode")) == "project"
                            and mcp_scope.get("mcp_project_slug")
                        ):
                            try:
                                scope_evt = {
                                    "type": "project_scope_selected",
                                    "project": {
                                        "slug": mcp_scope.get("mcp_project_slug"),
                                        "name": mcp_scope.get("mcp_project_name"),
                                        "lifecycle": mcp_scope.get("mcp_project_lifecycle"),
                                    },
                                    "source": mcp_scope.get("mcp_project_source"),
                                }
                                yield f"data: {json.dumps(scope_evt)}\n\n"
                            except Exception as scope_evt_error:
                                print(f"Warning: failed to emit project_scope_selected event: {scope_evt_error}")

                        pass_index = 1
                        loop_messages = list(combined_messages)
                        tool_loop_iterations = 0
                        tool_loop_stop_reason = "provider_final_response"
                        stream_executed_tool_calls: List[Dict[str, Any]] = []
                        approval_request_payload: Optional[Dict[str, Any]] = None
                        approval_resolution_payload: Optional[Dict[str, Any]] = None
                        mcp_runtime_service = MCPRegistryService(
                            db,
                            call_timeout_seconds=mcp_tool_timeout_seconds,
                        )

                        if approval_resume_context and approval_resume_context.get("action") == "reject":
                            approval_resolution_payload = {
                                "type": "approval_resolution",
                                "status": "rejected",
                                "request_id": approval_resume_context.get("request_id"),
                                "tool": approval_resume_context.get("tool"),
                                "summary": approval_resume_context.get("summary"),
                            }
                            tool_loop_stop_reason = "approval_rejected"
                            full_response = (
                                f"Understood. I did not run mutating tool "
                                f"'{approval_resume_context.get('tool')}'."
                            )
                            finish_reason_final = "stop"
                            finish_reason_history.append("stop")
                            token_count += max(1, len(full_response.split()))
                            reject_chunk = {
                                "choices": [
                                    {
                                        "delta": {
                                            "role": "assistant",
                                            "content": full_response,
                                        },
                                        "finish_reason": "stop",
                                    }
                                ]
                            }
                            yield f"data: {json.dumps(reject_chunk)}\n\n"
                        elif approval_resume_context and approval_resume_context.get("action") == "approve":
                            resume_tool_name = str(approval_resume_context.get("tool") or "").strip()
                            resume_tool_arguments = (
                                approval_resume_context.get("arguments")
                                if isinstance(approval_resume_context.get("arguments"), dict)
                                else {}
                            )
                            resume_request_id = approval_resume_context.get("request_id")
                            resume_call_id = (
                                f"resume_{resume_request_id}"
                                if isinstance(resume_request_id, str) and resume_request_id
                                else f"resume_{resume_tool_name}"
                            )
                            approval_resolution_payload = {
                                "type": "approval_resolution",
                                "status": "approved",
                                "request_id": resume_request_id,
                                "tool": resume_tool_name,
                                "summary": approval_resume_context.get("summary"),
                            }
                            if resume_tool_name:
                                loop_messages.append(
                                    {
                                        "role": "assistant",
                                        "content": "",
                                        "tool_calls": [
                                            {
                                                "id": resume_call_id,
                                                "type": "function",
                                                "function": {
                                                    "name": resume_tool_name,
                                                    "arguments": json.dumps(
                                                        resume_tool_arguments,
                                                        ensure_ascii=True,
                                                    ),
                                                },
                                            }
                                        ],
                                    }
                                )
                                try:
                                    resume_tool_evt = {
                                        "type": "tool_call",
                                        "name": resume_tool_name,
                                        "arguments": resume_tool_arguments,
                                        "resumed": True,
                                    }
                                    yield f"data: {json.dumps(resume_tool_evt)}\n\n"
                                except Exception as resume_evt_error:
                                    print(f"Warning: failed to emit resumed tool_call event: {resume_evt_error}")

                                execution = await mcp_runtime_service.execute_tool_call(
                                    user_id or "current",
                                    resume_tool_name,
                                    resume_tool_arguments,
                                )
                                if execution.get("ok"):
                                    tool_result_payload = execution.get("data", {})
                                    stream_executed_tool_calls.append(
                                        {
                                            "name": resume_tool_name,
                                            "status": "success",
                                            "latency_ms": execution.get("latency_ms"),
                                            "resumed": True,
                                        }
                                    )
                                else:
                                    tool_result_payload = {
                                        "ok": False,
                                        "error": execution.get("error"),
                                    }
                                    stream_executed_tool_calls.append(
                                        {
                                            "name": resume_tool_name,
                                            "status": "error",
                                            "error": execution.get("error"),
                                            "resumed": True,
                                        }
                                    )

                                loop_messages.append(
                                    {
                                        "role": "tool",
                                        "name": resume_tool_name,
                                        "content": json.dumps(tool_result_payload, ensure_ascii=True),
                                    }
                                )
                                tool_loop_iterations += 1
                                try:
                                    resume_result_evt = {
                                        "type": "tool_result",
                                        "name": resume_tool_name,
                                        "ok": bool(execution.get("ok")),
                                        "resumed": True,
                                    }
                                    yield f"data: {json.dumps(resume_result_evt)}\n\n"
                                except Exception as resume_result_evt_error:
                                    print(
                                        f"Warning: failed to emit resumed tool_result event: {resume_result_evt_error}"
                                    )

                        if approval_resolution_payload:
                            try:
                                yield f"data: {json.dumps(approval_resolution_payload)}\n\n"
                            except Exception as approval_resolution_evt_error:
                                print(
                                    f"Warning: failed to emit approval_resolution event: "
                                    f"{approval_resolution_evt_error}"
                                )

                        while True:
                            if tool_loop_stop_reason == "approval_rejected":
                                break
                            pass_text = ""
                            pass_finish_reason: Optional[str] = None
                            pass_tool_call_buffer: Dict[str, Dict[str, Any]] = {}

                            logger.info(
                                "chat_stream_pass_start provider=%s model=%s conversation_id=%s pass=%s",
                                request.provider,
                                request.model,
                                conversation.id,
                                pass_index,
                            )

                            async for chunk in provider_instance.chat_completion_stream(
                                loop_messages,
                                request.model,
                                provider_params
                            ):
                                if isinstance(chunk, dict) and "error" in chunk:
                                    raise RuntimeError(chunk.get("error") or chunk.get("message") or "Unknown provider error")

                                content = extract_chunk_content(chunk)
                                if content:
                                    pass_text += content
                                    full_response += content

                                tool_deltas = extract_chunk_tool_call_deltas(chunk)
                                if tool_deltas:
                                    update_stream_tool_call_buffer(pass_tool_call_buffer, tool_deltas)

                                detected_finish_reason = extract_finish_reason(chunk)
                                if detected_finish_reason:
                                    pass_finish_reason = detected_finish_reason

                                token_count += 1

                                # Yield each chunk and flush immediately
                                yield f"data: {json.dumps(chunk)}\n\n"
                                # Add an explicit flush marker
                                yield ""

                            pass_tool_calls = finalize_stream_tool_calls(pass_tool_call_buffer)
                            finish_reason_final = pass_finish_reason
                            finish_reason_history.append(pass_finish_reason or "unknown")

                            pass_chars = len(pass_text)
                            progress_chars = len(pass_text.strip())
                            is_truncated = is_truncation_finish_reason(pass_finish_reason, request.provider)
                            if is_truncated:
                                truncated_at_least_once = True

                            logger.info(
                                "chat_stream_pass_end provider=%s model=%s conversation_id=%s pass=%s finish_reason=%s chars=%s tool_calls=%s",
                                request.provider,
                                request.model,
                                conversation.id,
                                pass_index,
                                pass_finish_reason,
                                pass_chars,
                                len(pass_tool_calls),
                            )

                            if pass_tool_calls and resolved_tools:
                                tool_loop_iterations += 1
                                if tool_loop_iterations > mcp_max_tool_iterations:
                                    tool_loop_stop_reason = "max_tool_iterations_exceeded"
                                    stopped_by_guardrail = "max_tool_iterations_exceeded"
                                    break

                                assistant_payload: Dict[str, Any] = {
                                    "role": "assistant",
                                    "content": pass_text or "",
                                    "tool_calls": [
                                        {
                                            "id": call.get("id"),
                                            "type": "function",
                                            "function": {
                                                "name": call["name"],
                                                "arguments": json.dumps(call.get("arguments", {}), ensure_ascii=True),
                                            },
                                        }
                                        for call in pass_tool_calls
                                    ],
                                }
                                loop_messages.append(assistant_payload)

                                executed_any = False
                                for tool_call in pass_tool_calls:
                                    tool_name = tool_call["name"]
                                    tool_arguments = (
                                        tool_call.get("arguments")
                                        if isinstance(tool_call.get("arguments"), dict)
                                        else {}
                                    )
                                    try:
                                        tool_call_event = {
                                            "type": "tool_call",
                                            "name": tool_name,
                                            "arguments": tool_arguments,
                                        }
                                        yield f"data: {json.dumps(tool_call_event)}\n\n"
                                    except Exception as tool_call_evt_error:
                                        print(f"Warning: failed to emit tool_call event: {tool_call_evt_error}")

                                    tool_record = await mcp_runtime_service.get_enabled_tool(user_id or "current", tool_name)
                                    if tool_record is None:
                                        tool_error = {
                                            "ok": False,
                                            "error": {
                                                "code": "TOOL_NOT_ALLOWED",
                                                "message": f"Tool '{tool_name}' is not enabled.",
                                            },
                                        }
                                        loop_messages.append(
                                            {
                                                "role": "tool",
                                                "name": tool_name,
                                                "content": json.dumps(tool_error, ensure_ascii=True),
                                            }
                                        )
                                        stream_executed_tool_calls.append(
                                            {
                                                "name": tool_name,
                                                "status": "denied",
                                                "reason": "tool_not_enabled",
                                            }
                                        )
                                        continue

                                    if (
                                        tool_record.safety_class == "mutating"
                                        and not mcp_auto_approve_mutating
                                        and tool_name not in approved_mutating_tools
                                    ):
                                        approval_request_payload = _build_approval_request_payload(
                                            tool=tool_name,
                                            safety_class=tool_record.safety_class,
                                            arguments=tool_arguments,
                                            scope=mcp_scope,
                                        )
                                        tool_loop_stop_reason = "approval_required"
                                        try:
                                            yield f"data: {json.dumps(approval_request_payload)}\n\n"
                                        except Exception as approval_evt_error:
                                            print(f"Warning: failed to emit approval_request event: {approval_evt_error}")
                                        break

                                    execution = await mcp_runtime_service.execute_tool_call(
                                        user_id or "current",
                                        tool_name,
                                        tool_arguments,
                                    )
                                    executed_any = True
                                    if execution.get("ok"):
                                        tool_result_payload = execution.get("data", {})
                                        stream_executed_tool_calls.append(
                                            {
                                                "name": tool_name,
                                                "status": "success",
                                                "latency_ms": execution.get("latency_ms"),
                                            }
                                        )
                                    else:
                                        tool_result_payload = {
                                            "ok": False,
                                            "error": execution.get("error"),
                                        }
                                        stream_executed_tool_calls.append(
                                            {
                                                "name": tool_name,
                                                "status": "error",
                                                "error": execution.get("error"),
                                            }
                                        )

                                    loop_messages.append(
                                        {
                                            "role": "tool",
                                            "name": tool_name,
                                            "content": json.dumps(tool_result_payload, ensure_ascii=True),
                                        }
                                    )
                                    try:
                                        tool_result_event = {
                                            "type": "tool_result",
                                            "name": tool_name,
                                            "ok": bool(execution.get("ok")),
                                        }
                                        yield f"data: {json.dumps(tool_result_event)}\n\n"
                                    except Exception as tool_result_evt_error:
                                        print(f"Warning: failed to emit tool_result event: {tool_result_evt_error}")

                                if approval_request_payload:
                                    break

                                if not executed_any:
                                    tool_loop_stop_reason = "tool_calls_without_execution"

                                pass_index += 1
                                continue

                            if pass_tool_calls and not resolved_tools:
                                tool_loop_stop_reason = "tool_calls_without_enabled_tools"
                                break

                            can_continue = (
                                auto_continue_enabled
                                and is_truncated
                                and auto_continue_attempts < auto_continue_max_passes
                            )

                            if can_continue:
                                # Guardrail: if continuation pass added almost nothing, stop to avoid loops.
                                if pass_index > 1 and progress_chars <= auto_continue_min_progress_chars:
                                    stopped_by_guardrail = "no_progress"
                                    logger.warning(
                                        "chat_stream_auto_continue_stopped provider=%s model=%s conversation_id=%s pass=%s reason=no_progress chars=%s threshold=%s",
                                        request.provider,
                                        request.model,
                                        conversation.id,
                                        pass_index,
                                        progress_chars,
                                        auto_continue_min_progress_chars,
                                    )
                                    break

                                auto_continue_attempts += 1
                                next_pass = pass_index + 1
                                continuation_event = {
                                    "type": "auto_continue",
                                    "pass": next_pass,
                                    "trigger_finish_reason": pass_finish_reason,
                                    "attempt": auto_continue_attempts,
                                }
                                yield f"data: {json.dumps(continuation_event)}\n\n"
                                yield ""

                                loop_messages = build_continuation_messages(
                                    loop_messages,
                                    full_response,
                                    continuation_prompt=auto_continue_prompt,
                                )
                                pass_index = next_pass
                                continue

                            if auto_continue_enabled and is_truncated and auto_continue_attempts >= auto_continue_max_passes:
                                stopped_by_guardrail = "max_passes_reached"

                            break
                        
                        # Calculate tokens per second
                        elapsed_time = time.time() - start_time
                        tokens_per_second = token_count / elapsed_time if elapsed_time > 0 else 0
                        
                        mcp_tooling_metadata.update(
                            {
                                "tool_loop_enabled": bool(resolved_tools) or bool(stream_executed_tool_calls),
                                "tool_loop_iterations": tool_loop_iterations,
                                "tool_loop_stop_reason": tool_loop_stop_reason,
                                "tool_calls_executed_count": len(stream_executed_tool_calls),
                                "approval_required": bool(approval_request_payload),
                                "approval_resolved": bool(approval_resolution_payload),
                            }
                        )

                        # Store the LLM response in the database with persona metadata
                        message_metadata = {
                            "token_count": token_count,
                            "tokens_per_second": round(tokens_per_second, 1),
                            "model": request.model,
                            "temperature": provider_params.get("temperature", 0.7),
                            "streaming": True,
                            "auto_continue_enabled": auto_continue_enabled,
                            "auto_continue_attempts": auto_continue_attempts,
                            "auto_continue_max_passes": auto_continue_max_passes,
                            "finish_reason_final": finish_reason_final,
                            "finish_reason_history": finish_reason_history,
                            "truncated_at_least_once": truncated_at_least_once,
                            "mcp": {
                                **mcp_tooling_metadata,
                                "tools_passed_count": len(resolved_tools),
                                "tool_calls_executed": stream_executed_tool_calls,
                            },
                        }
                        if approval_request_payload:
                            message_metadata["mcp"]["approval_request"] = approval_request_payload
                        if approval_resolution_payload:
                            message_metadata["mcp"]["approval_resolution"] = approval_resolution_payload
                        if stopped_by_guardrail:
                            message_metadata["stopped_by_guardrail"] = stopped_by_guardrail
                        
                        # Add persona metadata if persona was used
                        if request.persona_id:
                            message_metadata.update({
                                "persona_id": request.persona_id,
                                "persona_applied": bool(request.persona_system_prompt),
                                "persona_model_settings_applied": bool(request.persona_model_settings)
                            })
                        
                        db_message = Message(
                            id=str(uuid.uuid4()),
                            conversation_id=conversation.id,
                            sender="llm",
                            message=full_response,
                            message_metadata=message_metadata
                        )
                        db.add(db_message)
                        
                        # Update the conversation's updated_at timestamp
                        conversation.updated_at = db_message.created_at
                        
                        await db.commit()

                        if approval_request_payload:
                            approval_event = {
                                "type": "approval_required",
                                "approval_request": approval_request_payload,
                            }
                            yield f"data: {json.dumps(approval_event)}\n\n"

                        yield "data: [DONE]\n\n"
                    except Exception as stream_error:
                        print(f"Error in stream_generator: {stream_error}")
                        logger.error(f"Streaming error with persona_id {request.persona_id}: {stream_error}")
                        
                        # Enhanced error message for persona-related errors
                        error_message = f"Streaming error: {str(stream_error)}"
                        if request.persona_id:
                            error_message += f" (Persona ID: {request.persona_id})"
                        
                        error_json = json.dumps({
                            "error": True,
                            "message": error_message,
                            "persona_id": request.persona_id if request.persona_id else None
                        })
                        yield f"data: {error_json}\n\n"
                        yield "data: [DONE]\n\n"
                
                # Add headers to prevent buffering
                headers = {
                    "Cache-Control": "no-cache, no-transform",
                    "X-Accel-Buffering": "no",  # Disable Nginx buffering
                    "Connection": "keep-alive",
                    "Content-Type": "text/event-stream"
                }
                
                return StreamingResponse(
                    stream_generator(),
                    media_type="text/event-stream",
                    headers=headers
                )
            
            # Handle non-streaming
            print(f"Starting non-streaming chat completion with model: {request.model}")
            start_time = time.time()
            print(f"Sending {len(combined_messages)} messages to chat_completion")
            for i, msg in enumerate(combined_messages):
                print(f"  Message {i+1}: role={msg.get('role', 'unknown')}, content={msg.get('content', '')[:50]}...")

            loop_messages = list(combined_messages)
            tool_loop_enabled = bool(resolved_tools)
            tool_loop_iterations = 0
            tool_loop_stop_reason = "provider_final_response"
            approval_request_payload: Optional[Dict[str, Any]] = None
            approval_resolution_payload: Optional[Dict[str, Any]] = None
            executed_tool_calls: List[Dict[str, Any]] = []
            result: Dict[str, Any] = {}

            mcp_runtime_service = MCPRegistryService(
                db,
                call_timeout_seconds=mcp_tool_timeout_seconds,
            )
            if approval_resume_context and approval_resume_context.get("action") == "reject":
                approval_resolution_payload = {
                    "type": "approval_resolution",
                    "status": "rejected",
                    "request_id": approval_resume_context.get("request_id"),
                    "tool": approval_resume_context.get("tool"),
                    "summary": approval_resume_context.get("summary"),
                }
                tool_loop_stop_reason = "approval_rejected"
                result = {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": (
                                    "Understood. I did not run mutating tool "
                                    f"'{approval_resume_context.get('tool')}'."
                                ),
                            },
                            "finish_reason": "stop",
                        }
                    ]
                }
            else:
                if approval_resume_context and approval_resume_context.get("action") == "approve":
                    resume_tool_name = str(approval_resume_context.get("tool") or "").strip()
                    resume_tool_arguments = (
                        approval_resume_context.get("arguments")
                        if isinstance(approval_resume_context.get("arguments"), dict)
                        else {}
                    )
                    resume_request_id = approval_resume_context.get("request_id")
                    resume_call_id = (
                        f"resume_{resume_request_id}"
                        if isinstance(resume_request_id, str) and resume_request_id
                        else f"resume_{resume_tool_name}"
                    )
                    approval_resolution_payload = {
                        "type": "approval_resolution",
                        "status": "approved",
                        "request_id": resume_request_id,
                        "tool": resume_tool_name,
                        "summary": approval_resume_context.get("summary"),
                    }

                    if resume_tool_name:
                        loop_messages.append(
                            {
                                "role": "assistant",
                                "content": "",
                                "tool_calls": [
                                    {
                                        "id": resume_call_id,
                                        "type": "function",
                                        "function": {
                                            "name": resume_tool_name,
                                            "arguments": json.dumps(
                                                resume_tool_arguments,
                                                ensure_ascii=True,
                                            ),
                                        },
                                    }
                                ],
                            }
                        )
                        execution = await mcp_runtime_service.execute_tool_call(
                            user_id or "current",
                            resume_tool_name,
                            resume_tool_arguments,
                        )
                        if execution.get("ok"):
                            tool_content = execution.get("data", {})
                            executed_tool_calls.append(
                                {
                                    "name": resume_tool_name,
                                    "status": "success",
                                    "latency_ms": execution.get("latency_ms"),
                                    "resumed": True,
                                }
                            )
                        else:
                            tool_content = {
                                "ok": False,
                                "error": execution.get("error"),
                            }
                            executed_tool_calls.append(
                                {
                                    "name": resume_tool_name,
                                    "status": "error",
                                    "error": execution.get("error"),
                                    "resumed": True,
                                }
                            )

                        loop_messages.append(
                            {
                                "role": "tool",
                                "name": resume_tool_name,
                                "content": json.dumps(tool_content, ensure_ascii=True),
                            }
                        )
                        tool_loop_iterations += 1

                for iteration in range(1, mcp_max_tool_iterations + 1):
                    tool_loop_iterations = iteration
                    result = await provider_instance.chat_completion(
                        loop_messages,
                        request.model,
                        provider_params,
                    )
                    if isinstance(result, dict) and result.get("error"):
                        raise HTTPException(
                            status_code=502,
                            detail=f"Provider error: {result.get('error') or result.get('message')}",
                        )

                    tool_calls = extract_response_tool_calls(result)
                    if not tool_loop_enabled or not tool_calls:
                        tool_loop_stop_reason = (
                            "provider_final_response" if not tool_calls else "tools_disabled"
                        )
                        break

                    assistant_content = extract_response_content(result)
                    assistant_payload: Dict[str, Any] = {
                        "role": "assistant",
                        "content": assistant_content or "",
                    }
                    assistant_payload["tool_calls"] = [
                        {
                            "id": call.get("id"),
                            "type": "function",
                            "function": {
                                "name": call["name"],
                                "arguments": json.dumps(call.get("arguments", {}), ensure_ascii=True),
                            },
                        }
                        for call in tool_calls
                    ]
                    loop_messages.append(assistant_payload)

                    executed_in_iteration = False
                    for tool_call in tool_calls:
                        tool_name = tool_call["name"]
                        tool_arguments = (
                            tool_call.get("arguments")
                            if isinstance(tool_call.get("arguments"), dict)
                            else {}
                        )
                        tool_record = await mcp_runtime_service.get_enabled_tool(user_id or "current", tool_name)
                        if tool_record is None:
                            error_payload = {
                                "ok": False,
                                "error": {
                                    "code": "TOOL_NOT_ALLOWED",
                                    "message": f"Tool '{tool_name}' is not enabled.",
                                },
                            }
                            loop_messages.append(
                                {
                                    "role": "tool",
                                    "name": tool_name,
                                    "content": json.dumps(error_payload, ensure_ascii=True),
                                }
                            )
                            executed_tool_calls.append(
                                {
                                    "name": tool_name,
                                    "status": "denied",
                                    "reason": "tool_not_enabled",
                                }
                            )
                            continue

                        if (
                            tool_record.safety_class == "mutating"
                            and not mcp_auto_approve_mutating
                            and tool_name not in approved_mutating_tools
                        ):
                            approval_request_payload = _build_approval_request_payload(
                                tool=tool_name,
                                safety_class=tool_record.safety_class,
                                arguments=tool_arguments,
                                scope=mcp_scope,
                            )
                            tool_loop_stop_reason = "approval_required"
                            break

                        execution = await mcp_runtime_service.execute_tool_call(
                            user_id or "current",
                            tool_name,
                            tool_arguments,
                        )
                        if execution.get("ok"):
                            tool_content = execution.get("data", {})
                            executed_tool_calls.append(
                                {
                                    "name": tool_name,
                                    "status": "success",
                                    "latency_ms": execution.get("latency_ms"),
                                }
                            )
                        else:
                            tool_content = {
                                "ok": False,
                                "error": execution.get("error"),
                            }
                            executed_tool_calls.append(
                                {
                                    "name": tool_name,
                                    "status": "error",
                                    "error": execution.get("error"),
                                }
                            )

                        loop_messages.append(
                            {
                                "role": "tool",
                                "name": tool_name,
                                "content": json.dumps(tool_content, ensure_ascii=True),
                            }
                        )
                        executed_in_iteration = True

                    if approval_request_payload:
                        break

                    if not executed_in_iteration:
                        # Tool calls were returned but none could execute; feed tool errors back in next pass.
                        continue
                else:
                    tool_loop_stop_reason = "max_tool_iterations_exceeded"

            if approval_request_payload:
                response_content = "Approval required before executing mutating tool call."
                result = {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": response_content,
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                    "approval_required": True,
                    "approval_request": approval_request_payload,
                }
            elif approval_resolution_payload and approval_resolution_payload.get("status") == "rejected":
                response_content = (
                    "Understood. I did not run mutating tool "
                    f"'{approval_resolution_payload.get('tool')}'."
                )
                result = result or {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": response_content,
                            },
                            "finish_reason": "stop",
                        }
                    ]
                }
            else:
                response_content = extract_response_content(result)
                if not response_content and tool_loop_stop_reason == "max_tool_iterations_exceeded":
                    response_content = "Tool execution stopped after reaching max iterations."
                    result = result or {
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": response_content,
                                },
                                "finish_reason": "length",
                            }
                        ]
                    }

            mcp_tooling_metadata.update(
                {
                    "tool_loop_enabled": tool_loop_enabled or bool(executed_tool_calls),
                    "tool_loop_iterations": tool_loop_iterations,
                    "tool_loop_stop_reason": tool_loop_stop_reason,
                    "tool_calls_executed_count": len(executed_tool_calls),
                    "approval_required": bool(approval_request_payload),
                    "approval_resolved": bool(approval_resolution_payload),
                }
            )

            elapsed_time = time.time() - start_time
            print(f"Chat completion result: {result}")
            
            # Estimate token count (this is a rough estimate)
            token_count = len(response_content.split()) * 1.3  # Rough estimate: words * 1.3
            tokens_per_second = token_count / elapsed_time if elapsed_time > 0 else 0
            
            # Store the LLM response in the database with persona metadata
            message_metadata = {
                "token_count": int(token_count),
                "tokens_per_second": round(tokens_per_second, 1),
                "model": request.model,
                "temperature": provider_params.get("temperature", 0.7),
                "streaming": False,
                "mcp": {
                    **mcp_tooling_metadata,
                    "tools_passed_count": len(resolved_tools),
                    "tool_calls_executed": executed_tool_calls,
                },
            }
            if approval_request_payload:
                message_metadata["mcp"]["approval_request"] = approval_request_payload
            if approval_resolution_payload:
                message_metadata["mcp"]["approval_resolution"] = approval_resolution_payload
            
            # Add persona metadata if persona was used
            if request.persona_id:
                message_metadata.update({
                    "persona_id": request.persona_id,
                    "persona_applied": bool(request.persona_system_prompt),
                    "persona_model_settings_applied": bool(request.persona_model_settings)
                })
            
            db_message = Message(
                id=str(uuid.uuid4()),
                conversation_id=conversation.id,
                sender="llm",
                message=response_content,
                message_metadata=message_metadata
            )
            db.add(db_message)
            
            # Update the conversation's updated_at timestamp
            conversation.updated_at = db_message.created_at
            
            await db.commit()
            
            # Add conversation_id to the result
            result["conversation_id"] = conversation.id
            result["tooling_state"] = {
                **mcp_tooling_metadata,
                "tools_passed_count": len(resolved_tools),
            }
            if approval_request_payload:
                result["approval_required"] = True
                result["approval_request"] = approval_request_payload
            if approval_resolution_payload:
                result["approval_resolution"] = approval_resolution_payload
            
            return result
    except HTTPException:
        # Re-raise HTTP exceptions with their original status codes and details
        raise
    except Exception as e:
        logger.error(f"Exception in chat_completion: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error in chat completion: {str(e)}. Please try again later or contact support."
        )


# Test routes for direct testing (disabled in production)
if TEST_ROUTES_ENABLED:
    test_router = APIRouter(prefix="/test", tags=["ai_test"])
    
    @test_router.post("/ollama/generate")
    async def test_ollama_generate(
        prompt: str = Body(..., description="Text prompt"),
        model: str = Body("llama2", description="Model name"),
        stream: bool = Body(False, description="Whether to stream the response"),
        temperature: float = Body(0.7, description="Temperature for generation"),
        max_tokens: int = Body(2048, description="Maximum tokens to generate"),
        server_url: str = Body("http://localhost:11434", description="Ollama server URL")
    ):
        """Test route for Ollama text generation."""
        print(f"Test route called with: prompt={prompt}, model={model}, stream={stream}, server_url={server_url}")
        try:
            # Create provider instance directly
            provider = OllamaProvider()
            await provider.initialize({
                "server_url": server_url,
                "api_key": "",  # No API key for local testing
                "server_name": "Test Ollama Server"
            })
            
            # Set up parameters
            params = {
                "temperature": temperature,
                "max_tokens": max_tokens
            }
            
            print(f"Initialized provider with server_url={server_url}, calling with params={params}")
            
            # Handle streaming vs. non-streaming
            if stream:
                async def stream_generator():
                    try:
                        async for chunk in provider.generate_stream(prompt, model, params):
                            print(f"Streaming chunk: {chunk}")
                            yield f"data: {json.dumps(chunk)}\n\n"
                            
                            # Add an explicit flush marker
                            yield ""
                        yield "data: [DONE]\n\n"
                    except Exception as stream_error:
                        print(f"Error in stream_generator: {stream_error}")
                        error_json = json.dumps({
                            "error": True,
                            "message": f"Streaming error: {str(stream_error)}"
                        })
                        yield f"data: {error_json}\n\n"
                        yield "data: [DONE]\n\n"
                
                # Add headers to prevent buffering
                headers = {
                    "Cache-Control": "no-cache, no-transform",
                    "X-Accel-Buffering": "no",  # Disable Nginx buffering
                    "Connection": "keep-alive",
                    "Content-Type": "text/event-stream"
                }
                
                return StreamingResponse(
                    stream_generator(),
                    media_type="text/event-stream",
                    headers=headers
                )
            else:
                print(f"Calling generate_text with prompt={prompt}, model={model}")
                result = await provider.generate_text(prompt, model, params)
                print(f"Result from generate_text: {result}")
                return result
        except Exception as e:
            print(f"Exception in test_ollama_generate: {e}")
            import traceback
            traceback.print_exc()
            return {
                "error": True,
                "message": f"Test route error: {str(e)}"
            }
    
    @test_router.post("/ollama/llmchat")
    async def test_ollama_chat(
        messages: List[Dict[str, Any]] = Body(..., description="Chat messages"),
        model: str = Body("llama2", description="Model name"),
        stream: bool = Body(False, description="Whether to stream the response"),
        temperature: float = Body(0.7, description="Temperature for generation"),
        max_tokens: int = Body(2048, description="Maximum tokens to generate"),
        server_url: str = Body("http://localhost:11434", description="Ollama server URL")
    ):
        from langchain_community.llms import Ollama
        from langchain_core.prompts import ChatPromptTemplate
        from langchain_core.output_parsers import StrOutputParser

        try:
            # Fallback input from messages
            user_input = messages[0].get("content", "Hello") if messages else "Hello"

            # Build chain using LangChain
            prompt = ChatPromptTemplate.from_messages([
                ("system", "You are a helpful assistant."),
                ("human", "{input}")
            ])
            llm = Ollama(model=model, base_url=server_url)
            chain = prompt | llm | StrOutputParser()

            # Streaming generator
            async def stream_generator():
                for chunk in chain.stream({"input": user_input}):
                    print("[🔹] Streaming chunk:", chunk)
                    yield f"data: {chunk}\n\n"
                    await asyncio.sleep(0.01)

                yield "data: [DONE]\n\n"

            if stream:
                return StreamingResponse(
                    stream_generator(),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache, no-transform",
                        "X-Accel-Buffering": "no",
                        "Connection": "keep-alive",
                        "Content-Type": "text/event-stream"
                    }
                )
            else:
                # For non-streaming response
                result = chain.invoke({"input": user_input})
                return {"answer": result}

        except Exception as e:
            print("❌ Exception in test_ollama_chat:", e)
            return {
                "error": True,
                "message": str(e)
            }


    @test_router.post("/ollama/chat")
    async def test_ollama_chat(
        messages: List[Dict[str, Any]] = Body(..., description="Chat messages"),
        model: str = Body("llama2", description="Model name"),
        stream: bool = Body(False, description="Whether to stream the response"),
        temperature: float = Body(0.7, description="Temperature for generation"),
        max_tokens: int = Body(2048, description="Maximum tokens to generate"),
        server_url: str = Body("http://localhost:11434", description="Ollama server URL")
    ):
        print(f"Test chat route called with: messages={messages}, model={model}, stream={stream}, server_url={server_url}")
        try:
            provider = OllamaProvider()
            await provider.initialize({
                "server_url": server_url,
                "api_key": "",
                "server_name": "Test Ollama Server"
            })

            params = {
                "temperature": temperature,
                "max_tokens": max_tokens
            }

            if stream:
                async def stream_generator():
                    try:
                        async for chunk in provider.chat_completion_stream(messages, model, params):
                            # content = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                            # print(f"Streaming chunk: {content}")
                            # yield f"data: {content}\n\n"
                            yield f"data: {json.dumps(chunk)}\n\n"
                            await asyncio.sleep(0.01)
                        yield "data: [DONE]\n\n"

                    except Exception as stream_error:
                        print(f"Streaming error: {stream_error}")
                        yield "data: [DONE]\n\n"


                return StreamingResponse(
                    stream_generator(),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache, no-transform",
                        "X-Accel-Buffering": "no",
                        "Connection": "keep-alive",
                        "Content-Type": "text/event-stream"
                    }
                )
            else:
                result = await provider.chat_completion(messages, model, params)
                return result

        except Exception as e:
            print(f"Exception in chat handler: {e}")
            return {
                "error": True,
                "message": str(e)
            }

        
    @test_router.get("/test/stream")
    async def minimal_stream_test():
        async def event_stream():
            for i in range(5):
                yield f"data: chunk {i} at {time.time()}\n\n"
                print(f"Yielded chunk {i}")
                await asyncio.sleep(1)

            yield "data: [DONE]\n\n"

        headers = {
            "Cache-Control": "no-cache",
            "Content-Type": "text/event-stream",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Important if using nginx
        }

        return StreamingResponse(event_stream(), headers=headers)


    @test_router.get("/test/stream-ollama-direct")
    async def stream_ollama_direct_test():
        from app.ai_providers.ollama import OllamaProvider

        async def stream():
            provider = OllamaProvider()
            await provider.initialize({"server_url": "http://localhost:11434"})
            async for chunk in provider._stream_ollama_api("Give me 5 dragon Names", "hf.co/Triangle104/Dolphin3.0-R1-Mistral-24B-Q6_K-GGUF:latest", {"temperature": 0.7}):
                print("STREAM CHUNK:", chunk)
                yield f"data: {json.dumps(chunk)}\n\n"
                await asyncio.sleep(0.01)
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive"
            }
        )
    
    # Include the test router in the main router
    router.include_router(test_router)
