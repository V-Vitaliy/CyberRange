import asyncio
from fastapi import HTTPException, status, Request

class LLMQueueManager:
    """
    Manages concurrent access to the GPU to prevent CUDA Out Of Memory (OOM) errors.
    Implements a strict waiting queue with timeouts and capacity limits.
    """
    def __init__(self):
        self.max_queue_size = 50
        self.timeout_seconds = 300

        self.queue = asyncio.Queue(maxsize=self.max_queue_size)
        self.gpu_lock = asyncio.Lock()

    async def wait_for_turn(self):
        ticket = object()
        try:
            self.queue.put_nowait(ticket)
        except asyncio.QueueFull:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Server queue is full. GPU is overloaded. Please try again later."
            )

        try:
            await asyncio.wait_for(self.gpu_lock.acquire(), timeout=self.timeout_seconds)
        except asyncio.TimeoutError:
            self.queue.get_nowait()
            self.queue.task_done()
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail="Request timed out while waiting for GPU resources."
            )

    def release_turn(self):
        self.gpu_lock.release()
        self.queue.get_nowait()
        self.queue.task_done()

    async def enqueue_request(self, full_prompt: str):
        """
        MOCK LLM GENERATOR for local testing without heavy GPU models.
        Streams back the constructed prompt to verify RAG and dynamic patching.
        """
        await self.wait_for_turn()
        try:
            # Simulate thinking time
            await asyncio.sleep(0.5)

            mock_response = (
                "🤖 [MOCK LLM MODE]\n\n"
                "Here is the exact prompt I received from the RAG Service:\n"
                "==================================================\n"
                f"{full_prompt}\n"
                "==================================================\n"
                "If you see your context and system prompt here, RAG works perfectly!"
            )

            # Stream the response word by word to simulate SSE
            for word in mock_response.split(" "):
                yield word + " "
                await asyncio.sleep(0.05)

        finally:
            self.release_turn()


def get_queue_manager(request: Request) -> LLMQueueManager:
    return request.app.state.queue_manager