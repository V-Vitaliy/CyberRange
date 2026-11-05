import logging

from fastapi import Request

from app.core.config import settings

logger = logging.getLogger(__name__)


class VectorStore:
    def __init__(self):
        if not settings.CHROMA_AUTH_TOKEN:
            raise RuntimeError("CHROMA_AUTH_TOKEN is required: Chroma is reachable only through the token proxy")
        import chromadb
        from chromadb.config import Settings as ChromaSettings
        from sentence_transformers import SentenceTransformer

        self.client = chromadb.HttpClient(
            host=settings.CHROMA_HOST,
            port=settings.CHROMA_PORT,
            settings=ChromaSettings(
                chroma_client_auth_provider="chromadb.auth.token_authn.TokenAuthClientProvider",
                chroma_client_auth_credentials=settings.CHROMA_AUTH_TOKEN,
            ),
        )
        model_name = "all-mpnet-base-v2"
        device_type = "cpu"
        logger.info("Loading embedding model %s on %s", model_name, device_type)
        self.encoder = SentenceTransformer(model_name, device=device_type)

        self.collection = self.client.get_or_create_collection(
            name="university_pdfs", metadata={"hnsw:space": "cosine"}
        )

    def add_documents(self, documents: list[str], metadatas: list[dict], ids: list[str]):
        embeddings = self.encoder.encode(documents).tolist()
        self.collection.add(embeddings=embeddings, documents=documents, metadatas=metadatas, ids=ids)

    def delete_documents(self, where: dict) -> None:
        self.collection.delete(where=where)

    def search_documents(self, query: str, n_results: int = 5, where: dict = None) -> dict:
        """
        Embeds the query and searches for the most similar documents in ChromaDB.
        """
        query_embedding = self.encoder.encode([query]).tolist()

        results = self.collection.query(query_embeddings=query_embedding, n_results=n_results, where=where)
        return results


def get_vector_store(request: Request) -> VectorStore:
    return request.app.state.vector_store
