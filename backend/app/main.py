import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.agents.soc_agent import create_soc_graph
from app.auth.router import blue_router as blue_auth_router
from app.auth.router import red_router as red_auth_router
from app.chat.router import router as chat_router
from app.core.config import require_strong_jwt_secret, settings
from app.core.queue_manager import LLMQueueManager
from app.ctf.router import router as ctf_router
from app.defense.router import router as defense_router
from app.documents.chroma_client import VectorStore
from app.documents.object_store import ObjectStore
from app.documents.router import router as documents_router
from app.llm.provider import create_llm
from app.siem.router import router as siem_router
from app.siem.soc_worker import run_soc_analyst_loop

# 2. Sub-application for Red Team Docs
red_app = FastAPI(
    title="Red Team API",
    description="Vulnerable endpoints for exploitation and data poisoning.",
    version="1.0.0",
)
red_app.include_router(red_auth_router, prefix="")
red_app.include_router(chat_router, prefix="")
red_app.include_router(documents_router, prefix="")
red_app.include_router(ctf_router, prefix="/ctf")

# 3. Sub-application for Blue Team Docs
blue_app = FastAPI(
    title="Blue Team API",
    description="Defense shop and system configuration endpoints.",
    version="1.0.0",
)
blue_app.include_router(blue_auth_router, prefix="")
blue_app.include_router(defense_router, prefix="")
blue_app.include_router(siem_router, prefix="")


@asynccontextmanager
async def lifespan(app: FastAPI):
    require_strong_jwt_secret(settings.JWT_SECRET)
    queue_manager = LLMQueueManager()
    vector_store = VectorStore()
    object_store = ObjectStore.from_settings()

    llm_instance = create_llm()

    app.state.llm = llm_instance
    app.state.queue_manager = queue_manager
    app.state.vector_store = vector_store

    red_app.state.llm = llm_instance
    red_app.state.queue_manager = queue_manager
    red_app.state.vector_store = vector_store
    red_app.state.object_store = object_store

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
    lifespan=lifespan,
)

# Allowance for multipart framing around the largest accepted upload.
MULTIPART_OVERHEAD_BYTES = 64 * 1024


@app.middleware("http")
async def reject_oversized_bodies(request, call_next):
    """Reject declared oversized bodies before they are read; routes enforce exact limits."""
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > settings.UPLOAD_MAX_BYTES + MULTIPART_OVERHEAD_BYTES:
        return JSONResponse(status_code=413, content={"detail": "Request body too large."})
    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.FRONTEND_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/api/red", red_app)
app.mount("/api/blue", blue_app)


@app.get("/api/health", tags=["System"])
async def health_check():
    return {"status": "online", "service": "AI CyberRange Core", "message": "System initialized."}
