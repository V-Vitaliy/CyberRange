import logging
import os

from fastapi import Request
from langchain_community.chat_models import ChatLlamaCpp
from langchain_core.language_models import BaseChatModel
from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)

MODEL_PATH = os.path.join(os.path.dirname(__file__), settings.LLM_MODEL_PATH)


def create_llm() -> BaseChatModel:
    """Select the chat model: explicit provider first, then Groq by key, else local GGUF."""
    if settings.LLM_PROVIDER == "openai_compatible":
        return init_openai_compatible()
    if settings.GROQ_API_KEY:
        return init_groq()
    if settings.LLM_MAX_CONCURRENCY != 1:
        # One llama.cpp context cannot run two generations at once.
        raise ValueError(
            "LLM_MAX_CONCURRENCY must be 1 for the in-process GGUF model; to serve requests in parallel, "
            "run a model server (for example llama-server --parallel N) with LLM_PROVIDER=openai_compatible"
        )
    return init_gguf()


def init_gguf() -> ChatLlamaCpp:
    """Load the local Llama-3 GGUF model into VRAM using llama-cpp-python."""
    logger.info(f"Loading model from {MODEL_PATH}...")

    llm = ChatLlamaCpp(
        model_path=MODEL_PATH,
        n_threads=settings.LLM_N_THREADS,
        n_batch=settings.LLM_N_BATCH,
        n_gpu_layers=settings.LLM_N_GPU_LAYERS,
        n_ctx=settings.LLM_N_CTX,
        temperature=0.1,
        max_tokens=512,
        verbose=False,
        streaming=True,
    )

    logger.info("Local LangChain model loaded")
    return llm


def init_openai_compatible() -> ChatOpenAI:
    """Use the chat-completions API, including for models without cloud keys."""
    if not settings.LLM_BASE_URL.strip() or not settings.LLM_MODEL.strip():
        raise ValueError("LLM_BASE_URL and LLM_MODEL must be nonempty")
    return ChatOpenAI(
        base_url=settings.LLM_BASE_URL,
        model=settings.LLM_MODEL,
        api_key=settings.LLM_API_KEY or "local-no-key",
        temperature=0.1,
        max_tokens=settings.LLM_MAX_TOKENS,
        streaming=True,
        stream_usage=False,
        use_responses_api=False,
        timeout=120,
        max_retries=0,
    )


def init_groq() -> ChatGroq:
    logger.info("Initializing Groq API via LangChain...")
    return ChatGroq(
        api_key=settings.GROQ_API_KEY, model="llama-3.1-8b-instant", temperature=0.1, max_tokens=512, streaming=True
    )


def get_llm_instance(request: Request) -> BaseChatModel:
    return request.app.state.llm
