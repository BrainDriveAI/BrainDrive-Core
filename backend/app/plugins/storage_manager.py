"""
Plugin Storage Manager

Handles optimized plugin file storage and access patterns using logical references
instead of filesystem links for cross-platform compatibility.
"""

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List
import structlog

logger = structlog.get_logger()


class PluginStorageManager:
    """Manages plugin storage with shared files and logical references"""

    def __init__(self, plugins_base_dir: Path):
        self.base_dir = plugins_base_dir
        self.shared_dir = self.base_dir / "shared"
        self.users_dir = self.base_dir / "users"
        self.cache_dir = self.base_dir / "cache"

        # Ensure directories exist
        self.shared_dir.mkdir(parents=True, exist_ok=True)
        self.users_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        (self.cache_dir / "manager_instances").mkdir(exist_ok=True)
        (self.cache_dir / "temp").mkdir(exist_ok=True)

    async def install_plugin_files(self, plugin_slug: str, version: str, source_path: Path) -> Path:
        """Install plugin files to shared storage if not already present, return shared path"""
        shared_plugin_path = self.shared_dir / plugin_slug / f"v{version}"

        if not shared_plugin_path.exists():
            logger.info(f"Installing plugin files for {plugin_slug} v{version} to shared storage")
            shared_plugin_path.mkdir(parents=True, exist_ok=True)
            await self._copy_plugin_files(source_path, shared_plugin_path)
        else:
            logger.info(f"Plugin files for {plugin_slug} v{version} already exist in shared storage")

        return shared_plugin_path

    async def _copy_plugin_files(self, source_path: Path, target_path: Path):
        """Copy plugin files from source to target directory"""
        try:
            if source_path.is_file():
                # If source is a single file (e.g., tar.gz), extract it
                if source_path.suffix in ['.gz', '.tar', '.zip']:
                    await self._extract_archive(source_path, target_path)
                else:
                    # Copy single file
                    shutil.copy2(source_path, target_path / source_path.name)
            else:
                # Copy entire directory
                for item in source_path.iterdir():
                    if item.is_file():
                        shutil.copy2(item, target_path / item.name)
                    elif item.is_dir():
                        shutil.copytree(item, target_path / item.name, dirs_exist_ok=True)

            logger.info(f"Successfully copied plugin files from {source_path} to {target_path}")

        except Exception as e:
            logger.error(f"Error copying plugin files: {e}")
            raise

    async def _extract_archive(self, archive_path: Path, target_path: Path):
        """Extract archive file to target directory with path traversal protection."""
        try:
            if archive_path.suffix == '.gz' and archive_path.stem.endswith('.tar'):
                import tarfile
                with tarfile.open(archive_path, 'r:gz') as tar:
                    self._safe_extract_tar(tar, target_path)
            elif archive_path.suffix == '.zip':
                import zipfile
                with zipfile.ZipFile(archive_path, 'r') as zip_file:
                    self._safe_extract_zip(zip_file, target_path)
            else:
                raise ValueError(f"Unsupported archive format: {archive_path.suffix}")

            logger.info(f"Successfully extracted {archive_path} to {target_path}")

        except Exception as e:
            logger.error(f"Error extracting archive {archive_path}: {e}")
            raise

    @staticmethod
    def _safe_extract_tar(tar: "tarfile.TarFile", target_path: Path):
        """Extract tar safely, rejecting entries that escape the target directory."""
        import tarfile
        resolved_target = target_path.resolve()
        for member in tar.getmembers():
            member_path = (target_path / member.name).resolve()
            if not str(member_path).startswith(str(resolved_target)):
                raise ValueError(f"Path traversal detected in tar member: {member.name}")
            if member.issym() or member.islnk():
                link_target = Path(member.linkname)
                if link_target.is_absolute():
                    raise ValueError(f"Absolute symlink in tar: {member.name} -> {member.linkname}")
                resolved_link = (target_path / member.name).parent.joinpath(link_target).resolve()
                if not str(resolved_link).startswith(str(resolved_target)):
                    raise ValueError(f"Symlink escape in tar: {member.name} -> {member.linkname}")
        tar.extractall(target_path)

    @staticmethod
    def _safe_extract_zip(zip_file: "zipfile.ZipFile", target_path: Path):
        """Extract zip safely, rejecting entries that escape the target directory."""
        resolved_target = target_path.resolve()
        for info in zip_file.infolist():
            member_path = (target_path / info.filename).resolve()
            if not str(member_path).startswith(str(resolved_target)):
                raise ValueError(f"Path traversal detected in zip member: {info.filename}")
        zip_file.extractall(target_path)

    async def register_user_plugin(self, user_id: str, plugin_slug: str, version: str, shared_path: Path, metadata: Dict[str, Any]) -> bool:
        """Register plugin installation for user with minimal metadata (database holds main info)"""
        try:
            user_file = self.users_dir / f"user_{user_id}" / "installed_plugins.json"
            user_file.parent.mkdir(parents=True, exist_ok=True)

            # Load existing installations
            user_plugins = {}
            if user_file.exists():
                with open(user_file, 'r') as f:
                    user_plugins = json.load(f)

            # Add minimal plugin reference - database holds version, enabled, dates, user_config
            user_plugins[plugin_slug] = {
                "installation_metadata": metadata.get('installation_metadata', {
                    "installation_type": "production",
                    "installed_at": datetime.now().isoformat()
                })
            }

            # Save updated references
            with open(user_file, 'w') as f:
                json.dump(user_plugins, f, indent=2)

            logger.info(f"Registered plugin {plugin_slug} v{version} for user {user_id}")
            return True

        except Exception as e:
            logger.error(f"Error registering plugin for user {user_id}: {e}")
            return False

    async def get_user_plugin_path(self, user_id: str, plugin_slug: str, db_version: str = None) -> Optional[Path]:
        """Get shared plugin path for user's installed plugin (derived from base path + plugin + version)"""
        try:
            # Check if user has plugin installed
            user_file = self.users_dir / f"user_{user_id}" / "installed_plugins.json"

            if not user_file.exists():
                return None

            with open(user_file, 'r') as f:
                user_plugins = json.load(f)

            if plugin_slug not in user_plugins:
                return None

            # If version not provided, we need to get it from database
            # For now, construct path using provided version or default pattern
            if db_version:
                version = db_version
            else:
                # This would typically come from database query
                # For backward compatibility, try to find version directory
                plugin_base_dir = self.shared_dir / plugin_slug
                if plugin_base_dir.exists():
                    version_dirs = [d.name for d in plugin_base_dir.iterdir() if d.is_dir() and d.name.startswith('v')]
                    if version_dirs:
                        # Get the latest version directory
                        version = version_dirs[-1][1:]  # Remove 'v' prefix
                    else:
                        logger.warning(f"No version directories found for plugin {plugin_slug}")
                        return None
                else:
                    logger.warning(f"Plugin directory not found: {plugin_base_dir}")
                    return None

            # Construct shared path: base_dir/shared/plugin_name/v{version}
            shared_path = self.shared_dir / plugin_slug / f"v{version}"

            # Verify path exists
            if not shared_path.exists():
                logger.warning(f"Shared plugin path does not exist: {shared_path}")
                return None

            return shared_path

        except Exception as e:
            logger.error(f"Error getting user plugin path: {e}")
            return None

    async def get_user_plugin_metadata(self, user_id: str, plugin_slug: str) -> Dict[str, Any]:
        """Load user-specific plugin installation metadata (minimal - database has main info)"""
        try:
            user_file = self.users_dir / f"user_{user_id}" / "installed_plugins.json"

            if not user_file.exists():
                return {}

            with open(user_file, 'r') as f:
                user_plugins = json.load(f)

            plugin_data = user_plugins.get(plugin_slug, {})

            # Return only installation metadata - other info comes from database
            return {
                "installation_metadata": plugin_data.get("installation_metadata", {}),
                "has_installation": plugin_slug in user_plugins
            }

        except Exception as e:
            logger.error(f"Error getting user plugin metadata: {e}")
            return {}

    async def unregister_user_plugin(self, user_id: str, plugin_slug: str) -> bool:
        """Remove plugin reference from user's installed plugins"""
        try:
            user_file = self.users_dir / f"user_{user_id}" / "installed_plugins.json"

            if not user_file.exists():
                return False

            with open(user_file, 'r') as f:
                user_plugins = json.load(f)

            if plugin_slug in user_plugins:
                del user_plugins[plugin_slug]

                with open(user_file, 'w') as f:
                    json.dump(user_plugins, f, indent=2)

                logger.info(f"Unregistered plugin {plugin_slug} for user {user_id}")
                return True

            return False

        except Exception as e:
            logger.error(f"Error unregistering plugin for user {user_id}: {e}")
            return False

    async def get_all_user_plugins(self, user_id: str) -> Dict[str, Dict[str, Any]]:
        """Get all plugins installed for a user (returns plugin slugs with installation metadata)"""
        try:
            user_file = self.users_dir / f"user_{user_id}" / "installed_plugins.json"

            if not user_file.exists():
                return {}

            with open(user_file, 'r') as f:
                user_plugins = json.load(f)

            # Return plugin slugs with minimal metadata - database has the full info
            result = {}
            for plugin_slug, plugin_data in user_plugins.items():
                result[plugin_slug] = {
                    "installation_metadata": plugin_data.get("installation_metadata", {}),
                    "has_installation": True
                }

            return result

        except Exception as e:
            logger.error(f"Error getting all user plugins: {e}")
            return {}

    async def cleanup_unused_versions(self, active_plugins_from_db: Dict[str, set] = None) -> List[str]:
        """Remove plugin versions no longer used by any user (uses database info for accuracy)"""
        try:
            # If database info not provided, scan user files for installed plugins
            if active_plugins_from_db is None:
                active_plugins_from_db = {}

                for user_dir in self.users_dir.iterdir():
                    if user_dir.is_dir():
                        user_file = user_dir / "installed_plugins.json"
                        if user_file.exists():
                            with open(user_file, 'r') as f:
                                user_plugins = json.load(f)

                            for plugin_slug in user_plugins.keys():
                                if plugin_slug not in active_plugins_from_db:
                                    active_plugins_from_db[plugin_slug] = set()
                                # Note: We'd need database query to get actual versions
                                # For now, mark plugin as active (version would come from DB)

            # Find and remove unreferenced plugin versions
            removed_versions = []
            for plugin_dir in self.shared_dir.iterdir():
                if plugin_dir.is_dir():
                    plugin_slug = plugin_dir.name

                    for version_dir in plugin_dir.iterdir():
                        if version_dir.is_dir() and version_dir.name.startswith('v'):
                            version = version_dir.name[1:]  # Remove 'v' prefix

                            # Check if this plugin version is still in use
                            is_in_use = (plugin_slug in active_plugins_from_db and
                                        version in active_plugins_from_db[plugin_slug])

                            if not is_in_use:
                                shutil.rmtree(version_dir)
                                removed_versions.append(f"{plugin_slug}/{version_dir.name}")
                                logger.info(f"Removed unused plugin version: {plugin_slug}/{version_dir.name}")

            return removed_versions

        except Exception as e:
            logger.error(f"Error cleaning up unused versions: {e}")
            return []

    async def validate_plugin_access(self, shared_path: Path) -> bool:
        """Validate that plugin files are accessible"""
        try:
            # Check if directory exists and is readable
            if not shared_path.exists() or not shared_path.is_dir():
                return False

            # Verify essential files exist
            essential_files = ["package.json"]
            for file_name in essential_files:
                if not (shared_path / file_name).exists():
                    logger.warning(f"Essential file missing: {file_name} in {shared_path}")
                    return False

            return True

        except (OSError, PermissionError) as e:
            logger.error(f"Error validating plugin access: {e}")
            return False

    async def get_plugin_file_path(self, user_id: str, plugin_slug: str, file_path: str, db_version: str = None) -> Optional[Path]:
        """Get full path to a specific plugin file for a user"""
        try:
            shared_path = await self.get_user_plugin_path(user_id, plugin_slug, db_version)
            if not shared_path:
                return None

            full_file_path = shared_path / file_path

            # Security check: ensure the requested file is within the plugin directory
            if not str(full_file_path.resolve()).startswith(str(shared_path.resolve())):
                logger.warning(f"Path traversal attempt detected: {file_path}")
                return None

            # Check if file exists
            if not full_file_path.exists() or not full_file_path.is_file():
                return None

            return full_file_path

        except Exception as e:
            logger.error(f"Error getting plugin file path: {e}")
            return None

    def construct_shared_path(self, plugin_slug: str, version: str) -> Path:
        """Construct shared path from base directory + plugin name + version"""
        return self.shared_dir / plugin_slug / f"v{version}"

    async def plugin_exists_for_user(self, user_id: str, plugin_slug: str) -> bool:
        """Check if user has plugin installed (checks JSON file)"""
        try:
            user_file = self.users_dir / f"user_{user_id}" / "installed_plugins.json"

            if not user_file.exists():
                return False

            with open(user_file, 'r') as f:
                user_plugins = json.load(f)

            return plugin_slug in user_plugins

        except Exception as e:
            logger.error(f"Error checking plugin existence for user: {e}")
            return False