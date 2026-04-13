import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer
from fastapi import Request

from app.core.config import settings

class VectorStore:
    """
    Manages the connection to ChromaDB and the sentence embedding model.
    """
    def __init__(self):
        # Initialize the ChromaDB HTTP client
        self.client = chromadb.HttpClient(
            host=settings.CHROMA_HOST,
            port=settings.CHROMA_PORT,
            settings=Settings(allow_reset=True)
        )

        # The standard version of 'mpnet' used for English/Polish semantics
        model_name = "all-mpnet-base-v2"

        # CRITICAL: Force the embedding model to run on CPU.
        # If run on 'cuda' (GPU), the server will crash because Llama-3 occupies 100% of the VRAM.
        device_type = "cpu"

        print(f"Loading embedding model {model_name} on {device_type}...")
        self.encoder = SentenceTransformer(model_name, device=device_type)

        # Main collection for document embeddings
        self.collection = self.client.get_or_create_collection(
            name="university_pdfs",
            metadata={"hnsw:space": "cosine"} # Used for defense heuristics later
        )

    def add_documents(self, documents: list[str], metadatas: list[dict], ids: list[str]):
        """
        Vectorizes texts using the CPU model and stores them in ChromaDB.
        """
        embeddings = self.encoder.encode(documents).tolist()
        self.collection.add(
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas,
            ids=ids
        )

def get_vector_store(request: Request) -> VectorStore:
    """
    FastAPI Dependency to retrieve the VectorStore instance from the app state.
    Ensures the heavy ML model is only loaded during the app lifespan.
    """
    return request.app.state.vector_store