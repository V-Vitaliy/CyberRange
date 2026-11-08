import logging
from collections.abc import AsyncIterator

from langchain_core.messages import AIMessageChunk, HumanMessage, SystemMessage, ToolMessage, message_chunk_to_message

from app.agents.tools.base import ToolContext
from app.agents.tools.registry import execute_tool_call, tool_schemas
from app.core.queue_manager import LLMQueueManager
from app.siem.service import log_security_event

logger = logging.getLogger(__name__)

AGENT_MAX_STEPS = 5
AGENT_LOG_VALUE_CHARS = 500
AGENT_STEP_LIMIT_MESSAGE = "The assistant reached its step limit before finishing. Please try a simpler request."


def _truncate(value: str) -> str:
    return value if len(value) <= AGENT_LOG_VALUE_CHARS else value[:AGENT_LOG_VALUE_CHARS] + "…"


async def _model_step(chat_model, messages: list, queue: LLMQueueManager) -> AsyncIterator[str | AIMessageChunk]:
    """Stream one model call under the GPU lock: text chunks, then the merged response."""
    response = None
    has_text = False
    async for chunk in queue.stream_with_lock(lambda: chat_model.astream(messages)):
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
    yield response


async def run_agent_turn(
    llm_instance,
    system_prompt: str,
    human_prompt: str,
    context: ToolContext,
    queue: LLMQueueManager,
):
    """Run one chat turn: the model calls tools and sees their results until it answers in text.

    Yields {"text": ...} for answer tokens and {"step": ...} for every executed tool call.
    """
    messages = [SystemMessage(content=system_prompt), HumanMessage(content=human_prompt)]

    # Bind only the chat request; SOC retains the unbound base model.
    try:
        chat_model = llm_instance.bind_tools(tool_schemas())
    except NotImplementedError:
        logger.warning("Selected model does not support tools; continuing text-only chat")
        chat_model = llm_instance

    steps: list[dict] = []
    status = "answered"
    try:
        for step in range(1, AGENT_MAX_STEPS + 1):
            response = None
            async for item in _model_step(chat_model, messages, queue):
                if isinstance(item, str):
                    yield {"text": item}
                else:
                    response = item
            if not response.tool_call_chunks:
                break

            messages.append(message_chunk_to_message(response))
            for call in response.tool_call_chunks:
                outcome = await execute_tool_call(call, context)
                messages.append(ToolMessage(content=outcome, tool_call_id=call.get("id") or ""))
                record = {
                    "step": step,
                    "tool": call.get("name"),
                    "arguments": _truncate(call.get("args") or ""),
                    "outcome": _truncate(outcome),
                }
                steps.append(record)
                yield {"step": record}
        else:
            status = "step_limit"
            yield {"text": AGENT_STEP_LIMIT_MESSAGE}
    except BaseException:
        status = "error"
        raise
    finally:
        if steps or status == "step_limit":
            await log_security_event(
                lab_instance_id=context.lab_instance_id,
                event_type="AGENT_TURN",
                payload={"status": status, "steps": steps},
                source_ip="LLM_AGENT",
            )
