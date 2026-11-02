import os

# Test-only signing key, set before application settings are imported.
os.environ.setdefault("JWT_SECRET", "test-only-signing-key-" + "x" * 32)

import httpx  # noqa: E402
import pytest_asyncio  # noqa: E402

from app.main import app  # noqa: E402


@pytest_asyncio.fixture
async def client():
    # ASGITransport does not run lifespan, load models or start services.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client
