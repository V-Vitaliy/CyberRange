from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.queue_manager import LLMQueueManager
from app.db.chroma_client import VectorStore
from app.api.routes_red import router as red_router
from app.api.routes_blue import router as blue_router

# 1. Создаем Sub-apps ДО lifespan, чтобы они были в зоне видимости
red_app = FastAPI(
    title="Red Team API",
    description="Vulnerable endpoints for exploitation and data poisoning.",
    version="1.0.0",
)
red_app.include_router(red_router, prefix="")

blue_app = FastAPI(
    title="Blue Team API",
    description="Defense shop and system configuration endpoints.",
    version="1.0.0",
)
blue_app.include_router(blue_router, prefix="")


# 2. Инициализируем ресурсы и раздаем их ВСЕМ приложениям
@asynccontextmanager
async def lifespan(app: FastAPI):
    queue_manager = LLMQueueManager()
    vector_store = VectorStore()

    # Отдаем главному app
    app.state.queue_manager = queue_manager
    app.state.vector_store = vector_store

    # Отдаем Red Team app (Именно здесь падала ошибка 500!)
    red_app.state.queue_manager = queue_manager
    red_app.state.vector_store = vector_store

    # Отдаем Blue Team app
    blue_app.state.queue_manager = queue_manager
    blue_app.state.vector_store = vector_store

    yield
    # Cleanup on shutdown


# 3. Главное приложение
app = FastAPI(
    title="AI Security CyberRange API",
    description="Backend for the Vulnerable-by-Design AI CyberRange platform",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 4. Монтируем
app.mount("/api/red", red_app)
app.mount("/api/blue", blue_app)


@app.get("/api/health", tags=["System"])
async def health_check():
    return {
        "status": "online",
        "service": "AI CyberRange Core",
        "message": "System initialized."
    }