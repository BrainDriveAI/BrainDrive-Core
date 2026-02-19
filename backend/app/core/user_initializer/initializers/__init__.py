"""
User initializer plugins.

This package contains plugins for initializing data for new users.
Each plugin should inherit from UserInitializerBase and register itself.
"""

# Import all initializers to ensure they are registered
from . import settings_initializer
from . import components_initializer
from . import navigation_initializer
from . import github_plugin_initializer  # GitHub plugin installer
from . import library_onboarding_initializer  # User library scaffold bootstrap
from . import pages_initializer  # Pages initializer (updated for BrainDriveChat)
# from . import brain_drive_basic_ai_chat_initializer  # Replaced by GitHub installer
# from . import brain_drive_settings_initializer  # Replaced by GitHub installer

# Add more imports as needed when new initializers are created
