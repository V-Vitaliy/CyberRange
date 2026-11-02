import asyncio
import json
import logging
import uuid

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.tools import send_email
from app.services.email_service import EmailService
from app.services.siem_logger import log_security_event

logger = logging.getLogger(__name__)


async def generate_unified_stream(
    llm_instance,
    system_prompt: str,
    human_prompt: str,
    db: AsyncSession,
    lab_instance_id: str | uuid.UUID,
):
    """Yields tokens from the LLM asynchronously to prevent event loop blocking."""
    messages = [SystemMessage(content=system_prompt), HumanMessage(content=human_prompt)]

    # Bind only the chat request; SOC retains the unbound base model.
    try:
        chat_model = llm_instance.bind_tools([send_email])
    except NotImplementedError:
        logger.warning("Selected model does not support tools; continuing text-only chat")
        chat_model = llm_instance
    response = None
    has_text = False
    async for chunk in chat_model.astream(messages):
        response = chunk if response is None else response + chunk
        if isinstance(chunk.content, str) and chunk.content:
            has_text = has_text or bool(chunk.content.strip())
            yield chunk.content

    if response is not None and response.response_metadata.get("finish_reason") == "length":
        raise RuntimeError(
            "Model output limit reached before completion. For the OpenAI-compatible provider, increase LLM_MAX_TOKENS."
        )
    if response is None or (not has_text and not response.tool_call_chunks):
        raise RuntimeError("Model returned no answer or tool call.")

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
                db=db,
                lab_instance_id=lab_instance_id,
                event_type="AI_TOOL_FAILED",
                payload={"tool": call.get("name"), "reason": "Invalid tool or arguments"},
                source_ip="LLM_AGENT",
            )
            yield "\n\n*[SYSTEM]: Tool execution failed: invalid tool or arguments.*"
            continue

        simulated = not settings.SMTP_HOST
        if not simulated and not EmailService.recipient_allowed(args.to_address):
            # Real delivery never leaves the lab mail domains.
            delivery = "blocked"
        else:
            try:
                sent = await asyncio.to_thread(EmailService.send_real_email, args.to_address, args.subject, args.body)
            except Exception:
                logger.exception("Email tool failed")
                sent = False
            delivery = ("simulated" if simulated else "sent") if sent else "failed"

        exfiltration = "flag{" in args.body.lower() or "flag{" in args.subject.lower()
        if delivery == "failed":
            event_type = "AI_EMAIL_FAILED"
        elif exfiltration:
            event_type = "AI_DATA_EXFILTRATION"
        else:
            event_type = {"blocked": "AI_EMAIL_BLOCKED", "simulated": "AI_EMAIL_SIMULATED", "sent": "AI_EMAIL_SENT"}[
                delivery
            ]
        await log_security_event(
            db=db,
            lab_instance_id=lab_instance_id,
            event_type=event_type,
            payload={
                "to": args.to_address,
                "subject": args.subject,
                "body": args.body,
                "delivery": delivery,
                "exfiltration_attempt": exfiltration,
            },
            source_ip="LLM_AGENT",
        )
        yield {
            "failed": "\n\n*[SYSTEM]: Tool `send_email` failed. Could not dispatch email.*",
            "blocked": "\n\n*[SYSTEM]: Tool `send_email` blocked: recipient is outside the lab mail domain.*",
            "simulated": "\n\n*[SYSTEM]: Tool `send_email` simulated successfully. No email was sent.*",
            "sent": f"\n\n*[SYSTEM]: Tool `send_email` sent email to {args.to_address}.*",
        }[delivery]
