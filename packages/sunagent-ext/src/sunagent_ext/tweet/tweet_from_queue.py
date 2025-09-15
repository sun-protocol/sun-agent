import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

import nats
from nats.aio.msg import Msg

logger = logging.getLogger(__name__)


class TweetFromQueueContext:
    def __init__(
        self,
        *,
        batch_size: int,
        flush_seconds: float,
        callback: Callable[[List[Dict[str, Any]]], Any],
        nats_url: str,
        subject: str,
        user_ids: Optional[List[str]] = None,
    ):
        if batch_size <= 0 or flush_seconds <= 0:
            raise ValueError("batch_size / flush_seconds 必须为正")
        self.batch_size = batch_size
        self.flush_seconds = flush_seconds
        self.callback = callback
        self.nats_url = nats_url
        self.subject = subject
        self.user_ids = set(user_ids) if user_ids else None

        self._nc: Optional[nats.NATS] = None  # type: ignore[name-defined]
        self._sub = None
        self._queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
        self._stop_evt = asyncio.Event()
        self._worker_task: Optional[asyncio.Task] = None  # type: ignore[type-arg]

    # -------------------- 生命周期 --------------------
    async def start(self) -> None:
        self._nc = await nats.connect(self.nats_url)
        self._sub = await self._nc.subscribe(self.subject, cb=self._on_msg)  # type: ignore[assignment]
        logger.info("Subscribed to <%s>, filter=%s", self.subject, self.user_ids)
        # 启动单协程 worker
        self._worker_task = asyncio.create_task(self._worker_loop())

    async def stop(self) -> None:
        logger.info("Stopping AsyncBatchingQueue...")
        self._stop_evt.set()
        if self._worker_task:
            await self._worker_task
        if self._sub:
            await self._sub.unsubscribe()
        await self._flush()  # 刷剩余
        if self._nc:
            await self._nc.close()
        logger.info("AsyncBatchingQueue stopped")

    async def __aenter__(self):  # type: ignore[no-untyped-def]
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb):  # type: ignore[no-untyped-def]
        await self.stop()

    # -------------------- 公共入口 --------------------
    async def add(self, item: Dict[str, Any]) -> None:
        await self._queue.put(item)

    # -------------------- NATS 回调 --------------------
    async def _on_msg(self, msg: Msg) -> None:
        try:
            tweet = json.loads(msg.data.decode())
            tweet = self._fix_tweet_dict(tweet)
            # if self.user_ids and tweet.get("author_id") not in self.user_ids:
            #     return
        except Exception as e:
            logger.exception("Bad msg: %s", e)
            return
        await self.add(tweet)

    # -------------------- 核心 worker：完全模仿 _worker_loop --------------------
    async def _worker_loop(self) -> None:
        """单协程：等第一条 -> 设 deadline -> 继续 get(timeout=剩余时间) -> 满批/超时刷"""
        while not self._stop_evt.is_set():
            batch: List[Dict[str, Any]] = []
            deadline = None

            # 1. 阻塞等第一条
            try:
                first = await asyncio.wait_for(self._queue.get(), timeout=0.1)
                batch.append(first)
                deadline = asyncio.get_event_loop().time() + self.flush_seconds
            except asyncio.TimeoutError:
                continue

            # 2. 收集剩余
            while len(batch) < self.batch_size and not self._stop_evt.is_set():
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    break
                try:
                    item = await asyncio.wait_for(self._queue.get(), timeout=remaining)
                    batch.append(item)
                except asyncio.TimeoutError:
                    break

            # 3. 处理
            if batch:
                try:
                    await self.callback(batch)
                except Exception as e:
                    logger.exception("Callback error: %s", e)
        # 4. 退出时刷剩余
        await self._drain_remaining()

    async def _flush(self) -> None:
        """同步刷剩余（给 stop 用）"""
        batch = []
        while not self._queue.empty():
            batch.append(self._queue.get_nowait())
        if batch:
            try:
                await self.callback(batch)
            except Exception as e:
                logger.exception("Final callback error: %s", e)

    async def _drain_remaining(self) -> None:
        await self._flush()

    @staticmethod
    def _fix_tweet_dict(msg: Dict[str, Any]) -> Dict[str, Any]:
        fixed = msg.copy()
        for key in ("created_at", "updated_at"):
            if key in fixed and isinstance(fixed[key], str):
                fixed[key] = datetime.fromisoformat(fixed[key])
        return fixed
