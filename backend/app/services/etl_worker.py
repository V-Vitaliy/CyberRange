import io
import uuid
import logging
from typing import List,Callable
from redis import Redis
from uuid import UUID
from pypdf import PdfReader
import nltk
from nltk.tokenize import sent_tokenize
import tiktoken

from app.db.chroma_client import VectorStore

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

try:
    nltk.data.find('tokenizers/punkt_tab')
except LookupError:
    logger.info("Downloading NLTK 'punkt_tab' dataset...")
    nltk.download('punkt_tab', quiet=True)

class ETLWorker:
    def __init__(self, vector_store: VectorStore):
        self.vector_store = vector_store

        self.max_tokens = 250
        self.overlap_tokens = 50
        self.batch_size = 100

        # Initialize a fast BPE tokenizer to accurately count tokens for any language
        self.tokenizer = tiktoken.get_encoding("cl100k_base")

    def _estimate_tokens(self, text: str) -> int:
        """
        Uses exact BPE token counting instead of naive heuristics.
        Handles Polish, English, and special characters accurately.
        """
        return len(self.tokenizer.encode(text))

    def _chunk_text_with_overlap(self, text: str) -> List[str]:
        sentences = sent_tokenize(text)
        chunks = []
        current_chunk = []
        current_tokens = 0

        for sentence in sentences:
            sentence_tokens = self._estimate_tokens(sentence)

            if current_tokens + sentence_tokens > self.max_tokens and current_chunk:
                chunks.append(" ".join(current_chunk))

                while current_tokens > self.overlap_tokens and len(current_chunk) > 1:
                    removed_sentence = current_chunk.pop(0)
                    current_tokens -= self._estimate_tokens(removed_sentence)

            current_chunk.append(sentence)
            current_tokens += sentence_tokens

        if current_chunk:
            chunks.append(" ".join(current_chunk))

        return chunks

    async def process_pdf(self, file_content: bytes, filename: str, task_id: UUID, redis: Redis, access_level: str= 'public') -> int:
        logger.info(f"Starting ETL pipeline for file: {filename}")

        redis_key = f"etl_job:{task_id}"

        await redis.hset(redis_key, mapping={
            "filename": filename,
            'progress': 5,
            "status": "Queued",
            'message': "Queued..."
        })
        await redis.expire(redis_key, 3600)

        try:
            reader = PdfReader(io.BytesIO(file_content))
            await redis.hincrby(redis_key, "progress", 5)
            await redis.hset(redis_key, mapping={
            "status": "Started",
            'message': "Processing PDF.."
            })
            extracted_pages = []

            for page_num, page in enumerate(reader.pages):
                text = page.extract_text()
                if text:
                    progress = int(((page_num+1)  / len(reader.pages) * 30))
                    await redis.hset(redis_key, mapping={
                        "status": "Processing",
                        "progress": progress,
                        'message': f"Extracting text from page {page_num} ({progress}%)"
                    })
                    extracted_pages.append(text)
                else:
                    await redis.hset(redis_key, mapping={
                        "status": "Failed",
                        "progress": 100,
                        'message': f"Failed to extract text from page {page_num}"
                    })
                    logger.warning(f"Failed to extract text from page {page_num} in {filename}")

            full_text = "\n".join(extracted_pages)

            if not full_text.strip():
                logger.error(f"No text extracted from {filename}. File might be a scanned image.")
                await redis.hset(redis_key, mapping={
                    "status": "Failed",
                    "progress": 100,
                    'message': "No text found in PDF"
                })
                return 0

            chunks = self._chunk_text_with_overlap(full_text)
            logger.info(f"Generated {len(chunks)} chunks from {filename}.")

            metadatas = []
            ids = []
            for i, chunk in enumerate(chunks):
                metadatas.append({
                    "source": filename,
                    "access_level" : access_level,
                    "chunk_id": i,
                    "token_length": self._estimate_tokens(chunk)
                })
                ids.append(f"{filename}_{i}_{uuid.uuid4().hex[:8]}")
                progress = 30+int((i+1) / len(chunks) * 30)
                await redis.hset(redis_key, mapping={
                    "progress": progress,
                    'message': f"Tokenizing chunks ({progress}%)"
                })

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
                    progress = 60+int((total_inserted+1) / len(chunks) * 30)
                    await redis.hset(redis_key, mapping={
                        "progress": progress,
                        'message': f"Inserting chunks into vector store ({progress}%)"
                    })
            await redis.hset(redis_key, mapping={
                "status": "Finished",
                "progress": 100,
                'message': "Finished processing PDF"
            })
            logger.info(f"Successfully finished ETL for {filename}.")
            return len(chunks)

        except Exception as e:
            await redis.hset(redis_key, mapping={
                "filename": filename,
                "status": "Failed",
                "progress": 100,
                'message': str(e)
            })
            logger.error(f"ETL pipeline failed for {filename}: {str(e)}", exc_info=True)
            return 0