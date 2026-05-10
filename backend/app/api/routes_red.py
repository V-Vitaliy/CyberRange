import os
import aiofiles
import asyncio
from redis import Redis
import uuid
from fastapi import APIRouter, Depends, Request, Query, HTTPException, UploadFile, File, WebSocket, WebSocketDisconnect, status, BackgroundTasks
from fastapi.responses import StreamingResponse
from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.queue_manager import LLMQueueManager, get_queue_manager
from app.db.database import get_db
from app.db.chroma_client import VectorStore, get_vector_store
from app.core.security import (
    get_current_user_vulnerable,
    verify_password,
    create_access_token
)
from app.services.rag_service import generate_chat_response_sse
from app.schemas.red_team import ChatRequest, LoginRequest
from app.services.etl_worker import ETLWorker
from app.core.redis_client import get_redis
from app.services.rate_limiter import enforce_rate_limit
from app.services.siem_logger import log_security_event
from app.core.llm_engine import get_llm_instance
from app.db.repository import UserRepository

from app.db.repository import ChatRepository

router = APIRouter()

@router.post("/login", tags=["Red Team Auth"])
async def red_team_login(login_req: LoginRequest, db: AsyncSession = Depends(get_db)):
    user = await UserRepository.get_by_username(db, login_req.username)

    if not user or not verify_password(login_req.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if user.role != "red_team":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access restricted")

    token_data = {
        "sub": user.username,
        "role": user.role,
        "id":str(user.id),
        "lab_instance_id": str(user.lab_instance_id)
    }

    token = create_access_token(data=token_data)

    # Log successful login
    await log_security_event(
        db=db,
        lab_instance_id=str(user.lab_instance_id),
        event_type="RED_TEAM_LOGIN",
        payload={"username": user.username, "status": "success"}
    )

    return {"access_token": token, "token_type": "bearer", "role": user.role}

@router.post("/chat/ask", tags=["Red Team"])
async def chat_ask(
    request: Request,
    chat_req: ChatRequest,
    queue: LLMQueueManager = Depends(get_queue_manager),
    vector_store: VectorStore = Depends(get_vector_store),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_vulnerable),
    redis=Depends(get_redis),
    llm_instance=Depends(get_llm_instance)
):
    lab_id = user.get("lab_instance_id")
    client_ip = request.client.host

    await enforce_rate_limit(db=db, redis=redis, session_id=chat_req.session_id, client_ip=client_ip)

    # Log the LLM Prompt
    await log_security_event(
        db=db,
        lab_instance_id=lab_id,
        event_type="LLM_PROMPT",
        payload={
            "username": user.get("sub"),
            "claimed_role": user.get("role"),
            "prompt": chat_req.prompt
        },
        source_ip=client_ip
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
            llm_instance=llm_instance),
        media_type="text/event-stream"
    )

@router.get("/chat/history", tags=["Red Team"])
async def search_chat_history(
    request: Request,
    q: str = Query(..., description="Search query"),
    session_id: str = Query(..., description="Session ID"),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_vulnerable)
):
    lab_id = user.get("lab_instance_id", "00000000-0000-0000-0000-000000000000")

    # Log the potential SQLi query
    await log_security_event(
        db=db,
        lab_instance_id=lab_id,
        event_type="DB_QUERY",
        payload={"query_param": q, "target": "chat_history"},
        source_ip=request.client.host
    )


    try:
        results, raw_query = ChatRepository.search_chat_history_vulnerable(db, session_id, q)
        return {"results": results}
    except Exception as e:
        # Log the exact SQL error (Blue Team can see the syntax error caused by the injection)
        await log_security_event(
            db=db, lab_instance_id=lab_id, event_type="SQL_ERROR",
            payload={"error": str(e), "failed_query": q}, source_ip=request.client.host
        )
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/upload", tags=["Red Team"])
async def upload_document(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    vector_store: VectorStore = Depends(get_vector_store),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_vulnerable),
    redis=Depends(get_redis),

):
    task_id = uuid.uuid4()
    redis_key = f"etl_job:{task_id}"
    await redis.hset(redis_key, mapping={
        "filename": file.filename,
        "status": "Queued",
        "progress": 0,
        "message": "Queued..."
    })


    base_upload_dir = "uploads"
    ALLOWED_PATH_PARTS = ["student_uploads"]

    absBase = os.path.abspath(base_upload_dir)
    absallowed = tuple(os.path.abspath(dir) for dir in ALLOWED_PATH_PARTS)

    os.makedirs(absBase, exist_ok=True)
    for d in ALLOWED_PATH_PARTS:
        os.makedirs(os.path.abspath(d), exist_ok=True)

    vulnerable_path = os.path.join(base_upload_dir, file.filename)
    validated_path = os.path.abspath(vulnerable_path)

    content = await file.read()

    if not validated_path.startswith(absallowed) and not validated_path.startswith(absBase):
        await log_security_event(
            db=db,
            lab_instance_id=user.get("lab_instance_id"),
            event_type="FILE_UPLOAD_FAILED",
            payload={"filename": file.filename, "reason": "Path Traversal Blocked"},
            source_ip=request.client.host
        )
        raise HTTPException(status_code=403, detail="Wrong directory.")

    os.makedirs(os.path.dirname(validated_path), exist_ok=True)
    async with aiofiles.open(validated_path, 'wb') as out_file:
            await out_file.write(content)

    if validated_path.startswith(absallowed):

        await log_security_event(
            db=db,
            lab_instance_id=user.get("lab_instance_id"),
            event_type="FILE_UPLOAD",
            payload={"filename": file.filename, "destination": validated_path, 'indexing': 'true'},
            source_ip=request.client.host
        )

        try:
            etl = ETLWorker(vector_store)
            background_tasks.add_task(etl.process_pdf, content, file.filename, task_id, redis)

            return {"task_id": task_id, "message": "Processing started."}
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    else:

        await log_security_event(
            db=db,
            lab_instance_id=user.get("lab_instance_id"),
            event_type="FILE_UPLOAD",
            payload={"filename": file.filename, "destination": validated_path, "indexing": "false"},
            source_ip=request.client.host
        )

        await redis.hset(redis_key, mapping={
            "status": "Completed (No ETL required)",
            "progress": 100,
            "message": "File uploaded securely."
        })

        return {
            "task_id": str(task_id),
            "message": "File uploaded successfully.",
            "chunks_vectorized": 0
        }


@router.websocket("/ws/etl-status/{task_id}")
async def etl_status_websocket(websocket: WebSocket, task_id: uuid.UUID, redis: Redis = Depends(get_redis)):
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
        print(f"{task_id} Finished ")