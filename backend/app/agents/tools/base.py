from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.tools import BaseTool
from pydantic import BaseModel


@dataclass(frozen=True)
class ToolResult:
    """Outcome of one tool call: the SIEM event and the text shown in chat."""

    event_type: str
    payload: dict[str, Any]
    message: str


@dataclass(frozen=True)
class AgentTool:
    """A chat tool: model-facing schema, risk level and audited handler."""

    schema: BaseTool
    risk: Literal["low", "medium", "high"]
    handler: Callable[[BaseModel], Awaitable[ToolResult]]
