"""
Settings initializer plugin.

This plugin initializes settings for a new user.
"""

import logging
import uuid
import datetime
import json
from app.core.user_initializer.utils import generate_uuid
from typing import Dict, Any, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text

from app.core.user_initializer.base import UserInitializerBase
from app.core.user_initializer.registry import register_initializer
from app.core.user_initializer.utils import prepare_record_for_new_user
from app.models.settings import SettingInstance, SettingDefinition

logger = logging.getLogger(__name__)

class SettingsInitializer(UserInitializerBase):
    """Initializer for user settings."""
    
    name = "settings_initializer"
    description = "Initializes default settings for a new user"
    priority = 900  # High priority - settings should be initialized early
    dependencies = []  # No dependencies
    
    # Default settings definitions
    DEFAULT_DEFINITIONS = [
        {
            "id": "theme_settings",
            "name": "Theme Settings",
            "description": "Auto-generated definition for Theme Settings",
            "category": "auto_generated",
            "type": "object",
            "default_value": '{"theme": "dark", "useSystemTheme": false}',
            "allowed_scopes": '["system", "user", "page", "user_page"]',
            "validation": None,
            "is_multiple": False,
            "tags": '["auto_generated"]'
        },
        {
            "id": "powered_by_settings",
            "name": "Powered By",
            "description": "Text and URL for the Sidebar footer tag",
            "category": "ui",
            "type": "object",
            "default_value": '{"text": "Powered by BrainDrive", "link": "https://community.braindrive.ai"}',
            "allowed_scopes": '["system", "user"]',
            "validation": None,
            "is_multiple": False,
            "tags": '["auto_generated", "ui"]'
        },
        {
            "id": "white_label_settings",
            "name": "White Label",
            "description": "Labels and links for product/brand surfaces",
            "category": "branding",
            "type": "object",
            "default_value": '{"PRIMARY":{"label":"BrainDrive","url":"https://tinyurl.com/4dx47m7p"},"OWNERS_MANUAL":{"label":"BrainDrive Owner\'s Manual","url":"https://tinyurl.com/vd99cuex"},"COMMUNITY":{"label":"BrainDrive Community","url":"https://tinyurl.com/yc2u5v2a"},"SUPPORT":{"label":"BrainDrive Support","url":"https://tinyurl.com/4h4rtx2m"},"DOCUMENTATION":{"label":"BrainDrive Docs","url":"https://tinyurl.com/ewajc7k3"}}',
            "allowed_scopes": '["system", "user"]',
            "validation": None,
            "is_multiple": False,
            "tags": '["auto_generated", "ui", "branding"]'
        },
        {
            "id": "branding_logo_settings",
            "name": "Branding Logo Settings",
            "description": "Light/dark logo URLs and alt text",
            "category": "ui",
            "type": "object",
            "default_value": '{"light": "/braindrive/braindrive-light.svg", "dark": "/braindrive/braindrive-dark.svg", "alt": "BrainDrive"}',
            "allowed_scopes": '["system", "user"]',
            "validation": None,
            "is_multiple": False,
            "tags": '["auto_generated", "ui"]'
        },
        {
            "id": "copyright_settings",
            "name": "Copyright",
            "description": "Footer copyright line content",
            "category": "ui",
            "type": "object",
            "default_value": '{"text": "AIs can make mistakes. Check important info."}',
            "allowed_scopes": '["system", "user"]',
            "validation": None,
            "is_multiple": False,
            "tags": '["auto_generated", "ui"]'
        },
        {
            "id": "ollama_servers_settings",
            "name": "Ollama Servers Settings",
            "description": "Auto-generated definition for Ollama Servers Settings",
            "category": "auto_generated",
            "type": "object",
            "default_value": '{"servers": [{"id": "server_1742054635336_5puc3mrll", "serverName": "New Server", "serverAddress": "http://localhost:11434", "apiKey": "", "connectionStatus": "idle"}]}',
            "allowed_scopes": '["system", "user", "page", "user_page"]',
            "validation": None,
            "is_multiple": False,
            "tags": '["auto_generated"]'
        },
        {
            "id": "general_settings",
            "name": "General Settings",
            "description": "Auto-generated definition for General Settings",
            "category": "auto_generated",
            "type": "object",
            "default_value": '{"settings":[{"Setting_Name":"default_page","Setting_Data":"Dashboard","Setting_Help":"This is the first page to be displayed after logging in to BrainDrive"}]}',
            "allowed_scopes": '["system", "user", "page", "user_page"]',
            "validation": None,
            "is_multiple": False,
            "tags": '["auto_generated"]'
        }
    ]
    
    # Default settings instances
    DEFAULT_SETTINGS = [
        {
            "definition_id": "theme_settings",
            "name": "Theme Settings",
            "value": '{"theme": "dark", "useSystemTheme": false}',
            "scope": "user",
            "page_id": None
        },
        {
            "definition_id": "powered_by_settings",
            "name": "Powered By",
            "value": '{"text": "Powered by BrainDrive", "link": "https://community.braindrive.ai"}',
            "scope": "user",
            "page_id": None
        },
        {
            "definition_id": "white_label_settings",
            "name": "White Label",
            "value": '{"PRIMARY":{"label":"BrainDrive","url":"https://tinyurl.com/4dx47m7p"},"OWNERS_MANUAL":{"label":"BrainDrive Owner\'s Manual","url":"https://tinyurl.com/vd99cuex"},"COMMUNITY":{"label":"BrainDrive Community","url":"https://tinyurl.com/yc2u5v2a"},"SUPPORT":{"label":"BrainDrive Support","url":"https://tinyurl.com/4h4rtx2m"},"DOCUMENTATION":{"label":"BrainDrive Docs","url":"https://tinyurl.com/ewajc7k3"}}',
            "scope": "user",
            "page_id": None
        },
        {
            "definition_id": "branding_logo_settings",
            "name": "Branding Logo Settings",
            "value": '{"light": "/braindrive/braindrive-light.svg", "dark": "/braindrive/braindrive-dark.svg", "alt": "BrainDrive"}',
            "scope": "user",
            "page_id": None
        },
        {
            "definition_id": "copyright_settings",
            "name": "Copyright",
            "value": '{"text": "AIs can make mistakes. Check important info."}',
            "scope": "user",
            "page_id": None
        },
        {
            "definition_id": "ollama_servers_settings",
            "name": "Ollama Servers Settings",
            "value": '{"servers": [{"id": "server_1742054635336_5puc3mrll", "serverName": "New Server", "serverAddress": "http://localhost:11434", "apiKey": "", "connectionStatus": "idle"}]}',
            "scope": "user",
            "page_id": None
        },
        {
            "definition_id": "general_settings",
            "name": "General Settings",
            "value": "{}",  # Will be populated dynamically with page ID
            "scope": "user",
            "page_id": None
        }
    ]
    
    async def initialize(self, user_id: str, db: AsyncSession, **kwargs) -> bool:
        """Initialize settings for a new user."""
        try:
            logger.info(f"Initializing settings for user {user_id}")
            
            # Ensure settings definitions exist
            await self._ensure_settings_definitions(db)
            
            # Retrieve the AI Chat page ID for this user
            page_stmt = text("SELECT id FROM pages WHERE name = :name AND creator_id = :uid LIMIT 1")
            result = await db.execute(page_stmt, {"name": "AI Chat", "uid": user_id})
            ai_chat_page_id = result.scalar_one_or_none()

            # Create settings instances for the user using hardcoded default settings
            for setting_data in self.DEFAULT_SETTINGS:
                # If this is the general settings entry, populate the value with the page ID
                if setting_data["definition_id"] == "general_settings":
                    default_page_value = ai_chat_page_id if ai_chat_page_id else "Dashboard"
                    setting_value = {
                        "settings": [
                            {
                                "Setting_Name": "default_page",
                                "Setting_Data": default_page_value,
                                "Setting_Help": "This is the first page to be displayed after logging in to BrainDrive"
                            }
                        ]
                    }
                    setting_data = setting_data.copy()
                    setting_data["value"] = json.dumps(setting_value)
                # Prepare the setting data for the new user
                # This will:
                # 1. Generate a new ID
                # 2. Set the user_id to the new user's ID
                # 3. Update created_at and updated_at timestamps
                prepared_data = prepare_record_for_new_user(
                    setting_data,
                    user_id,
                    preserve_fields=["definition_id", "name", "value", "scope", "page_id"],
                    user_id_field="user_id"  # Explicitly specify the user_id field
                )
                
                # Use direct SQL to avoid ORM relationship issues
                try:
                    # Get current timestamp
                    current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    
                    # Create SQL statement
                    stmt = text("""
                    INSERT INTO settings_instances
                    (id, definition_id, name, value, scope, user_id, page_id, created_at, updated_at)
                    VALUES
                    (:id, :definition_id, :name, :value, :scope, :user_id, :page_id, :created_at, :updated_at)
                    """)
                    
                    # Execute statement with parameters
                    await db.execute(stmt, {
                        "id": prepared_data.get("id", generate_uuid()),
                        "definition_id": prepared_data["definition_id"],
                        "name": prepared_data["name"],
                        "value": prepared_data["value"],
                        "scope": prepared_data["scope"],
                        "user_id": user_id,
                        "page_id": prepared_data.get("page_id"),
                        "created_at": current_time,
                        "updated_at": current_time
                    })
                    
                    logger.info(f"Created setting {prepared_data['name']} for user {user_id}")
                except Exception as e:
                    logger.error(f"Error creating setting {prepared_data.get('name')}: {e}")
                    raise
            
            await db.commit()
            logger.info(f"Settings initialized successfully for user {user_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error initializing settings for user {user_id}: {e}")
            await db.rollback()
            return False
    
    async def _ensure_settings_definitions(self, db: AsyncSession) -> None:
        """Ensure that all required settings definitions exist."""
        try:
            # Use hardcoded default definitions
            for definition_data in self.DEFAULT_DEFINITIONS:
                # Check if definition already exists using direct SQL
                check_stmt = text("SELECT id FROM settings_definitions WHERE id = :id")
                result = await db.execute(check_stmt, {"id": definition_data["id"]})
                existing = result.scalar_one_or_none()
                
                if not existing:
                    # Create the definition using direct SQL
                    current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    
                    insert_stmt = text("""
                    INSERT INTO settings_definitions
                    (id, name, description, category, type, default_value, allowed_scopes, validation, is_multiple, tags, created_at, updated_at)
                    VALUES
                    (:id, :name, :description, :category, :type, :default_value, :allowed_scopes, :validation, :is_multiple, :tags, :created_at, :updated_at)
                    """)
                    
                    await db.execute(insert_stmt, {
                        "id": definition_data["id"],
                        "name": definition_data["name"],
                        "description": definition_data.get("description", ""),
                        "category": definition_data.get("category", "auto_generated"),
                        "type": definition_data.get("type", "object"),
                        "default_value": definition_data.get("default_value", "{}"),
                        "allowed_scopes": definition_data.get("allowed_scopes", '["system", "user", "page", "user_page"]'),
                        "validation": definition_data.get("validation"),
                        "is_multiple": definition_data.get("is_multiple", False),
                        "tags": definition_data.get("tags", '["auto_generated"]'),
                        "created_at": current_time,
                        "updated_at": current_time
                    })
                    
                    logger.info(f"Created settings definition: {definition_data['name']}")
            
            await db.commit()
            logger.info("Settings definitions ensured")
            
        except Exception as e:
            logger.error(f"Error ensuring settings definitions: {e}")
            await db.rollback()
            raise

# Register the initializer
register_initializer(SettingsInitializer)
