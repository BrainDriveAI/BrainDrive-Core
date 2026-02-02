"""
End-to-end tests for Library integration.

Covers:
- Create project via Library plugin
- Write file (direct filesystem, fs primitives on separate branch)
- Read file via Library context endpoint
- List projects
- Get project context (aggregated)
- Full user journey

Note: /api/v1/fs/* endpoints live on feature/fs-primitives (PR #217).
These tests use direct filesystem writes + Library plugin API endpoints
which are available on feature/backend-plugin-arch (PR #219).
"""
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


async def _setup(db, library_root: Path):
    """Create user, plugin, and load routes."""
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
        description="Library plugin",
        version="1.0.0",
        type="backend",
        plugin_type="backend",
        enabled=True,
        user_id=user.id,
        endpoints_file="endpoints.py",
        route_prefix="/library",
    )
    db.add(plugin)
    await db.commit()

    loader = get_plugin_loader()
    loader.set_app(app)
    await loader.reload_routes(db)
    return user, plugin


@pytest.mark.asyncio
async def test_full_library_journey(db, tmp_path, monkeypatch, auth_overrides):
    """Full user journey: create project → write file → list → get context."""
    library_root = tmp_path / "library"
    (library_root / "projects" / "active").mkdir(parents=True)
    (library_root / "projects" / "ideas").mkdir(parents=True)
    (library_root / "projects" / "completed").mkdir(parents=True)
    (library_root / "projects" / "archived").mkdir(parents=True)

    monkeypatch.setattr(settings, "LIBRARY_PATH", str(library_root), raising=False)
    await _setup(db, library_root)

    with TestClient(app, base_url="http://test") as client:
        # 1. Create project via Library plugin
        resp = client.post(
            "/api/v1/plugin-api/braindrive-library/library/projects",
            json={"name": "E2E Project", "lifecycle": "active"},
        )
        assert resp.status_code == 200, f"Create project failed: {resp.text}"
        data = resp.json()
        assert data["success"] is True
        slug = data["project"]["slug"]
        assert slug == "e2e-project"

        project_path = library_root / "projects" / "active" / slug
        assert project_path.exists()

        # 2. Write a file directly to the project directory
        notes_path = project_path / "notes.md"
        notes_path.write_text("# E2E Notes\n\nThis is a test note.", encoding="utf-8")
        assert notes_path.exists()

        # 3. Read that file back by verifying on disk
        content = notes_path.read_text(encoding="utf-8")
        assert "E2E Notes" in content

        # 4. List projects — verify new project appears
        resp = client.get(
            "/api/v1/plugin-api/braindrive-library/library/projects",
            params={"lifecycle": "active"},
        )
        assert resp.status_code == 200
        list_data = resp.json()
        assert list_data["success"] is True
        slugs = [p["slug"] for p in list_data["projects"]]
        assert "e2e-project" in slugs

        # 5. Get context — verify aggregated context includes written file
        resp = client.get(
            f"/api/v1/plugin-api/braindrive-library/library/project/{slug}/context",
            params={"lifecycle": "active"},
        )
        assert resp.status_code == 200
        ctx_data = resp.json()
        assert ctx_data["success"] is True
        assert "notes.md" in ctx_data["files"]
        assert "E2E Notes" in ctx_data["files"]["notes.md"]["content"]


@pytest.mark.asyncio
async def test_library_enable_and_read(db, tmp_path, monkeypatch, auth_overrides):
    """Enable Library, read an existing file via context endpoint."""
    library_root = tmp_path / "library"
    project_dir = library_root / "projects" / "active" / "read-test"
    project_dir.mkdir(parents=True)
    (project_dir / "AGENT.md").write_text("# Read Test", encoding="utf-8")

    monkeypatch.setattr(settings, "LIBRARY_PATH", str(library_root), raising=False)
    await _setup(db, library_root)

    with TestClient(app, base_url="http://test") as client:
        resp = client.get(
            "/api/v1/plugin-api/braindrive-library/library/project/read-test/context",
            params={"lifecycle": "active"},
        )
        assert resp.status_code == 200
        ctx = resp.json()
        assert ctx["success"] is True
        assert "AGENT.md" in ctx["files"]
        assert "Read Test" in ctx["files"]["AGENT.md"]["content"]


@pytest.mark.asyncio
async def test_library_enable_and_write(db, tmp_path, monkeypatch, auth_overrides):
    """Enable Library, write a new file (direct fs), verify via context."""
    library_root = tmp_path / "library"
    project_dir = library_root / "projects" / "active" / "write-test"
    project_dir.mkdir(parents=True)
    (project_dir / "AGENT.md").write_text("# Write Test", encoding="utf-8")

    monkeypatch.setattr(settings, "LIBRARY_PATH", str(library_root), raising=False)
    await _setup(db, library_root)

    # Simulate writing a file (as the AI/fs endpoint would)
    # Use notes.md which is in the default context file list
    notes_file = project_dir / "notes.md"
    notes_file.write_text("# Written by E2E test", encoding="utf-8")
    assert notes_file.exists()

    with TestClient(app, base_url="http://test") as client:
        # Verify it appears in context
        resp = client.get(
            "/api/v1/plugin-api/braindrive-library/library/project/write-test/context",
            params={"lifecycle": "active"},
        )
        assert resp.status_code == 200
        ctx = resp.json()
        assert ctx["success"] is True
        assert "notes.md" in ctx["files"]
        assert "Written by E2E test" in ctx["files"]["notes.md"]["content"]


@pytest.mark.asyncio
async def test_library_create_project(db, tmp_path, monkeypatch, auth_overrides):
    """Create a project via the Library plugin."""
    library_root = tmp_path / "library"
    (library_root / "projects" / "active").mkdir(parents=True)

    monkeypatch.setattr(settings, "LIBRARY_PATH", str(library_root), raising=False)
    await _setup(db, library_root)

    with TestClient(app, base_url="http://test") as client:
        resp = client.post(
            "/api/v1/plugin-api/braindrive-library/library/projects",
            json={"name": "Created Project", "lifecycle": "active"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["project"]["slug"] == "created-project"
        assert (library_root / "projects" / "active" / "created-project").exists()
