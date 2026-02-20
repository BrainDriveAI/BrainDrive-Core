"""
GitHub Plugin Initializer

This initializer installs plugins from GitHub repositories during user registration.
Uses the same installation function as the frontend for perfect consistency.
"""

import logging
from typing import Dict, Any, List
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.user_initializer.base import UserInitializerBase
from app.core.user_initializer.registry import register_initializer

logger = logging.getLogger(__name__)


class GitHubPluginInitializer(UserInitializerBase):
    """
    Initializer that installs plugins from GitHub repositories.

    Uses the same install_plugin_from_url() function as the frontend
    to ensure identical installation behavior and error handling.
    """

    name = "github_plugin_initializer"
    description = "Installs default plugins from GitHub repositories"
    priority = 400  # Run after core system setup but before pages
    dependencies = ["settings_initializer", "components_initializer", "navigation_initializer"]  # Run after core setup

    # Default plugins to install for new users.
    # Install order is intentional: Library depends on Chat being present.
    DEFAULT_PLUGINS = [
        {
            "key": "settings",
            "repo_url": "https://github.com/BrainDriveAI/BrainDrive-Settings-Plugin",
            "version": "latest",
            "name": "BrainDrive Settings",
            "required": True,
            "depends_on": [],
        },
        {
            "key": "chat",
            "repo_url": "https://github.com/BrainDriveAI/BrainDrive-Chat-Plugin",
            "version": "latest",
            "name": "BrainDrive Chat",
            "required": True,
            "depends_on": [],
        },
        {
            "key": "ollama",
            "repo_url": "https://github.com/BrainDriveAI/BrainDrive-Ollama-Plugin",
            "version": "latest",
            "name": "BrainDrive Ollama",
            "required": False,
            "depends_on": [],
        },
        {
            "key": "library",
            "repo_url": "https://github.com/DJJones66/BrainDrive-Library-Plugin",
            "version": "latest",
            "name": "BrainDrive Library",
            "required": True,
            "depends_on": ["chat"],
        },
    ]

    async def initialize(self, user_id: str, db: AsyncSession, **kwargs) -> bool:
        """
        Install default GitHub plugins for the new user.

        Args:
            user_id: The ID of the newly registered user
            db: Database session
            **kwargs: Additional arguments (unused)

        Returns:
            bool: True if all required plugins installed successfully, False otherwise
        """
        del kwargs
        logger.info("Starting GitHub plugin installation for user %s", user_id)

        try:
            from app.plugins.remote_installer import install_plugin_from_url
        except ImportError as exc:
            logger.error("Failed to import install_plugin_from_url: %s", exc)
            return False

        successful_installs: List[Dict[str, Any]] = []
        failed_installs: List[Dict[str, Any]] = []
        install_results: Dict[str, bool] = {}

        for plugin_config in self.DEFAULT_PLUGINS:
            key = str(plugin_config.get("key") or plugin_config.get("name") or "unknown").strip()
            repo_url = str(plugin_config["repo_url"])
            version = str(plugin_config.get("version") or "latest")
            name = str(plugin_config.get("name") or key)
            required = bool(plugin_config.get("required", True))
            depends_on = [str(dep).strip() for dep in plugin_config.get("depends_on", []) if str(dep).strip()]

            blocked_dependencies = [dep for dep in depends_on if not install_results.get(dep, False)]
            if blocked_dependencies:
                error_msg = f"Dependency not installed: {', '.join(blocked_dependencies)}"
                failure = {
                    "key": key,
                    "name": name,
                    "repo_url": repo_url,
                    "error": error_msg,
                    "required": required,
                    "dependency_blocked": True,
                    "depends_on": blocked_dependencies,
                }
                failed_installs.append(failure)
                install_results[key] = False

                if required:
                    logger.error(
                        "Skipping required plugin %s for user %s because dependency failed: %s",
                        name,
                        user_id,
                        blocked_dependencies,
                    )
                else:
                    logger.warning(
                        "Skipping optional plugin %s for user %s because dependency failed: %s",
                        name,
                        user_id,
                        blocked_dependencies,
                    )
                continue

            logger.info(
                "Installing plugin %s from %s (version=%s, required=%s) for user %s",
                name,
                repo_url,
                version,
                required,
                user_id,
            )

            try:
                result = await install_plugin_from_url(
                    repo_url=repo_url,
                    user_id=user_id,
                    version=version,
                )

                if result.get("success", False):
                    successful_installs.append(
                        {
                            "key": key,
                            "name": name,
                            "repo_url": repo_url,
                            "required": required,
                            "plugin_id": result.get("plugin_id"),
                            "plugin_slug": result.get("plugin_slug"),
                        }
                    )
                    install_results[key] = True
                    logger.info("Successfully installed %s for user %s", name, user_id)
                else:
                    error_msg = str(result.get("error") or "Unknown error")
                    failed_installs.append(
                        {
                            "key": key,
                            "name": name,
                            "repo_url": repo_url,
                            "required": required,
                            "error": error_msg,
                            "dependency_blocked": False,
                        }
                    )
                    install_results[key] = False
                    logger.error("Failed to install %s for user %s: %s", name, user_id, error_msg)

            except Exception as exc:
                failed_installs.append(
                    {
                        "key": key,
                        "name": name,
                        "repo_url": repo_url,
                        "required": required,
                        "error": str(exc),
                        "dependency_blocked": False,
                    }
                )
                install_results[key] = False
                logger.error("Exception installing %s for user %s: %s", name, user_id, exc)

        required_failures = [item for item in failed_installs if item.get("required", False)]
        optional_failures = [item for item in failed_installs if not item.get("required", False)]

        logger.info(
            "GitHub plugin installation complete for user %s: success=%s required_failures=%s optional_failures=%s",
            user_id,
            len(successful_installs),
            len(required_failures),
            len(optional_failures),
        )

        if successful_installs:
            logger.info("Successfully installed plugins: %s", [p["name"] for p in successful_installs])

        if failed_installs:
            logger.warning(
                "Failed plugin installs: %s",
                [{"name": p["name"], "required": p["required"], "error": p["error"]} for p in failed_installs],
            )

        if successful_installs:
            # New-user plugin installs happen after app startup; reload dynamic plugin routes
            # so backend/fullstack endpoints are available immediately without restart.
            try:
                from app.core.database import db_factory
                from app.plugins.route_loader import get_plugin_loader

                reload_result = None
                async with db_factory.session_factory() as reload_db:
                    reload_result = await get_plugin_loader().reload_routes(reload_db)

                logger.info(
                    "Reloaded plugin API routes after GitHub plugin initialization for user %s (loaded_plugins=%s mounted_routes=%s)",
                    user_id,
                    reload_result.get("loaded_plugins") if isinstance(reload_result, dict) else None,
                    reload_result.get("mounted_routes") if isinstance(reload_result, dict) else None,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to reload plugin API routes after GitHub plugin initialization for user %s: %s",
                    user_id,
                    exc,
                )

        # Required plugin failures should fail onboarding; optional failures should not.
        return len(required_failures) == 0

    async def cleanup(self, user_id: str, db: AsyncSession, **kwargs) -> bool:
        """
        Clean up any plugins that were installed if initialization fails.

        Args:
            user_id: The ID of the user
            db: Database session
            **kwargs: Additional arguments

        Returns:
            bool: True if cleanup was successful
        """
        del db, kwargs
        logger.info("Cleaning up GitHub plugins for user %s", user_id)

        try:
            # For now, we'll implement basic cleanup logging.
            # More sophisticated cleanup can be added later if needed.
            logger.info("GitHub plugin cleanup initiated for user %s", user_id)
            return True
        except Exception as exc:
            logger.error("Error during GitHub plugin cleanup for user %s: %s", user_id, exc)
            return False


# Register the initializer
register_initializer(GitHubPluginInitializer)
