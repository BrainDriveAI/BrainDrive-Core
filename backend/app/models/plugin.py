from sqlalchemy import Column, String, Integer, Boolean, ForeignKey, Text, TIMESTAMP, DateTime
import sqlalchemy
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from datetime import datetime, UTC
import json

from app.models.base import Base


class Plugin(Base):
    """SQLAlchemy model for plugins."""
    
    id = Column(String(32), primary_key=True, index=True)
    # The plugin_slug stores the original plugin identifier used for file paths
    plugin_slug = Column(String, nullable=False)
    name = Column(String, nullable=False)
    description = Column(String, nullable=False)
    version = Column(String, nullable=False)
    type = Column(String, default="frontend")
    plugin_type = Column(String, default="frontend")
    enabled = Column(Boolean, default=True)
    icon = Column(String)
    category = Column(String)
    status = Column(String, default="activated")
    official = Column(Boolean, default=True)
    author = Column(String, default="BrainDrive Team")
    last_updated = Column(String)
    compatibility = Column(String, default="1.0.0")
    downloads = Column(Integer, default=0)
    scope = Column(String)
    bundle_method = Column(String)
    bundle_location = Column(String)
    is_local = Column(Boolean, default=False)
    long_description = Column(Text)
    
    # Update tracking fields
    source_type = Column(String(50), nullable=True)  # github, gitlab, npm, custom, local
    source_url = Column(Text, nullable=True)  # Original repository/source URL
    update_check_url = Column(Text, nullable=True)  # Specific API endpoint for checking updates
    last_update_check = Column(TIMESTAMP, nullable=True)  # When we last checked for updates
    update_available = Column(Boolean, default=False)  # Cached flag indicating if update is available
    latest_version = Column(String(50), nullable=True)  # Latest available version (cached)
    installation_type = Column(String(20), default='local')  # Installation type: local or remote
    permissions = Column(Text, nullable=True)  # JSON array of required permissions

    # JSON fields
    config_fields = Column(Text)  # Stored as JSON string
    messages = Column(Text)       # Stored as JSON string
    dependencies = Column(Text)   # Stored as JSON string
    required_services_runtime = Column(Text, nullable=True)
    endpoints_file = Column(String, nullable=True)
    route_prefix = Column(String, nullable=True)
    backend_dependencies = Column(Text, nullable=True)
    
    # Timestamps
    created_at = Column(String, default=func.now())
    updated_at = Column(String, default=func.now(), onupdate=func.now())
    
    # User relationship
    user_id = Column(String(32), ForeignKey("users.id", name="fk_plugin_user_id"), nullable=False)
    user = relationship("User", back_populates="plugins")
    
    # Add a unique constraint for user_id + plugin_slug
    __table_args__ = (
        sqlalchemy.UniqueConstraint('user_id', 'plugin_slug', name='unique_plugin_per_user'),
    )
    
    # Relationships
    modules = relationship("Module", back_populates="plugin", cascade="all, delete-orphan")
    service_runtimes = relationship("PluginServiceRuntime", back_populates="plugin", cascade="all, delete-orphan")
    
    def to_dict(self):
        """Convert model to dictionary."""
        import json

        def _deserialize_json(raw_value, default):
            if raw_value is None:
                return default
            if isinstance(raw_value, (dict, list)):
                return raw_value
            try:
                return json.loads(raw_value)
            except (TypeError, json.JSONDecodeError):
                return default

        result = {
            "id": self.id,
            "plugin_slug": self.plugin_slug,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "type": self.type,
            "pluginType": self.plugin_type,
            "enabled": self.enabled,
            "icon": self.icon,
            "category": self.category,
            "status": self.status,
            "official": self.official,
            "author": self.author,
            "lastUpdated": self.last_updated,
            "compatibility": self.compatibility,
            "downloads": self.downloads,
            "scope": self.scope,
            "bundlemethod": self.bundle_method,
            "bundlelocation": self.bundle_location,
            "islocal": self.is_local,
            "longDescription": self.long_description,
            "userId": self.user_id,
            # Update tracking fields
            "sourceType": self.source_type,
            "sourceUrl": self.source_url,
            "updateCheckUrl": self.update_check_url,
            "lastUpdateCheck": self.last_update_check.isoformat() if self.last_update_check else None,
            "updateAvailable": self.update_available,
            "latestVersion": self.latest_version,
            "installationType": self.installation_type,
            "endpointsFile": self.endpoints_file,
            "routePrefix": self.route_prefix,
        }
        
        # Deserialize JSON fields
        result["configFields"] = _deserialize_json(self.config_fields, {})
            
        result["messages"] = _deserialize_json(self.messages, {})
            
        result["dependencies"] = _deserialize_json(self.dependencies, [])
            
        result["permissions"] = _deserialize_json(self.permissions, [])

        result["requiredServicesRuntime"] = _deserialize_json(self.required_services_runtime, [])
        result["backendDependencies"] = _deserialize_json(self.backend_dependencies, [])

        return result
    
    @classmethod
    def from_dict(cls, data):
        """Create model from dictionary."""
        import json
        
        # Convert camelCase to snake_case for database fields
        field_mapping = {
            "lastUpdated": "last_updated",
            "bundlemethod": "bundle_method",
            "bundlelocation": "bundle_location",
            "islocal": "is_local",
            "longDescription": "long_description",
            "configFields": "config_fields",
            "userId": "user_id",
            "pluginSlug": "plugin_slug",
            "sourceType": "source_type",
            "sourceUrl": "source_url",
            "updateCheckUrl": "update_check_url",
            "lastUpdateCheck": "last_update_check",
            "updateAvailable": "update_available",
            "latestVersion": "latest_version",
            "installationType": "installation_type",
            "pluginType": "plugin_type",
            "endpointsFile": "endpoints_file",
            "routePrefix": "route_prefix",
            "backendDependencies": "backend_dependencies",
            "requiredServicesRuntime": "required_services_runtime",
        }
        
        # Create a new dictionary with snake_case keys
        db_data = {}
        for key, value in data.items():
            if key in field_mapping:
                db_key = field_mapping[key]
            else:
                # Convert camelCase to snake_case
                db_key = ''.join(['_' + c.lower() if c.isupper() else c for c in key]).lstrip('_')
            
            # Handle special fields
            if db_key in [
                "config_fields",
                "messages",
                "dependencies",
                "permissions",
                "backend_dependencies",
                "required_services_runtime",
            ] and value is not None and not isinstance(value, str):
                db_data[db_key] = json.dumps(value)
            else:
                db_data[db_key] = value
        
        # Remove modules from data as they are handled separately
        if "modules" in db_data:
            db_data.pop("modules")

        # Handle service runtimes (only store names in plugin table)
        runtime_value = db_data.get("required_services_runtime")
        if isinstance(runtime_value, list) and runtime_value and isinstance(runtime_value[0], dict):
            db_data["required_services_runtime"] = json.dumps(
                [entry["name"] for entry in runtime_value if isinstance(entry, dict) and entry.get("name")]
            )

        return cls(**db_data)


class PluginServiceRuntime(Base):
    """SQLAlchemy model for plugin service runtimes."""

    __tablename__ = "plugin_service_runtime"

    id = Column(String, primary_key=True, index=True)
    plugin_id = Column(String, ForeignKey("plugin.id"), nullable=False, index=True)
    plugin_slug = Column(String, nullable=False, index=True)

    name = Column(String, nullable=False)
    source_url = Column(String)
    type = Column(String)
    install_command = Column(Text)
    start_command = Column(Text)
    stop_command = Column(Text)
    restart_command = Column(Text)
    healthcheck_url = Column(String)
    definition_id = Column(String)
    required_env_vars = Column(Text)  # store as JSON string
    runtime_dir_key = Column(String)
    env_inherit = Column(String)
    env_overrides = Column(Text)  # store as JSON string
    status = Column(String, default="pending")

    created_at = Column(DateTime, default=datetime.now(UTC))
    updated_at = Column(DateTime, default=datetime.now(UTC), onupdate=datetime.now(UTC))

    user_id = Column(String(32), ForeignKey("users.id", name="fk_plugin_service_runtime_user_id"), nullable=False)
    user = relationship("User")

    # Relationship back to plugin
    plugin = relationship("Plugin", back_populates="service_runtimes")

    def to_dict(self):
        """
        Convert the model instance to a dictionary, handling JSON fields.
        """
        return {
            "id": self.id,
            "plugin_id": self.plugin_id,
            "plugin_slug": self.plugin_slug,
            "name": self.name,
            "source_url": self.source_url,
            "type": self.type,
            "install_command": self.install_command,
            "start_command": self.start_command,
            "stop_command": self.stop_command,
            "restart_command": self.restart_command,
            "healthcheck_url": self.healthcheck_url,
            "definition_id": self.definition_id,
            "required_env_vars": json.loads(self.required_env_vars) if self.required_env_vars else [],
            "runtime_dir_key": self.runtime_dir_key,
            "env_inherit": self.env_inherit,
            "env_overrides": json.loads(self.env_overrides) if self.env_overrides else {},
            "status": self.status,
            "user_id": self.user_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict):
        """
        Create a new instance from a dictionary, serializing JSON fields.
        """
        db_data = data.copy()
        
        if "required_env_vars" in db_data and db_data["required_env_vars"] is not None:
            db_data["required_env_vars"] = json.dumps(db_data["required_env_vars"])

        if "env_overrides" in db_data and db_data["env_overrides"] is not None:
            db_data["env_overrides"] = json.dumps(db_data["env_overrides"])
            
        # Handle conversion from camelCase to snake_case if necessary
        # For simplicity, we are assuming keys in the incoming dict match model attributes
        
        return cls(**db_data)


class Module(Base):
    """SQLAlchemy model for plugin modules."""
    
    id = Column(String(32), primary_key=True, index=True)
    plugin_id = Column(String(32), ForeignKey("plugin.id", ondelete="CASCADE"), primary_key=True)
    name = Column(String, nullable=False)
    display_name = Column(String)
    description = Column(String)
    icon = Column(String)
    category = Column(String)
    enabled = Column(Boolean, default=True)
    priority = Column(Integer, default=0)
    
    # JSON fields
    props = Column(Text)              # Stored as JSON string
    config_fields = Column(Text)      # Stored as JSON string
    messages = Column(Text)           # Stored as JSON string
    required_services = Column(Text)  # Stored as JSON string
    dependencies = Column(Text)       # Stored as JSON string
    layout = Column(Text)             # Stored as JSON string
    tags = Column(Text)               # Stored as JSON string
    
    # Timestamps
    created_at = Column(String, default=func.now())
    updated_at = Column(String, default=func.now(), onupdate=func.now())
    
    # User relationship
    user_id = Column(String(32), ForeignKey("users.id", name="fk_module_user_id"), nullable=False)
    user = relationship("User", back_populates="modules")
    
    # Relationships
    plugin = relationship("Plugin", back_populates="modules")
    
    def to_dict(self):
        """Convert model to dictionary."""
        import json
        
        result = {
            "id": self.id,
            "pluginId": self.plugin_id,
            "name": self.name,
            "displayName": self.display_name,
            "description": self.description,
            "icon": self.icon,
            "category": self.category,
            "enabled": self.enabled,
            "priority": self.priority,
            "userId": self.user_id,
        }
        
        # Deserialize JSON fields
        for field, attr in [
            ("props", self.props),
            ("configFields", self.config_fields),
            ("requiredServices", self.required_services),
            ("layout", self.layout)
        ]:
            if attr:
                result[field] = json.loads(attr)
            else:
                result[field] = {}
                
        for field, attr in [
            ("dependencies", self.dependencies),
            ("tags", self.tags)
        ]:
            if attr:
                result[field] = json.loads(attr)
            else:
                result[field] = []
                
        if self.messages:
            result["messages"] = json.loads(self.messages)
        else:
            result["messages"] = {"sends": [], "receives": []}
            
        return result
    
    @classmethod
    def from_dict(cls, data, plugin_id):
        """Create model from dictionary."""
        import json
        
        # Convert camelCase to snake_case for database fields
        field_mapping = {
            "displayName": "display_name",
            "configFields": "config_fields",
            "requiredServices": "required_services",
            "userId": "user_id",
        }
        
        # Create a new dictionary with snake_case keys
        db_data = {"plugin_id": plugin_id}
        for key, value in data.items():
            if key in field_mapping:
                db_key = field_mapping[key]
            else:
                # Convert camelCase to snake_case
                db_key = ''.join(['_' + c.lower() if c.isupper() else c for c in key]).lstrip('_')
            
            # Handle JSON fields
            if db_key in ["props", "config_fields", "messages", "required_services",
                         "dependencies", "layout", "tags"] and value is not None:
                db_data[db_key] = json.dumps(value)
            else:
                db_data[db_key] = value
        
        # If user_id is not provided, try to extract it from the plugin_id
        if "user_id" not in db_data and "_" in plugin_id:
            # Plugin ID format is expected to be "{user_id}_{plugin_slug}"
            user_id, _ = plugin_id.split("_", 1)
            db_data["user_id"] = user_id
            
        return cls(**db_data)
