from pydantic_settings import BaseSettings
from pydantic import ConfigDict

class Settings(BaseSettings):
    """
    Application settings.
    Values are overridden by environment variables in the .env file.
    """
    # Project Info
    PROJECT_NAME: str = "AI CyberRange"
    VERSION: str = "1.0.0"

    # LLM Hardware Optimizations (Defaults based on g4dn.xlarge / RTX 3060)
    LLM_MODEL_PATH: str = "../../models/Meta-Llama-3-8B.Q4_K_M.gguf"

    # CPU threads for non-GPU operations (PDF Section 7: 4 threads)
    LLM_N_THREADS: int = 4

    # Batch size for prompt processing (PDF Section 7: 512)
    LLM_N_BATCH: int = 512

    # Enable Flash Attention for faster, memory-efficient generation
    LLM_FLASH_ATTN: bool = True

    # KV-cache quantization (4-bit)
    LLM_CACHE_TYPE_K: str = "q4_0"

    # Number of layers to offload to GPU
    LLM_N_GPU_LAYERS: int = -1

    # Context window size (Standard for Llama-3)
    LLM_N_CTX: int = 8192

    #Chromadb
    CHROMA_HOST: str = "localhost"
    CHROMA_PORT: int = 8000

    # Pydantic v2 configuration to read from .env file
    model_config = ConfigDict(env_file=".env", extra="ignore")

# Global settings instance to be imported across the app
settings = Settings()