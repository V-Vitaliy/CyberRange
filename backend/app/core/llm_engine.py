import os
import logging
import asyncio
from llama_cpp import Llama
from app.core.config import settings
from app.core.queue_manager import LLMQueueManager
from groq import AsyncGroq
from fastapi import Request

logger = logging.getLogger(__name__)

# Path to the downloaded model weights (resolved relatively to this file)
MODEL_PATH = os.path.join(os.path.dirname(__file__), settings.LLM_MODEL_PATH)



def init_llm() -> Llama:
    """
    Initializes the Llama-3 model into VRAM using llama-cpp-python.
    """
    logger.info(f"Loading model from {MODEL_PATH}...")

    llm = Llama(
        model_path=MODEL_PATH,
        n_threads=settings.LLM_N_THREADS,
        n_batch=settings.LLM_N_BATCH,
        flash_attn=settings.LLM_FLASH_ATTN,
        type_k=settings.LLM_CACHE_TYPE_K,
        n_gpu_layers=settings.LLM_N_GPU_LAYERS,
        n_ctx=settings.LLM_N_CTX,
        verbose=False
    )
    
    print("Model loaded successfully!")
    return llm

def init_groq() -> AsyncGroq:
    return AsyncGroq(api_key=settings.GROQ_API_KEY)

async def generate_stream(llm: Llama, prompt: str):
    """Yields tokens from the LLM asynchronously to prevent event loop blocking."""
    streamer = llm(
        prompt,
        max_tokens=512,
        stop=["User:", "\nUser "],
        stream=True
    )

    for output in streamer:
        token = output["choices"][0]["text"]
        yield token
        await asyncio.sleep(0.01)


async def generate_stream_groq(client: AsyncGroq, prompt: str, model: str = "llama-3.1-8b-instant"):
    stream = await client.chat.completions.create(
        messages=[{"role": "user", "content": prompt}],
        model=model,
        stream=True,
        max_tokens=512,
        stop=["User:", "\nUser "]
    )
    async for chunk in stream:
        token = chunk.choices[0].delta.content
        if token:
            yield token

def get_llm_instance(request: Request):
    return request.app.state.llm

