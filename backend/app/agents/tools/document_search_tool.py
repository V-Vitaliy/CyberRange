import asyncio

from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field

from app.agents.tools.base import AgentTool, ToolContext, ToolResult

DOCUMENT_SEARCH_MAX_CHUNKS = 5
DOCUMENT_SEARCH_RERANKED_CHUNKS = 2
DOCUMENT_SEARCH_MAX_CHARS = 4000

_reranker = None


class SearchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=500)


@tool(args_schema=SearchArguments)
def search_documents(query: str) -> str:
    """Search the university documents for passages relevant to the query."""
    # The registry validates and executes this schema with the request's turn context.
    raise RuntimeError("Document search must execute through the audited tool registry")


def get_reranker():
    global _reranker
    if _reranker is None:
        from sentence_transformers import CrossEncoder

        _reranker = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2", max_length=512)
    return _reranker


def _rerank(query: str, documents: list[str]):
    # Runs in a worker thread: the first call also loads the model.
    return get_reranker().predict([[query, doc] for doc in documents])


async def handle_search_documents(args: SearchArguments, context: ToolContext) -> ToolResult:
    lab_filter = {"lab_instance_id": context.lab_instance_id}
    # Private documents are only for admins (EX-05 trusts the role in the token).
    where = lab_filter if context.role == "admin" else {"$and": [lab_filter, {"access_level": "public"}]}
    results = await asyncio.to_thread(
        context.vector_store.search_documents, args.query, n_results=DOCUMENT_SEARCH_MAX_CHUNKS, where=where
    )
    chunks = [doc for doc in ((results or {}).get("documents") or [[]])[0] if doc][:DOCUMENT_SEARCH_MAX_CHUNKS]

    if context.use_reranker and chunks:
        scores = await asyncio.to_thread(_rerank, args.query, chunks)
        ranked = sorted(zip(scores, chunks), key=lambda pair: pair[0], reverse=True)
        chunks = [doc for score, doc in ranked[:DOCUMENT_SEARCH_RERANKED_CHUNKS] if score > 0]

    if chunks:
        message = "Document excerpts:\n" + "\n---\n".join(chunks)
    else:
        message = "No matching documents found."
    payload = {"query": args.query, "chunks": len(chunks), "reranked": context.use_reranker}
    return ToolResult("AI_DOCUMENT_SEARCH", payload, message[:DOCUMENT_SEARCH_MAX_CHARS])


SEARCH_DOCUMENTS = AgentTool(schema=search_documents, risk="low", handler=handle_search_documents)
