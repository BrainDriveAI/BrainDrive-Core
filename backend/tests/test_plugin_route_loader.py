from pathlib import Path
import sys

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from app.core.auth_context import AuthContext
from app.core.auth_deps import require_user
from app.plugins.decorators import plugin_endpoint, PluginRequest
from app.plugins.route_loader import PluginRouteLoader, PluginInfo, PLUGIN_ROUTE_PREFIX


def _write_endpoints_file(path: Path) -> None:
    path.write_text(
        """
from app.plugins.decorators import plugin_endpoint, PluginRequest

@plugin_endpoint('/ping', methods=['GET'])
async def ping(request: PluginRequest):
    return {'ok': True}
""".lstrip(),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_load_and_unload_plugin_module(tmp_path):
    endpoints_path = tmp_path / "endpoints.py"
    _write_endpoints_file(endpoints_path)

    loader = PluginRouteLoader()
    module = loader._load_plugin_module("test-plugin", "1.2.3", endpoints_path)

    module_name = "braindrive.plugins.test_plugin.v1.endpoints"
    assert module is not None
    assert module.__name__ == module_name
    assert module_name in sys.modules

    loader._unload_plugin_module("test-plugin")
    assert module_name not in sys.modules


@pytest.mark.asyncio
async def test_swap_routes_replaces_previous_routes():
    app = FastAPI()
    loader = PluginRouteLoader()
    loader.set_app(app)

    router_a = APIRouter(prefix="/alpha")

    @router_a.get("/ping")
    async def ping_a():
        return {"ok": "a"}

    router_b = APIRouter(prefix="/beta")

    @router_b.get("/ping")
    async def ping_b():
        return {"ok": "b"}

    await loader._swap_routes({"plugin-a": router_a})

    paths_after_a = [route.path for route in app.routes if hasattr(route, "path")]
    assert f"{PLUGIN_ROUTE_PREFIX}/plugin-a/alpha/ping" in paths_after_a

    await loader._swap_routes({"plugin-b": router_b})

    paths_after_b = [route.path for route in app.routes if hasattr(route, "path")]
    assert f"{PLUGIN_ROUTE_PREFIX}/plugin-b/beta/ping" in paths_after_b
    assert not any(path.startswith(f"{PLUGIN_ROUTE_PREFIX}/plugin-a") for path in paths_after_b)


@pytest.mark.asyncio
async def test_route_wrapper_passes_path_params(monkeypatch):
    app = FastAPI()
    loader = PluginRouteLoader()
    loader.set_app(app)

    async def _fake_auth():
        return AuthContext(
            user_id="testuser",
            username="testuser",
            is_admin=True,
            roles={"admin"},
            tenant_id=None,
        )

    app.dependency_overrides[require_user] = _fake_auth

    @plugin_endpoint("/items/{item_id}", methods=["GET"])
    async def get_item(request: PluginRequest, item_id: str):
        return {"item_id": item_id}

    plugin_info = PluginInfo(
        slug="test-plugin",
        name="Test Plugin",
        version="1.0.0",
        plugin_type="backend",
        endpoints_file="endpoints.py",
        route_prefix="/test",
        plugin_path=Path("."),
        enabled=True,
    )

    router = loader._create_router_from_endpoints(plugin_info, [get_item])
    await loader._swap_routes({"test-plugin": router})

    with TestClient(app, base_url="http://test") as client:
        response = client.get("/api/v1/plugin-api/test-plugin/test/items/abc123")

    app.dependency_overrides.pop(require_user, None)

    assert response.status_code == 200
    assert response.json() == {"item_id": "abc123"}
