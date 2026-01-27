"""
Base Lifecycle Manager

Enhanced base class for plugin lifecycle managers that supports
shared storage, user isolation, and efficient resource management.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Set, Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
import structlog

logger = structlog.get_logger()


class BaseLifecycleManager(ABC):
    """Enhanced base class for plugin lifecycle managers"""

    def __init__(self, plugin_slug: str, version: str, shared_storage_path: Path):
        self.plugin_slug = plugin_slug
        self.version = version
        self.shared_path = shared_storage_path
        self.active_users: Set[str] = set()
        self.instance_id = f"{plugin_slug}_{version}"
        self.created_at = datetime.now()
        self.last_used = datetime.now()

    async def _initialize_active_users(self, db: AsyncSession):
        """Initialize active_users set from database (optional for consistency)"""
        try:
            if hasattr(self, '_check_existing_plugin'):
                # This is a placeholder - specific implementations can override this
                # to populate active_users from database if needed for consistency
                logger.debug(f"Base lifecycle manager: active_users initialization available for {self.plugin_slug}")
            else:
                logger.debug(f"Base lifecycle manager: No database check method available for {self.plugin_slug}")
        except Exception as e:
            logger.warning(f"Base lifecycle manager: Error initializing active users: {e}")

    @abstractmethod
    async def get_plugin_metadata(self) -> Dict[str, Any]:
        """Return plugin metadata and configuration.

        Required fields:
            - name: str
            - version: str
            - description: str
            - plugin_slug: str

        Optional fields:
            - type: str - "frontend" (default), "backend", or "fullstack"
            - endpoints_file: str - Required for backend/fullstack plugins (e.g., "endpoints.py")
            - route_prefix: str - API route prefix for backend plugins (e.g., "/library")
            - backend_dependencies: List[str] - List of required backend plugin slugs

        Note: Backend and fullstack plugins MUST specify endpoints_file.
        """
        pass

    @abstractmethod
    async def get_module_metadata(self) -> List[Dict[str, Any]]:
        """Return module definitions for this plugin"""
        pass

    async def install_for_user(self, user_id: str, db: AsyncSession, shared_plugin_path: Path) -> Dict[str, Any]:
        """Install plugin for specific user using shared plugin path"""
        try:
            logger.info(f"Installing {self.plugin_slug} v{self.version} for user {user_id}")

            # Note: We let the specific implementation check for existing installations
            # rather than relying on the in-memory active_users set which gets reset
            logger.info(f"Base lifecycle manager: Proceeding with installation for user {user_id}")

            # Perform user-specific installation using shared path
            result = await self._perform_user_installation(user_id, db, shared_plugin_path)

            if result['success']:
                self.active_users.add(user_id)
                self.last_used = datetime.now()

            return result

        except Exception as e:
            logger.error(f"Installation failed for {self.plugin_slug} v{self.version}, user {user_id}: {e}")
            return {'success': False, 'error': str(e)}

    async def uninstall_for_user(self, user_id: str, db: AsyncSession) -> Dict[str, Any]:
        """Uninstall plugin for specific user"""
        try:
            logger.info(f"Uninstalling {self.plugin_slug} v{self.version} for user {user_id}")

            # Check database instead of in-memory active_users set
            # The active_users set gets reset when the lifecycle manager is reloaded
            logger.info(f"Base lifecycle manager: Checking database for plugin installation for user {user_id}")

            # Perform user-specific cleanup (this will check the database)
            result = await self._perform_user_uninstallation(user_id, db)

            if result['success']:
                # Remove from active users if present (for consistency)
                self.active_users.discard(user_id)
                self.last_used = datetime.now()
                logger.info(f"Base lifecycle manager: Successfully uninstalled {self.plugin_slug} for user {user_id}")
            else:
                logger.warning(f"Base lifecycle manager: Uninstallation failed for {self.plugin_slug}, user {user_id}: {result.get('error')}")

            return result

        except Exception as e:
            logger.error(f"Uninstallation failed for {self.plugin_slug} v{self.version}, user {user_id}: {e}")
            return {'success': False, 'error': str(e)}

    async def update_for_user(self, user_id: str, db: AsyncSession, new_version_manager: 'BaseLifecycleManager') -> Dict[str, Any]:
        """Handle user migration to new plugin version"""
        try:
            # Export user data from current version
            user_data = await self._export_user_data(user_id, db)

            # Uninstall current version
            uninstall_result = await self.uninstall_for_user(user_id, db)
            if not uninstall_result['success']:
                return uninstall_result

            # Install new version
            install_result = await new_version_manager.install_for_user(user_id, db, user_data.get('shared_plugin_path'))
            if not install_result['success']:
                # Rollback - reinstall old version
                await self.install_for_user(user_id, db, user_data.get('shared_plugin_path'))
                return install_result

            # Import user data to new version
            await new_version_manager._import_user_data(user_id, db, user_data)

            return {'success': True, 'migrated_data': user_data}

        except Exception as e:
            logger.error(f"Update failed for {self.plugin_slug}, user {user_id}: {e}")
            return {'success': False, 'error': str(e)}

    async def get_plugin_status(self, user_id: str, db: AsyncSession) -> Dict[str, Any]:
        """Get current status of plugin installation for user"""
        try:
            # Check database instead of in-memory active_users set
            # Use the specific implementation's database check
            logger.info(f"Base lifecycle manager: Checking plugin status for user {user_id}")

            # Let the specific implementation check the database
            # This is an abstract method that should be implemented by subclasses
            try:
                # Try to call the specific implementation's status check
                if hasattr(self, '_check_existing_plugin'):
                    db_check = await self._check_existing_plugin(user_id, db)
                    is_installed = db_check.get('exists', False)
                else:
                    # Fallback: assume installed if user is in active_users (for compatibility)
                    is_installed = user_id in self.active_users
                    logger.warning(f"Base lifecycle manager: No _check_existing_plugin method, using active_users fallback")
            except Exception as e:
                logger.error(f"Base lifecycle manager: Error checking database status: {e}")
                # Fallback to active_users check
                is_installed = user_id in self.active_users

            if not is_installed:
                return {'exists': False, 'status': 'not_installed'}

            # Check if shared files exist
            files_exist = self.shared_path.exists()

            # Check modules status
            modules_status = await self._check_modules_status(user_id, db)

            if files_exist and modules_status.get('all_loaded', False):
                status = 'healthy'
            elif not files_exist:
                status = 'files_missing'
            elif not modules_status.get('all_loaded', False):
                status = 'modules_corrupted'
            else:
                status = 'unknown'

            return {
                'exists': True,
                'status': status,
                'plugin_info': await self.get_plugin_metadata(),
                'files_exist': files_exist,
                'modules_status': modules_status,
                'shared_path': str(self.shared_path)
            }

        except Exception as e:
            logger.error(f"Error checking plugin status for user {user_id}: {e}")
            return {'exists': False, 'status': 'error', 'error': str(e)}

    def can_be_unloaded(self) -> bool:
        """Check if this manager instance can be safely unloaded"""
        return len(self.active_users) == 0

    def get_usage_stats(self) -> Dict[str, Any]:
        """Get usage statistics for this manager instance"""
        return {
            'plugin_slug': self.plugin_slug,
            'version': self.version,
            'active_users': len(self.active_users),
            'user_list': list(self.active_users),
            'created_at': self.created_at.isoformat(),
            'last_used': self.last_used.isoformat(),
            'uptime_seconds': (datetime.now() - self.created_at).total_seconds(),
            'shared_path': str(self.shared_path)
        }

    @abstractmethod
    async def _perform_user_installation(self, user_id: str, db: AsyncSession, shared_plugin_path: Path) -> Dict[str, Any]:
        """Plugin-specific installation logic using shared plugin path"""
        pass

    @abstractmethod
    async def _perform_user_uninstallation(self, user_id: str, db: AsyncSession) -> Dict[str, Any]:
        """Plugin-specific uninstallation logic"""
        pass

    async def _export_user_data(self, user_id: str, db: AsyncSession) -> Dict[str, Any]:
        """Export user-specific data for migration (override if needed)"""
        return {
            'shared_plugin_path': self.shared_path,
            'user_id': user_id,
            'plugin_slug': self.plugin_slug,
            'version': self.version
        }

    async def _import_user_data(self, user_id: str, db: AsyncSession, user_data: Dict[str, Any]):
        """Import user-specific data after migration (override if needed)"""
        pass

    async def _check_modules_status(self, user_id: str, db: AsyncSession) -> Dict[str, Any]:
        """Check status of plugin modules (override if needed)"""
        try:
            # Default implementation - check if modules exist in database
            from sqlalchemy import text

            module_query = text("""
            SELECT COUNT(*) as module_count
            FROM module
            WHERE plugin_id LIKE :plugin_pattern AND user_id = :user_id
            """)

            result = await db.execute(module_query, {
                'plugin_pattern': f"{user_id}_{self.plugin_slug}%",
                'user_id': user_id
            })

            row = result.fetchone()
            module_count = row.module_count if row else 0

            expected_modules = await self.get_module_metadata()
            expected_count = len(expected_modules)

            return {
                'all_loaded': module_count == expected_count,
                'loaded_count': module_count,
                'expected_count': expected_count
            }

        except Exception as e:
            logger.error(f"Error checking modules status: {e}")
            return {'all_loaded': False, 'loaded_count': 0, 'expected_count': 0}

    async def cleanup(self):
        """Cleanup resources when manager is being unloaded (override if needed)"""
        logger.info(f"Cleaning up lifecycle manager for {self.plugin_slug} v{self.version}")
        self.active_users.clear()

    def __str__(self) -> str:
        return f"LifecycleManager({self.plugin_slug} v{self.version}, users: {len(self.active_users)})"

    def __repr__(self) -> str:
        return self.__str__()


# Valid plugin types
VALID_PLUGIN_TYPES = {'frontend', 'backend', 'fullstack'}


# Helper function to validate plugin metadata
def validate_plugin_metadata(metadata: Dict[str, Any]) -> bool:
    """Validate plugin metadata structure.

    Validates:
        - Required fields are present
        - plugin_type is valid (frontend, backend, fullstack)
        - backend/fullstack plugins have endpoints_file
        - backend_dependencies is a list if present
    """
    required_fields = ['name', 'version', 'description', 'plugin_slug']

    for field in required_fields:
        if field not in metadata:
            logger.error(f"Missing required field '{field}' in plugin metadata")
            return False

    # Validate plugin type if specified
    plugin_type = metadata.get('type', 'frontend')
    if plugin_type not in VALID_PLUGIN_TYPES:
        logger.error(f"Invalid plugin type '{plugin_type}'. Must be one of: {VALID_PLUGIN_TYPES}")
        return False

    # Backend and fullstack plugins require endpoints_file
    if plugin_type in ('backend', 'fullstack'):
        if not metadata.get('endpoints_file'):
            logger.error(f"Backend/fullstack plugins must specify 'endpoints_file'")
            return False

    # Validate backend_dependencies is a list if present
    backend_deps = metadata.get('backend_dependencies')
    if backend_deps is not None and not isinstance(backend_deps, list):
        logger.error(f"'backend_dependencies' must be a list of plugin slugs")
        return False

    return True


# Helper function to validate module metadata
def validate_module_metadata(modules: List[Dict[str, Any]]) -> bool:
    """Validate module metadata structure"""
    if not isinstance(modules, list):
        logger.error("Modules metadata must be a list")
        return False

    required_fields = ['name', 'display_name', 'description']

    for i, module in enumerate(modules):
        if not isinstance(module, dict):
            logger.error(f"Module at index {i} must be a dictionary")
            return False

        for field in required_fields:
            if field not in module:
                logger.error(f"Missing required field '{field}' in module at index {i}")
                return False

    return True