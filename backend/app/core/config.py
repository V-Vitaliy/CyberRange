from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """
    Application settings.
    Values are overridden by environment variables in the .env file.
    """

    # Project Info
    PROJECT_NAME: str = "AI CyberRange"
    VERSION: str = "1.0.0"

    # An empty key selects the existing local LLM path.
    GROQ_API_KEY: str = ""

    # Empty preserves automatic Groq/GGUF selection.
    LLM_PROVIDER: Literal["", "openai_compatible"] = ""
    LLM_BASE_URL: str = ""
    LLM_MODEL: str = ""
    LLM_API_KEY: str = ""
    # HTTP models may spend part of this budget on reasoning before answering.
    LLM_MAX_TOKENS: int = Field(default=2048, gt=0)

    # Input limits against resource exhaustion.
    UPLOAD_MAX_BYTES: int = Field(default=10 * 1024 * 1024, gt=0)
    PDF_MAX_PAGES: int = Field(default=200, gt=0)
    PROMPT_MAX_CHARS: int = Field(default=4000, gt=0)

    # Browser origins allowed to call the API with credentials (JSON list in .env).
    FRONTEND_ORIGINS: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]

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

    # Chromadb
    CHROMA_HOST: str = "localhost"
    CHROMA_PORT: int = 8000

    # Postgres
    # Credentials come only from the environment or .env.
    DATABASE_URL: str = "postgresql+asyncpg://cyberadmin@127.0.0.1:5432/cyberrange"

    # Explicit local JSON mapping {lab UUID: restricted PostgreSQL URL}.
    # Empty means history SQLi is unavailable; never fall back to DATABASE_URL.
    SQLI_CONNECTIONS_FILE: str = ""

    # Required: the API refuses to start with an empty, short or placeholder value.
    JWT_SECRET: str = ""
    JWT_ALGORITHM: str = "HS256"

    REDIS_URL: str = "redis://localhost:6379"

    # An empty host selects EmailService's existing mock behavior.
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    # Disable only for a local sink such as Mailpit that does not offer STARTTLS.
    SMTP_STARTTLS: bool = True
    SMTP_FROM: str = "assistant@lab.local"
    # Comma-separated recipient domains the agent may email when SMTP_HOST is set.
    EMAIL_ALLOWED_DOMAINS: str = "lab.local"

    model_config = SettingsConfigDict(env_file=str(ROOT_DIR / ".env"), env_file_encoding="utf-8", extra="ignore")


MIN_JWT_SECRET_BYTES = 32
# Values that were published as defaults or examples and must never sign tokens.
PUBLISHED_JWT_SECRETS = {
    "super-secret-blue-team-key",
    "replace_this_with_a_long_random_string_in_production",
}


def require_strong_jwt_secret(secret: str) -> None:
    """Reject secrets that would let lab users forge signed lab scopes or Blue Team tokens."""
    if secret in PUBLISHED_JWT_SECRETS or len(secret.encode()) < MIN_JWT_SECRET_BYTES:
        raise RuntimeError(
            f"JWT_SECRET must be a private random value of at least {MIN_JWT_SECRET_BYTES} bytes; "
            "generate one with: openssl rand -hex 32"
        )


settings = Settings()
