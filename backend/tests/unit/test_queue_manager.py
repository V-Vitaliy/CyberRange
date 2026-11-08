import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.core.queue_manager import LLMQueueManager

LAB_A, LAB_B, LAB_C = "lab-a", "lab-b", "lab-c"


async def enters(queue, lab, within=0.05):
    """Whether a turn for `lab` starts within `within` seconds; a started turn stays held."""
    waiter = asyncio.create_task(queue.wait_for_turn(lab))
    done, _ = await asyncio.wait({waiter}, timeout=within)
    if not done:
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
    return bool(done)


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_consume_capacity():
    queue = LLMQueueManager()
    await queue.wait_for_turn(LAB_A)
    waiter = asyncio.create_task(queue.wait_for_turn(LAB_A))
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert queue.model_slots.locked()
    queue.release_turn(LAB_A)
    assert queue.queue.empty()
    await queue.wait_for_turn(LAB_A)
    queue.release_turn(LAB_A)


@pytest.mark.asyncio
async def test_timeout_cleans_up_without_releasing_another_requests_slot():
    queue = LLMQueueManager()
    await queue.wait_for_turn(LAB_A)
    queue.timeout_seconds = 0.001
    with pytest.raises(HTTPException) as exc:
        await queue.wait_for_turn(LAB_A)
    assert exc.value.status_code == 504
    assert queue.model_slots.locked()
    queue.release_turn(LAB_A)
    assert queue.queue.empty()


@pytest.mark.asyncio
async def test_cancelled_active_stream_releases_its_slots():
    queue = LLMQueueManager()
    entered = asyncio.Event()

    async def generate():
        entered.set()
        await asyncio.Event().wait()
        yield "unreachable"

    async def consume():
        async for _ in queue.stream_with_slot(LAB_A, generate):
            pass

    task = asyncio.create_task(consume())
    await asyncio.wait_for(entered.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not queue.model_slots.locked()
    assert queue.queue.empty()
    assert queue.lab_slots == {}


@pytest.mark.asyncio
async def test_at_most_max_concurrency_generations_run_at_once():
    queue = LLMQueueManager(max_concurrency=2, max_per_lab=2)
    assert await enters(queue, LAB_A) and await enters(queue, LAB_B)
    assert not await enters(queue, LAB_C)
    queue.release_turn(LAB_A)
    assert await enters(queue, LAB_C)


@pytest.mark.asyncio
async def test_one_lab_runs_at_most_its_share_even_with_free_slots():
    queue = LLMQueueManager(max_concurrency=3, max_per_lab=1)
    assert await enters(queue, LAB_A)
    assert not await enters(queue, LAB_A)
    queue.release_turn(LAB_A)
    assert await enters(queue, LAB_A)


@pytest.mark.asyncio
async def test_a_lab_that_fills_its_share_does_not_delay_another_lab():
    queue = LLMQueueManager(max_concurrency=2, max_per_lab=1)
    await queue.wait_for_turn(LAB_A)
    # A second request of the same lab waits for its lab slot, not for a shared one.
    blocked = asyncio.create_task(queue.wait_for_turn(LAB_A))
    await asyncio.sleep(0.01)
    assert not blocked.done()
    assert await enters(queue, LAB_B)
    blocked.cancel()
    with pytest.raises(asyncio.CancelledError):
        await blocked


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["timeout", "cancel"])
async def test_waiting_for_a_shared_slot_gives_the_lab_slot_back(ending):
    queue = LLMQueueManager(max_concurrency=1, max_per_lab=1)
    await queue.wait_for_turn(LAB_A)
    if ending == "timeout":
        queue.timeout_seconds = 0.01
        with pytest.raises(HTTPException) as exc:
            await queue.wait_for_turn(LAB_B)
        assert exc.value.status_code == 504
        queue.timeout_seconds = 300
    else:
        assert not await enters(queue, LAB_B)
    queue.release_turn(LAB_A)
    assert await enters(queue, LAB_B)  # LAB_B's slot was not left taken
    queue.release_turn(LAB_B)
    assert queue.queue.empty()


@pytest.mark.asyncio
async def test_full_queue_is_rejected_with_503():
    queue = LLMQueueManager()
    queue.queue = asyncio.Queue(maxsize=1)
    await queue.wait_for_turn(LAB_A)
    with pytest.raises(HTTPException) as exc:
        await queue.wait_for_turn(LAB_B)
    assert exc.value.status_code == 503
    queue.release_turn(LAB_A)


@pytest.mark.asyncio
async def test_finished_labs_leave_no_slots_behind():
    queue = LLMQueueManager(max_concurrency=3, max_per_lab=1)
    for lab in (LAB_A, LAB_B, LAB_C):
        async with queue.turn(lab):
            assert lab in queue.lab_slots
    assert queue.lab_slots == {}
    assert not queue.model_slots.locked()


def test_limits_come_from_settings():
    queue = LLMQueueManager.from_settings(SimpleNamespace(LLM_MAX_CONCURRENCY=4, LLM_MAX_CONCURRENCY_PER_LAB=2))
    assert (queue.max_concurrency, queue.max_per_lab) == (4, 2)
