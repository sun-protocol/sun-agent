"""
Twitter 时间线 & Mention 增量抓取 + 对话链拼合
网络请求通过 TwitterClientPool，Prometheus 埋点带 client_key
"""

import asyncio
import logging
import traceback
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, cast

from prometheus_client import Counter, Gauge
from tweepy import Media, NotFound, TwitterServerError, User  # 保持原类型注解
from tweepy import Response as TwitterResponse
import tweepy

from sunagent_ext.tweet.twitter_client_pool import TwitterClientPool

logger = logging.getLogger("tweet_get_context")

# ---------- Prometheus 指标 ----------
read_tweet_success_count = Counter(
    "ext_read_tweet_success_count", "Number of successful read tweets", labelnames=["client_key"]
)
read_tweet_failure_count = Counter(
    "ext_read_tweet_failure_count", "Number of failed read tweets", labelnames=["client_key"]
)
tweet_monthly_cap = Gauge("ext_tweet_monthly_cap", "0=触顶 1=正常", labelnames=["client_key"])

# ---------- 字段 ----------
TWEET_FIELDS = [
    "id",
    "created_at",
    "author_id",
    "text",
    "public_metrics",
    "referenced_tweets",
    "conversation_id",
    "entities",
    "display_text_range",
    "attachments",
    "withheld",
    "note_tweet",
    "edit_controls",
    "edit_history_tweet_ids",
    "possibly_sensitive",
    "reply_settings",
    "source",
    "lang",
    "geo",
    "context_annotations",
    "card_uri",
    "community_id",
    "in_reply_to_user_id",
    "media_metadata",
]
EXPANSIONS = [
    "author_id",
    "referenced_tweets.id",
    "referenced_tweets.id.author_id",
    "attachments.media_keys",
    "attachments.poll_ids",
    "geo.place_id",
]
USER_FIELDS = [
    "id",
    "username",
    "name",
    "public_metrics",
    "created_at",
    "description",
    "entities",
    "location",
    "pinned_tweet_id",
    "profile_image_url",
    "protected",
    "verified",
    "verified_type",
    "is_identity_verified",
    "affiliation",
    "connection_status",
    "most_recent_tweet_id",
    "parody",
    "receives_your_dm",
    "subscription",
    "subscription_type",
    "profile_banner_url",
    "withheld",
]
MEDIA_FIELDS = [
    "alt_text",
    "duration_ms",
    "height",
    "media_key",
    "preview_image_url",
    "public_metrics",
    "type",
    "url",
    "variants",
    "width",
]
POLL_FIELDS = ["duration_minutes", "end_datetime", "id", "options", "voting_status"]
PLACE_FIELDS = ["contained_within", "country", "country_code", "full_name", "geo", "id", "name", "place_type"]
MAX_RESULTS = 100
PROCESS_KEY_PREFIX = "P:"
FREQ_KEY_PREFIX = "F:"
HOME_TIMELINE_ID = "last_home_timeline"
MENTIONS_TIMELINE_ID = "last_mentions_timeline"
MONTHLY_CAP_INFO = "Monthly product cap"


# ---------- 主类 ----------
class TweetGetContext:
    def __init__(  # type: ignore[no-untyped-def]
        self,
        pool: TwitterClientPool,  # 外部池子
        cache=None,  # 可选缓存
        max_results: int = MAX_RESULTS,
        block_user_ids: Optional[list[str]] = None,
        white_user_ids: Optional[list[str]] = None,
        reply_freq_limit: int = 5,
        max_depth: int = 5,
    ) -> None:
        self.pool = pool
        self.cache = cache
        self.max_depth = max_depth
        self.max_results = max_results
        self.block_uids = set(block_user_ids or [])
        self.white_uids = set(white_user_ids or [])
        self.freq_limit = reply_freq_limit
        # 用于 mentions_me 判断（可外部注入 me_id）
        self.me_id: Optional[str] = None

    # ===================== 对外 API =====================
    async def get_home_timeline_with_context(
        self,
        me_id: str,
        agent_id: str,
        hours: int = 24,
        since_id: Optional[str] = None,
        filter_func: Optional[Callable[[Dict[str, Any]], bool]] = None,
    ) -> list[Dict[str, Any]]:
        return await self._fetch_timeline(
            endpoint="home",
            me_id=me_id,
            hours=hours,
            since_id=since_id,
            agent_id=agent_id,
            filter_func=filter_func or (lambda _: True),
        )

    async def get_mentions_with_context(
        self,
        me_id: str,
        agent_id: str,
        hours: int = 24,
        since_id: Optional[str] = None,
        filter_func: Optional[Callable[[Dict[str, Any]], bool]] = None,
    ) -> list[Dict[str, Any]]:
        self.me_id = me_id
        return await self._fetch_timeline(
            endpoint="mentions",
            me_id=me_id,
            hours=hours,
            agent_id=agent_id,
            since_id=since_id,
            filter_func=filter_func or (lambda _: True),
        )

    # ===================== 统一抓取 =====================
    async def _fetch_timeline(
        self,
        endpoint: str,
        me_id: str,
        hours: int,
        since_id: Optional[str],
        filter_func: Callable[[Dict[str, Any]], bool],
        agent_id: str,
    ) -> list[Dict[str, Any]]:
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        start_time = since.isoformat(timespec="seconds")
        next_token = None
        all_raw: list[Dict[str, Any]] = []
        cache_key = f"{agent_id}:{MENTIONS_TIMELINE_ID}"
        if endpoint == "home":
            cache_key = f"{agent_id}:{HOME_TIMELINE_ID}"
        if not since_id and self.cache:
            since_id = self.cache.get(cache_key)

        while True:
            cli, client_key = await self.pool.acquire()
            try:
                if endpoint == "home":
                    resp = cli.get_home_timeline(
                        tweet_fields=TWEET_FIELDS,
                        expansions=EXPANSIONS,
                        media_fields=MEDIA_FIELDS,
                        poll_fields=POLL_FIELDS,
                        user_fields=USER_FIELDS,
                        place_fields=PLACE_FIELDS,
                        exclude=["replies", "retweets"],
                        start_time=start_time,
                        since_id=since_id,
                        max_results=self.max_results,
                        pagination_token=next_token,
                        user_auth=True,
                    )
                else:  # mentions
                    resp = cli.get_users_mentions(
                        id=me_id,
                        tweet_fields=TWEET_FIELDS,
                        expansions=EXPANSIONS,
                        media_fields=MEDIA_FIELDS,
                        poll_fields=POLL_FIELDS,
                        user_fields=USER_FIELDS,
                        place_fields=PLACE_FIELDS,
                        start_time=start_time,
                        since_id=since_id,
                        max_results=self.max_results,
                        pagination_token=next_token,
                        user_auth=True,
                    )

                # ③ 成功埋点
                read_tweet_success_count.labels(client_key=client_key).inc(len(resp.data or []))

                # 交给中间层处理
                tweet_list, next_token = await self.on_twitter_response(agent_id, me_id, resp, filter_func)
                all_raw.extend(tweet_list)
                if not next_token:
                    break
            except (NotFound, TwitterServerError):
                break
            except Exception as e:
                logger.warning("timeline %s error: %s", endpoint, e)
                # ④ 失败埋点
                read_tweet_failure_count.labels(client_key=client_key).inc()
                # ⑤ 月额度检测
                if MONTHLY_CAP_INFO in str(e):
                    tweet_monthly_cap.labels(client_key=client_key).set(0)
                    await self.pool.remove(cli)  # 永久踢出
                    logger.error("client %s removed due to monthly cap", client_key)
                    break
                else:
                    tweet_monthly_cap.labels(client_key=client_key).set(1)
                    await self.pool.report_failure(cli)
                break

        all_raw.sort(key=lambda t: t["id"])
        if all_raw and self.cache:
            newest_id = all_raw[-1]["id"]
            self.cache.set(cache_key, str(newest_id))
        return all_raw

    # ===================== 中间处理钩子（保留） =====================
    async def on_twitter_response(  # type: ignore[no-any-unimported]
        self,
        agent_id: str,
        me_id: str,
        response: TwitterResponse,
        filter_func: Callable[[Dict[str, Any]], bool],
    ) -> tuple[list[Dict[str, Any]], Optional[str]]:
        next_token = response.meta.get("next_token")
        if response.meta.get("result_count", 0) == 0 or response.data is None:
            return [], next_token

        users = self._build_users(response.includes)
        medias = self._build_medias(response.includes)
        all_tweets = self._get_all_tweets(response, users, medias)
        out: list[Dict[str, Any]] = []

        for tweet in all_tweets:
            if not await self._should_keep(agent_id, me_id, tweet, filter_func):
                continue
            norm = await self._normalize_tweet(tweet)
            out.append(norm)
        return out, next_token

    async def _should_keep(
        self, agent_id: str,  me_id: str, tweet: Dict[str, Any], filter_func: Callable[[Dict[str, Any]], bool]
    ) -> bool:
        is_processed = await self._check_tweet_process(tweet["id"], agent_id)
        if is_processed:
            logger.info("already processed %s", tweet["id"])
            return False
        author_id = str(tweet["author_id"])
        if me_id == author_id:
            logger.info("skip my tweet %s", tweet["id"])
            return False
        if author_id in self.block_uids:
            logger.info("blocked user %s", author_id)
            return False
        freq = await self._get_freq(agent_id, tweet)
        if freq >= self.freq_limit and author_id not in self.white_uids:
            logger.info(f"skip tweet {tweet['id']} freq {freq}")
            return False
        await self._increase_freq(agent_id, tweet)
        return filter_func(tweet)

    async def _check_tweet_process(self, tweet_id: str, agent_id: str) -> bool:
        if self.cache is None:
            return False
        try:
            return self.cache.get(f"{agent_id}:{PROCESS_KEY_PREFIX}{tweet_id}") is not None
        except Exception:
            # regard it as processed if cache not available
            return True

    async def _mark_tweet_process(self, tweet_id: str, agent_id: str) -> None:
        if self.cache is None:
            return
        try:
            self.cache.set(f"{agent_id}:{PROCESS_KEY_PREFIX}{tweet_id}", "")
        except Exception:
            pass

    async def _get_freq(self, agent_id: str, tweet: Dict[str, Any]) -> int:
        if self.cache is None:
            return -1
        try:
            freq = self.cache.get(f"{agent_id}:{FREQ_KEY_PREFIX}{tweet['conversation_id']}")
            return int(freq) if freq else 0
        except Exception:
            return 0

    async def _increase_freq(self, agent_id: str, tweet: Dict[str, Any]) -> None:
        if self.cache is None:
            return
        freq = await self._get_freq(agent_id, tweet)
        try:
            self.cache.set(f"{agent_id}:{FREQ_KEY_PREFIX}{tweet['conversation_id']}", str(freq + 1))
        except Exception:
            pass

    async def _normalize_tweet(self, tweet: Dict[str, Any]) -> Dict[str, Any]:
        wanted = [
            "id",
            "created_at",
            "author_id",
            "author",
            "text",
            "public_metrics",
            "conversation_id",
            "entities",
        ]
        out = {k: tweet[k] for k in wanted if k in tweet}
        out["history"] = await self._build_context(tweet)
        out["sampling_quote"] = not tweet.get("referenced_tweets")
        return out

    async def _build_context(self, tweet: Dict[str, Any]) -> str:
        chain: list[Dict[str, Any]] = []
        await self._recursive_fetch(tweet, chain, depth=0)
        lines = ["<conversation>"]
        for t in chain:
            lines.append(f"<tweet>{t.get('text', '')}</tweet>")
        lines.append("</conversation>")
        return "\n".join(lines)

    async def _recursive_fetch(self, tweet: Dict[str, Any], chain: list[Dict[str, Any]], depth: int) -> None:
        if depth > 5:
            chain.append(tweet)
            return
        parent_id = None
        if tweet.get("referenced_tweets"):
            ref = tweet["referenced_tweets"][0]
            if ref["type"] == "replied_to":
                parent_id = ref["id"]
        if parent_id:
            parent = await self._get_tweet_with_retry(parent_id)
            if parent:
                await self._recursive_fetch(parent, chain, depth + 1)
        chain.append(tweet)

    async def fetch_new_tweets_manual_( self,
            ids: List[str],
            last_seen_id: str | None = None,
            ):
        """
        1. 取所有 ALIVE KOL 的 twitter_id
        2. 将 id 列表拆分成多条不超长 query
        3. 逐条交给 fetch_new_tweets_manual_tweets 翻页
        4. 返回全部结果以及 **所有结果中最大的 tweet_id**
        """
        BASE_EXTRA = " -is:retweet"
        max_len = 512 - len(BASE_EXTRA) - 10
        queries: List[str] = []

        buf, first = [], True
        for uid in ids:
            clause = f"from:{uid}"
            if len(" OR ".join(buf + [clause])) > max_len:
                queries.append(" OR ".join(buf) + BASE_EXTRA)
                buf, first = [clause], True
            else:
                buf.append(clause)
                first = False
        if buf:
            queries.append(" OR ".join(buf) + BASE_EXTRA)

        # 3) 逐条调用内层并合并
        all_tweets: List[tweepy.Tweet] = []
        for q in queries:
            tweets = await self.fetch_new_tweets_manual_tweets(
                query=q,
                last_seen_id=last_seen_id
            )
            all_tweets.extend(tweets)
            await asyncio.sleep(30)

        # 4) 取所有结果中最大的 id 作为 last_seen_id
        last_id = max((tw.id for tw in all_tweets), default=None)
        return all_tweets, last_id

    async def get_kol_tweet(self, kol_ids: List[str]):
        cache_key = "kol_last_seen_id"
        last_seen_id = await self.cache.get(cache_key)
        tweets, last_seen_id = await self.fetch_new_tweets_manual_(ids=kol_ids, last_seen_id=last_seen_id)
        await self.cache.set(cache_key, last_seen_id)
        return tweets

    async def fetch_new_tweets_manual_tweets(
            self,
            query: str,
            last_seen_id: str | None = None,
            max_per_page: int = 100,
            hours: int = 24
    ):
        tweets = []
        next_token = None

        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        start_time = None if last_seen_id else since.isoformat(timespec="seconds")
        logger.info(f"query: {query}")
        while True:
            cli = None
            try:
                cli, key = await self.pool.acquire()
                resp = cli.search_recent_tweets(
                    query=query,
                    start_time=start_time,
                    since_id=last_seen_id,
                    max_results=max_per_page,
                    tweet_fields=TWEET_FIELDS,
                    next_token=next_token,
                    user_auth=True
                )
                page_data = resp.data or []
                logger.info(f"page_data: {len(page_data)}")
                for tw in page_data:
                    # 1. 已读过的直接停
                    tweets.append(tw)
                    read_tweet_success_count.inc()
                next_token = resp.meta.get("next_token")
                if not next_token:
                    break
            except tweepy.TooManyRequests as e:
                logger.error(traceback.format_exc())
                read_tweet_failure_count.inc()
                if cli:
                    await self.pool.report_failure(cli)
                return tweets
            except tweepy.TweepyException as e:
                if cli:
                    await self.pool.report_failure(cli)
                logger.error(traceback.format_exc())
                return tweets
        return tweets

    async def _get_tweet_with_retry(self, tweet_id: str) -> Optional[Dict[str, Any]]:
        for attempt in range(3):
            cli, client_key = await self.pool.acquire()
            try:
                resp = cli.get_tweet(
                    tweet_id, tweet_fields=TWEET_FIELDS, expansions=EXPANSIONS, user_fields=USER_FIELDS, user_auth=True
                )
                if not resp.data:
                    return None
                tw: Dict[str, Any] = resp.data.data
                users = self._build_users(resp.includes)
                self._format_tweet_data(tw, users, self._build_medias(resp.includes))
                return tw
            except (NotFound, TwitterServerError):
                return None
            except Exception as e:
                logger.warning("get_tweet retry %s: %s", attempt + 1, e)
                await self.pool.report_failure(cli)
                if attempt == 2:
                    return None
                await asyncio.sleep(2**attempt)
        return None

    # ===================== 原方法签名保持不变 =====================
    def _format_tweet_data(self, tweet: Dict[str, Any], users: Dict[str, User], medias: Dict[str, Media]) -> None:  # type: ignore[no-any-unimported]
        """标准化推文内容"""
        author_id = tweet["author_id"]
        user = users[author_id] if author_id in users else None
        author = str(user.username) if user and "username" in user else author_id
        tweet["author"] = author
        tweet["is_robot"] = (
            "Automated" in user["affiliation"]["description"]
            if user and "affiliation" in user and "description" in user["affiliation"]
            else False
        )
        tweet["mentions_me"] = (
            "entities" in tweet
            and "mentions" in tweet["entities"]
            and self.me_id in list(str(i["id"]) for i in tweet["entities"]["mentions"])
        )
        text = tweet["text"]
        if "display_text_range" in tweet:
            display_text_range: list[int] = tweet["display_text_range"]
            text = text[display_text_range[0] : display_text_range[1]]
        tweet["text"] = f"{author}:\n{text}\n\n"

        if (
            "attachments" in tweet
            and "media_keys" in tweet["attachments"]
            and len(tweet["attachments"]["media_keys"]) > 0
        ):
            key = tweet["attachments"]["media_keys"][0]
            if key in medias and medias[key].type == "photo":
                tweet["image_url"] = medias[key].url

    def _build_users(self, includes: Dict[str, Any]) -> Dict[str, User]:  # type: ignore[no-any-unimported]
        users: Dict[str, User] = {}  # type: ignore[no-any-unimported]
        if "users" in includes:
            for user in includes["users"]:
                users[str(user.id)] = user
        return users

    def _build_medias(self, includes: Dict[str, Any]) -> Dict[str, Media]:  # type: ignore[no-any-unimported]
        medias: Dict[str, Media] = {}  # type: ignore[no-any-unimported]
        if "media" in includes:
            for media in includes["media"]:
                medias[str(media.media_key)] = media
        return medias

    def _get_all_tweets(  # type: ignore[no-any-unimported]
        self, response: TwitterResponse, users: Dict[str, User], medias: Dict[str, Media]
    ) -> list[Dict[str, Any]]:
        all_tweets: list[Dict[str, Any]] = []
        for tweet in response.data:
            t = tweet.data
            self._format_tweet_data(t, users, medias)
            all_tweets.append(t)
        return all_tweets
