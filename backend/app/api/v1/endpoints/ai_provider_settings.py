"""
API endpoints for AI provider settings.
"""
from fastapi import APIRouter, HTTPException, Depends, Body
from typing import List, Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.auth_deps import require_user
from app.core.auth_context import AuthContext
from app.models.settings import SettingDefinition, SettingScope, SettingInstance
from app.ai_providers.registry import provider_registry
from app.schemas.ai_providers import ProviderSettingRequest

router = APIRouter()


async def ensure_provider_settings_definitions(db: AsyncSession):
    """Ensure settings definitions exist for all providers."""
    for provider_name in provider_registry.get_available_providers():
        definition_id = f"ai_provider_{provider_name}"
        definition = await SettingDefinition.get_by_id(db, definition_id)
        
        if not definition:
            # Create provider-specific validation schema
            validation = {
                "type": "object",
                "required": ["name"],
                "properties": {
                    "name": {"type": "string", "minLength": 1}
                }
            }
            
            # Add provider-specific properties
            if provider_name == "ollama":
                validation["required"].extend(["server_url"])
                validation["properties"].update({
                    "server_url": {"type": "string", "format": "uri"},
                    "api_key": {"type": "string"}
                })
            elif provider_name == "openai":
                validation["required"].extend(["api_key"])
                validation["properties"].update({
                    "api_key": {"type": "string", "minLength": 1},
                    "organization": {"type": "string"},
                    "base_url": {"type": "string", "format": "uri"}
                })
            
            # Create the definition
            definition = SettingDefinition(
                id=definition_id,
                name=f"{provider_name.capitalize()} Provider Settings",
                description=f"Configuration for {provider_name.capitalize()} AI provider",
                category="ai_providers",
                type="object",
                allowed_scopes=[SettingScope.USER],
                validation=validation
            )
            await definition.save(db)


@router.get("/providers")
async def get_provider_settings(
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user)
):
    """Get all provider settings instances."""
    user_id = str(auth.user_id)
    
    # Ensure settings definitions exist
    await ensure_provider_settings_definitions(db)
    
    # Get all provider settings
    instances = []
    for provider_name in provider_registry.get_available_providers():
        definition_id = f"ai_provider_{provider_name}"
        provider_instances = await SettingInstance.get_by_definition_and_scope(
            db, 
            definition_id, 
            SettingScope.USER, 
            user_id
        )
        instances.extend(provider_instances)
    
    return {
        "instances": [
            {
                "id": instance.id,
                "provider": instance.id.split("_")[2] if len(instance.id.split("_")) > 2 else "",
                "instance_id": instance.id.split("_")[3] if len(instance.id.split("_")) > 3 else "",
                "name": instance.name,
                "config": instance.value
            }
            for instance in instances
        ]
    }


@router.post("/providers")
async def create_provider_setting(
    request: ProviderSettingRequest,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user)
):
    """Create or update a provider setting."""
    user_id = str(auth.user_id)
    
    # Ensure settings definitions exist
    await ensure_provider_settings_definitions(db)
    
    # Validate provider
    if request.provider not in provider_registry.get_available_providers():
        raise HTTPException(status_code=404, detail=f"Provider '{request.provider}' not found")
    
    # Create or update setting
    setting_id = f"ai_provider_{request.provider}_{request.instance_id}"
    setting = await SettingInstance.get_by_id_and_scope(
        db, 
        setting_id, 
        SettingScope.USER, 
        user_id
    )
    
    if setting:
        # Update existing
        setting.value = request.config
        setting.name = request.name
        await setting.save(db)
    else:
        # Create new
        setting = SettingInstance(
            id=setting_id,
            definition_id=f"ai_provider_{request.provider}",
            name=request.name,
            value=request.config,
            scope=SettingScope.USER,
            user_id=user_id
        )
        await setting.save(db)
    
    return {
        "id": setting.id,
        "provider": request.provider,
        "instance_id": request.instance_id,
        "name": request.name,
        "config": request.config
    }


@router.delete("/providers/{provider}/{instance_id}")
async def delete_provider_setting(
    provider: str,
    instance_id: str,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user)
):
    """Delete a provider setting."""
    user_id = str(auth.user_id)
    
    # Validate provider
    if provider not in provider_registry.get_available_providers():
        raise HTTPException(status_code=404, detail=f"Provider '{provider}' not found")
    
    # Delete setting
    setting_id = f"ai_provider_{provider}_{instance_id}"
    setting = await SettingInstance.get_by_id_and_scope(
        db, 
        setting_id, 
        SettingScope.USER, 
        user_id
    )
    
    if not setting:
        raise HTTPException(status_code=404, detail=f"Provider instance not found")
    
    await setting.delete(db)
    
    return {
        "status": "success",
        "message": f"Provider instance '{instance_id}' deleted"
    }


@router.get("/servers/{settings_id}")
async def get_servers(
    settings_id: str,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_user)
):
    """Get servers from a settings instance."""
    user_id = str(auth.user_id)
    
    # Get settings instance
    setting = await SettingInstance.get_by_id_and_scope(
        db, 
        settings_id, 
        SettingScope.USER, 
        user_id
    )
    
    if not setting:
        raise HTTPException(status_code=404, detail=f"Settings not found")
    
    # Extract servers from settings value
    servers = setting.value.get("servers", [])
    
    return {
        "servers": servers
    }
