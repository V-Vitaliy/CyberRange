import logging
import uuid

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.tools.registry import execute_tool_call, tool_schemas

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
        chat_model = llm_instance.bind_tools(tool_schemas())
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

    for call in response.tool_call_chunks:
        yield await execute_tool_call(call, db, lab_instance_id)
