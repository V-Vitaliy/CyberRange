import asyncio

import pytest
from fastapi import HTTPException

from app.core.queue_manager import LLMQueueManager


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_consume_capacity():
    queue = LLMQueueManager()
    await queue.wait_for_turn()
    waiter = asyncio.create_task(queue.wait_for_turn())
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert queue.gpu_lock.locked()
    queue.release_turn()
    assert queue.queue.empty()
    await queue.wait_for_turn()
    queue.release_turn()


@pytest.mark.asyncio
async def test_timeout_cleans_up_without_releasing_another_requests_lock():
    queue = LLMQueueManager()
    await queue.wait_for_turn()
    queue.timeout_seconds = 0.001
    with pytest.raises(HTTPException) as exc:
        await queue.wait_for_turn()
    assert exc.value.status_code == 504
    assert queue.gpu_lock.locked()
    queue.release_turn()
    assert queue.queue.empty()


@pytest.mark.asyncio
async def test_cancelled_active_stream_releases_lock():
    queue = LLMQueueManager()
    entered = asyncio.Event()

    async def generate():
        entered.set()
        await asyncio.Event().wait()
        yield "unreachable"

    async def consume():
        async for _ in queue.stream_with_lock(generate):
            pass

    task = asyncio.create_task(consume())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not queue.gpu_lock.locked()
    assert queue.queue.empty()
