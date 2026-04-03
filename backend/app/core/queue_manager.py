import asyncio
from fastapi import HTTPException, status, Request

class LLMQueueManager:
    """
    Manages concurrent access to the GPU to prevent CUDA Out Of Memory (OOM) errors.
    Implements a strict waiting queue with timeouts and capacity limits.
    """
    def __init__(self):
        # Maxsize for the queue preventing RAM/VRAM exhaustion
        self.max_queue_size = 50

        # Timeout limit in seconds
        self.timeout_seconds = 300

        # The queue holds "tickets" for waiting requests.
        # IMPORTANT: This must be instantiated inside a running event loop (e.g., via lifespan).
        self.queue = asyncio.Queue(maxsize=self.max_queue_size)

        # A lock to ensure ONLY ONE generation process runs on the GPU at any given time
        self.gpu_lock = asyncio.Lock()

    async def wait_for_turn(self):
        """
        Adds the incoming request to the waiting queue and waits for the GPU lock.
        """
        ticket = object()
        try:
            # Put the ticket into the queue WITHOUT waiting/blocking.
            # Raises asyncio.QueueFull if the queue is at max capacity.
            self.queue.put_nowait(ticket)
        except asyncio.QueueFull:
            # Return exactly 503 Service Unavailable when max capacity is reached
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Server queue is full. GPU is overloaded. Please try again later."
            )

        try:
            # Wait for the GPU to be free, strictly bounded by the timeout
            await asyncio.wait_for(self.gpu_lock.acquire(), timeout=self.timeout_seconds)
        except asyncio.TimeoutError:
            # If we timed out, we must leave the queue and return an error
            self.queue.get_nowait()
            self.queue.task_done()
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail="Request timed out while waiting for GPU resources."
            )

    def release_turn(self):
        """
        Releases the GPU lock and removes the ticket from the queue.
        MUST be called in a 'finally' block after text generation finishes.
        """
        self.gpu_lock.release()
        self.queue.get_nowait()
        self.queue.task_done()


def get_queue_manager(request: Request) -> LLMQueueManager:
    """
    FastAPI Dependency to retrieve the LLMQueueManager instance from the app state.
    This adheres to the FastAPI best practices (using lifespan + app.state)
    instead of relying on a global singleton instantiated outside the event loop.
    """
    return request.app.state.queue_manager