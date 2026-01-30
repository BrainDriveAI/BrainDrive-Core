import pytest
from fastapi.testclient import TestClient
from app.core.security import hash_password
from app.models.user import User
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_login_updates_version(client: TestClient, db: AsyncSession):
    password = "pass123"
    hashed = hash_password(password)
    user = User(
        id="updateuser",
        username="updateuser",
        email="update@example.com",
        password=hashed,
        version="0.1.0",
    )
    await user.save(db)

    response = client.post(
        "/api/v1/auth/login",
        json={"email": "update@example.com", "password": password},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["user"]["version"] == "0.6.5"

    await db.refresh(user)
    assert user.version == "0.6.5"
