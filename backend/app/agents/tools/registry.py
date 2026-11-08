import json

from langchain_core.messages import ToolCallChunk
from langchain_core.tools import BaseTool

from app.agents.tools.base import AgentTool, ToolContext
from app.agents.tools.document_search_tool import SEARCH_DOCUMENTS
from app.agents.tools.email_tool import SEND_EMAIL
from app.siem.service import log_security_event

TOOLS: dict[str, AgentTool] = {tool.schema.name: tool for tool in (SEND_EMAIL, SEARCH_DOCUMENTS)}


def tool_schemas() -> list[BaseTool]:
    return [tool.schema for tool in TOOLS.values()]


async def execute_tool_call(call: ToolCallChunk, context: ToolContext) -> str:
    """Validate one completed tool call strictly, run its handler and audit the outcome."""
    name = call.get("name")
    agent_tool = TOOLS.get(name)
    # Parse the completed raw JSON strictly: partial streamed JSON must never
    # execute a tool, even if the adapter can repair it for display purposes.
    try:
        if agent_tool is None:
            raise ValueError("Unknown tool")
        raw_args = json.loads(call.get("args") or "")
        args = agent_tool.schema.args_schema.model_validate(raw_args, strict=True)
    except (ValueError, TypeError):
        await log_security_event(
            lab_instance_id=context.lab_instance_id,
            event_type="AI_TOOL_FAILED",
            payload={"tool": name, "reason": "Invalid tool or arguments"},
            source_ip="LLM_AGENT",
        )
        return "\n\n*[SYSTEM]: Tool execution failed: invalid tool or arguments.*"

    result = await agent_tool.handler(args, context)
    await log_security_event(
        lab_instance_id=context.lab_instance_id,
        event_type=result.event_type,
        payload={**result.payload, "tool": name, "risk": agent_tool.risk},
        source_ip="LLM_AGENT",
    )
    return result.message
