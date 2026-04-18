import json
import asyncio
import re
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.queue_manager import LLMQueueManager
from app.db.chroma_client import VectorStore
from app.db.models import GameSession

# Lazy-loaded reranker to prevent blocking application startup
_reranker = None

def get_reranker():
    global _reranker
    if _reranker is None:
        from sentence_transformers import CrossEncoder
        _reranker = CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2', max_length=512)
    return _reranker

async def generate_chat_response_sse(
    db: AsyncSession,
    session_id: UUID,
    prompt: str,
    queue: LLMQueueManager,
    vector_store: VectorStore,
    user: dict
):
    try:
        result = await db.execute(select(GameSession).where(GameSession.id == session_id))
        session = result.scalars().first()

        if not session:
            yield f"data: {json.dumps({'error': 'Game Session not found'})}\n\n"
            return

        retrieved_docs = vector_store.search_documents(prompt, n_results=5)

        context_texts = []
        if retrieved_docs and retrieved_docs.get('documents') and len(retrieved_docs['documents']) > 0:
            context_texts = [doc for doc in retrieved_docs['documents'][0] if doc]

        if session.use_reranker and context_texts:
            pairs = [[prompt, doc] for doc in context_texts]

            # Offload heavy CPU prediction to a separate thread to unblock the async event loop
            scores = await asyncio.to_thread(get_reranker().predict, pairs)

            scored_docs = list(zip(scores, context_texts))
            scored_docs.sort(key=lambda x: x[0], reverse=True)

            # Retain top 2 documents with positive semantic similarity
            context_texts = [doc for score, doc in scored_docs[:2] if score > 0]

        clean_username = re.sub(r'[^a-zA-Z0-9_]', '', user.get('username', 'Guest')) or 'Guest'

        context_block = (
            f"Context information is below.\n"
            f"---------------------\n"
            f"{chr(10).join(context_texts)}\n"
            f"---------------------\n"
        ) if context_texts else ""

        full_prompt = (
            f"{session.system_prompt}\n\n"
            f"{context_block}"
            f"User ({clean_username}): {prompt}\nAI:"
        )

        async for chunk in queue.enqueue_request(full_prompt):
            if chunk:
                yield f"data: {json.dumps({'text': chunk})}\n\n"

    except Exception as e:
        yield f"data: {json.dumps({'error': str(e)})}\n\n"
    finally:
        yield "data: [DONE]\n\n"