# tweet_from_queue.py
import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Callable, List

import nats
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from nats.aio.msg import Msg
from nats.aio.subscription import Subscription

logger = logging.getLogger(__name__)


class TweetFromQueueContext:
    """
    1. 订阅 NATS subject，累积推文
    2. APScheduler 每 10 秒强制 flush 一次（不管有没有消息）
    3. 支持优雅关闭
    """

    def __init__(
        self,
        size: int,
        user_ids: List[str],
        nats_url: str,
        callback: Callable[[List[dict[str, Any]]], Any],
        flush_seconds: int = 10,
    ):
        self.size = size
        self.user_ids = user_ids
        self.nats_url = nats_url
        self.flush_seconds = flush_seconds

        self._nc = None
        self._buffer: List[dict[str, Any]] = []
        self._callback: Callable[[List[dict[str, Any]]], Any] = callback
        self._sub: Subscription | None = None
        self._scheduler: AsyncIOScheduler | None = None  # type: ignore[no-any-unimported]
        self._lock = asyncio.Lock()

    # -------------------- 生命周期 --------------------
    async def start(self, subject: str) -> None:
        # 1. 连接 NATS
        self._nc = await nats.connect(self.nats_url)  # type: ignore[assignment]
        self._sub = await self._nc.subscribe(subject, cb=self._nc_consume)  # type: ignore[attr-defined]
        logger.info("Subscribed to <%s>, agent=%s", subject, self.user_ids)

        # 2. 启动 APScheduler 定时 flush
        self._scheduler = AsyncIOScheduler()
        self._scheduler.add_job(
            self._flush,  # 定时执行的协程
            trigger="interval",
            seconds=self.flush_seconds,
            max_instances=1,
            next_run_time=datetime.now(),  # 立即执行一次
        )
        self._scheduler.start()

    async def close(self) -> None:
        """优雅关闭：停订阅 -> 停调度器 -> 刷剩余数据 -> 断 NATS"""
        # 1. 停止订阅（不再接收新消息）
        if self._sub:
            await self._sub.unsubscribe()
            self._sub = None

        # 2. 停止调度器（不再定时 flush）
        if self._scheduler:
            self._scheduler.shutdown(wait=False)

        # 3. 刷剩余数据
        async with self._lock:
            await self._flush()

        # 4. 断开 NATS
        if self._nc:
            await self._nc.close()
            logger.info("NATS connection closed")

    # -------------------- NATS 回调 --------------------
    async def _nc_consume(self, msg: Msg) -> None:
        try:
            tweet = json.loads(msg.data.decode())
            tweet = self._fix_tweet_dict(tweet)
            if tweet["author_id"] not in self.user_ids:
                return
        except Exception as e:
            logger.exception("Bad message: %s", e)
            return

        need_flush = False
        async with self._lock:
            self._buffer.append(tweet)
            if len(self._buffer) >= self.size:
                need_flush = True

        # 锁外调用 flush，避免死锁
        if need_flush:
            await self._flush()

    async def _flush(self) -> None:
        async with self._lock:
            if not self._buffer:
                return
            to_send = self._buffer.copy()
            self._buffer.clear()

        # 锁外执行回调，避免阻塞消息处理
        logger.info("Flush %d tweets", len(to_send))
        try:
            await self._callback(to_send)
        except Exception as e:
            logger.exception("Callback error: %s", e)

    # -------------------- 工具 --------------------
    @staticmethod
    def _fix_tweet_dict(msg: dict[str, Any]) -> dict[str, Any]:
        fixed = msg.copy()
        for key in ("created_at", "updated_at"):
            if key in fixed and isinstance(fixed[key], str):
                fixed[key] = datetime.fromisoformat(fixed[key])
        return fixed
