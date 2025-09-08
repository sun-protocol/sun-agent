import asyncio
import logging
import time
from dataclasses import dataclass
from typing import List, Optional, Protocol

import tweepy

logger = logging.getLogger(__name__)

RETRY_AFTER_SEC = 15 * 60  # 15 分钟


@dataclass
class _PoolItem:  # type: ignore[no-any-unimported]
    client: tweepy.Client  # type: ignore[no-any-unimported]
    client_key: str  # 用 consumer_key 当唯一标识
    dead_at: Optional[float] = None  # None 表示 alive


class TwitterClientPool:
    """
    Twitter 客户端专用池：轮询获取、异常熔断、15 min 复活、支持永久摘除
    全部操作在锁内完成，保证并发安全。
    """

    def __init__(self, clients: list[tweepy.Client], retry_after: float = RETRY_AFTER_SEC) -> None:  # type: ignore[no-any-unimported]
        self._retry_after = retry_after
        self._pool: list[_PoolItem] = [_PoolItem(c, c.consumer_key) for c in clients]
        self._lock = asyncio.Lock()
        self._not_empty = asyncio.Event()
        # 轮询指针：永远按「当前池子长度」取模，保证真·RR
        self._rr_idx = 0
        if self._pool:
            self._not_empty.set()

    # -------------------- 对外 API --------------------
    async def acquire(self) -> tuple[tweepy.Client, str]:  # type: ignore[no-any-unimported]
        """真·Round-Robin：在完整池子上轮询，跳过 dead 的。"""
        while True:
            async with self._lock:
                now = time.time()
                # 1. 复活
                revived = False
                for it in self._pool:
                    if it.dead_at and now - it.dead_at >= self._retry_after:
                        it.dead_at = None
                        revived = True
                        logger.info("client %s revived", it.client_key)
                if revived:
                    self._not_empty.set()

                # 2. 真·轮询：在完整池子上跳过 dead 的
                for _ in range(len(self._pool)):
                    idx = self._rr_idx % len(self._pool)
                    self._rr_idx += 1
                    chosen = self._pool[idx]
                    if chosen.dead_at is None:
                        # 移到尾部，实现 RR
                        self._pool.pop(idx)
                        self._pool.append(chosen)
                        return chosen.client, chosen.client_key

                # 3. 没有 alive 的
                self._not_empty.clear()

            # 释放锁后再等，避免忙等
            await self._not_empty.wait()

    # -------------------- 加锁摘除 --------------------
    async def remove(self, client: tweepy.Client) -> None:  # type: ignore[no-any-unimported]
        """永久摘除某个 client（不再放回池子）。"""
        async with self._lock:
            for idx, it in enumerate(self._pool):
                if it.client is client:
                    self._pool.pop(idx)
                    logger.info("client %s removed permanently", it.client_key)
                    if not any(item.dead_at is None for item in self._pool):
                        self._not_empty.clear()
                    return

    # -------------------- 归还 --------------------
    async def release(self, client: tweepy.Client, *, failed: bool = False) -> None:  # type: ignore[no-any-unimported]
        async with self._lock:
            for it in self._pool:
                if it.client is client:
                    if failed:
                        it.dead_at = time.time()
                        logger.warning(
                            "client %s dead, will retry after %s min", it.client_key, self._retry_after // 60
                        )
                    # 异步唤醒等待者
                    asyncio.create_task(self._notify_maybe_alive())
                    return

    # -------------------- 内部 --------------------
    async def _notify_maybe_alive(self) -> None:
        async with self._lock:
            if any(item.dead_at is None for item in self._pool):
                self._not_empty.set()
