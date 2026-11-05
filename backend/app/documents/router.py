import asyncio
import logging
import uuid

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from redis import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import get_current_user_vulnerable
from app.core.config import settings
from app.core.redis_client import get_redis
from app.db.database import get_db
from app.documents.chroma_client import VectorStore, get_vector_store
from app.documents.object_store import ObjectStore, PathTraversalError, get_object_store, resolve_upload_target
from app.documents.service import ETLWorker
from app.siem.service import log_security_event

logger = logging.getLogger(__name__)

router = APIRouter()


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
