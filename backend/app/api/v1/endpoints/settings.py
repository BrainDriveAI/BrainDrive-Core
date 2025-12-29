from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from typing import List, Optional
from app.core.database import get_db
from app.core.auth_deps import require_user, require_admin, optional_user
from app.core.auth_context import AuthContext
from app.models.settings import SettingDefinition, SettingInstance, SettingScope
from app.schemas.settings import (
    SettingDefinitionCreate,
    SettingDefinitionUpdate,
    SettingDefinitionResponse,
    SettingInstanceCreate,
    SettingInstanceUpdate,
    SettingInstanceResponse
)
import logging
import uuid
import json
from sqlalchemy import text, func

router = APIRouter(prefix="/settings", dependencies=[Depends(require_user)])
logger = logging.getLogger(__name__)

def mask_sensitive_data(definition_id: str, value: any) -> any:
    """
    Mask sensitive data in settings values to prevent exposure to frontend.
    Currently handles OpenAI, OpenRouter, and Claude API keys.
    """
    if not value:
        return value
    
    # Handle OpenAI API keys
    if definition_id == "openai_api_keys_settings":
        if isinstance(value, dict) and "api_key" in value:
            api_key = value["api_key"]
            if api_key and len(api_key) >= 11:
                # Mask the API key (first 7 + last 4 characters)
                masked_key = api_key[:7] + "..." + api_key[-4:]
                return {
                    **value,
                    "api_key": masked_key,
                    "_has_key": bool(api_key.strip()),
                    "_key_valid": bool(api_key.startswith('sk-') and len(api_key) >= 23)
                }
    
    # Handle OpenRouter API keys
    if definition_id == "openrouter_api_keys_settings":
        if isinstance(value, dict) and "api_key" in value:
            api_key = value["api_key"]
            if api_key and len(api_key) >= 11:
                # Mask the API key (first 7 + last 4 characters)
                masked_key = api_key[:7] + "..." + api_key[-4:]
                return {
                    **value,
                    "api_key": masked_key,
                    "_has_key": bool(api_key.strip()),
                    "_key_valid": bool(api_key.startswith('sk-or-') and len(api_key) >= 26)
                }
    
    # Handle Claude API keys
    if definition_id == "claude_api_keys_settings":
        if isinstance(value, dict) and "api_key" in value:
            api_key = value["api_key"]
            if api_key and len(api_key) >= 11:
                # Mask the API key (first 7 + last 4 characters)
                masked_key = api_key[:7] + "..." + api_key[-4:]
                return {
                    **value,
                    "api_key": masked_key,
                    "_has_key": bool(api_key.strip()),
                    "_key_valid": bool(api_key.startswith('sk-ant-') and len(api_key) >= 26)
                }
    
    if definition_id == "groq_api_keys_settings":
        if isinstance(value, dict) and "api_key" in value:
            api_key = value["api_key"]
            if api_key and len(api_key) >= 11:
                # Mask the API key (first 4 + last 4 characters for gsk_ format)
                masked_key = api_key[:4] + "..." + api_key[-4:]
                return {
                    **value,
                    "api_key": masked_key,
                    "_has_key": bool(api_key.strip()),
                    "_key_valid": bool(api_key.startswith('gsk_') and len(api_key) >= 24)
                }
    
    return value

async def get_definition_by_id(db, definition_id: str):
    """Helper function to get a setting definition by ID using direct SQL."""
    query = text("""
    SELECT id, name, description, category, type, default_value,
           allowed_scopes, validation, is_multiple, tags, created_at, updated_at
    FROM settings_definitions
    WHERE id = :id
    """)
    
    result = await db.execute(query, {"id": definition_id})
    row = result.fetchone()
    
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Setting definition {definition_id} not found"
        )
    
    # Parse JSON fields
    allowed_scopes = json.loads(row[6]) if row[6] else []
    # Convert allowed_scopes from strings to SettingScope enum values
    allowed_scopes_enum = [SettingScope(scope) for scope in allowed_scopes]
    
    # Convert row to dict matching SettingDefinitionResponse
    return {
        "id": row[0],
        "name": row[1],
        "description": row[2],
        "category": row[3],
        "type": row[4],
        "default_value": json.loads(row[5]) if row[5] else None,
        "allowed_scopes": allowed_scopes_enum,
        "validation": json.loads(row[7]) if row[7] else None,
        "is_multiple": row[8],
        "tags": json.loads(row[9]) if row[9] else [],
        "created_at": row[10],
        "updated_at": row[11]
    }

@router.post("/definitions", response_model=SettingDefinitionResponse)
async def create_setting_definition(
    definition: SettingDefinitionCreate,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_admin)
):
    """Create a new setting definition."""
    try:
        # Check if definition already exists using direct SQL
        check_query = text("SELECT id FROM settings_definitions WHERE name = :name")
        result = await db.execute(check_query, {"name": definition.name})
        existing = result.scalar_one_or_none()
        
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Setting definition {definition.name} already exists"
            )

        # Convert allowed_scopes to JSON string
        allowed_scopes_json = json.dumps([s.value for s in definition.allowed_scopes])
        
        # Convert tags to JSON string if present
        tags_json = json.dumps(definition.tags) if definition.tags else None
        
        # Convert validation to JSON string if present
        validation_json = json.dumps(definition.validation) if definition.validation else None
        
        # Convert default_value to JSON string if present
        default_value_json = json.dumps(definition.default_value) if definition.default_value is not None else None

        # Create new definition using direct SQL
        insert_query = text("""
        INSERT INTO settings_definitions (
            id, name, description, category, type, default_value,
            allowed_scopes, validation, is_multiple, tags, created_at, updated_at
        ) VALUES (
            :id, :name, :description, :category, :type, :default_value,
            :allowed_scopes, :validation, :is_multiple, :tags, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        )
        """)
        
        await db.execute(insert_query, {
            "id": definition.id,
            "name": definition.name,
            "description": definition.description,
            "category": definition.category,
            "type": definition.type,
            "default_value": default_value_json,
            "allowed_scopes": allowed_scopes_json,
            "validation": validation_json,
            "is_multiple": definition.is_multiple,
            "tags": tags_json
        })
        
        await db.commit()
        
        # Fetch the created definition using direct SQL
        fetch_query = text("""
        SELECT id, name, description, category, type, default_value,
               allowed_scopes, validation, is_multiple, tags, created_at, updated_at
        FROM settings_definitions
        WHERE id = :id
        """)
        
        result = await db.execute(fetch_query, {"id": definition.id})
        row = result.fetchone()
        
        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Setting definition {definition.id} not found after creation"
            )
        
        # Parse JSON fields
        allowed_scopes = json.loads(row[6]) if row[6] else []
        # Convert allowed_scopes from strings to SettingScope enum values
        allowed_scopes_enum = [SettingScope(scope) for scope in allowed_scopes]
        
        # Convert row to dict
        setting_def = {
            "id": row[0],
            "name": row[1],
            "description": row[2],
            "category": row[3],
            "type": row[4],
            "default_value": json.loads(row[5]) if row[5] else None,
            "allowed_scopes": allowed_scopes_enum,
            "validation": json.loads(row[7]) if row[7] else None,
            "is_multiple": row[8],
            "tags": json.loads(row[9]) if row[9] else [],
            "created_at": row[10],
            "updated_at": row[11]
        }
        
        return setting_def
    except HTTPException as e:
        # Re-raise HTTP exceptions
        logger.error(f"HTTP exception in create_setting_definition: {e.detail}")
        raise
    except Exception as e:
        # Log and convert other exceptions to HTTP exceptions
        logger.error(f"Unexpected error in create_setting_definition: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not create setting definition: {str(e)}"
        )

@router.get("/definitions", response_model=List[SettingDefinitionResponse])
async def get_setting_definitions(
    category: Optional[str] = None,
    scope: Optional[SettingScope] = None,
    db: AsyncSession = Depends(get_db),
    auth: Optional[AuthContext] = Depends(optional_user)
):
    """Get all setting definitions, optionally filtered by category and scope."""
    try:
        # Build SQL query with conditions
        conditions = []
        params = {}
        
        if category:
            conditions.append("category = :category")
            params["category"] = category
            
        if scope:
            # For scope filtering, we need to check if the scope is in the allowed_scopes JSON array
            # This is a bit tricky with SQLite, but we can use the JSON1 extension
            conditions.append("json_extract(allowed_scopes, '$') LIKE :scope")
            params["scope"] = f'%"{scope.value if hasattr(scope, "value") else scope}"%'
            
        where_clause = " AND ".join(conditions) if conditions else "1=1"
        query = f"""
        SELECT id, name, description, category, type, default_value,
               allowed_scopes, validation, is_multiple, tags, created_at, updated_at
        FROM settings_definitions
        WHERE {where_clause}
        """
        
        # Execute the query
        result = await db.execute(text(query), params)
        rows = result.fetchall()
        
        # Convert rows to dictionaries
        definitions = []
        for row in rows:
            # Parse JSON fields
            allowed_scopes = json.loads(row[6]) if row[6] else []
            # Convert allowed_scopes from strings to SettingScope enum values
            allowed_scopes_enum = [SettingScope(scope) for scope in allowed_scopes]
            
            definition = {
                "id": row[0],
                "name": row[1],
                "description": row[2],
                "category": row[3],
                "type": row[4],
                "default_value": json.loads(row[5]) if row[5] else None,
                "allowed_scopes": allowed_scopes_enum,
                "validation": json.loads(row[7]) if row[7] else None,
                "is_multiple": row[8],
                "tags": json.loads(row[9]) if row[9] else [],
                "created_at": row[10],
                "updated_at": row[11]
            }
            definitions.append(definition)
            
        return definitions
    except HTTPException as e:
        # Re-raise HTTP exceptions
        logger.error(f"HTTP exception in get_setting_definitions: {e.detail}")
        raise
    except Exception as e:
        # Log and convert other exceptions to HTTP exceptions
        logger.error(f"Unexpected error in get_setting_definitions: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not retrieve setting definitions: {str(e)}"
        )

@router.patch("/definitions/{definition_id}", response_model=SettingDefinitionResponse)
async def update_setting_definition(
    definition_id: str,
    update_data: SettingDefinitionUpdate,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_admin)
):
    """Update a setting definition (partial update)."""
    try:
        # Check if the definition exists using direct SQL
        check_query = text("SELECT id FROM settings_definitions WHERE id = :id")
        result = await db.execute(check_query, {"id": definition_id})
        existing = result.scalar_one_or_none()
        
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Setting definition {definition_id} not found"
            )

        # Get the update fields from the update_data
        update_dict = update_data.model_dump(exclude_unset=True)
        if not update_dict:
            # No fields to update
            return await get_definition_by_id(db, definition_id)
            
        # Build the SET clause for the SQL UPDATE statement
        set_clauses = []
        params = {"id": definition_id}
        
        # Process each field that needs to be updated
        for key, value in update_dict.items():
            # Special handling for JSON fields
            if key in ['allowed_scopes', 'tags', 'validation', 'default_value']:
                if value is not None:
                    # Convert to JSON string
                    params[key] = json.dumps(value)
                    set_clauses.append(f"{key} = :{key}")
                else:
                    params[key] = None
                    set_clauses.append(f"{key} = :{key}")
            else:
                # Regular fields
                params[key] = value
                set_clauses.append(f"{key} = :{key}")
        
        # Always update the updated_at timestamp
        set_clauses.append("updated_at = CURRENT_TIMESTAMP")
        
        # Build and execute the UPDATE query
        update_query = text(f"""
        UPDATE settings_definitions
        SET {', '.join(set_clauses)}
        WHERE id = :id
        """)
        
        await db.execute(update_query, params)
        await db.commit()
        
        # Fetch the updated definition
        return await get_definition_by_id(db, definition_id)
    except HTTPException as e:
        # Re-raise HTTP exceptions
        logger.error(f"HTTP exception in update_setting_definition: {e.detail}")
        raise
    except Exception as e:
        # Log and convert other exceptions to HTTP exceptions
        logger.error(f"Unexpected error in update_setting_definition: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not update setting definition: {str(e)}"
        )


@router.put("/definitions/{definition_id}", response_model=SettingDefinitionResponse)
async def put_setting_definition(
    definition_id: str,
    update_data: SettingDefinitionCreate,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_admin)
):
    """Update a setting definition (full update)."""
    try:
        # Check if the definition exists using direct SQL
        check_query = text("SELECT id FROM settings_definitions WHERE id = :id")
        result = await db.execute(check_query, {"id": definition_id})
        existing = result.scalar_one_or_none()
        
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Setting definition {definition_id} not found"
            )

        # Ensure the ID in the path matches the ID in the request body
        if update_data.id != definition_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Definition ID in path does not match ID in request body"
            )

        # Convert allowed_scopes to JSON string
        allowed_scopes_json = json.dumps([s.value for s in update_data.allowed_scopes])
        
        # Convert tags to JSON string if present
        tags_json = json.dumps(update_data.tags) if update_data.tags else None
        
        # Convert validation to JSON string if present
        validation_json = json.dumps(update_data.validation) if update_data.validation else None
        
        # Convert default_value to JSON string if present
        default_value_json = json.dumps(update_data.default_value) if update_data.default_value is not None else None

        # Update all fields using direct SQL
        update_query = text("""
        UPDATE settings_definitions
        SET name = :name,
            description = :description,
            category = :category,
            type = :type,
            default_value = :default_value,
            allowed_scopes = :allowed_scopes,
            validation = :validation,
            is_multiple = :is_multiple,
            tags = :tags,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = :id
        """)
        
        await db.execute(update_query, {
            "id": definition_id,
            "name": update_data.name,
            "description": update_data.description,
            "category": update_data.category,
            "type": update_data.type,
            "default_value": default_value_json,
            "allowed_scopes": allowed_scopes_json,
            "validation": validation_json,
            "is_multiple": update_data.is_multiple,
            "tags": tags_json
        })
        
        await db.commit()
        
        # Fetch the updated definition
        return await get_definition_by_id(db, definition_id)
    except HTTPException as e:
        # Re-raise HTTP exceptions
        logger.error(f"HTTP exception in put_setting_definition: {e.detail}")
        raise
    except Exception as e:
        # Log and convert other exceptions to HTTP exceptions
        logger.error(f"Unexpected error in put_setting_definition: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not update setting definition: {str(e)}"
        )


@router.delete("/definitions/{definition_id}", response_model=dict)
async def delete_setting_definition(
    definition_id: str,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_admin)
):
    """Delete a setting definition."""
    try:
        # Check if the definition exists using direct SQL
        check_query = text("SELECT id FROM settings_definitions WHERE id = :id")
        result = await db.execute(check_query, {"id": definition_id})
        existing = result.scalar_one_or_none()
        
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Setting definition {definition_id} not found"
            )

        # Check if there are any instances using this definition
        # This is a safety check to prevent orphaned instances
        instances_query = text("""
        SELECT COUNT(*) FROM settings_instances WHERE definition_id = :definition_id
        """)
        result = await db.execute(instances_query, {"definition_id": definition_id})
        instance_count = result.scalar_one()
        
        if instance_count > 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot delete definition {definition_id} because it has {instance_count} instances. Delete the instances first."
            )

        # Delete the definition using direct SQL
        delete_query = text("""
        DELETE FROM settings_definitions WHERE id = :id
        """)
        await db.execute(delete_query, {"id": definition_id})
        await db.commit()
        
        return {"message": f"Setting definition {definition_id} deleted successfully"}
    except HTTPException as e:
        # Re-raise HTTP exceptions
        logger.error(f"HTTP exception in delete_setting_definition: {e.detail}")
        raise
    except Exception as e:
        # Log and convert other exceptions to HTTP exceptions
        logger.error(f"Unexpected error in delete_setting_definition: {e}")
        import traceback
        logger.error(traceback.format_exc())
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error deleting setting definition: {str(e)}"
        )


@router.post("/instances", response_model=None)
async def create_setting_instance(
    instance_data: SettingInstanceCreate,
    db: AsyncSession = Depends(get_db),
    auth: Optional[AuthContext] = Depends(optional_user)
):
    """Create a new setting instance."""
    logger.info(f"Creating setting instance with data: {instance_data.dict()}")
    logger.debug(f"Current user: {auth}")
    
    try:
        # Ensure scope is properly converted to enum
        if isinstance(instance_data.scope, str):
            try:
                # Try direct conversion first
                instance_data.scope = SettingScope(instance_data.scope)
            except ValueError:
                # Try case-insensitive matching
                for scope_enum in SettingScope:
                    if scope_enum.value.lower() == instance_data.scope.lower():
                        instance_data.scope = scope_enum
                        break
                else:
                    # No match found
                    logger.error(f"Invalid scope value: {instance_data.scope}")
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Invalid scope value: {instance_data.scope}. Valid values are: {', '.join([s.value for s in SettingScope])}"
                    )
        
        # Check if this is a delete action
        if instance_data.action == 'delete':
            logger.info(f"Processing delete action for instance ID: {instance_data.id}")
            if not instance_data.id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Instance ID is required for delete action"
                )
            
            # Use direct SQL to check if the instance exists and get its scope and user_id
            check_query = text("""
            SELECT id, user_id, scope FROM settings_instances WHERE id = :id
            """)
            
            result = await db.execute(check_query, {"id": instance_data.id})
            instance_row = result.fetchone()
            
            if not instance_row:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Setting instance {instance_data.id} not found"
                )
            
            # Extract user_id and scope from the result
            instance_id, instance_user_id, instance_scope = instance_row
            
            logger.info(f"Found existing instance with ID: {instance_id}, user_id: {instance_user_id}, scope: {instance_scope}")
            
            # Check access permission
            if instance_scope.lower() in ['user', 'user_page']:
                if not auth:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Authentication required for user settings"
                    )
                if str(instance_user_id) != str(auth.user_id):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Access denied to this setting instance"
                    )
            elif instance_scope.lower() == 'system':
                if not auth or not auth.is_admin:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Admin privileges required for system settings"
                    )
            
            # Delete the instance using direct SQL
            delete_query = text("""
            DELETE FROM settings_instances WHERE id = :id
            """)
            
            await db.execute(delete_query, {"id": instance_data.id})
            await db.commit()
            
            return {"id": instance_data.id, "message": "Setting instance deleted successfully"}
        
        # Check if this is an update (ID is provided)
        if instance_data.id:
            logger.info(f"Processing update for instance ID: {instance_data.id}")
            try:
                # First check if the instance exists using direct SQL
                # Check if the instance exists
                check_query = text("""
                SELECT id, user_id, scope FROM settings_instances WHERE id = :id
                """)
                
                result = await db.execute(check_query, {"id": instance_data.id})
                instance_row = result.fetchone()
                
                if not instance_row:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Setting instance {instance_data.id} not found"
                    )
                
                # Extract user_id and scope from the result
                instance_id, instance_user_id, instance_scope = instance_row
                
                logger.info(f"Found existing instance with ID: {instance_id}, user_id: {instance_user_id}, scope: {instance_scope}")
                
                # Check access permission
                if instance_scope.lower() in ['user', 'user_page']:
                    if not auth:
                        raise HTTPException(
                            status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Authentication required for user settings"
                        )
                    if str(instance_user_id) != str(auth.user_id):
                        raise HTTPException(
                            status_code=status.HTTP_403_FORBIDDEN,
                            detail="Access denied to this setting instance"
                        )
                elif instance_scope.lower() == 'system':
                    if not auth or not auth.is_admin:
                        raise HTTPException(
                            status_code=status.HTTP_403_FORBIDDEN,
                            detail="Admin privileges required for system settings"
                        )
                
                # Handle special case for 'current' user_id
                user_id_value = instance_data.user_id
                if user_id_value == 'current' and auth:
                    user_id_value = str(auth.user_id)
                
                # Use SQLAlchemy model to update the instance (enables encryption)
                logger.info("Using SQLAlchemy model to update the instance")
                
                # First check if instance exists using direct SQL
                check_query = text("SELECT scope FROM settings_instances WHERE id = :id")
                result = await db.execute(check_query, {"id": instance_data.id})
                existing_row = result.fetchone()
                
                if not existing_row:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Setting instance {instance_data.id} not found"
                    )
                
                # Convert the value to JSON string for direct SQL update
                value_json = instance_data.value
                if not isinstance(value_json, str):
                    value_json = json.dumps(value_json)
                
                # Use our encrypted column type to encrypt the value
                from app.core.encrypted_column import EncryptedJSON
                encrypted_column = EncryptedJSON("settings_instances", "value")
                encrypted_value = encrypted_column.process_bind_param(instance_data.value, None)
                
                # Update using direct SQL but with encrypted value
                update_query = text("""
                UPDATE settings_instances
                SET name = :name, value = :value, user_id = :user_id, updated_at = CURRENT_TIMESTAMP
                WHERE id = :id
                """)
                
                await db.execute(update_query, {
                    "name": instance_data.name,
                    "value": encrypted_value,
                    "user_id": user_id_value,
                    "id": instance_data.id
                })
                
                await db.commit()
                
                # Fetch the updated instance using direct SQL
                fetch_query = text("""
                SELECT id, definition_id, name, value, scope, user_id, page_id, created_at, updated_at
                FROM settings_instances
                WHERE id = :id
                """)
                
                result = await db.execute(fetch_query, {"id": instance_data.id})
                updated_row = result.fetchone()
                
                if not updated_row:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Setting instance {instance_data.id} not found after update"
                    )
                
                # Decrypt the value using our encrypted column type
                decrypted_value = encrypted_column.process_result_value(updated_row[3], None)
                
                # Convert row to dict
                updated_instance = {
                    "id": updated_row[0],
                    "definition_id": updated_row[1],
                    "name": updated_row[2],
                    "value": decrypted_value,
                    "scope": updated_row[4],
                    "user_id": updated_row[5],
                    "page_id": updated_row[6],
                    "created_at": updated_row[7],
                    "updated_at": updated_row[8]
                }
                
                return updated_instance
            except Exception as e:
                logger.error(f"Error updating instance: {e}")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Error updating instance: {str(e)}"
                )
        
        # Ensure user context is set correctly
        if instance_data.scope == SettingScope.USER or instance_data.scope == SettingScope.USER_PAGE:
            if not instance_data.user_id and auth:
                logger.info(f"Setting user_id to current user: {auth.user_id}")
                instance_data.user_id = auth.user_id
        
        try:
            # Get the definition to validate the value and scopes using direct SQL
            check_query = text("SELECT id FROM settings_definitions WHERE id = :id")
            result = await db.execute(check_query, {"id": instance_data.definition_id})
            definition_exists = result.scalar_one_or_none()
            
            # If definition doesn't exist, create it automatically
            if not definition_exists:
                logger.info(f"Setting definition {instance_data.definition_id} not found. Creating it automatically.")
                
                # Create a default definition based on the instance data
                default_definition = {
                    "id": instance_data.definition_id,
                    "name": instance_data.name,
                    "description": f"Auto-generated definition for {instance_data.name}",
                    "category": "auto_generated",
                    "type": "object",
                    "default_value": instance_data.value,
                    "allowed_scopes": [s.value for s in SettingScope],
                    "is_multiple": False,
                    "tags": ["auto_generated"]
                }
                
                # Convert JSON fields to strings
                default_value_json = json.dumps(default_definition["default_value"]) if default_definition["default_value"] is not None else None
                allowed_scopes_json = json.dumps(default_definition["allowed_scopes"])
                tags_json = json.dumps(default_definition["tags"])
                
                # Create the definition using direct SQL
                create_def_query = text("""
                INSERT INTO settings_definitions (
                    id, name, description, category, type, default_value,
                    allowed_scopes, is_multiple, tags, created_at, updated_at
                ) VALUES (
                    :id, :name, :description, :category, :type, :default_value,
                    :allowed_scopes, :is_multiple, :tags, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """)
                
                await db.execute(create_def_query, {
                    "id": default_definition["id"],
                    "name": default_definition["name"],
                    "description": default_definition["description"],
                    "category": default_definition["category"],
                    "type": default_definition["type"],
                    "default_value": default_value_json,
                    "allowed_scopes": allowed_scopes_json,
                    "is_multiple": default_definition["is_multiple"],
                    "tags": tags_json
                })
                
                await db.commit()
                logger.info(f"Created auto-generated definition: {default_definition['id']}")
            
            # Check if a similar instance already exists (if this is not an update)
            if not instance_data.id:
                # Convert scope to string if it's an enum
                scope_value = instance_data.scope
                if hasattr(scope_value, 'value'):
                    scope_value = scope_value.value
                
                # Build conditions for the query
                conditions = ["definition_id = :definition_id", "scope = :scope"]
                params = {
                    "definition_id": instance_data.definition_id,
                    "scope": scope_value
                }
                
                if instance_data.user_id:
                    conditions.append("user_id = :user_id")
                    params["user_id"] = instance_data.user_id
                else:
                    conditions.append("user_id IS NULL")
                
                if instance_data.page_id:
                    conditions.append("page_id = :page_id")
                    params["page_id"] = instance_data.page_id
                else:
                    conditions.append("page_id IS NULL")
                
                # Build and execute the query
                where_clause = " AND ".join(conditions)
                check_instance_query = text(f"""
                SELECT id, definition_id, name, value, scope, user_id, page_id, created_at, updated_at
                FROM settings_instances
                WHERE {where_clause}
                """)
                
                result = await db.execute(check_instance_query, params)
                existing_row = result.fetchone()
                
                if existing_row:
                    logger.info(f"Found existing instance for this context: {existing_row[0]}")
                    # Use direct SQL to update the existing instance
                    existing_id = existing_row[0]
                    
                    # Convert value to JSON string if it's not already
                    value_json = instance_data.value
                    if not isinstance(value_json, str):
                        value_json = json.dumps(value_json)
                    
                    update_query = text("""
                    UPDATE settings_instances
                    SET name = :name, value = :value, updated_at = CURRENT_TIMESTAMP
                    WHERE id = :id
                    """)
                    
                    await db.execute(update_query, {
                        "name": instance_data.name,
                        "value": value_json,
                        "id": existing_id
                    })
                    
                    await db.commit()
                    
                    # Fetch the updated instance
                    fetch_query = text("""
                    SELECT id, definition_id, name, value, scope, user_id, page_id, created_at, updated_at
                    FROM settings_instances
                    WHERE id = :id
                    """)
                    
                    result = await db.execute(fetch_query, {"id": existing_id})
                    updated_row = result.fetchone()
                    
                    if not updated_row:
                        raise HTTPException(
                            status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Setting instance {existing_id} not found after update"
                        )
                    
                    # Convert row to dict
                    updated_instance = {
                        "id": updated_row[0],
                        "definition_id": updated_row[1],
                        "name": updated_row[2],
                        "value": json.loads(updated_row[3]) if updated_row[3] else None,
                        "scope": updated_row[4],
                        "user_id": updated_row[5],
                        "page_id": updated_row[6],
                        "created_at": updated_row[7],
                        "updated_at": updated_row[8]
                    }
                    
                    return updated_instance
            
            # Create the instance using direct SQL
            logger.info("Using direct SQL to create the instance")
            
            # Generate a new UUID for the instance
            instance_id = str(uuid.uuid4())
            
            # Handle special case for 'current' user_id
            user_id_value = instance_data.user_id
            if user_id_value == 'current' and auth:
                user_id_value = str(auth.user_id)
            
            # Convert value to JSON string if it's not already
            value_json = instance_data.value
            if not isinstance(value_json, str):
                value_json = json.dumps(value_json)
            
            # Convert scope to string if it's an enum
            scope_value = instance_data.scope
            if hasattr(scope_value, 'value'):
                scope_value = scope_value.value
            
            # Use SQLAlchemy model to create the instance (enables encryption)
            logger.info("Using SQLAlchemy model to create the instance")
            
            # Ensure scope is enum, not string value
            if isinstance(scope_value, str):
                # Convert string back to enum
                try:
                    scope_enum = SettingScope(scope_value)
                except ValueError:
                    # Try case-insensitive matching
                    for scope_enum in SettingScope:
                        if scope_enum.value.lower() == scope_value.lower():
                            break
                    else:
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Invalid scope value: {scope_value}"
                        )
            else:
                scope_enum = scope_value
            
            # Create new instance using SQLAlchemy model
            new_instance = SettingInstance(
                id=instance_id,
                definition_id=instance_data.definition_id,
                name=instance_data.name,
                value=instance_data.value,  # This will be automatically encrypted
                scope=scope_enum,
                user_id=user_id_value,
                page_id=instance_data.page_id
            )
            
            # Add and commit the instance
            db.add(new_instance)
            await db.commit()
            await db.refresh(new_instance)
            
            # Convert to dict for response
            created_instance = {
                "id": new_instance.id,
                "definition_id": new_instance.definition_id,
                "name": new_instance.name,
                "value": new_instance.value,  # This will be automatically decrypted
                "scope": new_instance.scope.value if hasattr(new_instance.scope, 'value') else new_instance.scope,
                "user_id": new_instance.user_id,
                "page_id": new_instance.page_id,
                "created_at": new_instance.created_at,
                "updated_at": new_instance.updated_at
            }
            
            logger.info(f"Created setting instance: {instance_id}")
            return created_instance
        except Exception as e:
            logger.error(f"Error creating setting instance: {e}")
            # Log full exception details for debugging
            import traceback
            logger.error(traceback.format_exc())
            await db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Could not create setting instance: {str(e)}"
            )
    except HTTPException as e:
        # Re-raise HTTP exceptions
        logger.error(f"HTTP exception in create_setting_instance: {e.detail}")
        raise
    except Exception as e:
        # Log and convert other exceptions to HTTP exceptions
        logger.error(f"Unexpected error in create_setting_instance: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not create setting instance: {str(e)}"
        )


@router.get("/instances", response_model=List[SettingInstanceResponse])
async def get_setting_instances(
    definition_id: Optional[str] = None,
    scope: Optional[str] = None,
    user_id: Optional[str] = None,
    page_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    auth: Optional[AuthContext] = Depends(optional_user)
):
    """Get settings instances based on filters."""
    logger.info(f"Getting settings instances with filters: definition_id={definition_id}, scope={scope}, user_id={user_id}, page_id={page_id}")
    
    # If user_id is specified but no current user, require authentication
    if user_id and not auth:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required to access user settings"
        )

    # If user_id is 'current', use the current user's ID
    if user_id == "current" and auth:
        user_id = str(auth.user_id)
        logger.info(f"Using current user ID: {user_id}")
    elif user_id == "current" and not auth:
        logger.warning("User ID 'current' specified but no current user available")
        # Return empty list if no current user is available
        return []
    
    # If scope is 'user' but no user_id is provided, use the current user's ID
    if scope and scope.lower() == "user" and not user_id and auth:
        user_id = str(auth.user_id)
        logger.info(f"Scope is 'user' but no user_id provided, using current user ID: {user_id}")

    # Handle scope conversion - ensure it's lowercase for case-insensitive matching
    scope_value = None
    if scope:
        scope_value = scope.lower()
        logger.info(f"Normalized scope to lowercase: {scope_value}")
    
    logger.info(f"Final query parameters: definition_id={definition_id}, scope={scope_value}, user_id={user_id}, page_id={page_id}")
    
    try:
        # Build conditions for the SQL query
        conditions = []
        params = {}
        
        if definition_id:
            conditions.append("definition_id = :definition_id")
            params["definition_id"] = definition_id
        
        if scope_value:
            conditions.append("LOWER(scope) = :scope")
            params["scope"] = scope_value
        
        if user_id:
            conditions.append("user_id = :user_id")
            params["user_id"] = user_id
        else:
            # If no user_id is specified, include instances with null user_id
            conditions.append("(user_id IS NULL OR user_id = '')")
        
        if page_id:
            conditions.append("page_id = :page_id")
            params["page_id"] = page_id
        else:
            # If no page_id is specified, include instances with null page_id
            conditions.append("(page_id IS NULL OR page_id = '')")
        
        # Build the WHERE clause
        where_clause = " AND ".join(conditions) if conditions else "1=1"
        
        # Build and execute the query
        query = text(f"""
        SELECT id, definition_id, name, value, scope, user_id, page_id, created_at, updated_at
        FROM settings_instances
        WHERE {where_clause}
        """)
        
        result = await db.execute(query, params)
        rows = result.fetchall()
        
        # Convert rows to dictionaries with proper decryption
        from app.core.encrypted_column import EncryptedJSON
        encrypted_column = EncryptedJSON("settings_instances", "value")
        
        instances = []
        for row in rows:
            # Decrypt the value using our encrypted column type
            try:
                decrypted_value = encrypted_column.process_result_value(row[3], None)
            except Exception as e:
                # If decryption fails, try parsing as plain JSON (for backward compatibility)
                logger.warning(f"Failed to decrypt value for instance {row[0]}, trying plain JSON: {e}")
                try:
                    decrypted_value = json.loads(row[3]) if row[3] else None
                except Exception as json_error:
                    logger.error(f"Failed to parse value as JSON for instance {row[0]}: {json_error}")
                    decrypted_value = None
            
            # Mask sensitive data before sending to frontend
            masked_value = mask_sensitive_data(row[1], decrypted_value)
            
            instance = {
                "id": row[0],
                "definition_id": row[1],
                "name": row[2],
                "value": masked_value,
                "scope": row[4],
                "user_id": row[5],
                "page_id": row[6],
                "created_at": row[7],
                "updated_at": row[8]
            }
            instances.append(instance)
        
        logger.info(f"Found {len(instances)} settings instances")
        
        # Debug: Log the instances
        for instance in instances:
            logger.info(f"Instance: id={instance['id']}, definition_id={instance['definition_id']}, name={instance['name']}, scope={instance['scope']}, user_id={instance['user_id']}")
        
        # Return empty list instead of null if no instances found
        return instances or []
    except HTTPException as e:
        # Re-raise HTTP exceptions
        logger.error(f"HTTP exception in get_setting_instances: {e.detail}")
        raise
    except Exception as e:
        # Log and convert other exceptions to HTTP exceptions
        logger.error(f"Unexpected error in get_setting_instances: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not retrieve settings: {str(e)}"
        )

@router.get("/instances/{instance_id}", response_model=SettingInstanceResponse)
async def get_setting_instance(
    instance_id: str,
    db: AsyncSession = Depends(get_db),
    auth: Optional[AuthContext] = Depends(optional_user)
):
    """Get a specific setting instance by ID."""
    try:
        # Get the instance using direct SQL
        query = text("""
        SELECT id, definition_id, name, value, scope, user_id, page_id, created_at, updated_at
        FROM settings_instances
        WHERE id = :id
        """)
        
        result = await db.execute(query, {"id": instance_id})
        row = result.fetchone()
        
        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Setting instance {instance_id} not found"
            )
        
        # Decrypt the value using our encrypted column type
        from app.core.encrypted_column import EncryptedJSON
        encrypted_column = EncryptedJSON("settings_instances", "value")
        
        try:
            decrypted_value = encrypted_column.process_result_value(row[3], None)
        except Exception as e:
            # If decryption fails, try parsing as plain JSON (for backward compatibility)
            logger.warning(f"Failed to decrypt value for instance {row[0]}, trying plain JSON: {e}")
            try:
                decrypted_value = json.loads(row[3]) if row[3] else None
            except Exception as json_error:
                logger.error(f"Failed to parse value as JSON for instance {row[0]}: {json_error}")
                decrypted_value = None
        
        # Mask sensitive data before sending to frontend
        masked_value = mask_sensitive_data(row[1], decrypted_value)
        
        # Convert row to dict
        instance = {
            "id": row[0],
            "definition_id": row[1],
            "name": row[2],
            "value": masked_value,
            "scope": row[4],
            "user_id": row[5],
            "page_id": row[6],
            "created_at": row[7],
            "updated_at": row[8]
        }
        
        # Check access permission
        scope_value = instance["scope"]
        if scope_value in [SettingScope.USER.value, SettingScope.USER_PAGE.value]:
            if not auth:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Authentication required for user settings"
                )
            if str(instance["user_id"]) != str(auth.user_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access denied to this setting instance"
                )
        elif scope_value == SettingScope.SYSTEM.value:
            if not auth or not auth.is_admin:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Admin privileges required for system settings"
                )
        
        return instance
    except HTTPException as e:
        # Re-raise HTTP exceptions
        logger.error(f"HTTP exception in get_setting_instance: {e.detail}")
        raise
    except Exception as e:
        # Log and convert other exceptions to HTTP exceptions
        logger.error(f"Unexpected error in get_setting_instance: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not retrieve setting instance: {str(e)}"
        )

@router.patch("/instances/{instance_id}", response_model=SettingInstanceResponse)
async def update_setting_instance(
    instance_id: str,
    update_data: SettingInstanceUpdate,
    db: AsyncSession = Depends(get_db),
    auth: Optional[AuthContext] = Depends(optional_user)
):
    """Update a setting instance."""
    try:
        # Get the instance using direct SQL
        query = text("""
        SELECT id, definition_id, name, value, scope, user_id, page_id, created_at, updated_at
        FROM settings_instances
        WHERE id = :id
        """)
        
        result = await db.execute(query, {"id": instance_id})
        row = result.fetchone()
        
        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Setting instance {instance_id} not found"
            )
        
        # Convert row to dict
        instance = {
            "id": row[0],
            "definition_id": row[1],
            "name": row[2],
            "value": json.loads(row[3]) if row[3] else None,
            "scope": row[4],
            "user_id": row[5],
            "page_id": row[6],
            "created_at": row[7],
            "updated_at": row[8]
        }
        
        # Check access permission
        scope_value = instance["scope"]
        if scope_value in [SettingScope.USER.value, SettingScope.USER_PAGE.value]:
            if not auth:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Authentication required for user settings"
                )
            if str(instance["user_id"]) != str(auth.user_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access denied to this setting instance"
                )
        elif scope_value == SettingScope.SYSTEM.value:
            if not auth or not auth.is_admin:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Admin privileges required for system settings"
                )
        
        # Build the SET clause for the SQL UPDATE statement
        set_clauses = []
        params = {"id": instance_id}
        
        # Process each field that needs to be updated
        update_dict = update_data.dict(exclude_unset=True)
        for key, value in update_dict.items():
            # Special handling for value field (convert to JSON)
            if key == 'value' and value is not None:
                params[key] = json.dumps(value)
                set_clauses.append(f"{key} = :{key}")
            else:
                params[key] = value
                set_clauses.append(f"{key} = :{key}")
        
        # Always update the updated_at timestamp
        set_clauses.append("updated_at = CURRENT_TIMESTAMP")
        
        # Build and execute the UPDATE query
        update_query = text(f"""
        UPDATE settings_instances
        SET {', '.join(set_clauses)}
        WHERE id = :id
        """)
        
        await db.execute(update_query, params)
        await db.commit()
        
        # Fetch the updated instance
        fetch_query = text("""
        SELECT id, definition_id, name, value, scope, user_id, page_id, created_at, updated_at
        FROM settings_instances
        WHERE id = :id
        """)
        
        result = await db.execute(fetch_query, {"id": instance_id})
        updated_row = result.fetchone()
        
        if not updated_row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Setting instance {instance_id} not found after update"
            )
        
        # Convert row to dict
        updated_instance = {
            "id": updated_row[0],
            "definition_id": updated_row[1],
            "name": updated_row[2],
            "value": json.loads(updated_row[3]) if updated_row[3] else None,
            "scope": updated_row[4],
            "user_id": updated_row[5],
            "page_id": updated_row[6],
            "created_at": updated_row[7],
            "updated_at": updated_row[8]
        }
        
        return updated_instance
    except HTTPException as e:
        # Re-raise HTTP exceptions
        logger.error(f"HTTP exception in update_setting_instance: {e.detail}")
        raise
    except Exception as e:
        # Log and convert other exceptions to HTTP exceptions
        logger.error(f"Unexpected error in update_setting_instance: {e}")
        import traceback
        logger.error(traceback.format_exc())
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error updating setting instance: {str(e)}"
        )

@router.put("/instances/{instance_id}", response_model=SettingInstanceResponse)
async def put_setting_instance(
    instance_id: str,
    update_data: SettingInstanceCreate,
    db: AsyncSession = Depends(get_db),
    auth: Optional[AuthContext] = Depends(optional_user)
):
    """Update a setting instance with PUT (full replacement)."""
    try:
        # Get the instance using direct SQL
        query = text("""
        SELECT id, definition_id, name, value, scope, user_id, page_id, created_at, updated_at
        FROM settings_instances
        WHERE id = :id
        """)
        
        result = await db.execute(query, {"id": instance_id})
        row = result.fetchone()
        
        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Setting instance {instance_id} not found"
            )
        
        # Convert row to dict
        instance = {
            "id": row[0],
            "definition_id": row[1],
            "name": row[2],
            "value": json.loads(row[3]) if row[3] else None,
            "scope": row[4],
            "user_id": row[5],
            "page_id": row[6],
            "created_at": row[7],
            "updated_at": row[8]
        }
        
        # Check access permission
        scope_value = instance["scope"]
        if scope_value in [SettingScope.USER.value, SettingScope.USER_PAGE.value]:
            if not auth:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Authentication required for user settings"
                )
            if str(instance["user_id"]) != str(auth.user_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access denied to this setting instance"
                )
        elif scope_value == SettingScope.SYSTEM.value:
            if not auth or not auth.is_admin:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Admin privileges required for system settings"
                )
        
        # Handle special case for 'current' user_id
        user_id_value = update_data.user_id
        if user_id_value == 'current' and auth:
            user_id_value = str(auth.user_id)
        
        # Convert scope to string if it's an enum
        scope_value = update_data.scope
        if hasattr(scope_value, 'value'):
            scope_value = scope_value.value
        
        # Convert value to JSON string if it's not already
        value_json = update_data.value
        if not isinstance(value_json, str):
            value_json = json.dumps(value_json)
        
        # Use direct SQL to update the instance
        update_query = text("""
        UPDATE settings_instances
        SET definition_id = :definition_id,
            name = :name,
            value = :value,
            scope = :scope,
            user_id = :user_id,
            page_id = :page_id,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = :id
        """)
        
        await db.execute(update_query, {
            "definition_id": update_data.definition_id,
            "name": update_data.name,
            "value": value_json,
            "scope": scope_value,
            "user_id": user_id_value,
            "page_id": update_data.page_id,
            "id": instance_id
        })
        
        await db.commit()
        
        # Fetch the updated instance
        fetch_query = text("""
        SELECT id, definition_id, name, value, scope, user_id, page_id, created_at, updated_at
        FROM settings_instances
        WHERE id = :id
        """)
        
        result = await db.execute(fetch_query, {"id": instance_id})
        updated_row = result.fetchone()
        
        if not updated_row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Setting instance {instance_id} not found after update"
            )
        
        # Convert row to dict
        updated_instance = {
            "id": updated_row[0],
            "definition_id": updated_row[1],
            "name": updated_row[2],
            "value": json.loads(updated_row[3]) if updated_row[3] else None,
            "scope": updated_row[4],
            "user_id": updated_row[5],
            "page_id": updated_row[6],
            "created_at": updated_row[7],
            "updated_at": updated_row[8]
        }
        
        return updated_instance
    except HTTPException as e:
        # Re-raise HTTP exceptions
        logger.error(f"HTTP exception in put_setting_instance: {e.detail}")
        raise
    except Exception as e:
        # Log and convert other exceptions to HTTP exceptions
        logger.error(f"Unexpected error in put_setting_instance: {e}")
        import traceback
        logger.error(traceback.format_exc())
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error updating setting instance: {str(e)}"
        )

@router.delete("/instances/{instance_id}", response_model=dict)
async def delete_setting_instance(
    instance_id: str,
    db: AsyncSession = Depends(get_db),
    auth: Optional[AuthContext] = Depends(optional_user)
):
    """Delete a setting instance."""
    try:
        # Get the instance using direct SQL
        query = text("""
        SELECT id, definition_id, name, value, scope, user_id, page_id, created_at, updated_at
        FROM settings_instances
        WHERE id = :id
        """)
        
        result = await db.execute(query, {"id": instance_id})
        row = result.fetchone()
        
        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Setting instance {instance_id} not found"
            )
        
        # Convert row to dict
        instance = {
            "id": row[0],
            "definition_id": row[1],
            "name": row[2],
            "value": json.loads(row[3]) if row[3] else None,
            "scope": row[4],
            "user_id": row[5],
            "page_id": row[6],
            "created_at": row[7],
            "updated_at": row[8]
        }
        
        # Check access permission
        scope_value = instance["scope"]
        if scope_value in [SettingScope.USER.value, SettingScope.USER_PAGE.value]:
            if not auth:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Authentication required for user settings"
                )
            if str(instance["user_id"]) != str(auth.user_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access denied to this setting instance"
                )
        elif scope_value == SettingScope.SYSTEM.value:
            if not auth or not auth.is_admin:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Admin privileges required for system settings"
                )
        
        # Delete the instance using direct SQL
        delete_query = text("""
        DELETE FROM settings_instances
        WHERE id = :id
        """)
        
        await db.execute(delete_query, {"id": instance_id})
        await db.commit()
        
        return {"message": f"Setting instance {instance_id} deleted successfully"}
    except HTTPException as e:
        # Re-raise HTTP exceptions
        logger.error(f"HTTP exception in delete_setting_instance: {e.detail}")
        raise
    except Exception as e:
        # Log and convert other exceptions to HTTP exceptions
        logger.error(f"Unexpected error in delete_setting_instance: {e}")
        import traceback
        logger.error(traceback.format_exc())
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error deleting setting instance: {str(e)}"
        )

@router.post("/create-ollama-definition")
async def create_ollama_definition(
    data: dict,
    db: AsyncSession = Depends(get_db),
    auth: Optional[AuthContext] = Depends(optional_user)
):
    """Create the Ollama settings definition directly."""
    try:
        # Check if the definition already exists
        result = await db.execute(text("SELECT id FROM settings_definitions WHERE id = 'ollama_settings'"))
        existing = result.scalar_one_or_none()
        
        if existing:
            logger.info("Ollama settings definition already exists.")
        else:
            # Create the definition using direct SQL
            await db.execute(
                text("""
                INSERT INTO settings_definitions (
                    id, name, description, category, type, default_value, 
                    allowed_scopes, is_multiple, tags, created_at, updated_at
                ) VALUES (
                    :id, :name, :description, :category, :type, :default_value, 
                    :allowed_scopes, :is_multiple, :tags, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """),
                {
                    "id": "ollama_settings",
                    "name": "Ollama Server Settings",
                    "description": "Settings for connecting to Ollama server",
                    "category": "servers",
                    "type": "object",
                    "default_value": json.dumps({
                        "serverAddress": "http://localhost:11434",
                        "serverName": "Default Ollama Server",
                        "apiKey": ""
                    }),
                    "allowed_scopes": json.dumps(["user", "system"]),
                    "is_multiple": False,
                    "tags": json.dumps(["ollama", "server"])
                }
            )
            
            await db.commit()
            logger.info("Successfully created Ollama settings definition")
        
        # Now create the setting instance
        await db.execute(
            text("""
            INSERT INTO settings_instances (
                id, definition_id, name, value, scope, user_id, created_at, updated_at
            ) VALUES (
                :id, 
                'ollama_settings', 
                'Ollama Server Settings', 
                :value, 
                'user', 
                :user_id, 
                CURRENT_TIMESTAMP, 
                CURRENT_TIMESTAMP
            )
            """),
            {
                "id": str(uuid.uuid4()),
                "value": json.dumps({
                    "serverAddress": data.get("serverAddress", "http://localhost:11434"),
                    "serverName": data.get("serverName", "Default Ollama Server"),
                    "apiKey": data.get("apiKey", "")
                }),
                "user_id": str(auth.user_id) if auth else None
            }
        )
        
        await db.commit()
        logger.info("Successfully created Ollama settings instance")
        
        return {"status": "success", "message": "Ollama settings created successfully"}
    except HTTPException as e:
        # Re-raise HTTP exceptions
        logger.error(f"HTTP exception in create_ollama_definition: {e.detail}")
        raise
    except Exception as e:
        # Log and convert other exceptions to HTTP exceptions
        logger.error(f"Unexpected error in create_ollama_definition: {e}")
        import traceback
        logger.error(traceback.format_exc())
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not create Ollama settings: {str(e)}"
        )
