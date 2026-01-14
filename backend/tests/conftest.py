import sys
import os
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from typing import AsyncGenerator, Generator
import types

# Add the backend directory to the Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if "groq" not in sys.modules:
    class _AsyncGroqStub:
        def __init__(self, *args, **kwargs):
            async def _empty_models():
                return types.SimpleNamespace(data=[])

            async def _create_completion(*args, **kwargs):
                if kwargs.get("stream"):
                    async def _iterator():
                        yield types.SimpleNamespace(
                            choices=[types.SimpleNamespace(delta=types.SimpleNamespace(content=None), finish_reason="stop")],
                            id="stub-stream",
                        )

                    return _iterator()
                return types.SimpleNamespace(
                    choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=""), finish_reason="stop")],
                    usage=types.SimpleNamespace(prompt_tokens=0, completion_tokens=0, total_tokens=0),
                    id="stub-response",
                )

            self.models = types.SimpleNamespace(list=_empty_models)
            self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=_create_completion))

    sys.modules["groq"] = types.SimpleNamespace(AsyncGroq=_AsyncGroqStub)

if "PyPDF2" not in sys.modules:
    class _PdfReaderStub:
        def __init__(self, *args, **kwargs):
            self.pages = []

    sys.modules["PyPDF2"] = types.SimpleNamespace(PdfReader=_PdfReaderStub)

# Override settings for testing
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test.db"
os.environ["ENVIRONMENT"] = "test"
os.environ["PLUGIN_RUNTIME_TOKEN"] = "test-plugin-runtime-token"
os.environ["JOB_WORKER_TOKEN"] = "test-job-worker-token"
os.environ["PLUGIN_LIFECYCLE_TOKEN"] = "test-plugin-lifecycle-token"

from app.core.config import settings

if not hasattr(settings, "cors_origins_list"):
    object.__setattr__(settings, "cors_origins_list", settings.CORS_ORIGINS or [])
if not hasattr(settings, "cors_methods_list"):
    object.__setattr__(settings, "cors_methods_list", settings.CORS_METHODS or [])
if not hasattr(settings, "cors_headers_list"):
    object.__setattr__(settings, "cors_headers_list", settings.CORS_HEADERS or [])
if not hasattr(settings, "cors_expose_headers_list"):
    object.__setattr__(settings, "cors_expose_headers_list", settings.CORS_EXPOSE_HEADERS or [])
from app.models import Base  # Import all models so tables are created
from app.core.database import Base as DBBase
from app.main import app
from app.core.database import get_db

# Create test engine
engine = create_async_engine(str(settings.DATABASE_URL), echo=True)
TestingSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# Override the database dependency
async def override_get_db():
    async with TestingSessionLocal() as session:
        yield session

app.dependency_overrides[get_db] = override_get_db

@pytest_asyncio.fixture(autouse=True)
async def setup_database():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(DBBase.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(DBBase.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(DBBase.metadata.drop_all)

@pytest_asyncio.fixture
async def db() -> AsyncGenerator[AsyncSession, None]:
    async with TestingSessionLocal() as session:
        yield session

@pytest.fixture
def client() -> Generator:
    with TestClient(app, base_url="http://test") as c:
        c.headers["host"] = "test"  # Set the host header to match base_url
        c.cookies.jar.clear()  # Clear any existing cookies
        yield c

class _LifespanManager:
    def __init__(self, application):
        self.app = application
        self._context = None

    async def __aenter__(self):
        self._context = self.app.router.lifespan_context(self.app)
        await self._context.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self._context is not None:
            await self._context.__aexit__(exc_type, exc, tb)


@pytest_asyncio.fixture
async def async_client() -> AsyncGenerator[AsyncClient, None]:
    async with _LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
