import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.auth_context import AuthContext
from app.core.auth_deps import require_user, require_admin
from app.core.config import settings
from app.core.security import hash_password
from app.main import app
from app.models.plugin import Plugin
from app.models.user import User
from app.plugins.route_loader import get_plugin_loader


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
    app.dependency_overrides[require_admin] = _fake_auth
    try:
        yield
    finally:
        app.dependency_overrides.pop(require_user, None)
        app.dependency_overrides.pop(require_admin, None)


async def _create_user_and_plugin(db, route_prefix: str = "/library"):
    user = User(
        id="testuser",
        username="testuser",
        email="testuser@example.com",
        password=hash_password("pass123"),
        version="0.1.0",
    )
    await user.save(db)

    plugin = Plugin(
        id=uuid.uuid4().hex,
        plugin_slug="braindrive-library",
        name="BrainDrive Library",
        description="Test backend plugin",
        version="1.0.0",
        type="backend",
        plugin_type="backend",
        enabled=True,
        user_id=user.id,
        endpoints_file="endpoints.py",
        route_prefix=route_prefix,
    )
    db.add(plugin)
    await db.commit()
    return user, plugin


async def _load_library_routes(db):
    loader = get_plugin_loader()
    loader.set_app(app)
    return await loader.reload_routes(db)


@pytest.mark.asyncio
async def test_library_plugin_list_projects(db, tmp_path, monkeypatch, auth_overrides):
    library_root = tmp_path / "library"
    projects_dir = library_root / "projects" / "active" / "alpha"
    projects_dir.mkdir(parents=True)
    (projects_dir / "AGENT.md").write_text("# Alpha", encoding="utf-8")

    monkeypatch.setattr(settings, "LIBRARY_PATH", str(library_root), raising=False)

    await _create_user_and_plugin(db)
    await _load_library_routes(db)

    with TestClient(app, base_url="http://test") as client:
        response = client.get(
            "/api/v1/plugin-api/braindrive-library/library/projects",
            params={"lifecycle": "active"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["count"] == 1
    assert data["projects"][0]["slug"] == "alpha"


@pytest.mark.asyncio
async def test_library_plugin_get_project_context(db, tmp_path, monkeypatch, auth_overrides):
    library_root = tmp_path / "library"
    project_dir = library_root / "projects" / "active" / "alpha"
    project_dir.mkdir(parents=True)
    (project_dir / "AGENT.md").write_text("# Alpha", encoding="utf-8")

    monkeypatch.setattr(settings, "LIBRARY_PATH", str(library_root), raising=False)

    await _create_user_and_plugin(db)
    await _load_library_routes(db)

    with TestClient(app, base_url="http://test") as client:
        response = client.get(
            "/api/v1/plugin-api/braindrive-library/library/project/alpha/context",
            params={"lifecycle": "active"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "AGENT.md" in data["files"]
    assert "# Alpha" in data["files"]["AGENT.md"]["content"]


@pytest.mark.asyncio
async def test_library_plugin_create_project(db, tmp_path, monkeypatch, auth_overrides):
    library_root = tmp_path / "library"
    (library_root / "projects" / "active").mkdir(parents=True)

    monkeypatch.setattr(settings, "LIBRARY_PATH", str(library_root), raising=False)

    await _create_user_and_plugin(db)
    await _load_library_routes(db)

    with TestClient(app, base_url="http://test") as client:
        response = client.post(
            "/api/v1/plugin-api/braindrive-library/library/projects",
            json={"name": "New Project", "lifecycle": "active"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["project"]["slug"] == "new-project"

    project_path = library_root / "projects" / "active" / "new-project"
    assert project_path.exists()
    assert (project_path / "AGENT.md").exists()
    assert (project_path / "spec.md").exists()
    assert (project_path / "build-plan.md").exists()
    assert (project_path / "decisions.md").exists()
