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
    client_key: str  # 用 consumer_key 当唯一标识
    dead_at: Optional[float] = None  # None 表示 alive


class TwitterClientPool:
    """
    Twitter 客户端专用池：轮询获取、异常熔断、15 min 复活、支持永久摘除。
    所有操作在锁内完成，保证并发安全。
    """

    def __init__(self, clients: list[tweepy.Client], retry_after: float = RETRY_AFTER_SEC) -> None:  # type: ignore[no-any-unimported]
        self._retry_after = retry_after
        self._pool: list[_PoolItem] = [_PoolItem(c, c.consumer_key) for c in clients]
        self._lock = asyncio.Lock()
        self._not_empty = asyncio.Event()
        # 轮询指针：指向下一次应该开始检查的索引
        self._rr_idx = 0
        if any(item.dead_at is None for item in self._pool):
            self._not_empty.set()

    async def acquire(self) -> tuple[Client, str]:  # type: ignore[no-any-unimported]
        while True:
            async with self._lock:
                # 0. 处理池子为空的边缘情况
                if not self._pool:
                    self._not_empty.clear()
                    # 在锁外等待，但先释放锁
                    raise RuntimeError("TwitterClientPool: 所有客户端已被永久摘除，请重建池子")
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
                    # 2. 简化的、正确的轮询
                    # 从上一次的位置开始，最多检查 N 次 (N=池子大小)
                    for i in range(len(self._pool)):
                        # 使用 % 来确保索引总在有效范围内
                        idx = (self._rr_idx + i) % len(self._pool)
                        chosen = self._pool[idx]
                        if chosen.dead_at is None:
                            # 找到了一个可用的，更新下一次开始搜索的指针
                            self._rr_idx = idx + 1
                            return chosen.client, chosen.client_key
                    # 3. 如果循环走完都没有找到 alive 的
                    self._not_empty.clear()
                    # 4. 释放锁后等待，避免死锁和忙等
                    await self._not_empty.wait()

    # -------------------- 加锁摘除 --------------------
    async def remove(self, client: tweepy.Client) -> None:  # type: ignore[no-any-unimported]
        """永久摘除某个 client（不再放回池子）。"""
        async with self._lock:
            # 使用列表推导式过滤掉要移除的客户端，比 pop 更安全
            original_len = len(self._pool)
            client_key_to_remove = client.consumer_key
            self._pool = [it for it in self._pool if it.client is not client]
            if len(self._pool) < original_len:
                logger.info("client %s removed permanently", client_key_to_remove)
                # 检查移除后是否还有存活的客户端
                if not any(item.dead_at is None for item in self._pool):
                    self._not_empty.clear()

    # -------------------- 归还 --------------------
    async def report_failure(self, client: tweepy.Client) -> None:  # type: ignore[no-any-unimported]
        """
        报告一个客户端操作失败，将其置于熔断状态。
        这不会将客户端从池中移除，它将在指定时间后自动复活。
        """
        async with self._lock:
            for it in self._pool:
                if it.client is client:
                    # 只有当它还活着时才标记为死亡，避免重复记录
                    if it.dead_at is None:
                        it.dead_at = time.time()
                        logger.warning(
                            "client %s dead, will retry after %s min", it.client_key, self._retry_after // 60
                        )
                        # 检查此操作是否导致所有客户端都死亡
                        if not any(item.dead_at is None for item in self._pool):
                            self._not_empty.clear()
                    return  # 找到后即可退出
