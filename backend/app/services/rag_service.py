import json
import asyncio
from typing import AsyncGenerator

from app.core.queue_manager import LLMQueueManager
from app.db.chroma_client import VectorStore

async def generate_chat_response_sse(
    prompt: str, 
    queue: LLMQueueManager, 
    vector_store: VectorStore, 
    user: dict
) -> AsyncGenerator[str, None]:
    """
    Service layer for Retrieval-Augmented Generation (RAG).
    Handles queue waiting, ChromaDB retrieval, and SSE stream generation.
    """
    await queue.wait_for_turn()
    try:
        yield f"event: ping\ndata: {{}}\n\n"

        # 1. RAG: Retrieve context from ChromaDB
        query_emb = vector_store.encoder.encode(prompt).tolist()
        results = vector_store.collection.query(query_embeddings=[query_emb], n_results=2)

        # Format the retrieved context
        context = ""
        if results and results['documents'] and results['documents'][0]:
            context = "Context: " + " ".join(results['documents'][0])

        # 2. Build the final prompt (Prompt Injection target)
        final_prompt = f"{context}\n\nUser: {prompt}\nAI:"

        # --- MOCK LLM GENERATION (Will be replaced with real Llama-3 later) ---
        mock_response = f"[User role: {user.get('role', 'unknown')}] "
        yield f"event: message\ndata: {json.dumps({'token': mock_response })}\n\n"

        tokens = ["Processing ", "with ", "context: ", f"{context[:20]}..."]
        for token in tokens:
            payload = json.dumps({"token": token})
            yield f"event: message\ndata: {payload}\n\n"
            await asyncio.sleep(0.1)

        yield f"event: complete\ndata: {{}}\n\n"

    except Exception as e:
        error_payload = json.dumps({"error": str(e)})
        yield f"event: error\ndata: {error_payload}\n\n"
    finally:
        queue.release_turn()