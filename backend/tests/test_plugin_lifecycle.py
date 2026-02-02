import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.auth_context import AuthContext
from app.core.auth_deps import require_user
from app.core.security import hash_password
from app.main import app
from app.models.plugin import Plugin
from app.models.user import User
from app.plugins import lifecycle_api
from app.plugins.repository import PluginRepository


@pytest.fixture
def auth_overrides():
    async def _fake_auth():
        return AuthContext(
            user_id="testuser",
            username="testuser",
            is_admin=True,
            roles={"admin"},
            tenant_id=None,
        )

    app.dependency_overrides[require_user] = _fake_auth
    try:
        yield
    finally:
        app.dependency_overrides.pop(require_user, None)


@pytest.mark.asyncio
async def test_cascade_disable_backend_dependencies(db, auth_overrides):
    user = User(
        id="testuser",
        username="testuser",
        email="testuser@example.com",
        hashed_password=hash_password("pass123"),
        version="0.1.0",
    )
    await user.save(db)

    backend_plugin = Plugin(
        id=uuid.uuid4().hex,
        plugin_slug="backend-alpha",
        name="Backend Alpha",
        description="Backend plugin",
        version="1.0.0",
        type="backend",
        plugin_type="backend",
        enabled=True,
        user_id=user.id,
    )

    dependent_plugin = Plugin(
        id=uuid.uuid4().hex,
        plugin_slug="frontend-beta",
        name="Frontend Beta",
        description="Frontend plugin",
        version="1.0.0",
        type="frontend",
        plugin_type="frontend",
        enabled=True,
        user_id=user.id,
        backend_dependencies=json.dumps(["backend-alpha"]),
    )

    db.add(backend_plugin)
    db.add(dependent_plugin)
    await db.commit()

    with TestClient(app, base_url="http://test") as client:
        response = client.patch(
            f"/api/v1/plugins/{backend_plugin.id}",
            json={"enabled": False},
        )

    assert response.status_code == 200
    payload = response.json()
    assert "cascade_disabled" in payload
    assert payload["cascade_disabled"][0]["slug"] == "frontend-beta"

    # Refresh session state to see updates from the request's DB session
    await db.rollback()

    result = await db.execute(
        select(Plugin)
        .where(Plugin.id == dependent_plugin.id)
        .execution_options(populate_existing=True)
    )
    updated_dep = result.scalars().first()
    assert updated_dep is not None
    assert updated_dep.enabled is False


@pytest.mark.asyncio
async def test_auto_install_backend_dependencies(db, monkeypatch):
    calls = []

    class DummyManager:
        plugin_data = {"backend_dependencies": ["dep-backend"]}

    async def fake_install_plugin(slug, user_id, db_session):
        calls.append((slug, user_id))
        return {"success": True, "plugin_id": "dep-id"}

    async def fake_get_plugin_by_slug(self, slug, user_id=None):
        return None

    monkeypatch.setattr(
        lifecycle_api.universal_manager,
        "_load_plugin_manager",
        lambda slug: DummyManager(),
    )
    monkeypatch.setattr(
        lifecycle_api.universal_manager,
        "install_plugin",
        fake_install_plugin,
    )
    monkeypatch.setattr(
        PluginRepository,
        "get_plugin_by_slug",
        fake_get_plugin_by_slug,
    )

    result = await lifecycle_api._auto_install_backend_dependencies(
        "main-plugin",
        "testuser",
        db,
    )

    assert result["success"] is True
    assert result["auto_installed"] == [{"slug": "dep-backend", "plugin_id": "dep-id"}]
    assert calls == [("dep-backend", "testuser")]
