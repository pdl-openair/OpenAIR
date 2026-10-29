from __future__ import annotations

import asyncio

import pytest

from app.modules.task_events import (
    publish_task_event,
    subscribe_task_events,
    unsubscribe_task_events,
)


@pytest.mark.asyncio
async def test_publish_fans_out_only_to_subscribers_for_the_same_task() -> None:
    task_a = subscribe_task_events("task-a")
    task_b = subscribe_task_events("task-b")
    try:
        publish_task_event("task-a", "task_updated", payload={"revision": 3})
        event = await asyncio.wait_for(task_a.queue.get(), timeout=1)

        assert event["taskId"] == "task-a"
        assert event["type"] == "task_updated"
        assert event["payload"] == {"revision": 3}
        assert task_b.queue.empty()
    finally:
        unsubscribe_task_events(task_a)
        unsubscribe_task_events(task_b)


@pytest.mark.asyncio
async def test_slow_consumer_keeps_newest_event_and_unsubscribe_stops_delivery() -> None:
    subscription = subscribe_task_events("task-overflow", max_queue_size=1)
    publish_task_event("task-overflow", "stale", payload={"revision": 1})
    publish_task_event("task-overflow", "current", payload={"revision": 2})
    await asyncio.sleep(0)

    retained = subscription.queue.get_nowait()
    assert retained["type"] == "current"
    assert retained["payload"] == {"revision": 2}
    assert subscription.queue.empty()

    unsubscribe_task_events(subscription)
    publish_task_event("task-overflow", "after_close", payload={"revision": 3})
    await asyncio.sleep(0)
    assert subscription.queue.empty()
