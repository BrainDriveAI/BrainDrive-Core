#!/usr/bin/env python3
"""
Remote Plugin Installer

This module handles downloading and installing plugins from remote sources like GitHub repositories.
It supports downloading prebuilt releases (tar.gz files) and integrating them with the universal
lifecycle management system.
"""

import asyncio
import aiohttp
import aiofiles
import tarfile
import zipfile
import json
import tempfile
import shutil
import re
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
from urllib.parse import urlparse
import structlog
from .service_installler.plugin_service_manager import install_and_run_required_services
from .service_installler.service_runtime_extractor import extract_required_services_runtime

logger = structlog.get_logger()

class RemotePluginInstaller:
    """Handles downloading and installing plugins from remote sources"""

    def __init__(self, plugins_base_dir: str = None, temp_dir: str = None):
        """
        Initialize the remote plugin installer

        Args:
            plugins_base_dir: Base directory for plugins
            temp_dir: Temporary directory for downloads
        """
        if plugins_base_dir:
            self.plugins_base_dir = Path(plugins_base_dir)
        else:
            # Use backend/plugins directory for user-specific plugin installations
            self.plugins_base_dir = Path(__file__).parent.parent.parent.parent / "backend" / "plugins"

        self.temp_dir = Path(temp_dir) if temp_dir else Path(tempfile.gettempdir()) / "braindrive_plugins"
        self.temp_dir.mkdir(exist_ok=True)

        # GitHub API patterns
        self.github_repo_pattern = re.compile(r'github\.com/([^/]+)/([^/]+)')
        self.github_release_pattern = re.compile(r'github\.com/([^/]+)/([^/]+)/releases')

    async def install_from_file(self, file_path: Path, user_id: str, filename: str) -> Dict[str, Any]:
        """
        Install a plugin from a local file

        Args:
            file_path: Path to the uploaded plugin file
            user_id: User ID to install plugin for
            filename: Original filename of the uploaded file

        Returns:
            Dict with installation result
        """
        try:
            logger.info(f"Installing plugin from local file {filename} for user {user_id}")

            # Extract the uploaded file
            extract_result = await self._extract_local_file(file_path, filename)
            if not extract_result['success']:
                logger.error(f"Extraction failed: {extract_result.get('error', 'Unknown extraction error')}")
                return {
                    'success': False,
                    'error': f"Extraction failed: {extract_result.get('error', 'Unknown extraction error')}",
                    'details': {
                        'step': 'file_extraction',
                        'filename': filename
                    }
                }

            logger.info(f"Successfully extracted to: {extract_result['extracted_path']}")

            # Validate plugin structure
            validation_result = await self._validate_plugin_structure(extract_result['extracted_path'])
            if not validation_result['valid']:
                await self._cleanup_temp_files(extract_result['extracted_path'])
                error_msg = f"Plugin validation failed: {validation_result['error']}"
                logger.error(error_msg)
                return {
                    'success': False,
                    'error': error_msg,
                    'details': {
                        'step': 'plugin_validation',
                        'validation_error': validation_result['error']
                    }
                }

            logger.info(f"Plugin validation successful. Plugin info: {validation_result['plugin_info']}")

            # Install plugin using lifecycle manager
            install_result = await self._install_plugin_locally(
                extract_result['extracted_path'],
                validation_result['plugin_info'],
                user_id
            )

            # Cleanup temporary files
            await self._cleanup_temp_files(extract_result['extracted_path'])

            if install_result['success']:
                logger.info(f"Plugin installation successful: {install_result}")

                # Store installation metadata for local file
                try:
                    await self._store_local_file_metadata(
                        user_id,
                        install_result['plugin_id'],
                        filename,
                        validation_result['plugin_info']
                    )
                    logger.info("Installation metadata stored successfully")
                except Exception as metadata_error:
                    logger.warning(f"Failed to store installation metadata: {metadata_error}")
                    # Don't fail the installation for metadata storage issues

                # Trigger plugin discovery to refresh the plugin manager cache
                try:
                    await self._refresh_plugin_discovery(user_id)
                    logger.info("Plugin discovery refreshed successfully")
                except Exception as discovery_error:
                    logger.warning(f"Failed to refresh plugin discovery: {discovery_error}")
                    # Don't fail the installation for discovery refresh issues
            else:
                logger.error(f"Plugin installation failed: {install_result}")

            return install_result

        except Exception as e:
            error_msg = f"Unexpected error during plugin installation from file {filename}: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return {
                'success': False,
                'error': error_msg,
                'details': {
                    'step': 'unexpected_exception',
                    'exception_type': type(e).__name__,
                    'filename': filename,
                    'user_id': user_id
                }
            }

    async def install_from_url(self, repo_url: str, user_id: str, version: str = "latest") -> Dict[str, Any]:
        """
        Install a plugin from a remote repository URL

        Args:
            repo_url: GitHub repository URL
            user_id: User ID to install plugin for
            version: Version to install ("latest" or specific version)

        Returns:
            Dict with installation result
        """
        try:
            logger.info(f"Installing plugin from {repo_url} for user {user_id}, version: {version}")

            # Parse repository information
            repo_info = self._parse_repo_url(repo_url)
            if not repo_info:
                error_msg = f'Invalid repository URL format: {repo_url}'
                logger.error(error_msg)
                return {
                    'success': False,
                    'error': error_msg,
                    'details': {'step': 'url_parsing', 'repo_url': repo_url}
                }

            logger.info(f"Parsed repository: {repo_info['owner']}/{repo_info['repo']}")

            # Get release information
            release_info = await self._get_release_info(repo_info['owner'], repo_info['repo'], version)
            if not release_info:
                error_msg = f'No release found for version: {version} in repository {repo_info["owner"]}/{repo_info["repo"]}'
                logger.error(error_msg)
                return {
                    'success': False,
                    'error': error_msg,
                    'details': {
                        'step': 'release_lookup',
                        'repo_owner': repo_info['owner'],
                        'repo_name': repo_info['repo'],
                        'requested_version': version
                    }
                }

            logger.info(f"Found release: {release_info['version']} published at {release_info['published_at']}")

            # Download and extract plugin
            download_result = await self._download_and_extract(release_info)
            if not download_result['success']:
                logger.error(f"Download failed: {download_result.get('error', 'Unknown download error')}")
                return {
                    'success': False,
                    'error': f"Download failed: {download_result.get('error', 'Unknown download error')}",
                    'details': {
                        'step': 'download_and_extract',
                        'release_version': release_info['version']
                    }
                }

            logger.info(f"Successfully downloaded and extracted to: {download_result['extracted_path']}")

            # Validate plugin structure
            validation_result = await self._validate_plugin_structure(download_result['extracted_path'])
            if not validation_result['valid']:
                await self._cleanup_temp_files(download_result['extracted_path'])
                error_msg = f"Plugin validation failed: {validation_result['error']}"
                logger.error(error_msg)
                return {
                    'success': False,
                    'error': error_msg,
                    'details': {
                        'step': 'plugin_validation',
                        'validation_error': validation_result['error']
                    }
                }

            logger.info(f"Plugin validation successful. Plugin info: {validation_result['plugin_info']}")

            # Install plugin using lifecycle manager
            install_result = await self._install_plugin_locally(
                download_result['extracted_path'],
                validation_result['plugin_info'],
                user_id
            )

            # Cleanup temporary files
            await self._cleanup_temp_files(download_result['extracted_path'])

            if install_result['success']:
                logger.info(f"Plugin installation successful: {install_result}")

                service_runtimes: list = validation_result.get("service_runtime", [])
                logger.info(f"\n\n>>>>>>>>SERVICE RUNTIMES\n\n: {service_runtimes}\n\n>>>>>>>>>>")
                if service_runtimes:
                    plugin_slug = validation_result["plugin_info"].get("plugin_slug")
                    plugin_id = install_result['plugin_id']
                    logger.info(f"PLUGIN ID: {plugin_id}\n\nUser id: {user_id}")
                    # Run service setup in background
                    asyncio.create_task(
                        install_and_run_required_services(
                            service_runtimes,
                            plugin_slug,
                            plugin_id,
                            user_id
                        )
                    )

                # Store installation metadata
                try:
                    await self._store_installation_metadata(
                        user_id,
                        install_result['plugin_id'],
                        repo_info,
                        release_info
                    )
                    logger.info("Installation metadata stored successfully")
                except Exception as metadata_error:
                    logger.warning(f"Failed to store installation metadata: {metadata_error}")
                    # Don't fail the installation for metadata storage issues

                # Trigger plugin discovery to refresh the plugin manager cache
                try:
                    await self._refresh_plugin_discovery(user_id)
                    logger.info("Plugin discovery refreshed successfully")
                except Exception as discovery_error:
                    logger.warning(f"Failed to refresh plugin discovery: {discovery_error}")
                    # Don't fail the installation for discovery refresh issues
            else:
                logger.error(f"Plugin installation failed: {install_result}")

            return install_result

        except Exception as e:
            error_msg = f"Unexpected error during plugin installation from {repo_url}: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return {
                'success': False,
                'error': error_msg,
                'details': {
                    'step': 'unexpected_exception',
                    'exception_type': type(e).__name__,
                    'repo_url': repo_url,
                    'user_id': user_id,
                    'version': version
                }
            }

    async def update_plugin(self, user_id: str, plugin_id: str, version: str = "latest") -> Dict[str, Any]:
        """
        Update an installed plugin to a new version

        Args:
            user_id: User ID
            plugin_id: Plugin ID to update
            version: Version to update to

        Returns:
            Dict with update result
        """
        try:
            # Get current installation metadata
            metadata = await self._get_installation_metadata(user_id, plugin_id)
            if not metadata:
                return {'success': False, 'error': 'Plugin installation metadata not found'}

            # Get plugin slug from plugin_id (format: {user_id}_{plugin_slug})
            plugin_slug = plugin_id.split('_', 1)[1] if '_' in plugin_id else plugin_id

            # Get release information for the new version
            repo_url = f"https://github.com/{metadata['repo_owner']}/{metadata['repo_name']}"
            repo_info = self._parse_repo_url(repo_url)
            if not repo_info:
                return {'success': False, 'error': 'Invalid repository URL in metadata'}

            release_info = await self._get_release_info(repo_info['owner'], repo_info['repo'], version)
            if not release_info:
                return {'success': False, 'error': f'No release found for version: {version}'}

            # Download and extract the new version
            download_result = await self._download_and_extract(release_info)
            if not download_result['success']:
                return download_result

            # Validate plugin structure
            validation_result = await self._validate_plugin_structure(download_result['extracted_path'])
            if not validation_result['valid']:
                await self._cleanup_temp_files(download_result['extracted_path'])
                return {'success': False, 'error': validation_result['error']}

            # Use the new architecture with Universal Lifecycle Manager
            logger.info(f"Updating plugin using new architecture for {plugin_slug}")

            # Get the current plugin version from database to determine shared storage path
            from app.models.plugin import Plugin
            from sqlalchemy import select
            from app.core.database import get_db

            current_version = None
            plugin_type_hint = None
            async for db in get_db():
                stmt = select(Plugin).where(Plugin.id == plugin_id, Plugin.user_id == user_id)
                result = await db.execute(stmt)
                plugin_record = result.scalar_one_or_none()
                if plugin_record:
                    current_version = plugin_record.version
                    plugin_type_hint = getattr(plugin_record, "type", None) or getattr(plugin_record, "plugin_type", None)
                break

            # Create universal lifecycle manager and perform update
            from .lifecycle_api import UniversalPluginLifecycleManager
            universal_manager = UniversalPluginLifecycleManager(str(self.plugins_base_dir))

            # Copy new version to shared storage
            shared_storage_path = self.plugins_base_dir / "shared" / plugin_slug / f"v{release_info['version']}"
            shared_storage_path.parent.mkdir(parents=True, exist_ok=True)

            # Remove existing version if it exists
            if shared_storage_path.exists():
                shutil.rmtree(shared_storage_path)

            # Copy new version to shared storage
            shutil.copytree(download_result['extracted_path'], shared_storage_path)
            logger.info(f"Copied new version to shared storage: {shared_storage_path}")
            self._ensure_major_version_alias(plugin_slug, release_info['version'])

            # Update database with new version information
            async for db in get_db():
                stmt = select(Plugin).where(Plugin.id == plugin_id, Plugin.user_id == user_id)
                result = await db.execute(stmt)
                plugin_record = result.scalar_one_or_none()

                if plugin_record:
                    plugin_record.version = release_info['version']
                    await db.commit()
                    logger.info(f"Updated database record to version {release_info['version']}")
                break

            # Update metadata file
            await self._store_installation_metadata(
                user_id,
                plugin_id,
                repo_info,
                release_info
            )

            # Cleanup temporary files only (no backup_dir in new architecture)
            await self._cleanup_temp_files(download_result['extracted_path'])
            logger.info(f"Plugin update completed successfully")

            return {
                'success': True,
                'message': f'Plugin updated successfully to version {release_info["version"]}',
                'plugin_id': plugin_id,
                'version': release_info['version'],
                'plugin_type': plugin_type_hint,
            }

        except Exception as e:
            logger.error(f"Error updating plugin {plugin_id}: {e}")
            return {'success': False, 'error': str(e)}

    def _ensure_major_version_alias(self, plugin_slug: str, version: str) -> None:
        """Ensure ``v{major}`` points to ``v{full_version}`` for shared plugin files."""
        normalized_version = str(version or "").strip().lstrip("v")
        if not normalized_version:
            return
        major = normalized_version.split(".", 1)[0] or normalized_version
        shared_root = self.plugins_base_dir / "shared" / plugin_slug
        target_dir = shared_root / f"v{normalized_version}"
        major_dir = shared_root / f"v{major}"

        if not target_dir.exists() or target_dir == major_dir:
            return

        try:
            if major_dir.exists() or major_dir.is_symlink():
                try:
                    if major_dir.resolve() == target_dir.resolve():
                        return
                except Exception:
                    pass

                if major_dir.is_symlink() or major_dir.is_file():
                    major_dir.unlink()
                else:
                    shutil.rmtree(major_dir)

            major_dir.symlink_to(target_dir, target_is_directory=True)
        except Exception:
            if major_dir.exists() or major_dir.is_symlink():
                if major_dir.is_symlink() or major_dir.is_file():
                    major_dir.unlink()
                else:
                    shutil.rmtree(major_dir)
            shutil.copytree(target_dir, major_dir, dirs_exist_ok=True)

    def _parse_repo_url(self, url: str) -> Optional[Dict[str, str]]:
        """Parse GitHub repository URL to extract owner and repo name"""
        try:
            # Handle various GitHub URL formats
            url = url.strip().rstrip('/')

            # Remove .git suffix if present
            if url.endswith('.git'):
                url = url[:-4]

            # Extract owner and repo from URL
            match = self.github_repo_pattern.search(url)
            if match:
                owner, repo = match.groups()
                return {
                    'owner': owner,
                    'repo': repo,
                    'url': f"https://github.com/{owner}/{repo}"
                }

            return None

        except Exception as e:
            logger.error(f"Error parsing repository URL {url}: {e}")
            return None

    async def _get_release_info(self, owner: str, repo: str, version: str) -> Optional[Dict[str, Any]]:
        """Get release information from GitHub API"""
        try:
            if version == "latest":
                api_url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
                logger.info(f"Fetching latest release for {owner}/{repo}")
            else:
                api_url = f"https://api.github.com/repos/{owner}/{repo}/releases/tags/{version}"
                logger.info(f"Fetching release {version} for {owner}/{repo}")

            async with aiohttp.ClientSession() as session:
                async with session.get(api_url) as response:
                    logger.info(f"GitHub API response status: {response.status} for {api_url}")

                    if response.status == 404:
                        if version == "latest":
                            logger.warning(f"No releases found for repository {owner}/{repo}")
                        else:
                            logger.warning(f"Release {version} not found for repository {owner}/{repo}")
                        return None
                    elif response.status == 403:
                        logger.error(f"GitHub API rate limit exceeded or access forbidden for {owner}/{repo}")
                        return None
                    elif response.status != 200:
                        logger.error(f"GitHub API returned status {response.status} for {owner}/{repo}")
                        return None

                    response.raise_for_status()
                    release_data = await response.json()

                    logger.info(f"Found release: {release_data['tag_name']} published at {release_data['published_at']}")

                    # Find suitable asset (tar.gz or zip)
                    suitable_asset = None
                    available_assets = []

                    for asset in release_data.get('assets', []):
                        available_assets.append(asset['name'])
                        name = asset['name'].lower()
                        if name.endswith('.tar.gz') or name.endswith('.zip'):
                            suitable_asset = asset
                            break

                    if available_assets:
                        logger.info(f"Available assets: {available_assets}")
                    else:
                        logger.info("No assets found in release, will use source code archive")

                    if not suitable_asset:
                        # If no assets, try to use source code archive
                        logger.info(f"Using source code archive for {owner}/{repo}@{release_data['tag_name']}")
                        suitable_asset = {
                            'name': f"{repo}-{release_data['tag_name']}.tar.gz",
                            'browser_download_url': release_data['tarball_url'],
                            'content_type': 'application/gzip'
                        }
                    else:
                        logger.info(f"Using asset: {suitable_asset['name']}")

                    return {
                        'version': release_data['tag_name'],
                        'name': release_data['name'],
                        'description': release_data.get('body', ''),
                        'published_at': release_data['published_at'],
                        'asset': suitable_asset,
                        'repo_owner': owner,
                        'repo_name': repo
                    }

        except aiohttp.ClientError as e:
            logger.error(f"Network error getting release info for {owner}/{repo}@{version}: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error getting release info for {owner}/{repo}@{version}: {e}", exc_info=True)
            return None

    async def _download_and_extract(self, release_info: Dict[str, Any]) -> Dict[str, Any]:
        """Download and extract plugin archive"""
        download_dir = None
        try:
            asset = release_info['asset']
            download_url = asset['browser_download_url']
            filename = asset['name']

            logger.info(f"Starting download of {filename} from {download_url}")

            # Create temporary download directory
            download_dir = self.temp_dir / f"download_{release_info['repo_owner']}_{release_info['repo_name']}"
            download_dir.mkdir(exist_ok=True)

            # Download file
            file_path = download_dir / filename

            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(download_url) as response:
                        if response.status != 200:
                            logger.error(f"Download failed with status {response.status}: {response.reason}")
                            return {
                                'success': False,
                                'error': f'Download failed: HTTP {response.status} - {response.reason}'
                            }

                        response.raise_for_status()

                        # Get content length for progress tracking
                        content_length = response.headers.get('content-length')
                        if content_length:
                            logger.info(f"Downloading {filename} ({content_length} bytes)")

                        async with aiofiles.open(file_path, 'wb') as f:
                            downloaded = 0
                            async for chunk in response.content.iter_chunked(8192):
                                await f.write(chunk)
                                downloaded += len(chunk)

                        logger.info(f"Downloaded {filename} ({downloaded} bytes) to {file_path}")

            except aiohttp.ClientError as e:
                logger.error(f"Network error during download: {e}")
                return {'success': False, 'error': f'Network error during download: {str(e)}'}
            except Exception as e:
                logger.error(f"Error during file download: {e}")
                return {'success': False, 'error': f'Download failed: {str(e)}'}

            # Verify file was downloaded
            if not file_path.exists() or file_path.stat().st_size == 0:
                logger.error(f"Downloaded file is missing or empty: {file_path}")
                return {'success': False, 'error': 'Downloaded file is missing or empty'}

            # Extract archive
            extract_dir = download_dir / "extracted"
            extract_dir.mkdir(exist_ok=True)

            logger.info(f"Extracting {filename} to {extract_dir}")

            try:
                if filename.endswith('.tar.gz') or filename.endswith('.tgz'):
                    with tarfile.open(file_path, 'r:gz') as tar:
                        tar.extractall(extract_dir)
                        logger.info(f"Successfully extracted tar.gz archive")
                elif filename.endswith('.zip'):
                    with zipfile.ZipFile(file_path, 'r') as zip_file:
                        zip_file.extractall(extract_dir)
                        logger.info(f"Successfully extracted zip archive")
                else:
                    logger.error(f"Unsupported archive format: {filename}")
                    return {'success': False, 'error': f'Unsupported archive format: {filename}'}
            except tarfile.TarError as e:
                logger.error(f"Error extracting tar archive: {e}")
                return {'success': False, 'error': f'Failed to extract tar archive: {str(e)}'}
            except zipfile.BadZipFile as e:
                logger.error(f"Error extracting zip archive: {e}")
                return {'success': False, 'error': f'Failed to extract zip archive: {str(e)}'}
            except Exception as e:
                logger.error(f"Error during archive extraction: {e}")
                return {'success': False, 'error': f'Archive extraction failed: {str(e)}'}

            # Find the actual plugin directory (may be nested)
            plugin_dir = self._find_plugin_directory(extract_dir)
            if not plugin_dir:
                # List contents for debugging
                try:
                    contents = list(extract_dir.rglob('*'))
                    logger.error(f"Could not find plugin directory. Archive contents: {[str(p.relative_to(extract_dir)) for p in contents[:10]]}")
                except Exception:
                    logger.error("Could not find plugin directory and failed to list archive contents")

                return {
                    'success': False,
                    'error': 'Could not find plugin directory in archive. Archive may not contain a valid BrainDrive plugin.'
                }

            logger.info(f"Found plugin directory: {plugin_dir}")

            return {
                'success': True,
                'extracted_path': plugin_dir,
                'download_dir': download_dir
            }

        except Exception as e:
            logger.error(f"Unexpected error downloading and extracting plugin: {e}", exc_info=True)
            # Clean up on error
            if download_dir and download_dir.exists():
                try:
                    shutil.rmtree(download_dir)
                except Exception as cleanup_error:
                    logger.error(f"Failed to cleanup download directory: {cleanup_error}")

            return {'success': False, 'error': f'Download and extraction failed: {str(e)}'}

    async def _extract_local_file(self, file_path: Path, filename: str) -> Dict[str, Any]:
        """Extract a local plugin file"""
        extract_dir = None
        try:
            logger.info(f"Extracting local file {filename} from {file_path}")

            # Create temporary extraction directory
            extract_dir = self.temp_dir / f"local_extract_{filename}_{Path(file_path).stem}"
            extract_dir.mkdir(exist_ok=True)

            # Extract archive based on file extension
            try:
                if filename.lower().endswith(('.tar.gz', '.tgz')):
                    with tarfile.open(file_path, 'r:gz') as tar:
                        tar.extractall(extract_dir)
                        logger.info(f"Successfully extracted tar.gz archive")
                elif filename.lower().endswith('.zip'):
                    with zipfile.ZipFile(file_path, 'r') as zip_file:
                        zip_file.extractall(extract_dir)
                        logger.info(f"Successfully extracted zip archive")
                elif filename.lower().endswith('.rar'):
                    # Note: RAR extraction requires additional library (rarfile)
                    # For now, return an error for RAR files
                    logger.error(f"RAR extraction not yet implemented")
                    return {'success': False, 'error': 'RAR file extraction is not yet supported. Please use ZIP or TAR.GZ format.'}
                else:
                    logger.error(f"Unsupported archive format: {filename}")
                    return {'success': False, 'error': f'Unsupported archive format: {filename}. Supported formats: ZIP, TAR.GZ'}
            except tarfile.TarError as e:
                logger.error(f"Error extracting tar archive: {e}")
                return {'success': False, 'error': f'Failed to extract tar archive: {str(e)}'}
            except zipfile.BadZipFile as e:
                logger.error(f"Error extracting zip archive: {e}")
                return {'success': False, 'error': f'Failed to extract zip archive: {str(e)}'}
            except Exception as e:
                logger.error(f"Error during archive extraction: {e}")
                return {'success': False, 'error': f'Archive extraction failed: {str(e)}'}

            # Find the actual plugin directory (may be nested)
            plugin_dir = self._find_plugin_directory(extract_dir)
            if not plugin_dir:
                # List contents for debugging
                try:
                    contents = list(extract_dir.rglob('*'))
                    logger.error(f"Could not find plugin directory. Archive contents: {[str(p.relative_to(extract_dir)) for p in contents[:10]]}")
                except Exception:
                    logger.error("Could not find plugin directory and failed to list archive contents")

                return {
                    'success': False,
                    'error': 'Could not find plugin directory in archive. Archive may not contain a valid BrainDrive plugin.'
                }

            logger.info(f"Found plugin directory: {plugin_dir}")

            return {
                'success': True,
                'extracted_path': plugin_dir,
                'extract_dir': extract_dir
            }

        except Exception as e:
            logger.error(f"Unexpected error extracting local file: {e}", exc_info=True)
            # Clean up on error
            if extract_dir and extract_dir.exists():
                try:
                    shutil.rmtree(extract_dir)
                except Exception as cleanup_error:
                    logger.error(f"Failed to cleanup extraction directory: {cleanup_error}")

            return {'success': False, 'error': f'File extraction failed: {str(e)}'}

    def _find_plugin_directory(self, extract_dir: Path) -> Optional[Path]:
        """Find the actual plugin directory within extracted archive"""
        # Look for directory containing lifecycle_manager.py or package.json
        import os
        for root, dirs, files in os.walk(extract_dir):
            root_path = Path(root)
            if ('lifecycle_manager.py' in files or 'package.json' in files):
                return root_path

        # If not found, check if extract_dir itself contains plugin files
        if (extract_dir / 'lifecycle_manager.py').exists() or (extract_dir / 'package.json').exists():
            return extract_dir

        # Check first subdirectory
        subdirs = [d for d in extract_dir.iterdir() if d.is_dir()]
        if len(subdirs) == 1:
            subdir = subdirs[0]
            if (subdir / 'lifecycle_manager.py').exists() or (subdir / 'package.json').exists():
                return subdir

        return None

    async def _validate_plugin_structure(self, plugin_dir: Path) -> Dict[str, Any]:
        """Validate that the downloaded plugin has the required structure"""
        try:
            # Check for required files
            required_files = ['lifecycle_manager.py']
            missing_files = []

            for file_name in required_files:
                if not (plugin_dir / file_name).exists():
                    missing_files.append(file_name)

            if missing_files:
                return {
                    'valid': False,
                    'error': f'Missing required files: {", ".join(missing_files)}'
                }

            # Try to load plugin metadata
            plugin_info = {}
            service_runtime = []

            # Check package.json
            package_json_path = plugin_dir / 'package.json'
            if package_json_path.exists():
                try:
                    with open(package_json_path, 'r') as f:
                        package_data = json.load(f)
                        plugin_info.update({
                            'name': package_data.get('name', 'Unknown Plugin'),
                            'version': package_data.get('version', '1.0.0'),
                            'description': package_data.get('description', ''),
                            'author': package_data.get('author', 'Unknown')
                        })
                except Exception as e:
                    logger.warning(f"Could not parse package.json: {e}")

            # Try to import lifecycle manager to validate it
            try:
                import importlib.util
                spec = importlib.util.spec_from_file_location(
                    "temp_lifecycle_manager",
                    plugin_dir / "lifecycle_manager.py"
                )
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                # Find lifecycle manager class
                manager_class = None
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if (isinstance(attr, type) and
                        attr_name.endswith('LifecycleManager') and
                        attr_name != 'LifecycleManager'):
                        manager_class = attr
                        break

                if not manager_class:
                    return {
                        'valid': False,
                        'error': 'No valid lifecycle manager class found'
                    }

                # Try to instantiate to check for basic errors
                # Some lifecycle managers require initialization parameters
                try:
                    # First try without arguments (for simple managers)
                    manager_instance = manager_class()
                except TypeError as te:
                    # If that fails, try with common initialization parameters
                    try:
                        # Try with plugins_base_dir parameter (common for new architecture)
                        # Use the parent directory of the plugin directory as plugins_base_dir
                        plugins_base_dir = str(plugin_dir.parent)
                        manager_instance = manager_class(plugins_base_dir=plugins_base_dir)
                        logger.info(f"Successfully instantiated lifecycle manager with plugins_base_dir: {plugins_base_dir}")
                    except Exception as plugins_dir_error:
                        logger.warning(f"Failed with plugins_base_dir: {plugins_dir_error}")
                        try:
                            # Try with positional arguments for BaseLifecycleManager
                            from pathlib import Path
                            temp_shared_path = plugin_dir / "shared" / "temp" / "v1.0.0"
                            manager_instance = manager_class(
                                plugin_slug="temp_plugin",
                                version="1.0.0",
                                shared_storage_path=temp_shared_path
                            )
                            logger.info(f"Successfully instantiated lifecycle manager with BaseLifecycleManager parameters")
                        except Exception as base_error:
                            logger.warning(f"Failed with BaseLifecycleManager parameters: {base_error}")
                            try:
                                # Try with None parameter (some managers accept None)
                                manager_instance = manager_class(plugins_base_dir=None)
                                logger.info(f"Successfully instantiated lifecycle manager with None parameter")
                            except Exception as init_error:
                                logger.warning(f"Could not instantiate lifecycle manager for validation: {init_error}")
                                # If we can't instantiate it, we'll skip the PLUGIN_DATA check
                                # but still consider the class valid since it exists and has the right name
                                manager_instance = None

                # Try to get plugin data if instance was created successfully
                if manager_instance:
                    if hasattr(manager_instance, 'PLUGIN_DATA'):
                        plugin_info.update(manager_instance.PLUGIN_DATA)
                    elif hasattr(manager_instance, 'plugin_data'):
                        plugin_info.update(manager_instance.plugin_data)
                    # Try to get metadata if available
                    try:
                        if hasattr(manager_instance, 'get_plugin_metadata'):
                            import asyncio
                            metadata = asyncio.run(manager_instance.get_plugin_metadata())
                            if metadata:
                                plugin_info.update(metadata)
                    except Exception as metadata_error:
                        logger.warning(f"Could not get plugin metadata: {metadata_error}")
                else:
                    # If we couldn't instantiate the manager, try to extract plugin_slug from source code
                    logger.info("Attempting to extract plugin_slug from lifecycle manager source code")
                    try:
                        lifecycle_manager_path = plugin_dir / "lifecycle_manager.py"
                        if lifecycle_manager_path.exists():
                            with open(lifecycle_manager_path, 'r') as f:
                                content = f.read()
                                # Look for plugin_slug in the source code
                                import re
                                # Look for "plugin_slug": "value" pattern
                                slug_match = re.search(r'"plugin_slug":\s*"([^"]+)"', content)
                                if slug_match:
                                    extracted_slug = slug_match.group(1)
                                    plugin_info['plugin_slug'] = extracted_slug
                                    logger.info(f"Extracted plugin_slug from source: {extracted_slug}")

                                # Extract services using the dedicated function
                                services = extract_required_services_runtime(content, plugin_info.get('plugin_slug'))
                                if services:
                                    plugin_info['required_services_runtime'] = services
                                    service_runtime.extend(services)
                    except Exception as extract_error:
                        logger.warning(f"Could not extract plugin_slug from source: {extract_error}")

            except Exception as e:
                return {
                    'valid': False,
                    'error': f'Invalid lifecycle manager: {e}'
                }

            return {
                'valid': True,
                'plugin_info': plugin_info,
                'service_runtime': service_runtime
            }

        except Exception as e:
            logger.error(f"Error validating plugin structure: {e}")
            return {'valid': False, 'error': str(e)}

    async def _install_plugin_locally(self, plugin_source_dir: Path, plugin_info: Dict[str, Any], user_id: str) -> Dict[str, Any]:
        """Install the downloaded plugin using the universal lifecycle system"""
        temp_plugin_dir = None
        try:
            # Determine plugin slug
            # First try to get plugin_slug from the lifecycle manager metadata
            plugin_slug = plugin_info.get('plugin_slug')

            # If not available, use the name from package.json but preserve casing for proper plugin names
            if not plugin_slug:
                name = plugin_info.get('name', '')
                # Only convert to lowercase if it contains spaces or special characters
                if ' ' in name or '-' in name:
                    plugin_slug = name.lower().replace(' ', '-')
                else:
                    # Preserve the original casing for single-word plugin names
                    plugin_slug = name

            logger.info(f"Installing plugin locally: {plugin_slug} for user {user_id}")

            # Create temporary plugin directory in the plugins folder with the exact plugin slug
            temp_plugin_dir = self.plugins_base_dir / plugin_slug

            # Copy plugin files to temporary location
            if temp_plugin_dir.exists():
                logger.info(f"Removing existing temporary directory: {temp_plugin_dir}")
                shutil.rmtree(temp_plugin_dir)

            logger.info(f"Copying plugin files from {plugin_source_dir} to {temp_plugin_dir}")
            shutil.copytree(plugin_source_dir, temp_plugin_dir)

            try:
                # Import and use the universal lifecycle manager
                from .lifecycle_api import UniversalPluginLifecycleManager

                # Create universal manager with updated plugins directory
                universal_manager = UniversalPluginLifecycleManager(str(self.plugins_base_dir))
                logger.info(f"Created universal lifecycle manager for plugin installation")

                # Install plugin for user
                from app.core.database import get_db
                async for db in get_db():
                    logger.info(f"Calling universal manager install_plugin for {plugin_slug}")
                    result = await universal_manager.install_plugin(plugin_slug, user_id, db)
                    logger.info(f"Universal manager install result: {result}")
                    break

                # Check if installation was successful
                if not result.get('success', False):
                    error_msg = result.get('error', 'Unknown installation error')
                    logger.error(f"Plugin installation failed: {error_msg}")
                    return {
                        'success': False,
                        'error': f'Plugin installation failed: {error_msg}',
                        'details': {
                            'plugin_slug': plugin_slug,
                            'user_id': user_id,
                            'step': 'lifecycle_manager_install'
                        }
                    }

                logger.info(f"Plugin {plugin_slug} installed successfully for user {user_id}")

                # Ensure plugin name and slug are included in the result for frontend display
                if not result.get('plugin_name') or not result.get('plugin_slug'):
                    # Try to get plugin name from the lifecycle manager
                    try:
                        manager = universal_manager._load_plugin_manager(plugin_slug)
                        if hasattr(manager, 'plugin_data'):
                            result['plugin_name'] = manager.plugin_data.get('name', plugin_slug)
                            result['plugin_slug'] = manager.plugin_data.get('plugin_slug', plugin_slug)
                        elif hasattr(manager, 'PLUGIN_DATA'):
                            result['plugin_name'] = manager.PLUGIN_DATA.get('name', plugin_slug)
                            result['plugin_slug'] = manager.PLUGIN_DATA.get('plugin_slug', plugin_slug)
                        else:
                            result['plugin_name'] = plugin_slug
                            result['plugin_slug'] = plugin_slug
                    except Exception as e:
                        logger.warning(f"Could not get plugin name from manager: {e}")
                        result['plugin_name'] = plugin_slug
                        result['plugin_slug'] = plugin_slug

                return result

            except Exception as e:
                logger.error(f"Error during plugin installation step: {e}", exc_info=True)
                return {
                    'success': False,
                    'error': f'Installation process failed: {str(e)}',
                    'details': {
                        'plugin_slug': plugin_slug,
                        'user_id': user_id,
                        'step': 'lifecycle_manager_execution',
                        'exception_type': type(e).__name__
                    }
                }
            finally:
                # Always clean up temporary directory
                if temp_plugin_dir and temp_plugin_dir.exists():
                    logger.info(f"Cleaning up temporary directory: {temp_plugin_dir}")
                    shutil.rmtree(temp_plugin_dir)

        except Exception as e:
            logger.error(f"Error in plugin local installation setup: {e}", exc_info=True)
            # Clean up if we created temp directory but failed before the finally block
            if temp_plugin_dir and temp_plugin_dir.exists():
                try:
                    shutil.rmtree(temp_plugin_dir)
                except Exception as cleanup_error:
                    logger.error(f"Failed to cleanup temp directory: {cleanup_error}")

            return {
                'success': False,
                'error': f'Plugin installation setup failed: {str(e)}',
                'details': {
                    'step': 'setup_and_copy',
                    'exception_type': type(e).__name__
                }
            }

    async def _refresh_plugin_discovery(self, user_id: str):
        """Refresh plugin discovery to make newly installed plugins visible"""
        try:
            # Import the plugin manager from the routers module
            from app.routers.plugins import plugin_manager

            # Ensure plugin manager is initialized
            if not plugin_manager._initialized:
                await plugin_manager.initialize()

            # Discover plugins for the user
            await plugin_manager._discover_plugins(user_id=user_id)

            # Also refresh the plugin cache
            await plugin_manager.refresh_plugin_cache()

            logger.info(f"Refreshed plugin discovery and cache for user {user_id}")

        except Exception as e:
            logger.error(f"Error refreshing plugin discovery for user {user_id}: {e}")
            # Don't fail the installation if discovery fails

    async def _store_installation_metadata(self, user_id: str, plugin_id: str, repo_info: Dict[str, Any], release_info: Dict[str, Any]):
        """Store metadata about the remote installation"""
        try:
            metadata_dir = self.plugins_base_dir / user_id / ".metadata"
            metadata_dir.mkdir(parents=True, exist_ok=True)

            metadata_file = metadata_dir / f"{plugin_id}_remote.json"

            metadata = {
                'plugin_id': plugin_id,
                'user_id': user_id,
                'repo_owner': repo_info['owner'],
                'repo_name': repo_info['repo'],
                'repo_url': repo_info['url'],
                'version': release_info['version'],
                'installed_at': release_info['published_at'],
                'installation_type': 'remote',
                'source': 'github'
            }

            with open(metadata_file, 'w') as f:
                json.dump(metadata, f, indent=2)

            logger.info(f"Stored installation metadata for {plugin_id}")

        except Exception as e:
            logger.error(f"Error storing installation metadata: {e}")

    async def _get_installation_metadata(self, user_id: str, plugin_id: str) -> Optional[Dict[str, Any]]:
        """Get metadata about a remote installation"""
        try:
            metadata_file = self.plugins_base_dir / user_id / ".metadata" / f"{plugin_id}_remote.json"

            if not metadata_file.exists():
                return None

            with open(metadata_file, 'r') as f:
                return json.load(f)

        except Exception as e:
            logger.error(f"Error getting installation metadata: {e}")
            return None

    async def _store_local_file_metadata(self, user_id: str, plugin_id: str, filename: str, plugin_info: Dict[str, Any]):
        """Store metadata about a local file installation"""
        try:
            metadata_dir = self.plugins_base_dir / user_id / ".metadata"
            metadata_dir.mkdir(parents=True, exist_ok=True)

            metadata_file = metadata_dir / f"{plugin_id}_local.json"

            metadata = {
                'plugin_id': plugin_id,
                'user_id': user_id,
                'filename': filename,
                'plugin_info': plugin_info,
                'installed_at': str(Path().cwd()),  # Current timestamp would be better
                'installation_type': 'local',
                'source': 'local-file'
            }

            # Add current timestamp
            from datetime import datetime
            metadata['installed_at'] = datetime.utcnow().isoformat()

            with open(metadata_file, 'w') as f:
                json.dump(metadata, f, indent=2)

            logger.info(f"Stored local file installation metadata for {plugin_id}")

        except Exception as e:
            logger.error(f"Error storing local file installation metadata: {e}")

    async def _cleanup_temp_files(self, temp_path: Path):
        """Clean up temporary files and directories"""
        try:
            if temp_path.exists():
                if temp_path.is_dir():
                    shutil.rmtree(temp_path)
                else:
                    temp_path.unlink()
                logger.info(f"Cleaned up temporary files: {temp_path}")
        except Exception as e:
            logger.error(f"Error cleaning up temporary files: {e}")

    async def list_available_updates(self, user_id: str) -> List[Dict[str, Any]]:
        """List available updates for installed remote plugins"""
        try:
            updates = []
            metadata_dir = self.plugins_base_dir / user_id / ".metadata"

            if not metadata_dir.exists():
                return updates

            for metadata_file in metadata_dir.glob("*_remote.json"):
                try:
                    with open(metadata_file, 'r') as f:
                        metadata = json.load(f)

                    # Check for newer version
                    latest_release = await self._get_release_info(
                        metadata['repo_owner'],
                        metadata['repo_name'],
                        "latest"
                    )

                    if latest_release and latest_release['version'] != metadata['version']:
                        updates.append({
                            'plugin_id': metadata['plugin_id'],
                            'current_version': metadata['version'],
                            'latest_version': latest_release['version'],
                            'repo_url': metadata['repo_url']
                        })

                except Exception as e:
                    logger.error(f"Error checking updates for {metadata_file}: {e}")

            return updates

        except Exception as e:
            logger.error(f"Error listing available updates: {e}")
            return []


# Integration with universal lifecycle API
async def install_plugin_from_url(repo_url: str, user_id: str, version: str = "latest") -> Dict[str, Any]:
    """
    Convenience function to install a plugin from a repository URL

    Args:
        repo_url: GitHub repository URL
        user_id: User ID to install plugin for
        version: Version to install

    Returns:
        Dict with installation result
    """
    installer = RemotePluginInstaller()
    return await installer.install_from_url(repo_url, user_id, version)


if __name__ == "__main__":
    import sys

    async def main():
        if len(sys.argv) < 4:
            print("Usage: python remote_installer.py <repo_url> <user_id> [version]")
            print("Example: python remote_installer.py https://github.com/user/plugin user123 latest")
            sys.exit(1)

        repo_url = sys.argv[1]
        user_id = sys.argv[2]
        version = sys.argv[3] if len(sys.argv) > 3 else "latest"

        print(f"Remote Plugin Installer")
        print(f"Repository: {repo_url}")
        print(f"User ID: {user_id}")
        print(f"Version: {version}")

        result = await install_plugin_from_url(repo_url, user_id, version)

        if result['success']:
            print("✅ Plugin installed successfully!")
            print(f"Plugin ID: {result.get('plugin_id')}")
        else:
            print("❌ Plugin installation failed!")
            print(f"Error: {result['error']}")

    asyncio.run(main())
