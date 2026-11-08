import asyncio
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from fastapi import HTTPException, Request, status


@dataclass
class _LabSlots:
    slots: asyncio.Semaphore
    # Requests of the lab that are running or waiting; the entry is dropped at zero.
    users: int = field(default=0)


class LLMQueueManager:
    """
    Shares the model between chats and the Auto-SOC: at most `max_concurrency` generations
    run at once, and at most `max_per_lab` of them for one lab, so a lab cannot take the
    whole model from the others. Waiting requests form a bounded queue with a timeout.
    """

    def __init__(self, max_concurrency: int = 1, max_per_lab: int = 1):
        self.max_queue_size = 50
        self.timeout_seconds = 300
        self.max_concurrency = max_concurrency
        self.max_per_lab = max_per_lab

        self.queue = asyncio.Queue(maxsize=self.max_queue_size)
        self.model_slots = asyncio.Semaphore(max_concurrency)
        self.lab_slots: dict[str, _LabSlots] = {}

    @classmethod
    def from_settings(cls, settings) -> "LLMQueueManager":
        return cls(settings.LLM_MAX_CONCURRENCY, settings.LLM_MAX_CONCURRENCY_PER_LAB)

    async def wait_for_turn(self, lab_instance_id: str):
        try:
            self.queue.put_nowait(object())
        except asyncio.QueueFull:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Server queue is full. The model is overloaded. Please try again later.",
            )

        lab = self.lab_slots.setdefault(lab_instance_id, _LabSlots(asyncio.Semaphore(self.max_per_lab)))
        lab.users += 1
        has_lab_slot = False
        try:
            # The lab slot comes first: requests over a lab's share never hold a shared slot.
            async with asyncio.timeout(self.timeout_seconds):
                await lab.slots.acquire()
                has_lab_slot = True
                await self.model_slots.acquire()
        except BaseException as error:
            if has_lab_slot:
                lab.slots.release()
            self._leave(lab_instance_id)
            if isinstance(error, TimeoutError):
                raise HTTPException(
                    status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                    detail="Request timed out while waiting for the model.",
                ) from None
            raise

    def release_turn(self, lab_instance_id: str):
        self.model_slots.release()
        self.lab_slots[lab_instance_id].slots.release()
        self._leave(lab_instance_id)

    def _leave(self, lab_instance_id: str):
        lab = self.lab_slots[lab_instance_id]
        lab.users -= 1
        if not lab.users:
            del self.lab_slots[lab_instance_id]
        self.queue.get_nowait()
        self.queue.task_done()

    @asynccontextmanager
    async def turn(self, lab_instance_id: str):
        await self.wait_for_turn(lab_instance_id)
        try:
            yield
        finally:
            self.release_turn(lab_instance_id)

    async def stream_with_slot(
        self, lab_instance_id: str, generator_func: Callable[[], AsyncGenerator[str, None]]
    ) -> AsyncGenerator[str, None]:
        async with self.turn(lab_instance_id):
            async for chunk in generator_func():
                yield chunk


def get_queue_manager(request: Request) -> LLMQueueManager:
    return request.app.state.queue_manager
