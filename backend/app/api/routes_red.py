import os
import aiofiles
import asyncio
from fastapi import APIRouter, Depends, Request, Query, HTTPException, UploadFile, File, WebSocket, WebSocketDisconnect, status
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
from app.db.models import User
from app.services.rag_service import generate_chat_response_sse
from app.schemas.red_team import ChatRequest, LoginRequest
from app.services.etl_worker import ETLWorker
from app.core.redis_client import get_redis
from app.services.rate_limiter import enforce_rate_limit
from app.services.siem_logger import log_security_event

router = APIRouter()

@router.post("/login", tags=["Red Team Auth"])
async def red_team_login(login_req: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.username == login_req.username))
    user = result.scalars().first()

    if not user or not verify_password(login_req.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if user.role != "red_team":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access restricted")

    token_data = {
        "sub": user.username,
        "role": user.role,
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
    redis=Depends(get_redis)
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
        generate_chat_response_sse(db=db, session_id=chat_req.session_id, prompt=chat_req.prompt, queue=queue, vector_store=vector_store, user=user),
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

    raw_sql_query = f"SELECT * FROM chat_history WHERE session_id = '{session_id}' AND user_message LIKE '%{q}%'"

    try:
        await db.execute(text("SET ROLE db_readonly"))
        result = await db.execute(text(raw_sql_query))
        await db.execute(text("RESET ROLE"))
        return {"results": result.mappings().all()}
    except Exception as e:
        # Log the exact SQL error (Blue Team can see the syntax error caused by the injection)
        await log_security_event(
            db=db, lab_instance_id=lab_id, event_type="SQL_ERROR",
            payload={"error": str(e), "failed_query": raw_sql_query}, source_ip=request.client.host
        )
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/upload", tags=["Red Team"])
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    vector_store: VectorStore = Depends(get_vector_store),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_vulnerable)
):
    base_upload_dir = "uploads/"
    os.makedirs(base_upload_dir, exist_ok=True)
    vulnerable_path = os.path.join(base_upload_dir, file.filename)

    await log_security_event(
        db=db,
        lab_instance_id=user.get("lab_instance_id"),
        event_type="FILE_UPLOAD",
        payload={"filename": file.filename, "destination": vulnerable_path},
        source_ip=request.client.host
    )

    try:
        content = await file.read()
        async with aiofiles.open(vulnerable_path, 'wb') as out_file:
            await out_file.write(content)

        etl = ETLWorker(vector_store)
        chunks_inserted = await asyncio.to_thread(etl.process_pdf, content, file.filename)

        return {"filename": file.filename, "message": "File processed.", "chunks_vectorized": chunks_inserted}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.websocket("/ws/etl-status/{session_id}")
async def etl_status_websocket(websocket: WebSocket, session_id: str):
    await websocket.accept()
    try:
        stages = [
            {"status": "Processing", "progress": 10, "message": "Extracting text from PDF..."},
            {"status": "Processing", "progress": 50, "message": "Chunking text with NLTK..."},
            {"status": "Processing", "progress": 90, "message": "Generating embeddings (CPU)..."},
            {"status": "Complete", "progress": 100, "message": "Vectors loaded into ChromaDB!"}
        ]

        for stage in stages:
            await asyncio.sleep(1.5)
            await websocket.send_json(stage)

    except WebSocketDisconnect:
        print(f"Client {session_id} disconnected from ETL status stream.")