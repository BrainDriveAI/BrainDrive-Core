# Backend Plugin Development

Backend plugins let you add server-side API endpoints without changing core code. Routes are loaded dynamically at runtime and mounted under a dedicated prefix so they never collide with core routes.

## Directory Structure

Backend plugins live under the backend shared plugins directory:

```
backend/plugins/shared/<plugin-slug>/v1/
├── lifecycle_manager.py
├── endpoints.py
└── __init__.py
```

The route loader looks up plugins by slug and major version:
`backend/plugins/shared/<slug>/v<major>/endpoints.py`.

## Required Metadata

Your `lifecycle_manager.py` must define `plugin_data` with the fields below.
Backend and fullstack plugins must include `endpoints_file`.

Required for all plugins:
- `name`
- `plugin_slug`
- `version`
- `description`

Backend-specific fields:
- `plugin_type`: `"backend"` or `"fullstack"`
- `endpoints_file`: e.g. `"endpoints.py"`
- `route_prefix`: e.g. `"/library"`
- `backend_dependencies`: list of backend plugin slugs

Example:

```python
self.plugin_data = {
    "name": "Example Backend",
    "plugin_slug": "example-backend",
    "version": "1.0.0",
    "description": "Example backend plugin",
    "plugin_type": "backend",
    "endpoints_file": "endpoints.py",
    "route_prefix": "/example",
    "backend_dependencies": [],
}
```

## Endpoint Decorator

Use `@plugin_endpoint()` to register endpoints in `endpoints.py`.

```python
from app.plugins.decorators import plugin_endpoint

@plugin_endpoint("/ping", methods=["GET"])
async def ping(request):
    return {"ok": True}
```

Rules:
- Paths are **relative**, not absolute.
- Path validation rejects `".."`, `"//"`, or absolute paths.
- `admin_only=True` requires admin auth for that endpoint.

## Route Prefixes

Routes are mounted at:

```
/api/v1/plugin-api/<plugin-slug><route_prefix><endpoint_path>
```

Example:

```
plugin_slug = "braindrive-library"
route_prefix = "/library"
endpoint_path = "/projects"

Full path:
/api/v1/plugin-api/braindrive-library/library/projects
```

## Reloading Routes

Routes are reloaded automatically after install/enable/disable/uninstall of backend plugins.
You can also trigger a reload manually:

```
POST /api/v1/admin/plugins/reload-routes
```

## Example Template

`lifecycle_manager.py`:

```python
from pathlib import Path
from app.plugins.base_lifecycle_manager import BaseLifecycleManager

class ExampleLifecycleManager(BaseLifecycleManager):
    def __init__(self, plugin_slug="example-backend", version="1.0.0", shared_storage_path: Path = None):
        if shared_storage_path is None:
            shared_storage_path = Path(__file__).parent
        super().__init__(plugin_slug, version, shared_storage_path)

        self.plugin_data = {
            "name": "Example Backend",
            "plugin_slug": "example-backend",
            "version": "1.0.0",
            "description": "Example backend plugin",
            "plugin_type": "backend",
            "endpoints_file": "endpoints.py",
            "route_prefix": "/example",
            "backend_dependencies": [],
        }

    async def get_plugin_metadata(self):
        return self.plugin_data

    async def get_module_metadata(self):
        return []

    async def _perform_user_installation(self, user_id, db, shared_plugin_path):
        return {"success": True}

    async def _perform_user_uninstallation(self, user_id, db):
        return {"success": True}
```

`endpoints.py`:

```python
from app.plugins.decorators import plugin_endpoint

@plugin_endpoint("/ping", methods=["GET"])
async def ping(request):
    return {"ok": True}
```

## Testing Tips

- Use `TestClient` to call `/api/v1/admin/plugins/reload-routes` after inserting plugin records.
- Override `require_user` / `require_admin` in tests to bypass auth.
- If a plugin fails to load, it will show up in the reload response `errors` list, but other plugins still load.
