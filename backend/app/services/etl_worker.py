import io
import uuid
import logging
from typing import List
from pypdf import PdfReader
from nltk.tokenize import sent_tokenize

from app.db.chroma_client import VectorStore

# Configure production-grade logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# NOTE: The NLTK 'punkt' model download was removed from runtime.
# It MUST be added to the Dockerfile:
# RUN python -m nltk.downloader punkt

class ETLWorker:
    """
    Production-ready background service that processes files from MinIO (S3),
    extracts text, applies semantic chunking with overlap, and batches embeddings to ChromaDB.
    """
    def __init__(self, vector_store: VectorStore):
        self.vector_store = vector_store

        # Configuration for Chunking (in approximate tokens)
        self.max_tokens = 250       # ~1000 characters
        self.overlap_tokens = 50    # ~200 characters overlap
        self.batch_size = 100       # Number of vectors to insert at once

    def _estimate_tokens(self, text: str) -> int:
        """
        Fast heuristic to estimate token count without heavy libraries like tiktoken.
        Roughly 4 characters = 1 token in English.
        """
        return len(text) // 4

    def _chunk_text_with_overlap(self, text: str) -> List[str]:
        """
        Splits text into chunks by sentence boundaries, maintaining a context overlap.
        """
        sentences = sent_tokenize(text)
        chunks = []
        current_chunk = []
        current_tokens = 0

        for sentence in sentences:
            sentence_tokens = self._estimate_tokens(sentence)

            # If a single sentence is larger than max_tokens, we still add it
            # (or we could forcefully split it, but keeping it intact is usually safer for semantics)
            if current_tokens + sentence_tokens > self.max_tokens and current_chunk:
                chunks.append(" ".join(current_chunk))

                # Create overlap: keep popping from the start of the chunk
                # until the remaining sentences fit within the overlap limit
                while current_tokens > self.overlap_tokens and len(current_chunk) > 1:
                    removed_sentence = current_chunk.pop(0)
                    current_tokens -= self._estimate_tokens(removed_sentence)

            current_chunk.append(sentence)
            current_tokens += sentence_tokens

        if current_chunk:
            chunks.append(" ".join(current_chunk))

        return chunks

    def process_pdf(self, file_content: bytes, filename: str) -> int:
        """
        Reads a PDF file, splits it into semantic chunks with overlap,
        and saves it to the vector database in batches.
        """
        logger.info(f"Starting ETL pipeline for file: {filename}")

        try:
            # 1. Extraction (O(N) string building)
            reader = PdfReader(io.BytesIO(file_content))
            extracted_pages = []

            for page_num, page in enumerate(reader.pages):
                text = page.extract_text()
                if text:
                    extracted_pages.append(text)
                else:
                    logger.warning(f"Failed to extract text from page {page_num} in {filename}")

            full_text = "\n".join(extracted_pages)

            if not full_text.strip():
                logger.error(f"No text extracted from {filename}. File might be a scanned image.")
                return 0

            # 2. Chunking with overlap
            chunks = self._chunk_text_with_overlap(full_text)
            logger.info(f"Generated {len(chunks)} chunks from {filename}.")

            # 3. Prepare rich metadata and secure IDs
            metadatas = []
            ids = []
            for i, chunk in enumerate(chunks):
                metadatas.append({
                    "source": filename,
                    "chunk_id": i,
                    "token_length": self._estimate_tokens(chunk)
                })
                # Using UUID4 prevents ID collisions if the same filename is uploaded twice
                ids.append(f"{filename}_{i}_{uuid.uuid4().hex[:8]}")

            # 4. Load (Batch insertion to Vector DB)
            if chunks:
                total_inserted = 0
                for i in range(0, len(chunks), self.batch_size):
                    batch_chunks = chunks[i : i + self.batch_size]
                    batch_meta = metadatas[i : i + self.batch_size]
                    batch_ids = ids[i : i + self.batch_size]

                    self.vector_store.add_documents(
                        documents=batch_chunks,
                        metadatas=batch_meta,
                        ids=batch_ids
                    )
                    total_inserted += len(batch_chunks)
                    logger.info(f"Inserted batch: {total_inserted}/{len(chunks)} chunks.")

            logger.info(f"Successfully finished ETL for {filename}.")
            return len(chunks)

        except Exception as e:
            logger.error(f"ETL pipeline failed for {filename}: {str(e)}", exc_info=True)
            return 0