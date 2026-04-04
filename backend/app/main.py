from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.queue_manager import LLMQueueManager
from app.api.routes_red import router as red_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for startup and shutdown events.
    Initializes the queue manager inside the running event loop.
    """
    app.state.queue_manager = LLMQueueManager()

    # Here we will later initialize the LLM model:
    # app.state.llm = init_llm()

    yield
    # Cleanup resources on shutdown if needed

# Initialize the application
app = FastAPI(
    title="AI Security CyberRange API",
    description="Backend for the Vulnerable-by-Design AI CyberRange platform",
    version="1.0.0",
    lifespan=lifespan
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ВАЖНО: Роутеры нужно подключать ПОСЛЕ инициализации app
app.include_router(red_router, prefix="/api")

@app.get("/api/health", tags=["System"])
async def health_check():
    """System health check endpoint."""
    return {
        "status": "online",
        "service": "AI CyberRange Core",
        "message": "System initialized. Ready for security analysis queries."
    }