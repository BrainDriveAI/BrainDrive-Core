from pydantic import BaseModel
from typing import List, Optional, Union, Dict, Any
from datetime import datetime
import uuid

# This schema is used for returning data from the repository.
# It ensures that JSON fields are correctly converted to Python types.
class PluginServiceRuntimeDTO(BaseModel):
    """
    A Pydantic model to represent a PluginServiceRuntime object,
    with required_env_vars as a list of strings.
    """
    id: str
    plugin_id: str
    plugin_slug: str
    name: str
    source_url: Optional[str] = None
    type: Optional[str] = None
    install_command: Optional[str] = None
    start_command: Optional[str] = None
    stop_command: Optional[str] = None
    restart_command: Optional[str] = None
    healthcheck_url: Optional[str] = None
    definition_id: Optional[str] = None
    required_env_vars: List[str] = []
    runtime_dir_key: Optional[str] = None
    env_inherit: Optional[str] = None
    env_overrides: Optional[Dict[str, Any]] = None
    status: Optional[str] = None
    user_id: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @classmethod
    def from_github_data(cls, service_dict: dict, plugin_id: str, plugin_slug: str, user_id: str) -> 'PluginServiceRuntimeDTO':
        """
        Create a PluginServiceRuntimeDTO from raw GitHub service data (dict).
        This handles first-time installation where database fields don't exist yet.
        """
        return cls(
            id=str(uuid.uuid4()),  # Generate new UUID for first install
            plugin_id=plugin_id,
            plugin_slug=plugin_slug,
            user_id=user_id,
            name=service_dict.get('name'),
            source_url=service_dict.get('source_url'),
            type=service_dict.get('type', 'python'),
            install_command=service_dict.get('install_command'),
            start_command=service_dict.get('start_command'),
            stop_command=service_dict.get('stop_command'),
            restart_command=service_dict.get('restart_command'),
            healthcheck_url=service_dict.get('healthcheck_url'),
            definition_id=service_dict.get('definition_id'),
            required_env_vars=service_dict.get('required_env_vars', []),
            runtime_dir_key=service_dict.get('runtime_dir_key'),
            env_inherit=service_dict.get('env_inherit'),
            env_overrides=service_dict.get('env_overrides'),
            status='installing',
            created_at=datetime.now(),
            updated_at=datetime.now()
        )

    @classmethod
    def from_dict_or_dto(cls, data: Union[dict, 'PluginServiceRuntimeDTO'], plugin_id: str = None, plugin_slug: str = None, user_id: str = None) -> 'PluginServiceRuntimeDTO':
        """
        Flexible factory method that handles both dict (GitHub) and DTO (database) inputs.
        """
        if isinstance(data, cls):
            return data  # Already a DTO, return as-is
        elif isinstance(data, dict):
            # Dict from GitHub, convert using factory method
            if not all([plugin_id, plugin_slug, user_id]):
                raise ValueError("plugin_id, plugin_slug, and user_id are required when converting from dict")
            return cls.from_github_data(data, plugin_id, plugin_slug, user_id)
        else:
            raise TypeError(f"Expected dict or {cls.__name__}, got {type(data)}")
