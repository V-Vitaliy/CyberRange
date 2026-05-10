import asyncio
import json
import re
from uuid import UUID

from app.core.llm_engine import generate_stream, generate_stream_groq
from app.core.prompt_builder import PromptBuilder
from app.core.queue_manager import LLMQueueManager
from app.db.chroma_client import VectorStore
from app.db.repository import ChatRepository
from app.db.repository import GameSessionRepository
from groq import AsyncGroq
from sqlalchemy.ext.asyncio import AsyncSession

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
    thread_id: UUID,
    prompt: str,
    queue: LLMQueueManager,
    vector_store: VectorStore,
    user: dict,
    llm_instance
):
    try:
        thread = await ChatRepository.get_or_create_thread(
                    db=db,
                    thread_id=thread_id,
                    user_id=user.get("id"),
                    session_id=session_id,
                    initial_prompt=prompt
                    )
        session = await GameSessionRepository.get_by_id(db, session_id)

        if not session:
            yield f"data: {json.dumps({'error': 'Game Session not found'})}\n\n"
            return

        where = None if user.get("role") == "admin" else {'access_level': 'public'}

        retrieved_docs = vector_store.search_documents(prompt, n_results=5, where=where)
        context_texts = []

        if retrieved_docs and retrieved_docs.get('documents') and len(retrieved_docs['documents']) > 0:
            context_texts = [doc for doc in retrieved_docs['documents'][0] if doc]

        if session.use_reranker and context_texts:
            pairs = [[prompt, doc] for doc in context_texts]
            scores = await asyncio.to_thread(get_reranker().predict, pairs)
            scored_docs = list(zip(scores, context_texts))
            scored_docs.sort(key=lambda x: x[0], reverse=True)
            context_texts = [doc for score, doc in scored_docs[:2] if score > 0]

        clean_username = re.sub(r'[^a-zA-Z0-9_]', '', user.get('username', 'Guest')) or 'Guest'

        full_prompt = PromptBuilder.build_prompt(
            user_query=prompt,
            context_chunks=context_texts,
            system_instruction=session.system_prompt,
            username=clean_username
        )

        async def llm_task():
            if isinstance(llm_instance, AsyncGroq):
                async for chunk in generate_stream_groq(llm_instance, full_prompt):
                    yield chunk
            else:
                async for chunk in generate_stream(llm_instance, full_prompt):
                    yield chunk

        full_ai_response = ""

        async for chunk in queue.stream_with_lock(llm_task):
            if chunk:
                full_ai_response += chunk
                yield f"data: {json.dumps({'text': chunk})}\n\n"

        await ChatRepository.append_messages(
            db=db,
            thread_id = thread.id,
            prompt=prompt,
            ai_response=full_ai_response
            )

    except Exception as e:
        yield f"data: {json.dumps({'error': str(e)})}\n\n"
    finally:
        yield "data: [DONE]\n\n"