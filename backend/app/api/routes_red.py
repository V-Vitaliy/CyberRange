import json
import asyncio
from fastapi import APIRouter, Depends, Request, Query, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.queue_manager import LLMQueueManager, get_queue_manager
# from app.core.llm_engine import llm_instance (We will connect the real LLM later)
from app.db.database import get_db #will be created later

router = APIRouter()

class ChatRequest(BaseModel):
    prompt: str
    session_id: str

async def sse_generator(request: ChatRequest, queue: LLMQueueManager):
    """
    Generator function that streams tokens back to the client using SSE format.
    """
    # Wait for our turn in the GPU queue before starting generation.
    await queue.wait_for_turn()

    try:
        # Send a heartbeat event to keep the connection alive
        yield f"event: ping\ndata: {{}}\n\n"

        # --- MOCK LLM GENERATION ---
        # (We use dummy text here to test the SSE connection.
        # In Sprint 2, we will replace this with actual Llama-3 inference.)
        tokens = ["System ", "initialized. ", "Ready ", "for ", "security ", "analysis."]
        for token in tokens:
            payload = json.dumps({"token": token})
            # Sending standard message events
            yield f"event: message\ndata: {payload}\n\n"
            await asyncio.sleep(0.1) # Simulating GPU generation time

        # Yield the completion event
        yield f"event: complete\ndata: {{}}\n\n"

    except Exception as e:
        # Yield an error event if something goes wrong (e.g., CUDA OOM)
        error_payload = json.dumps({"error": str(e)})
        yield f"event: error\ndata: {error_payload}\n\n"

    finally:
        # Release the GPU lock!
        queue.release_turn()


@router.post("/chat/ask", tags=["Red Team"])
async def chat_ask(
    chat_req: ChatRequest,
    request: Request,
    queue: LLMQueueManager = Depends(get_queue_manager)
):
    """
    Main chat endpoint for the Red Team.
    Returns a Server-Sent Events (SSE) stream.
    """
    return StreamingResponse(
        sse_generator(chat_req, queue),
        media_type="text/event-stream"
    )

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