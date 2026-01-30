import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.user import User

@pytest.mark.asyncio
async def test_registration_sets_version(client: TestClient, db: AsyncSession):
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": "versiontest@example.com",
            "username": "versionuser",
            "password": "strongpassword",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["version"] == "0.6.0"

    user = await User.get_by_email(db, "versiontest@example.com")
    assert user is not None
    assert user.version == "0.6.0"
