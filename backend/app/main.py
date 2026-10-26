from contextlib import asynccontextmanager
import asyncio
import logging
from contextlib import suppress
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.queue_manager import LLMQueueManager
from app.db.chroma_client import VectorStore
from app.api.routes_red import router as red_router
from app.api.routes_ctf import router as ctf_router
from app.api.routes_blue import router as blue_router
from app.core.config import settings
from app.core.llm_engine import init_groq, init_llm, init_openai_compatible
from app.services.soc_worker import run_soc_analyst_loop
from app.agents.soc_agent import create_soc_graph

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

    if settings.LLM_PROVIDER == "openai_compatible":
        llm_instance = init_openai_compatible()
    elif settings.GROQ_API_KEY:
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

    soc_task = None
    try:
        soc_graph = create_soc_graph(llm_instance)
    except (NotImplementedError, ValueError, AttributeError):
        logging.getLogger(__name__).exception(
            "SOC structured output is unavailable for the selected model; logs remain pending"
        )
    else:
        soc_task = asyncio.create_task(run_soc_analyst_loop(soc_graph, queue_manager))

    try:
        yield
    finally:
        if soc_task is not None:
            soc_task.cancel()
            with suppress(asyncio.CancelledError):
                await soc_task

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
