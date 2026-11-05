import asyncio
import logging
import uuid

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import StreamingResponse
from redis import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import get_current_user_vulnerable
from app.core.config import settings
from app.core.queue_manager import LLMQueueManager, get_queue_manager
from app.core.redis_client import get_redis
from app.db.chroma_client import VectorStore, get_vector_store
from app.db.database import get_db
from app.db.object_store import ObjectStore, PathTraversalError, get_object_store, resolve_upload_target
from app.db.repository import ChatRepository
from app.db.sqli_isolation import IsolationError
from app.db.sqli_search import HistoryQueryError
from app.defense.service import enforce_rate_limit
from app.labs.service import get_authorized_session
from app.llm.provider import get_llm_instance
from app.schemas.red_team import ChatRequest
from app.services.etl_worker import ETLWorker
from app.services.rag_service import generate_chat_response_sse
from app.services.siem_logger import log_security_event

logger = logging.getLogger(__name__)

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
    await ChatRepository.get_or_create_thread(db, chat_req.thread_id, user["id"], chat_req.session_id, chat_req.prompt)

    # Log the LLM Prompt
    await log_security_event(
        db=db,
        lab_instance_id=lab_id,
        event_type="LLM_PROMPT",
        payload={"username": user.get("sub"), "claimed_role": user.get("role"), "prompt": chat_req.prompt},
        source_ip=client_ip,
    )

    return StreamingResponse(
        generate_chat_response_sse(
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
        db=db,
        lab_instance_id=lab_id,
        event_type="DB_QUERY",
        payload={"query_param": q, "target": "chat_history"},
        source_ip=request.client.host,
    )

    try:
        results, raw_query = await ChatRepository.search_chat_history_vulnerable(lab_id, session_id, q)
        return {"results": results}
    except (HistoryQueryError, IsolationError) as e:
        # Search owns a separate transaction; this session remains usable for SIEM.
        await log_security_event(
            db=db,
            lab_instance_id=lab_id,
            event_type="SQL_ERROR",
            payload={"error": str(e), "failed_query": q},
            source_ip=request.client.host,
        )
        raise HTTPException(status_code=503 if isinstance(e, IsolationError) else 400, detail=str(e))


@router.post("/upload", tags=["Red Team"])
async def upload_document(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    vector_store: VectorStore = Depends(get_vector_store),
    store: ObjectStore = Depends(get_object_store),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_vulnerable),
    redis=Depends(get_redis),
):
    # Read at most one byte past the limit so oversized files are never held in full.
    content = await file.read(settings.UPLOAD_MAX_BYTES + 1)
    rejection = None
    if len(content) > settings.UPLOAD_MAX_BYTES:
        rejection = (413, f"File exceeds the {settings.UPLOAD_MAX_BYTES} byte limit.")
    elif not content.startswith(b"%PDF-"):
        rejection = (415, "Only PDF documents are accepted.")
    if rejection:
        await log_security_event(
            db=db,
            lab_instance_id=user["lab_instance_id"],
            event_type="FILE_UPLOAD_REJECTED",
            payload={"filename": file.filename, "reason": rejection[1]},
            source_ip=request.client.host,
        )
        raise HTTPException(status_code=rejection[0], detail=rejection[1])

    task_id = uuid.uuid4()
    redis_key = f"etl_job:{task_id}"
    await redis.hset(
        redis_key,
        mapping={
            "filename": file.filename,
            "user_id": user["id"],
            "lab_instance_id": user["lab_instance_id"],
            "status": "Queued",
            "progress": 0,
            "message": "Queued...",
        },
    )

    await redis.expire(redis_key, 3600)

    # The uploads -> student_uploads traversal exercise stays inside this lab's key prefix.
    try:
        target = resolve_upload_target(user["lab_instance_id"], file.filename)
    except PathTraversalError:
        await log_security_event(
            db=db,
            lab_instance_id=user.get("lab_instance_id"),
            event_type="FILE_UPLOAD_FAILED",
            payload={"filename": file.filename, "reason": "Path Traversal Blocked"},
            source_ip=request.client.host,
        )
        raise HTTPException(status_code=403, detail="Wrong directory.")

    await store.put(target.key, content)
    await log_security_event(
        db=db,
        lab_instance_id=user.get("lab_instance_id"),
        event_type="FILE_UPLOAD",
        payload={"filename": file.filename, "destination": target.key, "indexing": str(target.indexed).lower()},
        source_ip=request.client.host,
    )

    if target.indexed:
        background_tasks.add_task(
            ETLWorker(vector_store).process_stored_pdf,
            store,
            target.key,
            file.filename,
            task_id,
            redis,
            uuid.UUID(user["lab_instance_id"]),
        )
        return {"task_id": task_id, "message": "Processing started."}

    await redis.hset(
        redis_key,
        mapping={"status": "Completed (No ETL required)", "progress": 100, "message": "File uploaded securely."},
    )
    return {"task_id": str(task_id), "message": "File uploaded successfully.", "chunks_vectorized": 0}


@router.websocket("/ws/etl-status/{task_id}")
async def etl_status_websocket(
    websocket: WebSocket,
    task_id: uuid.UUID,
    redis: Redis = Depends(get_redis),
    db: AsyncSession = Depends(get_db),
):
    try:
        user = await get_current_user_vulnerable(websocket.headers.get("authorization"), db)
    except HTTPException:
        await websocket.close(code=1008)
        return
    job = await redis.hgetall(f"etl_job:{task_id}")
    if job.get("user_id") != user["id"] or job.get("lab_instance_id") != user["lab_instance_id"]:
        await websocket.close(code=1008)
        return
    redis_key = f"etl_job:{task_id}"
    await websocket.accept()
    try:
        while True:
            redis_data = await redis.hgetall(redis_key)
            if not redis_data:
                await websocket.close(code=1000)
                break

            await websocket.send_json(redis_data)
            await asyncio.sleep(0.5)
            if int(redis_data.get("progress", 0)) >= 100:
                await websocket.close(code=1000)
                break

    except WebSocketDisconnect:
        logger.debug("ETL status socket for %s closed", task_id)
