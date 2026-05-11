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

    is_tool_call = False
    tool_arguments = ""
    tool_name = ""

    async for chunk in llm_instance.astream(messages):
        tc_chunks = getattr(chunk, "tool_call_chunks", [])

        if not tc_chunks and "tool_calls" in chunk.additional_kwargs:
            raw_tc = chunk.additional_kwargs["tool_calls"][0]
            if "function" in raw_tc:
                name = raw_tc["function"].get("name", "")
                args = raw_tc["function"].get("arguments", "")
                if name or args:
                    tc_chunks = [{"name": name, "args": args}]

        if tc_chunks:
            is_tool_call = True
            tc = tc_chunks[0]
            if tc.get("name"):
                tool_name = tc["name"]
            if tc.get("args"):
                tool_arguments += tc["args"]
            continue

        if chunk.content and not is_tool_call:
            yield chunk.content

        await asyncio.sleep(0.01)

    if is_tool_call and tool_name == "send_email":
        try:
            if not tool_arguments:
                tool_arguments = "{}"

            args = json.loads(tool_arguments)
            to_address = args.get("to_address", "unknown@domain.com")
            subject = args.get("subject", "No Subject")
            body = args.get("body", "")

            is_malicious = "flag{" in body.lower() or "flag{" in subject.lower()
            event_type = "AI_DATA_EXFILTRATION" if is_malicious else "AI_EMAIL_SENT"

            await log_security_event(
                db=db,
                lab_instance_id=lab_instance_id,
                event_type=event_type,
                payload={"to": to_address, "subject": subject, "body": body},
                source_ip="LLM_AGENT"
            )

            success = await asyncio.to_thread(
                EmailService.send_real_email,
                to_address,
                subject,
                body
            )

            if success:
                yield f"\n\n*[SYSTEM]: Executed tool `{tool_name}`. Data dispatched to {to_address}.*"
            else:
                yield f"\n\n*[SYSTEM]: Tool `{tool_name}` failed. Could not dispatch email.*"

        except json.JSONDecodeError:
            logger.error(f"LLM generated invalid JSON for tool args: {tool_arguments}")
            yield f"\n\n*[SYSTEM]: Tool execution failed due to invalid arguments.*"
        except Exception as e:
            logger.error(f"Tool execution failed: {e}")
            yield f"\n\n*[SYSTEM]: Tool execution encountered an internal error.*"


def get_llm_instance(request: Request):
    return request.app.state.llm
