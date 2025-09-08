import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Coroutine, List, Optional

import tweepy
from tweepy import Client

logger = logging.getLogger(__name__)

RETRY_AFTER_SEC = 15 * 60  # 15 分钟


@dataclass
class _PoolItem:  # type: ignore[no-any-unimported]
    client: tweepy.Client  # type: ignore[no-any-unimported]
    dead_at: Optional[float] = None  # None 表示 alive


class TwitterClientPool:
    """
    Twitter 客户端专用池：轮询获取、异常熔断、15 min 复活、支持永久摘除
    """

    def __init__(self, clients: List[Client], retry_after: float = RETRY_AFTER_SEC):  # type: ignore[no-any-unimported]
        self._retry_after = retry_after
        self._pool: List[_PoolItem] = [_PoolItem(c) for c in clients]
        self._lock = asyncio.Lock()
        self._not_empty = asyncio.Event()
        self._rr_idx = 0
        if self._pool:
            self._not_empty.set()

    # -------------------- 对外 API --------------------
    async def acquire(self) -> tuple[Client, Any]:  # type: ignore[no-any-unimported]
        """轮询获取一个健康 client；池空时阻塞直到有可用实例。"""
        while True:
            async with self._lock:
                now = time.time()
                # 1. 复活
                for it in self._pool:
                    if it.dead_at and now - it.dead_at >= self._retry_after:
                        it.dead_at = None
                        logger.info("client %s revived", id(it.client))

                # 2. 留活的
                alive = [it for it in self._pool if it.dead_at is None]
                if not alive:
                    self._not_empty.clear()
                    logger.warning("all clients dead, waiting ...")
                    await asyncio.wait_for(self._not_empty.wait(), timeout=1)
                    continue

                # 3. 轮询
                idx = self._rr_idx % len(alive)
                self._rr_idx += 1
                chosen = alive[idx]
                # 移到尾部，公平 RR
                self._pool.remove(chosen)
                self._pool.append(chosen)
                client = chosen.client
                return client, client.consumer_key

    def remove(self, client: tweepy.Client) -> None:  # type: ignore[no-any-unimported]
        """永久摘除某个 client（不再放回池子）。"""
        for it in self._pool:
            if it.client is client:
                self._pool.remove(it)
                logger.info("client %s removed permanently", id(client))
                if not any(it.dead_at is None for it in self._pool):
                    self._not_empty.clear()
                return

    def release(self, client: tweepy.Client, *, failed: bool = False) -> None:  # type: ignore[no-any-unimported]
        """归还 client；failed=True 表示请求异常，触发 15 min 熔断。"""
        for it in self._pool:
            if it.client is client:
                if failed:
                    it.dead_at = time.time()
                    logger.warning("client %s dead, will retry after %s min", id(client), self._retry_after // 60)
                asyncio.create_task(self._notify_maybe_alive())
                return

    # -------------------- 内部 --------------------
    async def _notify_maybe_alive(self) -> None:
        async with self._lock:
            if any(it.dead_at is None for it in self._pool):
                self._not_empty.set()
