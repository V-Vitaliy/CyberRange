import os
import aiofiles
from fastapi import APIRouter, Depends, Request, Query, HTTPException, UploadFile, File, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
import asyncio

from app.core.queue_manager import LLMQueueManager, get_queue_manager
from app.db.database import get_db
from app.db.chroma_client import VectorStore, get_vector_store
from app.core.security import get_current_user_vulnerable
from app.services.rag_service import generate_chat_response_sse
from app.schemas.red_team import ChatRequest
from app.services.etl_worker import ETLWorker


router = APIRouter()

# ---------------------------------------------------------
# CHAT ENDPOINT WITH RAG
# ---------------------------------------------------------
@router.post("/chat/ask", tags=["Red Team"])
async def chat_ask(
    chat_req: ChatRequest,
    queue: LLMQueueManager = Depends(get_queue_manager),
    vector_store: VectorStore = Depends(get_vector_store),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_vulnerable) # Injecting  vulnerable auth
):
    """
    Main chat endpoint for the Red Team.
    Retrieves context from ChromaDB (RAG) and streams the LLM response.
    Vulnerable to JWT bypass (alg: none).
    """
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

# ---------------------------------------------------------
# CHAT HISTORY ENDPOINT WITH SQL Injection
# ---------------------------------------------------------
@router.get("/chat/history", tags=["Red Team"])
async def search_chat_history(
    q: str = Query(..., description="Search query for chat history"),
    session_id: str = Query(..., description="Current game session ID"),
    db: AsyncSession = Depends(get_db)
):
    """
    VULNERABLE ENDPOINT: Search chat history.
    This endpoint intentionally bypasses SQLAlchemy ORM to demonstrate SQL Injection.
    """
    raw_sql_query = f"SELECT * FROM chat_history WHERE session_id = '{session_id}' AND user_message LIKE '%{q}%'"

    try:
        await db.execute(text("SET ROLE db_readonly"))
        result = await db.execute(text(raw_sql_query))
        await db.execute(text("RESET ROLE"))

        rows = result.mappings().all()
        return {"results": rows}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# ---------------------------------------------------------
# FILE UPLOAD WITH PATH TRAVERSAL
# ---------------------------------------------------------
@router.post("/upload", tags=["Red Team"])
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


# ---------------------------------------------------------
# ETL PROGRESS WEBSOCKET
# ---------------------------------------------------------
@router.websocket("/ws/etl-status/{session_id}")
async def etl_status_websocket(websocket: WebSocket, session_id: str):
    """
    WebSocket endpoint for streaming ETL progress to the frontend.
    """
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