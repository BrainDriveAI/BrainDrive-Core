import json
import shutil
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


async def _create_user(db, user_id: str = "testuser") -> User:
    user = User(
        id=user_id,
        username=user_id,
        email=f"{user_id}@example.com",
        password=hash_password("pass123"),
        version="0.1.0",
    )
    await user.save(db)
    return user


def _plugin_dir(repo_root: Path, slug: str, version: str = "1.0.0") -> Path:
    major_version = version.split(".")[0]
    return repo_root / "backend" / "plugins" / "shared" / slug / f"v{major_version}"


def _write_endpoints(plugin_dir: Path, code: str) -> None:
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "endpoints.py").write_text(code, encoding="utf-8")


async def _create_backend_plugin_record(
    db,
    user_id: str,
    slug: str,
    name: str,
    version: str = "1.0.0",
    route_prefix: str = "/test",
    endpoints_file: str = "endpoints.py",
    enabled: bool = True,
):
    plugin = Plugin(
        id=f"{user_id}_{slug}",
        plugin_slug=slug,
        name=name,
        description=f"{name} plugin",
        version=version,
        type="backend",
        plugin_type="backend",
        enabled=enabled,
        user_id=user_id,
        endpoints_file=endpoints_file,
        route_prefix=route_prefix,
        backend_dependencies=json.dumps([]),
    )
    db.add(plugin)
    await db.commit()
    return plugin


@pytest.mark.asyncio
async def test_backend_plugin_install_disable_enable_routes(db, tmp_path, monkeypatch, auth_overrides):
    library_root = tmp_path / "library"
    project_dir = library_root / "projects" / "active" / "alpha"
    project_dir.mkdir(parents=True)
    (project_dir / "AGENT.md").write_text("# Alpha", encoding="utf-8")

    monkeypatch.setattr(settings, "LIBRARY_PATH", str(library_root), raising=False)

    await _create_user(db)

    with TestClient(app, base_url="http://test") as client:
        install_response = client.post("/api/v1/plugins/braindrive-library/install")
        assert install_response.status_code == 200
        plugin_id = install_response.json()["data"]["plugin_id"]

        response = client.get(
            "/api/v1/plugin-api/braindrive-library/library/projects",
            params={"lifecycle": "active"},
        )
        assert response.status_code == 200

        disable_response = client.patch(
            f"/api/v1/plugins/{plugin_id}",
            json={"enabled": False},
        )
        assert disable_response.status_code == 200

        response = client.get(
            "/api/v1/plugin-api/braindrive-library/library/projects",
            params={"lifecycle": "active"},
        )
        assert response.status_code == 404

        enable_response = client.patch(
            f"/api/v1/plugins/{plugin_id}",
            json={"enabled": True},
        )
        assert enable_response.status_code == 200

        response = client.get(
            "/api/v1/plugin-api/braindrive-library/library/projects",
            params={"lifecycle": "active"},
        )
        assert response.status_code == 200


@pytest.mark.asyncio
async def test_route_reload_with_multiple_plugins(db, tmp_path, monkeypatch, auth_overrides):
    repo_root = Path(__file__).resolve().parents[2]
    plugin_slug = "test-backend-plugin"
    plugin_dir = _plugin_dir(repo_root, plugin_slug)

    endpoints_code = """
from app.plugins.decorators import plugin_endpoint

@plugin_endpoint("/ping", methods=["GET"])
async def ping(request):
    return {"ok": True}
"""

    _write_endpoints(plugin_dir, endpoints_code)

    try:
        library_root = tmp_path / "library"
        (library_root / "projects" / "active").mkdir(parents=True)
        monkeypatch.setattr(settings, "LIBRARY_PATH", str(library_root), raising=False)

        user = await _create_user(db)
        await _create_backend_plugin_record(
            db,
            user_id=user.id,
            slug="braindrive-library",
            name="BrainDrive Library",
            route_prefix="/library",
        )
        await _create_backend_plugin_record(
            db,
            user_id=user.id,
            slug=plugin_slug,
            name="Test Backend",
            route_prefix="/test",
        )

        with TestClient(app, base_url="http://test") as client:
            reload_response = client.post("/api/v1/admin/plugins/reload-routes")
            assert reload_response.status_code == 200
            payload = reload_response.json()
            assert set(payload["loaded"]) >= {"braindrive-library", plugin_slug}

            response = client.get("/api/v1/plugin-api/test-backend-plugin/test/ping")
            assert response.status_code == 200
            assert response.json() == {"ok": True}
    finally:
        shutil.rmtree(plugin_dir.parent, ignore_errors=True)


@pytest.mark.asyncio
async def test_bad_plugin_does_not_break_others(db, tmp_path, monkeypatch, auth_overrides):
    repo_root = Path(__file__).resolve().parents[2]
    bad_slug = "bad-backend-plugin"
    bad_dir = _plugin_dir(repo_root, bad_slug)

    bad_code = """
from app.plugins.decorators import plugin_endpoint

@plugin_endpoint("/boom", methods=["GET"])
async def boom(request):
    return {"ok": True
"""

    _write_endpoints(bad_dir, bad_code)

    try:
        library_root = tmp_path / "library"
        (library_root / "projects" / "active").mkdir(parents=True)
        monkeypatch.setattr(settings, "LIBRARY_PATH", str(library_root), raising=False)

        user = await _create_user(db)
        await _create_backend_plugin_record(
            db,
            user_id=user.id,
            slug="braindrive-library",
            name="BrainDrive Library",
            route_prefix="/library",
        )
        await _create_backend_plugin_record(
            db,
            user_id=user.id,
            slug=bad_slug,
            name="Bad Backend",
            route_prefix="/bad",
        )

        with TestClient(app, base_url="http://test") as client:
            reload_response = client.post("/api/v1/admin/plugins/reload-routes")
            assert reload_response.status_code == 200
            payload = reload_response.json()
            assert "braindrive-library" in payload["loaded"]
            assert any(error["plugin_slug"] == bad_slug for error in payload["errors"])
    finally:
        shutil.rmtree(bad_dir.parent, ignore_errors=True)


@pytest.mark.asyncio
async def test_cascade_disable_via_api(db, auth_overrides):
    user = await _create_user(db)

    backend_plugin = Plugin(
        id=f"{user.id}_backend-alpha",
        plugin_slug="backend-alpha",
        name="Backend Alpha",
        description="Backend plugin",
        version="1.0.0",
        type="backend",
        plugin_type="backend",
        enabled=True,
        user_id=user.id,
        endpoints_file="endpoints.py",
        route_prefix="/backend-alpha",
    )
    dependent_plugin = Plugin(
        id=f"{user.id}_frontend-beta",
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
