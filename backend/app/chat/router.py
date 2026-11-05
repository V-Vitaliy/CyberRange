from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import get_current_user_vulnerable
from app.chat import service
from app.chat.schemas import ChatRequest
from app.chat.sqli_isolation import IsolationError
from app.chat.sqli_search import HistoryQueryError
from app.core.queue_manager import LLMQueueManager, get_queue_manager
from app.core.redis_client import get_redis
from app.db.database import get_db
from app.defense.service import enforce_rate_limit
from app.documents.chroma_client import VectorStore, get_vector_store
from app.labs.service import get_authorized_session
from app.llm.provider import get_llm_instance
from app.siem.service import log_security_event

router = APIRouter()


@router.post("/chat/ask", tags=["Red Team"])
async def chat_ask(
    request: Request,
    chat_req: ChatRequest,
    queue: LLMQueueManager = Depends(get_queue_manager),
    vector_store: VectorStore = Depends(get_vector_store),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_vulnerable),
    redis=Depends(get_redis),
    llm_instance=Depends(get_llm_instance),
):
    lab_id = user.get("lab_instance_id")
    client_ip = request.client.host

    await get_authorized_session(db, chat_req.session_id, lab_id)
    await enforce_rate_limit(db=db, redis=redis, session_id=chat_req.session_id, client_ip=client_ip)
    # Validate thread ownership before streaming starts.
    await service.get_or_create_thread(db, chat_req.thread_id, user["id"], chat_req.session_id, chat_req.prompt)

    # Log the LLM Prompt
    await log_security_event(
        lab_instance_id=lab_id,
        event_type="LLM_PROMPT",
        payload={"username": user.get("sub"), "claimed_role": user.get("role"), "prompt": chat_req.prompt},
        source_ip=client_ip,
    )

    return StreamingResponse(
        service.generate_chat_response_sse(
            db=db,
            thread_id=chat_req.thread_id,
            session_id=chat_req.session_id,
            prompt=chat_req.prompt,
            queue=queue,
            vector_store=vector_store,
            user=user,
            llm_instance=llm_instance,
        ),
        media_type="text/event-stream",
    )


@router.get("/chat/history", tags=["Red Team"])
async def search_chat_history(
    request: Request,
    q: str = Query(..., description="Search query"),
    session_id: str = Query(..., description="Session ID"),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_vulnerable),
):
    lab_id = user.get("lab_instance_id", "00000000-0000-0000-0000-000000000000")

    await get_authorized_session(db, session_id, lab_id)

    # Log the potential SQLi query
    await log_security_event(
        lab_instance_id=lab_id,
        event_type="DB_QUERY",
        payload={"query_param": q, "target": "chat_history"},
        source_ip=request.client.host,
    )

    try:
        results, raw_query = await service.search_chat_history(lab_id, session_id, q)
        return {"results": results}
    except (HistoryQueryError, IsolationError) as e:
        # Search owns a separate transaction; this session remains usable for SIEM.
        await log_security_event(
            lab_instance_id=lab_id,
            event_type="SQL_ERROR",
            payload={"error": str(e), "failed_query": q},
            source_ip=request.client.host,
        )
        raise HTTPException(status_code=503 if isinstance(e, IsolationError) else 400, detail=str(e))
