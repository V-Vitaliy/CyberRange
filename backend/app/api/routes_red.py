import json
import asyncio
from fastapi import APIRouter, Depends, Request, Query, HTTPException, Header
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
import jwt # Make sure you have PyJWT installed

from app.core.queue_manager import LLMQueueManager, get_queue_manager
from app.db.database import get_db
from app.db.chroma_client import VectorStore, get_vector_store
from app.core.config import settings

router = APIRouter()

class ChatRequest(BaseModel):
    prompt: str
    session_id: str

# ---------------------------------------------------------
# VULNERABLE JWT VALIDATION
# ---------------------------------------------------------
async def get_current_user_vulnerable(authorization: str = Header(None)):
    """
    VULNERABLE: This function verifies the JWT token but intentionally
    bypasses signature validation. This allows attackers to forge tokens
    and elevate privileges (e.g., change their role to 'admin').
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    token = authorization.split(" ")[1]

    try:
        # Intentionally vulnerable: Signature verification is disabled.
        # An attacker can modify the payload (e.g., change role) without invalidating the token.
        payload = jwt.decode(token, options={"verify_signature": False})

        return payload
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")


# ---------------------------------------------------------
# CHAT ENDPOINT WITH RAG
# ---------------------------------------------------------
async def sse_generator(request: ChatRequest, queue: LLMQueueManager, vector_store: VectorStore, user: dict):
    await queue.wait_for_turn()
    try:
        yield f"event: ping\ndata: {{}}\n\n"

        # 1. RAG: Retrieve context from ChromaDB
        query_emb = vector_store.encoder.encode(request.prompt).tolist()
        results = vector_store.collection.query(query_embeddings=[query_emb], n_results=2)

        # Format the retrieved context
        context = ""
        if results and results['documents'] and results['documents'][0]:
            context = "Context: " + " ".join(results['documents'][0])

        # 2. Build the final prompt (Prompt Injection target)
        final_prompt = f"{context}\n\nUser: {request.prompt}\nAI:"

        # --- MOCK LLM GENERATION (Will be replaced with real Llama-3 later) ---
        yield f"event: message\ndata: {json.dumps({'token': f'[User role: {user.get('role', 'unknown')}] '})}\n\n"

        tokens = ["Processing ", "with ", "context: ", f"{context[:20]}..."]
        for token in tokens:
            payload = json.dumps({"token": token})
            yield f"event: message\ndata: {payload}\n\n"
            await asyncio.sleep(0.1)

        yield f"event: complete\ndata: {{}}\n\n"

    except Exception as e:
        error_payload = json.dumps({"error": str(e)})
        yield f"event: error\ndata: {error_payload}\n\n"
    finally:
        queue.release_turn()


@router.post("/chat/ask", tags=["Red Team"])
async def chat_ask(
    chat_req: ChatRequest,
    request: Request,
    queue: LLMQueueManager = Depends(get_queue_manager),
    vector_store: VectorStore = Depends(get_vector_store),
    user: dict = Depends(get_current_user_vulnerable) # Injecting our vulnerable auth
):
    """
    Main chat endpoint for the Red Team.
    Retrieves context from ChromaDB (RAG) and streams the LLM response.
    Vulnerable to JWT bypass (alg: none).
    """
    return StreamingResponse(
        sse_generator(chat_req, queue, vector_store, user),
        media_type="text/event-stream"
    )

# ---------------------------------------------------------
# CHAT HISTORY ENDPOINT WITH SQL Injection
# ---------------------------------------------------------
@router.get("/chat/history", tags=["Red Team"])
async def search_chat_history(
    # The 'q' parameter is the search query from the user
    q: str = Query(..., description="Search query for chat history"),
    session_id: str = Query(..., description="Current game session ID"),
    db: AsyncSession = Depends(get_db)
):
    """
    VULNERABLE ENDPOINT: Search chat history.
    This endpoint intentionally bypasses SQLAlchemy ORM to demonstrate SQL Injection.
    """
    # Intentionally vulnerable raw SQL query using direct string formatting
    raw_sql_query = f"SELECT * FROM chat_history WHERE session_id = '{session_id}' AND user_message LIKE '%{q}%'"

    try:
        # Switch to a read-only role to prevent destructive attacks (e.g., DROP TABLE)
        await db.execute(text("SET ROLE db_readonly"))

        # Execute the vulnerable query
        result = await db.execute(text(raw_sql_query))

        # Reset role back to the default application user
        await db.execute(text("RESET ROLE"))

        # Fetch all results
        rows = result.mappings().all()
        return {"results": rows}

    except Exception as e:
        # Deliberately expose the raw database error message to aid attackers (students)
        raise HTTPException(status_code=400, detail=str(e))