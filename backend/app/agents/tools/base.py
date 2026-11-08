from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.tools import BaseTool
from pydantic import BaseModel

from app.documents.chroma_client import VectorStore


@dataclass(frozen=True)
class ToolContext:
    """What a tool call may use from the chat turn that made it."""

    lab_instance_id: str
    role: str
    use_reranker: bool
    vector_store: VectorStore


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
    handler: Callable[[BaseModel, ToolContext], Awaitable[ToolResult]]
