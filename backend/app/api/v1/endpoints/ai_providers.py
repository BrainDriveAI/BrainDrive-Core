"""
API endpoints for AI providers.
"""
import os
import json
import time
import asyncio
import logging
import traceback
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, HTTPException, Depends, Body, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from app.core.database import get_db
from app.core.security import get_current_user
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

# Flag to enable/disable test routes (set to False in production)
TEST_ROUTES_ENABLED = os.getenv("ENABLE_TEST_ROUTES", "True").lower() == "true"

router = APIRouter()

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
    current_user: Optional[User] = Depends(get_current_user)
):
    """
    Return provider metadata for UI selection (provider -> settings_id, server strategy, etc)
    plus per-user configured/enabled signals (no secrets).
    """
    # Resolve user_id from authentication if "current" is specified
    if user_id == "current":
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")
        user_id = str(current_user.id)

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
    current_user: Optional[User] = Depends(get_current_user)
):
    """Get available models from a provider."""
    try:
        # Resolve user_id from authentication if "current" is specified
        if user_id == "current":
            if not current_user:
                raise HTTPException(status_code=401, detail="Authentication required")
            user_id = str(current_user.id)
        
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
    current_user: Optional[User] = Depends(get_current_user)
):
    """Get models from ALL connected providers for a user."""
    try:
        # Resolve user_id from authentication if "current" is specified
        if user_id == "current":
            if not current_user:
                raise HTTPException(status_code=401, detail="Authentication required")
            user_id = str(current_user.id)
        
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
            
            document_context_mode = request.params.get("document_context_mode")

            # Apply persona prompt/model settings after history merge (preserve new persona changes)
            combined_messages, enhanced_params = apply_persona_prompt_and_params(
                combined_messages,
                request.params or {},
                request.persona_system_prompt,
                request.persona_model_settings,
                max_history=100  # trim oldest history messages if needed
            )

            # Remove local-only params before sending to provider
            if "document_context_mode" in enhanced_params:
                enhanced_params.pop("document_context_mode", None)

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

                        print(f"Sending {len(combined_messages)} messages to chat_completion_stream")
                        for i, msg in enumerate(combined_messages):
                            print(f"  Message {i+1}: role={msg.get('role', 'unknown')}, content={msg.get('content', '')[:50]}...")
                        
                        async for chunk in provider_instance.chat_completion_stream(
                            combined_messages,
                            request.model,
                            enhanced_params
                        ):
                            if isinstance(chunk, dict) and "error" in chunk:
                                raise RuntimeError(chunk["error"])
                            # Extract content from the chunk
                            content = ""
                            if "choices" in chunk and len(chunk["choices"]) > 0:
                                if "delta" in chunk["choices"][0]:
                                    content = chunk["choices"][0]["delta"].get("content", "")
                                elif "text" in chunk["choices"][0]:
                                    content = chunk["choices"][0]["text"]
                            
                            # Accumulate the full response
                            full_response += content
                            token_count += 1
                            
                            # Yield each chunk and flush immediately
                            yield f"data: {json.dumps(chunk)}\n\n"
                            # Add an explicit flush marker
                            yield ""
                        
                        # Calculate tokens per second
                        elapsed_time = time.time() - start_time
                        tokens_per_second = token_count / elapsed_time if elapsed_time > 0 else 0
                        
                        # Store the LLM response in the database with persona metadata
                        message_metadata = {
                            "token_count": token_count,
                            "tokens_per_second": round(tokens_per_second, 1),
                            "model": request.model,
                            "temperature": enhanced_params.get("temperature", 0.7),
                            "streaming": True
                        }
                        
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
            
            result = await provider_instance.chat_completion(
                combined_messages,
                request.model,
                enhanced_params
            )
            if isinstance(result, dict) and result.get("error"):
                raise HTTPException(
                    status_code=502,
                    detail=f"Provider error: {result['error']}"
                )
            elapsed_time = time.time() - start_time
            
            print(f"Chat completion result: {result}")
            
            # Extract the response content
            response_content = ""
            if "choices" in result and len(result["choices"]) > 0:
                if "message" in result["choices"][0]:
                    response_content = result["choices"][0]["message"].get("content", "")
                elif "text" in result["choices"][0]:
                    response_content = result["choices"][0]["text"]
            if not response_content and isinstance(result, dict):
                if isinstance(result.get("content"), str):
                    response_content = result["content"]
                elif isinstance(result.get("message"), dict):
                    response_content = result["message"].get("content", "")
                elif isinstance(result.get("text"), str):
                    response_content = result["text"]
            
            # Estimate token count (this is a rough estimate)
            token_count = len(response_content.split()) * 1.3  # Rough estimate: words * 1.3
            tokens_per_second = token_count / elapsed_time if elapsed_time > 0 else 0
            
            # Store the LLM response in the database with persona metadata
            message_metadata = {
                "token_count": int(token_count),
                "tokens_per_second": round(tokens_per_second, 1),
                "model": request.model,
                "temperature": enhanced_params.get("temperature", 0.7),
                "streaming": False
            }
            
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
