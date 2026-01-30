from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.auth_context import AuthContext
from app.core.auth_deps import require_user, require_admin, get_auth_context
from app.core.config import settings
from app.main import app


def _fs_routes_available() -> bool:
    return any(
        hasattr(route, "path") and route.path.startswith("/api/v1/fs")
        for route in app.routes
    )


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


@pytest.fixture
def non_admin_auth_overrides():
    async def _fake_auth_context():
        return AuthContext(
            user_id="testuser",
            username="testuser",
            is_admin=False,
            roles=set(),
            tenant_id=None,
        )

    app.dependency_overrides[get_auth_context] = _fake_auth_context
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_auth_context, None)


@pytest.mark.asyncio
async def test_fs_write_read_append_list_delete(tmp_path, monkeypatch, auth_overrides):
    if not _fs_routes_available():
        pytest.skip("fs primitives not available in this branch")

    library_root = tmp_path / "library"
    (library_root / "projects" / "active").mkdir(parents=True)

    monkeypatch.setattr(settings, "LIBRARY_PATH", str(library_root), raising=False)

    target_path = "projects/active/alpha.md"

    with TestClient(app, base_url="http://test") as client:
        write_response = client.post(
            "/api/v1/fs/write",
            json={"path": target_path, "content": "hello"},
        )
        assert write_response.status_code == 200

        read_response = client.get(
            "/api/v1/fs/read",
            params={"path": target_path},
        )
        assert read_response.status_code == 200

        append_response = client.patch(
            "/api/v1/fs/append",
            json={"path": target_path, "content": " world"},
        )
        assert append_response.status_code == 200

        list_response = client.get(
            "/api/v1/fs/list",
            params={"path": "projects/active"},
        )
        assert list_response.status_code == 200

        delete_response = client.delete(
            "/api/v1/fs/delete",
            params={"path": target_path},
        )
        assert delete_response.status_code == 200

    on_disk = library_root / target_path
    assert not on_disk.exists()


@pytest.mark.asyncio
async def test_fs_path_traversal_blocked(tmp_path, monkeypatch, auth_overrides):
    if not _fs_routes_available():
        pytest.skip("fs primitives not available in this branch")

    library_root = tmp_path / "library"
    library_root.mkdir(parents=True)
    monkeypatch.setattr(settings, "LIBRARY_PATH", str(library_root), raising=False)

    with TestClient(app, base_url="http://test") as client:
        response = client.get(
            "/api/v1/fs/read",
            params={"path": "../secrets.txt"},
        )

    assert response.status_code == 403
    assert response.json()["detail"] == "Path traversal not allowed"


@pytest.mark.asyncio
async def test_fs_disallowed_extension(tmp_path, monkeypatch, auth_overrides):
    if not _fs_routes_available():
        pytest.skip("fs primitives not available in this branch")

    library_root = tmp_path / "library"
    library_root.mkdir(parents=True)
    monkeypatch.setattr(settings, "LIBRARY_PATH", str(library_root), raising=False)

    with TestClient(app, base_url="http://test") as client:
        response = client.post(
            "/api/v1/fs/write",
            json={"path": "projects/active/bad.exe", "content": "nope"},
        )

    assert response.status_code == 400
    assert "File type not allowed" in response.json()["detail"]


@pytest.mark.asyncio
async def test_fs_delete_requires_admin(tmp_path, monkeypatch, non_admin_auth_overrides):
    if not _fs_routes_available():
        pytest.skip("fs primitives not available in this branch")

    library_root = tmp_path / "library"
    (library_root / "projects" / "active").mkdir(parents=True)
    monkeypatch.setattr(settings, "LIBRARY_PATH", str(library_root), raising=False)

    target_path = library_root / "projects" / "active" / "alpha.md"
    target_path.write_text("hello", encoding="utf-8")

    with TestClient(app, base_url="http://test") as client:
        response = client.delete(
            "/api/v1/fs/delete",
            params={"path": "projects/active/alpha.md"},
        )

    assert response.status_code == 403
