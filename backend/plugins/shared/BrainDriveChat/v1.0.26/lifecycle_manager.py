#!/usr/bin/env python3
"""
BrainDriveChat Plugin Lifecycle Manager (New Architecture)

This script handles install/update/delete operations for the BrainDriveChat plugin
using the new multi-user plugin lifecycle management architecture.
"""

import json
import logging
import datetime
import os
import shutil
import asyncio
from pathlib import Path
from typing import Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
import structlog

logger = structlog.get_logger()

# Import the new base lifecycle manager
try:
    # Try to import from the BrainDrive system first (when running in production)
    from app.plugins.base_lifecycle_manager import BaseLifecycleManager
    logger.info("Using new architecture: BaseLifecycleManager imported from app.plugins")
except ImportError:
    try:
        # Try local import for development
        import sys
        current_dir = os.path.dirname(os.path.abspath(__file__))
        backend_path = os.path.join(current_dir, "..", "..", "backend", "app", "plugins")
        backend_path = os.path.abspath(backend_path)
        
        if os.path.exists(backend_path):
            if backend_path not in sys.path:
                sys.path.insert(0, backend_path)
            from base_lifecycle_manager import BaseLifecycleManager
            logger.info(f"Using new architecture: BaseLifecycleManager imported from local backend: {backend_path}")
        else:
            # For remote installation, the base class might not be available
            # In this case, we'll create a minimal implementation
            logger.warning(f"BaseLifecycleManager not found at {backend_path}, using minimal implementation")
            from abc import ABC, abstractmethod
            from datetime import datetime
            from pathlib import Path
            from typing import Set
            
            class BaseLifecycleManager(ABC):
                """Minimal base class for remote installations"""
                def __init__(self, plugin_slug: str, version: str, shared_storage_path: Path):
                    self.plugin_slug = plugin_slug
                    self.version = version
                    self.shared_path = shared_storage_path
                    self.active_users: Set[str] = set()
                    self.instance_id = f"{plugin_slug}_{version}"
                    self.created_at = datetime.now()
                    self.last_used = datetime.now()
                
                async def install_for_user(self, user_id: str, db, shared_plugin_path: Path):
                    if user_id in self.active_users:
                        return {'success': False, 'error': 'Plugin already installed for user'}
                    result = await self._perform_user_installation(user_id, db, shared_plugin_path)
                    if result['success']:
                        self.active_users.add(user_id)
                        self.last_used = datetime.now()
                    return result
                
                async def uninstall_for_user(self, user_id: str, db):
                    if user_id not in self.active_users:
                        return {'success': False, 'error': 'Plugin not installed for user'}
                    result = await self._perform_user_uninstallation(user_id, db)
                    if result['success']:
                        self.active_users.discard(user_id)
                        self.last_used = datetime.now()
                    return result
                
                @abstractmethod
                async def get_plugin_metadata(self): pass
                @abstractmethod
                async def get_module_metadata(self): pass
                @abstractmethod
                async def _perform_user_installation(self, user_id, db, shared_plugin_path): pass
                @abstractmethod
                async def _perform_user_uninstallation(self, user_id, db): pass
            
            logger.info("Using minimal BaseLifecycleManager implementation for remote installation")
            
    except ImportError as e:
        logger.error(f"Failed to import BaseLifecycleManager: {e}")
        raise ImportError("BrainDriveChat plugin requires the new architecture BaseLifecycleManager")


class BrainDriveChatLifecycleManager(BaseLifecycleManager):
    """Lifecycle manager for BrainDriveChat plugin using new architecture"""
    
    def __init__(self, plugins_base_dir: str = None):
        """Initialize the lifecycle manager"""
        # Define plugin-specific data
        self.plugin_data = {
            "name": "BrainDriveChat",
            "description": "Comprehensive AI chat interface with model selection and conversation history",
            "version": "1.0.25",
            "type": "frontend",
            "icon": "MessageSquare",
            "category": "ai",
            "official": True,
            "author": "BrainDrive",
            "compatibility": "1.0.0",
            "scope": "BrainDriveChat",
            "bundle_method": "webpack",
            "bundle_location": "dist/remoteEntry.js",
            "is_local": False,
            "long_description": "A unified AI chat interface that combines AI prompt chat, model selection, and conversation history management in a single, responsive plugin with light/dark theme support.",
            "plugin_slug": "BrainDriveChat",
            # Update tracking fields (matching plugin model)
            "source_type": "github",
            "source_url": "https://github.com/BrainDriveAI/BrainDrive-Chat-Plugin",
            "update_check_url": "https://github.com/BrainDriveAI/BrainDrive-Chat-Plugin/releases/latest",
            "last_update_check": None,
            "update_available": False,
            "latest_version": None,
            "installation_type": "remote",
            "permissions": ["storage.read", "storage.write", "api.access"]
        }
        
        self.module_data = [
            {
                "name": "BrainDriveChat",
                "display_name": "AI Chat Interface",
                "description": "Complete AI chat interface with model selection and conversation history",
                "icon": "MessageSquare",
                "category": "ai",
                "priority": 1,
                "props": {
                    "initialGreeting": "Welcome to your BrainDrive!\\n\\nRemember you always have your {white_label_settings:OWNERS_MANUAL} and {white_label_settings:COMMUNITY} available.\\n\\nhow can I help you today?",
                    "defaultStreamingMode": True,
                    "promptQuestion": "What would you like to know?"
                },
                "config_fields": {
                    "initial_greeting": {
                        "type": "text",
                        "description": "Initial greeting message from AI",
                        "default": "Welcome to your BrainDrive!\\n\\nRemember you always have your {white_label_settings:OWNERS_MANUAL} and {white_label_settings:COMMUNITY} available.\\n\\nhow can I help you today?"
                    },
                    "enable_streaming": {
                        "type": "boolean",
                        "description": "Enable streaming responses by default",
                        "default": True
                    },
                    "max_conversation_history": {
                        "type": "number",
                        "description": "Maximum number of conversations to show in history",
                        "default": 50
                    },
                    "auto_save_conversations": {
                        "type": "boolean",
                        "description": "Automatically save conversations",
                        "default": True
                    },
                    "show_model_selection": {
                        "type": "boolean",
                        "description": "Show model selection dropdown",
                        "default": True
                    },
                    "show_conversation_history": {
                        "type": "boolean",
                        "description": "Show conversation history panel",
                        "default": True
                    }
                },
                "messages": {},
                "required_services": {
                    "api": {"methods": ["get", "post", "put", "delete", "postStreaming"], "version": "1.0.0"},
                    "event": {"methods": ["sendMessage", "subscribeToMessages", "unsubscribeFromMessages"], "version": "1.0.0"},
                    "theme": {"methods": ["getCurrentTheme", "addThemeChangeListener", "removeThemeChangeListener"], "version": "1.0.0"},
                    "settings": {"methods": ["getSetting", "setSetting", "getSettingDefinitions"], "version": "1.0.0"},
                    "pageContext": {"methods": ["getCurrentPageContext", "onPageContextChange"], "version": "1.0.0"}
                },
                "dependencies": [],
                "layout": {
                    "minWidth": 6,
                    "minHeight": 6,
                    "defaultWidth": 8,
                    "defaultHeight": 8
                },
                "tags": ["ai", "chat", "conversation", "assistant", "model-selection", "history"]
            }
        ]
        
        # Initialize base class with required parameters
        logger.info(f"BrainDriveChat: plugins_base_dir - {plugins_base_dir}")
        if plugins_base_dir:
            # When instantiated by the remote installer, plugins_base_dir points to the plugins directory
            # Shared plugins are stored under plugins_base_dir/shared/plugin_slug/version
            shared_path = Path(plugins_base_dir) / "shared" / self.plugin_data['plugin_slug'] / f"v{self.plugin_data['version']}"
        else:
            # When running from the PluginBuild directory during development,
            # resolve the path to backend/plugins/shared
            shared_path = Path(__file__).parent.parent.parent / "backend" / "plugins" / "shared" / self.plugin_data['plugin_slug'] / f"v{self.plugin_data['version']}"
        logger.info(f"BrainDriveChat: shared_path - {shared_path}")
        super().__init__(
            plugin_slug=self.plugin_data['plugin_slug'],
            version=self.plugin_data['version'],
            shared_storage_path=shared_path
        )
    
    @property
    def PLUGIN_DATA(self):
        """Compatibility property for remote installer validation"""
        return self.plugin_data
    
    async def get_plugin_metadata(self) -> Dict[str, Any]:
        """Return plugin metadata and configuration"""
        return self.plugin_data
    
    async def get_module_metadata(self) -> list:
        """Return module definitions for this plugin"""
        return self.module_data
    
    async def _perform_user_installation(self, user_id: str, db: AsyncSession, shared_plugin_path: Path) -> Dict[str, Any]:
        """Perform user-specific installation using shared plugin path"""
        try:
            # Create database records for this user
            db_result = await self._create_database_records(user_id, db)
            if not db_result['success']:
                return db_result
            
            logger.info(f"BrainDriveChat: User installation completed for {user_id}")
            return {
                'success': True,
                'plugin_id': db_result['plugin_id'],
                'plugin_slug': self.plugin_data['plugin_slug'],
                'plugin_name': self.plugin_data['name'],
                'modules_created': db_result['modules_created']
            }
            
        except Exception as e:
            logger.error(f"BrainDriveChat: User installation failed for {user_id}: {e}")
            return {'success': False, 'error': str(e)}
    
    async def _perform_user_uninstallation(self, user_id: str, db: AsyncSession) -> Dict[str, Any]:
        """Perform user-specific uninstallation"""
        try:
            # Check if plugin exists for user
            existing_check = await self._check_existing_plugin(user_id, db)
            if not existing_check['exists']:
                return {'success': False, 'error': 'Plugin not found for user'}
            
            plugin_id = existing_check['plugin_id']
            
            # Delete database records
            delete_result = await self._delete_database_records(user_id, plugin_id, db)
            if not delete_result['success']:
                return delete_result
            
            logger.info(f"BrainDriveChat: User uninstallation completed for {user_id}")
            return {
                'success': True,
                'plugin_id': plugin_id,
                'deleted_modules': delete_result['deleted_modules']
            }
            
        except Exception as e:
            logger.error(f"BrainDriveChat: User uninstallation failed for {user_id}: {e}")
            return {'success': False, 'error': str(e)}
    
    async def _copy_plugin_files_impl(self, user_id: str, target_dir: Path, update: bool = False) -> Dict[str, Any]:
        """
        BrainDriveChat-specific implementation of file copying.
        This method is called by the base class during installation.
        Copies all files from the plugin source directory to the target directory.
        """
        try:
            source_dir = Path(__file__).parent
            copied_files = []
            
            # Define files and directories to exclude (similar to build_archive.py)
            exclude_patterns = {
                'node_modules',
                'package-lock.json',
                '.git',
                '.gitignore',
                '__pycache__',
                '*.pyc',
                '.DS_Store',
                'Thumbs.db'
            }
            
            def should_copy(path: Path) -> bool:
                """Check if a file/directory should be copied"""
                # Check if any part of the path matches exclude patterns
                for part in path.parts:
                    if part in exclude_patterns:
                        return False
                # Check for pattern matches
                for pattern in exclude_patterns:
                    if '*' in pattern and path.name.endswith(pattern.replace('*', '')):
                        return False
                return True
            
            # Copy all files and directories recursively
            for item in source_dir.rglob('*'):
                # Skip the lifecycle_manager.py file itself to avoid infinite recursion
                if item.name == 'lifecycle_manager.py' and item == Path(__file__):
                    continue
                    
                # Get relative path from source directory
                relative_path = item.relative_to(source_dir)
                
                # Check if we should copy this item
                if not should_copy(relative_path):
                    continue
                
                target_path = target_dir / relative_path
                
                try:
                    if item.is_file():
                        # Create parent directories if they don't exist
                        target_path.parent.mkdir(parents=True, exist_ok=True)
                        
                        # Copy file
                        if update and target_path.exists():
                            target_path.unlink()  # Remove existing file
                        shutil.copy2(item, target_path)
                        copied_files.append(str(relative_path))
                        logger.debug(f"Copied file: {relative_path}")
                        
                    elif item.is_dir():
                        # Create directory
                        target_path.mkdir(parents=True, exist_ok=True)
                        logger.debug(f"Created directory: {relative_path}")
                        
                except Exception as e:
                    logger.warning(f"Failed to copy {relative_path}: {e}")
                    continue
            
            # Also copy the lifecycle_manager.py file itself
            lifecycle_manager_source = source_dir / 'lifecycle_manager.py'
            lifecycle_manager_target = target_dir / 'lifecycle_manager.py'
            if lifecycle_manager_source.exists():
                lifecycle_manager_target.parent.mkdir(parents=True, exist_ok=True)
                if update and lifecycle_manager_target.exists():
                    lifecycle_manager_target.unlink()
                shutil.copy2(lifecycle_manager_source, lifecycle_manager_target)
                copied_files.append('lifecycle_manager.py')
                logger.info(f"Copied lifecycle_manager.py")
            
            logger.info(f"BrainDriveChat: Copied {len(copied_files)} files/directories to {target_dir}")
            return {'success': True, 'copied_files': copied_files}
            
        except Exception as e:
            logger.error(f"BrainDriveChat: Error copying plugin files: {e}")
            return {'success': False, 'error': str(e)}
    
    async def _validate_installation_impl(self, user_id: str, plugin_dir: Path) -> Dict[str, Any]:
        """
        BrainDriveChat-specific validation logic.
        This method is called by the base class during installation.
        """
        try:
            # Check for BrainDriveChat-specific required files
            required_files = ["package.json", "dist/remoteEntry.js"]
            missing_files = []
            
            for file_path in required_files:
                if not (plugin_dir / file_path).exists():
                    missing_files.append(file_path)
            
            if missing_files:
                return {
                    'valid': False,
                    'error': f"BrainDriveChat: Missing required files: {', '.join(missing_files)}"
                }
            
            # Validate package.json structure
            package_json_path = plugin_dir / "package.json"
            try:
                with open(package_json_path, 'r') as f:
                    package_data = json.load(f)
                
                # Check for required package.json fields
                required_fields = ["name", "version"]
                for field in required_fields:
                    if field not in package_data:
                        return {
                            'valid': False,
                            'error': f'BrainDriveChat: package.json missing required field: {field}'
                        }
                        
            except (json.JSONDecodeError, FileNotFoundError) as e:
                return {
                    'valid': False,
                    'error': f'BrainDriveChat: Invalid or missing package.json: {e}'
                }
            
            # Validate bundle file exists and is not empty
            bundle_path = plugin_dir / "dist" / "remoteEntry.js"
            if bundle_path.stat().st_size == 0:
                return {
                    'valid': False,
                    'error': 'BrainDriveChat: Bundle file (remoteEntry.js) is empty'
                }
            
            logger.info(f"BrainDriveChat: Installation validation passed for user {user_id}")
            return {'valid': True}
            
        except Exception as e:
            logger.error(f"BrainDriveChat: Error validating installation: {e}")
            return {'valid': False, 'error': str(e)}
    
    async def _get_plugin_health_impl(self, user_id: str, plugin_dir: Path) -> Dict[str, Any]:
        """
        BrainDriveChat-specific health check logic.
        This method is called by the base class during status checks.
        """
        try:
            health_info = {
                'bundle_exists': False,
                'bundle_size': 0,
                'package_json_valid': False,
                'assets_present': False
            }
            
            # Check bundle file
            bundle_path = plugin_dir / "dist" / "remoteEntry.js"
            if bundle_path.exists():
                health_info['bundle_exists'] = True
                health_info['bundle_size'] = bundle_path.stat().st_size
            
            # Check package.json
            package_json_path = plugin_dir / "package.json"
            if package_json_path.exists():
                try:
                    with open(package_json_path, 'r') as f:
                        json.load(f)
                    health_info['package_json_valid'] = True
                except json.JSONDecodeError:
                    pass
            
            # Check for assets directory
            assets_path = plugin_dir / "assets"
            if assets_path.exists() and assets_path.is_dir():
                health_info['assets_present'] = True
            
            # Determine overall health
            is_healthy = (
                health_info['bundle_exists'] and 
                health_info['bundle_size'] > 0 and
                health_info['package_json_valid']
            )
            
            return {
                'healthy': is_healthy,
                'details': health_info
            }
            
        except Exception as e:
            logger.error(f"BrainDriveChat: Error checking plugin health: {e}")
            return {
                'healthy': False,
                'details': {'error': str(e)}
            }
    
    async def _check_existing_plugin(self, user_id: str, db: AsyncSession) -> Dict[str, Any]:
        """Check if plugin already exists for user"""
        try:
            plugin_slug = self.plugin_data['plugin_slug']
            logger.info(f"BrainDriveChat: Checking for existing plugin - user_id: {user_id}, plugin_slug: {plugin_slug}")
            
            # First test database connectivity
            test_query = text("SELECT COUNT(*) as count FROM plugin")
            test_result = await db.execute(test_query)
            test_row = test_result.fetchone()
            logger.info(f"BrainDriveChat: Database connectivity test - total plugins: {test_row.count}")
            
            plugin_query = text("""
            SELECT id, name, version, enabled, created_at, updated_at, plugin_slug
            FROM plugin
            WHERE user_id = :user_id AND plugin_slug = :plugin_slug
            """)
            
            query_params = {
                'user_id': user_id,
                'plugin_slug': plugin_slug
            }
            logger.info(f"BrainDriveChat: Executing query with params: {query_params}")
            
            result = await db.execute(plugin_query, query_params)
            
            plugin_row = result.fetchone()
            logger.info(f"BrainDriveChat: Query result: {plugin_row}")
            if plugin_row:
                logger.info(f"BrainDriveChat: Found existing plugin - id: {plugin_row.id}, name: {plugin_row.name}")
                return {
                    'exists': True,
                    'plugin_id': plugin_row.id,
                    'plugin_info': {
                        'id': plugin_row.id,
                        'name': plugin_row.name,
                        'version': plugin_row.version,
                        'enabled': plugin_row.enabled,
                        'created_at': plugin_row.created_at,
                        'updated_at': plugin_row.updated_at
                    }
                }
            else:
                logger.warning(f"BrainDriveChat: No plugin found for user_id: {user_id}, plugin_slug: {plugin_slug}")
                
                # Debug: Check if there are any plugins for this user
                debug_query = text("SELECT id, plugin_slug FROM plugin WHERE user_id = :user_id")
                debug_result = await db.execute(debug_query, {'user_id': user_id})
                debug_rows = debug_result.fetchall()
                if debug_rows:
                    logger.info(f"BrainDriveChat: User has {len(debug_rows)} other plugins:")
                    for row in debug_rows:
                        logger.info(f"  - {row.plugin_slug} (id: {row.id})")
                else:
                    logger.info(f"BrainDriveChat: User has no plugins installed")
                
                return {'exists': False}
                
        except Exception as e:
            logger.error(f"BrainDriveChat: Error checking existing plugin: {e}")
            return {'exists': False, 'error': str(e)}
    
    async def _create_database_records(self, user_id: str, db: AsyncSession) -> Dict[str, Any]:
        """Create plugin and module records in database"""
        try:
            current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            plugin_slug = self.plugin_data['plugin_slug']
            plugin_id = f"{user_id}_{plugin_slug}"
            
            logger.info(f"BrainDriveChat: Creating database records - user_id: {user_id}, plugin_slug: {plugin_slug}, plugin_id: {plugin_id}")
            
            plugin_stmt = text("""
            INSERT INTO plugin
            (id, name, description, version, type, enabled, icon, category, status,
            official, author, last_updated, compatibility, downloads, scope,
            bundle_method, bundle_location, is_local, long_description,
            config_fields, messages, dependencies, created_at, updated_at, user_id,
            plugin_slug, source_type, source_url, update_check_url, last_update_check,
            update_available, latest_version, installation_type, permissions)
            VALUES
            (:id, :name, :description, :version, :type, :enabled, :icon, :category,
            :status, :official, :author, :last_updated, :compatibility, :downloads,
            :scope, :bundle_method, :bundle_location, :is_local, :long_description,
            :config_fields, :messages, :dependencies, :created_at, :updated_at, :user_id,
            :plugin_slug, :source_type, :source_url, :update_check_url, :last_update_check,
            :update_available, :latest_version, :installation_type, :permissions)
            """)
            
            await db.execute(plugin_stmt, {
                'id': plugin_id,
                'name': self.plugin_data['name'],
                'description': self.plugin_data['description'],
                'version': self.plugin_data['version'],
                'type': self.plugin_data['type'],
                'enabled': True,
                'icon': self.plugin_data['icon'],
                'category': self.plugin_data['category'],
                'status': 'activated',
                'official': self.plugin_data['official'],
                'author': self.plugin_data['author'],
                'last_updated': current_time,
                'compatibility': self.plugin_data['compatibility'],
                'downloads': 0,
                'scope': self.plugin_data['scope'],
                'bundle_method': self.plugin_data['bundle_method'],
                'bundle_location': self.plugin_data['bundle_location'],
                'is_local': self.plugin_data['is_local'],
                'long_description': self.plugin_data['long_description'],
                'config_fields': json.dumps({}),
                'messages': None,
                'dependencies': None,
                'created_at': current_time,
                'updated_at': current_time,
                'user_id': user_id,
                'plugin_slug': plugin_slug,
                'source_type': self.plugin_data['source_type'],
                'source_url': self.plugin_data['source_url'],
                'update_check_url': self.plugin_data['update_check_url'],
                'last_update_check': self.plugin_data['last_update_check'],
                'update_available': self.plugin_data['update_available'],
                'latest_version': self.plugin_data['latest_version'],
                'installation_type': self.plugin_data['installation_type'],
                'permissions': json.dumps(self.plugin_data['permissions'])
            })
            
            modules_created = []
            for module_data in self.module_data:
                module_id = f"{user_id}_{plugin_slug}_{module_data['name']}"
                
                module_stmt = text("""
                INSERT INTO module
                (id, plugin_id, name, display_name, description, icon, category,
                enabled, priority, props, config_fields, messages, required_services,
                dependencies, layout, tags, created_at, updated_at, user_id)
                VALUES
                (:id, :plugin_id, :name, :display_name, :description, :icon, :category,
                :enabled, :priority, :props, :config_fields, :messages, :required_services,
                :dependencies, :layout, :tags, :created_at, :updated_at, :user_id)
                """)
                
                await db.execute(module_stmt, {
                    'id': module_id,
                    'plugin_id': plugin_id,
                    'name': module_data['name'],
                    'display_name': module_data['display_name'],
                    'description': module_data['description'],
                    'icon': module_data['icon'],
                    'category': module_data['category'],
                    'enabled': True,
                    'priority': module_data['priority'],
                    'props': json.dumps(module_data['props']),
                    'config_fields': json.dumps(module_data['config_fields']),
                    'messages': json.dumps(module_data['messages']),
                    'required_services': json.dumps(module_data['required_services']),
                    'dependencies': json.dumps(module_data['dependencies']),
                    'layout': json.dumps(module_data['layout']),
                    'tags': json.dumps(module_data['tags']),
                    'created_at': current_time,
                    'updated_at': current_time,
                    'user_id': user_id
                })
                
                modules_created.append(module_id)
            
            # Commit the transaction
            await db.commit()
            logger.info(f"BrainDriveChat: Database transaction committed successfully")
            
            # Verify the plugin was actually created
            verify_query = text("SELECT id, plugin_slug FROM plugin WHERE id = :plugin_id AND user_id = :user_id")
            verify_result = await db.execute(verify_query, {'plugin_id': plugin_id, 'user_id': user_id})
            verify_row = verify_result.fetchone()
            
            if verify_row:
                logger.info(f"BrainDriveChat: Successfully created and verified database records for plugin {plugin_id} with {len(modules_created)} modules")
            else:
                logger.error(f"BrainDriveChat: Plugin creation appeared to succeed but verification failed for plugin_id: {plugin_id}")
                return {'success': False, 'error': 'Plugin creation verification failed'}
            
            return {'success': True, 'plugin_id': plugin_id, 'modules_created': modules_created}
            
        except Exception as e:
            logger.error(f"Error creating database records: {e}")
            await db.rollback()
            return {'success': False, 'error': str(e)}
    
    async def _delete_database_records(self, user_id: str, plugin_id: str, db: AsyncSession) -> Dict[str, Any]:
        """Delete plugin and module records from database"""
        try:
            # Delete modules first (foreign key constraint)
            module_delete_stmt = text("""
            DELETE FROM module 
            WHERE plugin_id = :plugin_id AND user_id = :user_id
            """)
            
            module_result = await db.execute(module_delete_stmt, {
                'plugin_id': plugin_id,
                'user_id': user_id
            })
            
            deleted_modules = module_result.rowcount
            
            # Delete plugin
            plugin_delete_stmt = text("""
            DELETE FROM plugin 
            WHERE id = :plugin_id AND user_id = :user_id
            """)
            
            plugin_result = await db.execute(plugin_delete_stmt, {
                'plugin_id': plugin_id,
                'user_id': user_id
            })
            
            if plugin_result.rowcount == 0:
                await db.rollback()
                return {'success': False, 'error': 'Plugin not found or not owned by user'}
            
            # Commit the transaction
            await db.commit()
            
            logger.info(f"Deleted database records for plugin {plugin_id} ({deleted_modules} modules)")
            return {'success': True, 'deleted_modules': deleted_modules}
            
        except Exception as e:
            logger.error(f"Error deleting database records: {e}")
            await db.rollback()
            return {'success': False, 'error': str(e)}
    
    async def _export_user_data(self, user_id: str, db: AsyncSession) -> Dict[str, Any]:
        """Export user-specific data for migration during updates"""
        try:
            # Get plugin configuration
            plugin_query = text("""
            SELECT config_fields, enabled, status
            FROM plugin 
            WHERE user_id = :user_id AND plugin_slug = :plugin_slug
            """)
            
            plugin_result = await db.execute(plugin_query, {
                'user_id': user_id,
                'plugin_slug': self.plugin_data['plugin_slug']
            })
            
            plugin_row = plugin_result.fetchone()
            if not plugin_row:
                return {'success': False, 'error': 'Plugin not found for user'}
            
            # Get module configurations
            module_query = text("""
            SELECT name, config_fields, enabled, priority
            FROM module 
            WHERE plugin_id LIKE :plugin_pattern AND user_id = :user_id
            """)
            
            module_result = await db.execute(module_query, {
                'plugin_pattern': f"{user_id}_{self.plugin_data['plugin_slug']}_%",
                'user_id': user_id
            })
            
            modules_data = {}
            for module_row in module_result.fetchall():
                try:
                    config_fields = json.loads(module_row.config_fields) if module_row.config_fields else {}
                except json.JSONDecodeError:
                    config_fields = {}
                
                modules_data[module_row.name] = {
                    'config_fields': config_fields,
                    'enabled': module_row.enabled,
                    'priority': module_row.priority
                }
            
            user_data = {
                'plugin_config': {
                    'config_fields': json.loads(plugin_row.config_fields) if plugin_row.config_fields else {},
                    'enabled': plugin_row.enabled,
                    'status': plugin_row.status
                },
                'modules_config': modules_data,
                'export_timestamp': datetime.datetime.now().isoformat()
            }
            
            logger.info(f"BrainDriveChat: Exported user data for {user_id}")
            return {'success': True, 'user_data': user_data}
            
        except Exception as e:
            logger.error(f"BrainDriveChat: Error exporting user data: {e}")
            return {'success': False, 'error': str(e)}
    
    async def _import_user_data(self, user_id: str, db: AsyncSession, user_data: Dict[str, Any]):
        """Import user-specific data after migration during updates"""
        try:
            if not user_data:
                logger.info(f"BrainDriveChat: No user data to import for {user_id}")
                return
            
            plugin_config = user_data.get('plugin_config', {})
            modules_config = user_data.get('modules_config', {})
            
            # Update plugin configuration
            if plugin_config:
                plugin_update_stmt = text("""
                UPDATE plugin 
                SET config_fields = :config_fields, enabled = :enabled, status = :status
                WHERE user_id = :user_id AND plugin_slug = :plugin_slug
                """)
                
                await db.execute(plugin_update_stmt, {
                    'config_fields': json.dumps(plugin_config.get('config_fields', {})),
                    'enabled': plugin_config.get('enabled', True),
                    'status': plugin_config.get('status', 'activated'),
                    'user_id': user_id,
                    'plugin_slug': self.plugin_data['plugin_slug']
                })
            
            # Update module configurations
            for module_name, module_config in modules_config.items():
                module_update_stmt = text("""
                UPDATE module 
                SET config_fields = :config_fields, enabled = :enabled, priority = :priority
                WHERE name = :module_name AND plugin_id LIKE :plugin_pattern AND user_id = :user_id
                """)
                
                await db.execute(module_update_stmt, {
                    'config_fields': json.dumps(module_config.get('config_fields', {})),
                    'enabled': module_config.get('enabled', True),
                    'priority': module_config.get('priority', 1),
                    'module_name': module_name,
                    'plugin_pattern': f"{user_id}_{self.plugin_data['plugin_slug']}_%",
                    'user_id': user_id
                })
            
            logger.info(f"BrainDriveChat: Imported user data for {user_id}")
            
        except Exception as e:
            logger.error(f"BrainDriveChat: Error importing user data: {e}")
            raise
    
    def get_plugin_info(self) -> Dict[str, Any]:
        """Get plugin information (compatibility method)"""
        return self.plugin_data
    
    @property
    def MODULE_DATA(self):
        """Compatibility property for accessing module data"""
        return self.module_data
    
    # Compatibility methods for old interface
    async def install_plugin(self, user_id: str, db: AsyncSession) -> Dict[str, Any]:
        """Install BrainDriveChat plugin for specific user (compatibility method)"""
        try:
            logger.info(f"BrainDriveChat: Starting installation for user {user_id}")
            
            # Check if plugin is already installed for this user
            existing_check = await self._check_existing_plugin(user_id, db)
            if existing_check['exists']:
                logger.warning(f"BrainDriveChat: Plugin already installed for user {user_id}")
                return {
                    'success': False,
                    'error': 'Plugin already installed for user',
                    'plugin_id': existing_check['plugin_id']
                }
            
            shared_path = self.shared_path
            shared_path.mkdir(parents=True, exist_ok=True)
            logger.info(f"BrainDriveChat: Created shared directory: {shared_path}")

            # Copy plugin files to the shared directory first
            copy_result = await self._copy_plugin_files_impl(user_id, shared_path)
            if not copy_result['success']:
                logger.error(f"BrainDriveChat: File copying failed: {copy_result.get('error')}")
                return copy_result

            logger.info(f"BrainDriveChat: Files copied successfully, proceeding with database installation")
            
            # Ensure we're in a transaction
            try:
                result = await self.install_for_user(user_id, db, shared_path)
                
                if result.get('success'):
                    # Verify the installation was successful
                    verify_check = await self._check_existing_plugin(user_id, db)
                    if not verify_check['exists']:
                        logger.error(f"BrainDriveChat: Installation appeared successful but verification failed")
                        return {'success': False, 'error': 'Installation verification failed'}
                    
                    logger.info(f"BrainDriveChat: Installation verified successfully for user {user_id}")
                    result.update({
                        'plugin_slug': self.plugin_data['plugin_slug'],
                        'plugin_name': self.plugin_data['name']
                    })
                else:
                    logger.error(f"BrainDriveChat: Database installation failed: {result.get('error')}")
                
                return result
                
            except Exception as db_error:
                logger.error(f"BrainDriveChat: Database operation failed: {db_error}")
                # Try to rollback if possible
                try:
                    await db.rollback()
                except:
                    pass
                return {'success': False, 'error': f'Database operation failed: {str(db_error)}'}
                
        except Exception as e:
            logger.error(f"BrainDriveChat: Install plugin failed: {e}")
            return {'success': False, 'error': str(e)}
    
    async def delete_plugin(self, user_id: str, db: AsyncSession) -> Dict[str, Any]:
        """Delete BrainDriveChat plugin for user (compatibility method)"""
        try:
            logger.info(f"BrainDriveChat: Starting deletion for user {user_id}")
            
            # Let the base class handle the deletion - it will call _perform_user_uninstallation
            # which includes the database check
            result = await self.uninstall_for_user(user_id, db)
            
            if result.get('success'):
                logger.info(f"BrainDriveChat: Successfully deleted plugin for user {user_id}")
            else:
                logger.error(f"BrainDriveChat: Deletion failed: {result.get('error')}")
            
            return result
        except Exception as e:
            logger.error(f"BrainDriveChat: Delete plugin failed: {e}")
            return {'success': False, 'error': str(e)}
    
    async def get_plugin_status(self, user_id: str, db: AsyncSession) -> Dict[str, Any]:
        """Get current status of BrainDriveChat plugin installation (compatibility method)"""
        try:
            existing_check = await self._check_existing_plugin(user_id, db)
            if not existing_check['exists']:
                return {'exists': False, 'status': 'not_installed'}
            
            # Check if shared plugin files exist
            plugin_health = await self._get_plugin_health_impl(user_id, self.shared_path)
            
            return {
                'exists': True,
                'status': 'healthy' if plugin_health['healthy'] else 'unhealthy',
                'plugin_id': existing_check['plugin_id'],
                'plugin_info': existing_check['plugin_info'],
                'health_details': plugin_health['details']
            }
            
        except Exception as e:
            logger.error(f"BrainDriveChat: Error checking plugin status: {e}")
            return {'exists': False, 'status': 'error', 'error': str(e)}
    
    async def update_plugin(self, user_id: str, db: AsyncSession, new_version_manager: 'BrainDriveChatLifecycleManager') -> Dict[str, Any]:
        """Update BrainDriveChat plugin for user (compatibility method)"""
        try:
            # Export current user data
            export_result = await self._export_user_data(user_id, db)
            if not export_result['success']:
                return export_result
            
            # Uninstall current version
            uninstall_result = await self.uninstall_for_user(user_id, db)
            if not uninstall_result['success']:
                return uninstall_result
            
            # Install new version
            install_result = await new_version_manager.install_for_user(user_id, db, new_version_manager.shared_path)
            if not install_result['success']:
                return install_result
            
            # Import user data to new version
            await new_version_manager._import_user_data(user_id, db, export_result.get('user_data', {}))
            
            logger.info(f"BrainDriveChat: Plugin updated successfully for user {user_id}")
            return {
                'success': True,
                'old_version': self.version,
                'new_version': new_version_manager.version,
                'plugin_id': install_result['plugin_id']
            }
            
        except Exception as e:
            logger.error(f"BrainDriveChat: Plugin update failed for user {user_id}: {e}")
            return {'success': False, 'error': str(e)}


# Standalone functions for compatibility with remote installer
async def install_plugin(user_id: str, db: AsyncSession, plugins_base_dir: str = None) -> Dict[str, Any]:
    manager = BrainDriveChatLifecycleManager(plugins_base_dir)
    return await manager.install_plugin(user_id, db)

async def delete_plugin(user_id: str, db: AsyncSession, plugins_base_dir: str = None) -> Dict[str, Any]:
    manager = BrainDriveChatLifecycleManager(plugins_base_dir)
    return await manager.delete_plugin(user_id, db)

async def get_plugin_status(user_id: str, db: AsyncSession, plugins_base_dir: str = None) -> Dict[str, Any]:
    manager = BrainDriveChatLifecycleManager(plugins_base_dir)
    return await manager.get_plugin_status(user_id, db)

async def update_plugin(user_id: str, db: AsyncSession, new_version_manager: 'BrainDriveChatLifecycleManager', plugins_base_dir: str = None) -> Dict[str, Any]:
    old_manager = BrainDriveChatLifecycleManager(plugins_base_dir)
    return await old_manager.update_plugin(user_id, db, new_version_manager)


# Test script for development
if __name__ == "__main__":
    import sys
    import asyncio
    
    async def main():
        print("BrainDriveChat Plugin Lifecycle Manager - Test Mode")
        print("=" * 50)
        
        # Test manager initialization
        manager = BrainDriveChatLifecycleManager()
        print(f"Plugin: {manager.plugin_data['name']}")
        print(f"Version: {manager.plugin_data['version']}")
        print(f"Slug: {manager.plugin_data['plugin_slug']}")
        print(f"Modules: {len(manager.module_data)}")
        
        for module in manager.module_data:
            print(f"  - {module['display_name']} ({module['name']})")
    
    asyncio.run(main())
