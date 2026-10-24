from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field


class EmailArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    to_address: str = Field(min_length=1)
    subject: str
    body: str


@tool(args_schema=EmailArguments)
def send_email(to_address: str, subject: str, body: str) -> str:
    """Send an email only when explicitly requested. Requires recipient, subject and body."""
    # llm_engine validates and executes this schema with the request's audit context.
    raise RuntimeError("Email tools must execute through the audited chat handler")
