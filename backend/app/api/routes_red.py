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

router = APIRouter()

@router.post("/login", tags=["Red Team - User"])
async def red_team_login(
    login_data: LoginRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Authenticates Red Team members and provides an initial valid JWT.
    This token contains the lab_instance_id for tracking across the system.
    """
    result = await db.execute(select(User).where(User.username == login_data.username))
    user = result.scalars().first()

    if not user or not verify_password(login_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials"
        )

    if user.role != "red_team":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access restricted to Red Team members"
        )

    # lab_instance_id is the key for SIEM attribution
    token_data = {
        "sub": user.username,
        "role": user.role,
        "lab_instance_id": str(user.lab_instance_id)
    }

    token = create_access_token(data=token_data)

    return {
        "access_token": token,
        "token_type": "bearer",
        "role": user.role
    }



@router.post("/chat/ask", tags=["Red Team - Chat"])
async def chat_ask(
    request: Request,
    chat_req: ChatRequest,
    queue: LLMQueueManager = Depends(get_queue_manager),
    vector_store: VectorStore = Depends(get_vector_store),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_vulnerable),
    redis=Depends(get_redis)
):
    """
    Main chat endpoint for the Red Team.
    Retrieves context from ChromaDB (RAG) and streams the LLM response.
    Vulnerable to JWT bypass (alg: none). Includes dynamic rate limiting.
    """
    await enforce_rate_limit(
        db=db,
        redis=redis,
        session_id=chat_req.session_id,
        client_ip=request.client.host
    )

    return StreamingResponse(
        generate_chat_response_sse(
            db=db,
            session_id=chat_req.session_id,
            prompt=chat_req.prompt,
            queue=queue,
            vector_store=vector_store,
            user=user
        ),
        media_type="text/event-stream"
    )

@router.get("/chat/history", tags=["Red Team - Chat"])
async def search_chat_history(
    q: str = Query(..., description="Search query for chat history"),
    session_id: str = Query(..., description="Current game session ID"),
    db: AsyncSession = Depends(get_db)
):
    raw_sql_query = f"SELECT * FROM chat_history WHERE session_id = '{session_id}' AND user_message LIKE '%{q}%'"

    try:
        await db.execute(text("SET ROLE db_readonly"))
        result = await db.execute(text(raw_sql_query))
        await db.execute(text("RESET ROLE"))

        rows = result.mappings().all()
        return {"results": rows}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/upload", tags=["Red Team - Upload"])
async def upload_document(
    file: UploadFile = File(...),
    vector_store: VectorStore = Depends(get_vector_store)
):
    base_upload_dir = "uploads/"
    os.makedirs(base_upload_dir, exist_ok=True)

    vulnerable_path = os.path.join(base_upload_dir, file.filename)

    try:
        content = await file.read()

        async with aiofiles.open(vulnerable_path, 'wb') as out_file:
            await out_file.write(content)

        etl = ETLWorker(vector_store)
        chunks_inserted = await asyncio.to_thread(etl.process_pdf, content, file.filename)

        return {
            "filename": file.filename,
            "message": "File uploaded and processed successfully.",
            "chunks_vectorized": chunks_inserted
        }
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