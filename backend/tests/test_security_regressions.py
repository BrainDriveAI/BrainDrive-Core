import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.rate_limit import rate_limiter


pytestmark = pytest.mark.security


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    rate_limiter.buckets.clear()
    yield
    rate_limiter.buckets.clear()


def _register_user(client: TestClient, email: str, username: str, password: str) -> None:
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "username": username, "password": password},
    )
    assert response.status_code == 200


def _login_user(client: TestClient, email: str, password: str) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert response.status_code == 200
    return response.json()["access_token"]

def _create_user_setting_instance(client: TestClient, token: str, instance_id: str = None) -> dict:
    payload = {
        "definition_id": "security_user_setting",
        "name": "security-user-setting",
        "value": {"flag": True},
        "scope": "user",
        "user_id": "current",
    }
    if instance_id:
        payload["id"] = instance_id
    response = client.post(
        "/api/v1/settings/instances",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    return response.json()


def test_protected_endpoint_requires_auth(client: TestClient) -> None:
    response = client.get("/api/v1/settings/instances")
    assert response.status_code == 401


def test_admin_endpoint_denied_for_non_admin(client: TestClient) -> None:
    _register_user(client, "user@example.com", "user", "password123")
    token = _login_user(client, "user@example.com", "password123")

    response = client.post(
        "/api/v1/settings/definitions",
        json={
            "id": "testdef",
            "name": "test-definition",
            "description": "test definition",
            "category": "system",
            "type": "string",
            "default_value": "value",
            "allowed_scopes": ["system"],
            "validation": None,
            "is_multiple": False,
            "tags": ["security"],
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403


def test_internal_endpoint_rejects_missing_service_token(client: TestClient) -> None:
    response = client.get("/api/v1/_internal/health")
    assert response.status_code == 401


def test_internal_endpoint_rejects_user_jwt(client: TestClient) -> None:
    _register_user(client, "internal@example.com", "internal", "password123")
    token = _login_user(client, "internal@example.com", "password123")

    response = client.get(
        "/api/v1/_internal/health",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401


def test_internal_endpoint_accepts_service_token(client: TestClient) -> None:
    assert settings.PLUGIN_RUNTIME_TOKEN

    response = client.get(
        "/api/v1/_internal/health",
        headers={"Authorization": f"Bearer {settings.PLUGIN_RUNTIME_TOKEN}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["service_name"] == "plugin_runtime"


def test_internal_job_endpoint_requires_job_worker_scope(client: TestClient) -> None:
    assert settings.JOB_WORKER_TOKEN

    response = client.post(
        "/api/v1/_internal/job/progress",
        params={"job_id": "job-123"},
        headers={"Authorization": f"Bearer {settings.JOB_WORKER_TOKEN}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["job_id"] == "job-123"

def test_user_cannot_read_other_users_setting_instance(client: TestClient) -> None:
    _register_user(client, "owner@example.com", "owner", "password123")
    _register_user(client, "other@example.com", "other", "password123")

    owner_token = _login_user(client, "owner@example.com", "password123")
    other_token = _login_user(client, "other@example.com", "password123")

    created = _create_user_setting_instance(client, owner_token)
    instance_id = created["id"]

    response = client.get(
        f"/api/v1/settings/instances/{instance_id}",
        headers={"Authorization": f"Bearer {other_token}"},
    )

    assert response.status_code == 404

def test_user_can_read_own_setting_instance(client: TestClient) -> None:
    _register_user(client, "self@example.com", "self", "password123")
    token = _login_user(client, "self@example.com", "password123")

    created = _create_user_setting_instance(client, token)
    instance_id = created["id"]

    response = client.get(
        f"/api/v1/settings/instances/{instance_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == instance_id


def test_login_rate_limit_enforced(client: TestClient) -> None:
    for _ in range(5):
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "missing@example.com", "password": "wrong"},
        )
        assert response.status_code == 401

    response = client.post(
        "/api/v1/auth/login",
        json={"email": "missing@example.com", "password": "wrong"},
    )
    assert response.status_code == 429


def test_request_size_limit_enforced(client: TestClient) -> None:
    oversized_body = b"a" * (settings.MAX_REQUEST_SIZE + 1)
    content_length = str(len(oversized_body))
    response = client.post(
        "/api/v1/auth/login",
        content=oversized_body,
        headers={"Content-Type": "application/json", "Content-Length": content_length},
    )
    assert response.status_code == 413
