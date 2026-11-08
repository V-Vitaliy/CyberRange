import asyncio
import json
import logging
import re
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.llm_engine import run_agent_turn
from app.chat.prompt_builder import PromptBuilder
from app.chat.repository import ChatRepository
from app.chat.schemas import ChatThreadResponse
from app.core.queue_manager import LLMQueueManager
from app.documents.chroma_client import VectorStore
from app.labs.service import get_authorized_session, get_exercise_secret

logger = logging.getLogger(__name__)

CHAT_ERROR_MESSAGE = "The assistant could not answer. Please try again."

_reranker = None


async def get_or_create_thread(db: AsyncSession, thread_id, user_id, session_id, initial_prompt: str):
    """Return the caller's thread, creating it on first use; a foreign thread is a 404."""
    return await ChatRepository.get_or_create_thread(db, thread_id, user_id, session_id, initial_prompt)


async def add_seeded_thread(db: AsyncSession, user_id, session_id, title: str, messages: list[dict]):
    """A ready-made conversation for an exercise; staged in the caller's transaction."""
    return await ChatRepository.add_thread(
        db, id=uuid4(), user_id=user_id, session_id=session_id, title=title, messages=messages
    )


async def list_threads(db: AsyncSession, user_id, session_id) -> list[ChatThreadResponse]:
    """The student's own threads in a session, newest first, for the chat sidebar."""
    threads = await ChatRepository.get_threads_by_user_and_session(db, user_id, session_id)
    return [
        ChatThreadResponse(id=t.id, title=t.title, updated_at=t.updated_at, messages=t.messages or []) for t in threads
    ]


async def search_chat_history(lab_id, session_id, query):
    """Intentionally injectable history search, run by the lab's isolated reader."""
    return await ChatRepository.search_chat_history_vulnerable(lab_id, session_id, query)


def get_reranker():
    global _reranker
    if _reranker is None:
        from sentence_transformers import CrossEncoder

        _reranker = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2", max_length=512)
    return _reranker


def _rerank(prompt: str, documents: list[str]):
    # Runs in a worker thread: the first call also loads the model.
    return get_reranker().predict([[prompt, doc] for doc in documents])


async def generate_chat_response_sse(
    db: AsyncSession,
    session_id: UUID,
    thread_id: UUID,
    prompt: str,
    queue: LLMQueueManager,
    vector_store: VectorStore,
    user: dict,
    llm_instance,
):
    try:
        user_id = user.get("id")
        if not user_id:
            yield f"data: {json.dumps({'error': 'Invalid token: Missing user ID'})}\n\n"
            return

        session = await get_authorized_session(db, session_id, user["lab_instance_id"])
        await ChatRepository.get_or_create_thread(
            db=db,
            thread_id=thread_id,
            user_id=user_id,
            session_id=session_id,
            initial_prompt=prompt,
        )

        lab_instance_id = str(session.lab_instance_id)
        use_reranker = session.use_reranker
        system_instruction = session.system_prompt

        lab_filter = {"lab_instance_id": lab_instance_id}
        where = lab_filter if user.get("role") == "admin" else {"$and": [lab_filter, {"access_level": "public"}]}

        retrieved_docs = await asyncio.to_thread(vector_store.search_documents, prompt, n_results=5, where=where)
        context_texts = []

        if retrieved_docs and retrieved_docs.get("documents") and len(retrieved_docs["documents"]) > 0:
            context_texts = [doc for doc in retrieved_docs["documents"][0] if doc]

        if use_reranker and context_texts:
            scores = await asyncio.to_thread(_rerank, prompt, context_texts)
            scored_docs = list(zip(scores, context_texts))
            scored_docs.sort(key=lambda x: x[0], reverse=True)
            context_texts = [doc for score, doc in scored_docs[:2] if score > 0]

        clean_username = re.sub(r"[^a-zA-Z0-9_]", "", user.get("sub", "Guest")) or "Guest"

        # EX-03: the lab's secret stays in context even after Prompt Patch replaces the instruction.
        secret = await get_exercise_secret(db, session.lab_instance_id, "EX-03")
        # A turn can take many seconds: return the request's connection to the pool meanwhile.
        await db.rollback()

        system_prompt, human_prompt = PromptBuilder.build_prompts(
            user_query=prompt,
            context_chunks=context_texts,
            system_instruction=system_instruction,
            username=clean_username,
            confidential_note=secret,
        )

        full_ai_response = ""
        async for event in run_agent_turn(
            llm_instance=llm_instance,
            system_prompt=system_prompt,
            human_prompt=human_prompt,
            lab_instance_id=lab_instance_id,
            queue=queue,
        ):
            if event.get("text"):
                full_ai_response += event["text"]
            yield f"data: {json.dumps(event)}\n\n"

        await ChatRepository.append_messages(db=db, thread_id=thread_id, prompt=prompt, ai_response=full_ai_response)

    except Exception:
        logger.exception("Chat response failed for session %s", session_id)
        yield f"data: {json.dumps({'error': CHAT_ERROR_MESSAGE})}\n\n"
    finally:
        yield "data: [DONE]\n\n"
