import asyncio
import json
import logging
import os
import uuid

from app.core.config import settings
from app.core.queue_manager import LLMQueueManager
from app.core.tools import send_email
from app.services.email_service import EmailService
from fastapi import Request
from langchain_community.chat_models import ChatLlamaCpp
from langchain_core.messages import HumanMessage,SystemMessage
from langchain_groq import ChatGroq
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.siem_logger import log_security_event

logger = logging.getLogger(__name__)

MODEL_PATH = os.path.join(os.path.dirname(__file__), settings.LLM_MODEL_PATH)



def init_llm() -> ChatLlamaCpp:
    """
    Initializes the Llama-3 model into VRAM using llama-cpp-python.
    """
    logger.info(f"Loading model from {MODEL_PATH}...")

    llm = ChatLlamaCpp(
        model_path=MODEL_PATH,
        n_threads=settings.LLM_N_THREADS,
        n_batch=settings.LLM_N_BATCH,
        n_gpu_layers=settings.LLM_N_GPU_LAYERS,
        n_ctx=settings.LLM_N_CTX,
        temperature=0.1,
        max_tokens=512,
        verbose=False,
        streaming=True
    )
    
    print("Local LangChain Model loaded successfully!")
    return llm

def init_groq() -> ChatGroq:
    logger.info("Initializing Groq API via LangChain...")
    return ChatGroq(
        api_key=settings.GROQ_API_KEY,
        model="llama-3.1-8b-instant",
        temperature=0.1,
        max_tokens=512,
        streaming=True
    )

async def generate_unified_stream(
        llm_instance,
        system_prompt: str,
        human_prompt: str,
        db: AsyncSession,
        lab_instance_id: str| uuid.UUID,):
    """Yields tokens from the LLM asynchronously to prevent event loop blocking."""
    messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=human_prompt)
        ]

    # Bind only the chat request; SOC retains the unbound base model.
    try:
        chat_model = llm_instance.bind_tools([send_email])
    except NotImplementedError:
        logger.warning("Selected model does not support tools; continuing text-only chat")
        chat_model = llm_instance
    response = None
    async for chunk in chat_model.astream(messages):
        response = chunk if response is None else response + chunk
        if isinstance(chunk.content, str) and chunk.content:
            yield chunk.content

    if response is None:
        return

    # Parse the completed raw JSON strictly: partial streamed JSON must never
    # execute a tool, even if the adapter can repair it for display purposes.
    for call in response.tool_call_chunks:
        try:
            if call.get("name") != "send_email":
                raise ValueError("Unknown tool")
            raw_args = json.loads(call.get("args") or "")
            args = send_email.args_schema.model_validate(raw_args, strict=True)
        except (ValueError, TypeError):
            await log_security_event(
                db=db, lab_instance_id=lab_instance_id,
                event_type="AI_TOOL_FAILED",
                payload={"tool": call.get("name"), "reason": "Invalid tool or arguments"},
                source_ip="LLM_AGENT",
            )
            yield "\n\n*[SYSTEM]: Tool execution failed: invalid tool or arguments.*"
            continue

        simulated = not settings.SMTP_HOST
        try:
            success = await asyncio.to_thread(
                EmailService.send_real_email, args.to_address, args.subject, args.body
            )
        except Exception:
            logger.exception("Email tool failed")
            success = False

        exfiltration = "flag{" in args.body.lower() or "flag{" in args.subject.lower()
        event_type = (
            "AI_EMAIL_FAILED" if not success else
            "AI_DATA_EXFILTRATION" if exfiltration else
            "AI_EMAIL_SIMULATED" if simulated else "AI_EMAIL_SENT"
        )
        await log_security_event(
            db=db, lab_instance_id=lab_instance_id, event_type=event_type,
            payload={"to": args.to_address, "subject": args.subject, "body": args.body,
                     "delivery": "failed" if not success else "simulated" if simulated else "sent",
                     "exfiltration_attempt": exfiltration},
            source_ip="LLM_AGENT",
        )
        if not success:
            yield "\n\n*[SYSTEM]: Tool `send_email` failed. Could not dispatch email.*"
        elif simulated:
            yield "\n\n*[SYSTEM]: Tool `send_email` simulated successfully. No email was sent.*"
        else:
            yield f"\n\n*[SYSTEM]: Tool `send_email` sent email to {args.to_address}.*"


def get_llm_instance(request: Request):
    return request.app.state.llm
