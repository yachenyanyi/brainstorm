"""异步生成器交错调度 — 公平轮转，谁先出结果谁先 yield"""

from __future__ import annotations
import asyncio
from typing import AsyncGenerator, Any


async def interleave(*generators: AsyncGenerator) -> AsyncGenerator[tuple[int, Any], None]:
    """
    公平交错多个异步生成器的结果。

    使用 asyncio.Queue 作为缓冲，asyncio.wait(FIRST_COMPLETED) 做调度。
    任何一个生成器有结果到达就立刻 yield (index, value)，不阻塞其他生成器。

    Yields:
        (index, value) — index 是生成器在参数列表中的位置，value 是产出的值

    对标 Mysti 的 BrainstormManager._interleaveGenerators()
    """
    if not generators:
        return

    n = len(generators)
    queues: list[asyncio.Queue] = [asyncio.Queue() for _ in range(n)]
    tasks: list[asyncio.Task] = []

    async def _feed(gen: AsyncGenerator, q: asyncio.Queue, idx: int):
        """从生成器读取，逐个放入队列"""
        try:
            async for item in gen:
                await q.put(("item", item, idx))
            await q.put(("done", None, idx))
        except Exception as e:
            await q.put(("error", e, idx))

    for i, gen in enumerate(generators):
        tasks.append(asyncio.create_task(_feed(gen, queues[i], i)))

    active = set(range(n))

    try:
        while active:
            get_tasks = [
                asyncio.create_task(queues[i].get())
                for i in active
            ]

            done, pending = await asyncio.wait(
                get_tasks, return_when=asyncio.FIRST_COMPLETED
            )

            for p in pending:
                p.cancel()

            for task in done:
                tag, value, idx = task.result()

                if tag == "done":
                    active.discard(idx)
                elif tag == "error":
                    active.discard(idx)
                    raise value
                else:
                    yield (idx, value)

    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def interleave_with_timeout(
    *generators: AsyncGenerator,
    timeout: float = 90.0,
) -> AsyncGenerator[tuple[int, Any], None]:
    """
    带静默超时的交错调度。

    行为：
    - 某个生成器超时 → 产出一个特殊标记 (idx, {"_timeout": True})
    - 调用者可以检查超时标记，决定是继续还是跳过
    - 其他未超时的生成器继续正常产出
    - 所有生成器都完成（或超时）后结束
    """
    if not generators:
        return

    n = len(generators)
    queues: list[asyncio.Queue] = [asyncio.Queue() for _ in range(n)]
    tasks: list[asyncio.Task] = []

    async def _feed_with_timeout(gen: AsyncGenerator, q: asyncio.Queue, idx: int):
        """从生成器读取，带逐条超时"""
        try:
            while True:
                try:
                    item = await asyncio.wait_for(gen.__anext__(), timeout=timeout)
                    await q.put(("item", item, idx))
                except StopAsyncIteration:
                    await q.put(("done", None, idx))
                    return
                except asyncio.TimeoutError:
                    # 超时 → 产出标记，然后标记 done
                    await q.put(("item", {"_timeout": True}, idx))
                    await q.put(("done", None, idx))
                    return
        except Exception as e:
            await q.put(("error", e, idx))

    for i, gen in enumerate(generators):
        tasks.append(asyncio.create_task(_feed_with_timeout(gen, queues[i], i)))

    active = set(range(n))

    try:
        while active:
            get_tasks = [
                asyncio.create_task(queues[i].get())
                for i in active
            ]
            done, pending = await asyncio.wait(
                get_tasks, return_when=asyncio.FIRST_COMPLETED
            )
            for p in pending:
                p.cancel()

            for task in done:
                tag, value, idx = task.result()
                if tag == "done":
                    active.discard(idx)
                elif tag == "error":
                    active.discard(idx)
                    raise value
                else:
                    yield (idx, value)
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
