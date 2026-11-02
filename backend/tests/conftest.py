import httpx
import pytest_asyncio

from app.main import app


@pytest_asyncio.fixture
async def client():
    # ASGITransport does not run lifespan, load models or start services.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client
