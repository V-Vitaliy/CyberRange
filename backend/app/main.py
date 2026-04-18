from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.queue_manager import LLMQueueManager
from app.db.chroma_client import VectorStore
from app.api.routes_red import router as red_router
from app.api.routes_blue import router as blue_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.queue_manager = LLMQueueManager()
    app.state.vector_store = VectorStore()
    yield
    # Cleanup on shutdown

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

app.mount("/api/red", red_app)
app.mount("/api/blue", blue_app)

@app.get("/api/health", tags=["System"])
async def health_check():
    return {
        "status": "online",
        "service": "AI CyberRange Core",
        "message": "System initialized."
    }