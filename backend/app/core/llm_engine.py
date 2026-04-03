import os
from llama_cpp import Llama
from backend.app.core.config import settings

# Path to the downloaded model weights (resolved relatively to this file)
MODEL_PATH = os.path.join(os.path.dirname(__file__), settings.LLM_MODEL_PATH)

def init_llm() -> Llama:
    """
    Initializes the Llama-3 model into VRAM using llama-cpp-python.
    """
    print(f"Loading model from {MODEL_PATH}...")

    # We initialize the LLM with hardware optimizations pulled dynamically from config.
    # This prevents CUDA Out Of Memory (OOM) errors and allows easy scaling.

    llm = Llama(
        model_path=MODEL_PATH,

        # CPU threads for processing
        n_threads=settings.LLM_N_THREADS,

        # Batch size for prompt processing
        n_batch=settings.LLM_N_BATCH,

        # Flash Attention for memory-efficient generation
        flash_attn=settings.LLM_FLASH_ATTN,

        # KV-cache quantization to save context memory
        type_k=settings.LLM_CACHE_TYPE_K,

        # Offload layers to GPU
        n_gpu_layers=settings.LLM_N_GPU_LAYERS,

        # Context window size
        n_ctx=settings.LLM_N_CTX,
        
        # Disable verbose logging to keep the console clean for SIEM logs
        verbose=False
    )
    
    print("Model loaded successfully!")
    return llm

# Global instance to be used by FastAPI endpoints.
# This will be properly initialized during the FastAPI lifespan event in main.py.
llm_instance = None