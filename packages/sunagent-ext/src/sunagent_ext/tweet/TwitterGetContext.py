import asyncio
import json
import logging
from typing import List, Dict, Optional, Any, cast
from datetime import datetime, timedelta, timezone

from sunagent_ext.cache_store import CacheStore
from tweepy import Response as TwitterResponse, TwitterServerError, NotFound

from sunagent_ext.tweet import TwitterClientPool

logger = logging.getLogger("tweet_get_context")

# 字段按需裁剪
TWEET_FIELDS = [
    "id", "created_at", "author_id", "text", "public_metrics",
    "referenced_tweets", "conversation_id", "entities",
]
EXPANSIONS = ["author_id", "referenced_tweets.id", "referenced_tweets.id.author_id"]
USER_FIELDS = ["id", "username", "name", "public_metrics"]
MAX_RESULTS = 100   # 每页上限

class TweetGetContext:
    """
    只负责「Home 时间线 & Mention 时间线」增量抓取，
    所有网络请求通过外部 TwitterClientPool 完成。
    """
    def __init__(
        self,
        pool: TwitterClientPool,               # 前面实现的池子
        cache: Optional[CacheStore[str]] = None,
        max_results: int = MAX_RESULTS,
    ) -> None:
        self.pool = pool
        self.cache = cache
        self.max_results = max_results

    # -------------------- 对外 API --------------------
    async def get_home_timeline_with_context(
        self,
        me_id: str,
        hours: int = 24,
        since_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        抓取 **Home 时间线** 最近 N 小时推文（不含回复/转推），
        返回 List[Dict]（按时间升序）。
        """
        return await self._fetch_timeline(
            endpoint="home",
            me_id=me_id,
            hours=hours,
            since_id=since_id,
        )

    async def get_mentions_with_context(
        self,
        me_id: str,
        hours: int = 24,
        since_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        抓取 **@我 的 Mention** 最近 N 小时推文，
        返回 List[Dict]（按时间升序）。
        """
        return await self._fetch_timeline(
            endpoint="mentions",
            me_id=me_id,
            hours=hours,
            since_id=since_id,
        )

    # -------------------- 统一抓取逻辑 --------------------
    async def _fetch_timeline(
        self,
        endpoint: str,          # "home" | "mentions"
        me_id: str,
        hours: int,
        since_id: Optional[str],
    ) -> List[Dict[str, Any]]:
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        start_time = since.isoformat(timespec="seconds")

        all_tweets: List[Dict[str, Any]] = []
        next_token = None
        cache_key = f"{endpoint}:{me_id}"

        # 增量游标
        if not since_id and self.cache:
            since_id = self.cache.get(cache_key)

        while True:
            cli = await self.pool.acquire()
            try:
                if endpoint == "home":
                    resp = cli.get_home_timeline(
                        tweet_fields=TWEET_FIELDS,
                        expansions=EXPANSIONS,
                        user_fields=USER_FIELDS,
                        exclude=["replies", "retweets"],
                        start_time=start_time,
                        since_id=since_id,
                        max_results=self.max_results,
                        pagination_token=next_token,
                    )
                else:  # mentions
                    resp = cli.get_users_mentions(
                        id=me_id,
                        tweet_fields=TWEET_FIELDS,
                        expansions=EXPANSIONS,
                        user_fields=USER_FIELDS,
                        start_time=start_time,
                        since_id=since_id,
                        max_results=self.max_results,
                        pagination_token=next_token,
                    )

                if resp.data:
                    users = {str(u.id): u.data for u in resp.includes.get("users", [])}
                    for tw in resp.data:
                        d = tw.data
                        d["author"] = users.get(d["author_id"], {}).get("username", d["author_id"])
                        all_tweets.append(d)

                next_token = resp.meta.get("next_token")
                if not next_token:
                    break

                self.pool.release(cli, failed=False)

            except (NotFound, TwitterServerError):
                # 404 / 5xx 不再重试
                self.pool.release(cli, failed=False)
                break
            except Exception as e:
                logger.warning("timeline %s error: %s", endpoint, e)
                self.pool.release(cli, failed=True)
                break

        # 升序（老→新）并缓存 newest_id
        all_tweets.sort(key=lambda t: t["id"])
        if all_tweets and self.cache:
            newest_id = all_tweets[-1]["id"]
            self.cache.set(cache_key, str(newest_id))

        return all_tweets