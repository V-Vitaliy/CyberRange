import redis.asyncio as redis
from app.core.config import settings

REDIS_URL = getattr(settings, "REDIS_URL", "redis://localhost:6379")
redis_client = redis.from_url(REDIS_URL, decode_responses=True)

async def get_redis():
    try:
        yield redis_client
    finally:
        pass