from contextlib import asynccontextmanager
import asyncio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.queue_manager import LLMQueueManager
from app.db.chroma_client import VectorStore
from app.api.routes_red import router as red_router
from app.api.routes_ctf import router as ctf_router
from app.api.routes_blue import router as blue_router
from app.core.config import settings
from app.core.llm_engine import init_groq,init_llm
from app.services.soc_worker import run_soc_analyst_loop

# 2. Sub-application for Red Team Docs
red_app = FastAPI(
    title="Red Team API",
    description="Vulnerable endpoints for exploitation and data poisoning.",
    version="1.0.0",
)
red_app.include_router(red_router, prefix="")
red_app.include_router(ctf_router, prefix="/ctf")

# 3. Sub-application for Blue Team Docs
blue_app = FastAPI(
    title="Blue Team API",
    description="Defense shop and system configuration endpoints.",
    version="1.0.0",
)
blue_app.include_router(blue_router, prefix="")

@asynccontextmanager
async def lifespan(app: FastAPI):
    queue_manager = LLMQueueManager()
    vector_store = VectorStore()
    soc_task = asyncio.create_task(run_soc_analyst_loop())

    if settings.GROQ_API_KEY:
        llm_instance = init_groq()
    else:
        llm_instance = init_llm()

    app.state.llm = llm_instance
    app.state.queue_manager = queue_manager
    app.state.vector_store = vector_store

    red_app.state.llm = llm_instance
    red_app.state.queue_manager = queue_manager
    red_app.state.vector_store = vector_store

    blue_app.state.llm = llm_instance
    blue_app.state.queue_manager = queue_manager
    blue_app.state.vector_store = vector_store

    yield
    soc_task.cancel()

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

app.mount("/api/red", red_app)
app.mount("/api/blue", blue_app)

@app.get("/api/health", tags=["System"])
async def health_check():
    return {
        "status": "online",
        "service": "AI CyberRange Core",
        "message": "System initialized."
    }