"""
Registry for user initializer plugins.

This module provides functions to register and manage user initializer plugins.
"""

import logging
import importlib
import pkgutil
from typing import Dict, List, Type, Optional, Any
from sqlalchemy.ext.asyncio import AsyncSession

from .base import UserInitializerBase

logger = logging.getLogger(__name__)

# Registry of initializer plugins
_initializers: Dict[str, Type[UserInitializerBase]] = {}


def register_initializer(initializer_class: Type[UserInitializerBase]) -> None:
    """
    Register a user initializer plugin.

    Args:
        initializer_class: The initializer class to register
    """
    name = initializer_class.name
    if name in _initializers:
        logger.warning(f"Initializer '{name}' already registered. Overwriting.")

    _initializers[name] = initializer_class
    logger.info(f"Registered initializer: {name}")


def get_initializers() -> Dict[str, Type[UserInitializerBase]]:
    """
    Get all registered initializer plugins.

    Returns:
        Dict[str, Type[UserInitializerBase]]: Dictionary of initializer name to class
    """
    return _initializers


def _sort_initializers() -> List[Type[UserInitializerBase]]:
    """
    Sort initializers by priority and dependencies.

    Returns:
        List[Type[UserInitializerBase]]: Sorted list of initializer classes

    Raises:
        ValueError: If a circular dependency is detected.
    """
    # Convert to list of (name, class) tuples
    initializers = list(_initializers.items())

    # Sort by priority (higher priority first)
    initializers.sort(key=lambda x: x[1].priority, reverse=True)

    sorted_initializers: List[Type[UserInitializerBase]] = []
    visit_state: Dict[str, str] = {}
    visit_stack: List[str] = []

    def add_initializer(name: str) -> None:
        state = visit_state.get(name)
        if state == "visited":
            return

        if state == "visiting":
            cycle_start = visit_stack.index(name) if name in visit_stack else 0
            cycle_path = visit_stack[cycle_start:] + [name]
            cycle_text = " -> ".join(cycle_path)
            raise ValueError(f"Circular initializer dependency detected: {cycle_text}")

        visit_state[name] = "visiting"
        visit_stack.append(name)
        initializer_class = _initializers[name]

        for dep in initializer_class.dependencies:
            if dep not in _initializers:
                logger.warning(
                    "Initializer '%s' depends on unknown initializer '%s'; ignoring dependency",
                    name,
                    dep,
                )
                continue
            add_initializer(dep)

        visit_stack.pop()
        visit_state[name] = "visited"
        sorted_initializers.append(initializer_class)

    for name, _initializer_class in initializers:
        add_initializer(name)

    return sorted_initializers


async def initialize_user_data(user_id: str, db: AsyncSession, **kwargs) -> bool:
    """
    Initialize data for a new user using all registered initializers.

    Args:
        user_id: The ID of the newly registered user
        db: Database session
        **kwargs: Additional arguments to pass to initializers

    Returns:
        bool: True if all initializers succeeded, False otherwise
    """
    logger.info(f"Initializing data for user {user_id}")

    # Check if there are any initializers registered
    if not _initializers:
        logger.warning("No initializers are registered! Initialization will be skipped.")
        return True

    # Track which initializers have been run successfully
    successful_initializers: List[UserInitializerBase] = []

    try:
        # Sort initializers by priority and dependencies
        sorted_initializers = _sort_initializers()
        logger.info(f"Found {len(sorted_initializers)} initializers to run")

        # Run each initializer
        for initializer_class in sorted_initializers:
            initializer = initializer_class()
            logger.info(f"Running initializer: {initializer.name}")

            success = await initializer.initialize(user_id, db, **kwargs)
            if success:
                successful_initializers.append(initializer)
                logger.info(f"Initializer {initializer.name} completed successfully")
            else:
                logger.error(f"Initializer {initializer.name} failed")
                raise Exception(f"Initializer {initializer.name} failed")

        logger.info(f"All initializers completed successfully for user {user_id}")
        return True

    except Exception as e:
        logger.error(f"Error during user initialization: {e}")

        # Run cleanup for successful initializers in reverse order
        if successful_initializers:
            logger.info("Rolling back and cleaning up successful initializers")
        for initializer in reversed(successful_initializers):
            try:
                await initializer.cleanup(user_id, db, **kwargs)
            except Exception as cleanup_error:
                logger.error(f"Error during cleanup of {initializer.name}: {cleanup_error}")

        return False


def discover_initializers(package_name: str = "app.core.user_initializer.initializers") -> None:
    """
    Discover and import initializer plugins from a package.

    Args:
        package_name: The package to search for initializers
    """
    try:
        logger.info(f"Attempting to import package: {package_name}")
        package = importlib.import_module(package_name)
        logger.info(f"Successfully imported package: {package_name}")

        # List all modules in the package
        modules = list(pkgutil.iter_modules(package.__path__, package.__name__ + "."))
        logger.info(f"Found {len(modules)} modules in package {package_name}: {[name for _, name, _ in modules]}")

        for _, name, is_pkg in modules:
            try:
                # Import the module
                logger.info(f"Attempting to import module: {name}")
                importlib.import_module(name)
                logger.info(f"Successfully imported initializer module: {name}")

                # If it's a package, recursively discover initializers
                if is_pkg:
                    discover_initializers(name)
            except Exception as e:
                logger.error(f"Error importing initializer module {name}: {e}")
    except Exception as e:
        logger.error(f"Error discovering initializers in {package_name}: {e}")
