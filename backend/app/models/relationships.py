"""
This module defines relationships between models after all models are loaded.
This prevents circular import issues by ensuring all model classes are defined
before establishing relationships between them.
"""
import uuid
from sqlalchemy.orm import relationship
# Remove unused PostgreSQL UUID import

# Import all models that will be involved in relationships
from app.models.user import User
from app.models.page import Page
from app.models.navigation import NavigationRoute
from app.models.conversation import Conversation
from app.models.tag import Tag
from app.models.plugin import Plugin, Module
from app.models.plugin_state import PluginState, PluginStateHistory, PluginStateConfig
from app.models.component import Component
from app.models.persona import Persona
from app.models.settings import SettingDefinition, SettingInstance
from app.models.message import Message
from app.models.role import Role
from app.models.tenant_models import Tenant, UserRole, TenantUser, RolePermission, Session, OAuthAccount
from app.models.job import Job, JobTypeDefinition


# Define User relationships
User.pages = relationship("Page", back_populates="creator", lazy="selectin", foreign_keys="Page.creator_id")
User.navigation_routes = relationship("NavigationRoute", back_populates="creator", lazy="selectin")
User.conversations = relationship("Conversation", back_populates="user", lazy="selectin")
User.tags = relationship("Tag", back_populates="user", lazy="selectin")
User.plugins = relationship("Plugin", back_populates="user", lazy="selectin")
User.modules = relationship("Module", back_populates="user", lazy="selectin")
User.components = relationship("Component", back_populates="user", lazy="selectin")
User.personas = relationship("Persona", back_populates="user", lazy="selectin")
User.plugin_states = relationship("PluginState", back_populates="user", lazy="selectin")
User.jobs = relationship("Job", back_populates="user", lazy="selectin")

# Define Page relationships
Page.creator = relationship("User", back_populates="pages")

# Define NavigationRoute relationships
NavigationRoute.creator = relationship("User", back_populates="navigation_routes")
NavigationRoute.pages = relationship("Page", foreign_keys="Page.navigation_route_id", back_populates="navigation_route")
NavigationRoute.default_page = relationship("Page", foreign_keys="NavigationRoute.default_page_id", backref="default_for_routes")

# Define Persona relationships
Persona.user = relationship("User", back_populates="personas")

# Job relationships
Tenant.jobs = relationship("Job", back_populates="workspace", lazy="selectin")
