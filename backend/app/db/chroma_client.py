import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer
from fastapi import Request

from app.core.config import settings

class VectorStore:
    def __init__(self):
        self.client = chromadb.HttpClient(
            host=settings.CHROMA_HOST,
            port=settings.CHROMA_PORT,
            settings=Settings(allow_reset=True)
        )
        model_name = "all-mpnet-base-v2"
        device_type = "cpu"
        print(f"Loading embedding model {model_name} on {device_type}...")
        self.encoder = SentenceTransformer(model_name, device=device_type)

        self.collection = self.client.get_or_create_collection(
            name="university_pdfs",
            metadata={"hnsw:space": "cosine"}
        )

    def add_documents(self, documents: list[str], metadatas: list[dict], ids: list[str]):
        embeddings = self.encoder.encode(documents).tolist()
        self.collection.add(
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas,
            ids=ids
        )

    def search_documents(self, query: str, n_results: int = 5) -> dict:
        """
        Embeds the query and searches for the most similar documents in ChromaDB.
        """
        query_embedding = self.encoder.encode([query]).tolist()

        results = self.collection.query(
            query_embeddings=query_embedding,
            n_results=n_results
        )
        return results

def get_vector_store(request: Request) -> VectorStore:
    return request.app.state.vector_store