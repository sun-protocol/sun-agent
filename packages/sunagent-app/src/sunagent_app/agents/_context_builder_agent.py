import asyncio
import json
import logging
import os
import random
import re
import time
import traceback
from collections import deque
from datetime import datetime, timedelta
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
)

import pytz
import requests
from requests_oauthlib import OAuth1
from autogen_core import CacheStore
from tweepy import Client as TwitterClient
from tweepy import (
    Media,
    NotFound,
    Response,
    StreamResponse,
    StreamRule,
    TweepyException,
    TwitterServerError,
    User,
)
from tweepy import Response as TwitterResponse
from tweepy.asynchronous import AsyncStreamingClient

from .._constants import LOGGER_NAME

logger = logging.getLogger(LOGGER_NAME)

PROCESS_KEY_PREFIX = "P:"
TWEET_KEY_PREFIX = "T:"
CONVERSATION_KEY_PREFIX = "C:"
FREQ_KEY_PREFIX = "F:"
HOME_TIMELINE_ID = "last_home_timeline"
MENTIONS_TIMELINE_ID = "last_mentions_timeline"
MAX_RESULTS = 20
# fetch tweet data fields
TWEET_FIELDS = [
    "article",
    "attachments",
    "author_id",
    "card_uri",
    "community_id",
    "context_annotations",
    "conversation_id",
    "created_at",
    "display_text_range",
    "edit_controls",
    "edit_history_tweet_ids",
    "entities",
    "geo",
    "id",
    "in_reply_to_user_id",
    "lang",
    "media_metadata",
    "note_tweet",
    "possibly_sensitive",
    "public_metrics",
    "referenced_tweets",
    "reply_settings",
    "scopes",
    "source",
    "text",
    "withheld",
]
EXPANSIONS = [
    "article.cover_media",
    "article.media_entities",
    "attachments.media_keys",
    "attachments.media_source_tweet",
    "attachments.poll_ids",
    "author_id",
    "edit_history_tweet_ids",
    "entities.mentions.username",
    "geo.place_id",
    "in_reply_to_user_id",
    "entities.note.mentions.username",
    "referenced_tweets.id",
    "referenced_tweets.id.attachments.media_keys",
    "referenced_tweets.id.author_id",
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
USER_FIELDS = [
    "affiliation",
    "connection_status",
    "created_at",
    "description",
    "entities",
    "id",
    "is_identity_verified",
    "location",
    "most_recent_tweet_id",
    "name",
    "parody",
    "pinned_tweet_id",
    "profile_banner_url",
    "profile_image_url",
    "protected",
    "public_metrics",
    "receives_your_dm",
    "subscription",
    "subscription_type",
    "url",
    "username",
    "verified",
    "verified_followers_count",
    "verified_type",
    "withheld",
]
PLACE_FIELDS = ["contained_within", "country", "country_code", "full_name", "geo", "id", "name", "place_type"]


class RateLimit:
    """
    RateLimit not persistent
    """

    def __init__(self, limit: int, window: int):
        self.limit = limit
        self.window = window
        self.timestamps = deque()

    def acquire_quota(self) -> bool:
        current_time = int(time.time())
        self._release_quota(current_time)
        if len(self.timestamps) >= self.limit:
            return False
        self.timestamps.append(current_time)
        return True

    def remain_quota(self) -> int:
        current_time = int(time.time())
        self._release_quota(current_time)
        return self.limit - len(self.timestamps)

    def recover_time(self) -> int:
        current_time = int(time.time())
        self._release_quota(current_time)
        return self.timestamps[0] + self.window if len(self.timestamps) >= self.limit else 0

    def _release_quota(self, current_time: int):
        cutoff = current_time - self.window
        while len(self.timestamps) > 0 and self.timestamps[0] < cutoff:
            self.timestamps.popleft()


class DailyRateLimit(RateLimit):
    def __init__(self, limit: int, utc_timezone: int):
        super().__init__(limit, 86400)
        timezone = pytz.FixedOffset(utc_timezone * 60)
        now = datetime.now(timezone)
        tomorrow = datetime(now.year, now.month, now.day, 0, 0, 0, tzinfo=timezone) + timedelta(days=1)
        self.fresh_time = int(tomorrow.timestamp())
        self.cnt = 0

    def acquire_quota(self) -> bool:
        current_time = int(time.time())
        self._release_quota(current_time)
        if self.cnt >= self.limit:
            return False
        self.cnt += 1
        return True

    def rollback(self) -> None:
        self.cnt -= 1

    def remain_quota(self) -> int:
        current_time = int(time.time())
        self._release_quota(current_time)
        return self.limit - self.cnt

    def recover_time(self) -> int:
        current_time = int(time.time())
        self._release_quota(current_time)
        return self.fresh_time if len(self.timestamps) >= self.limit else 0

    def _release_quota(self, current_time: int):
        if current_time < self.fresh_time:
            return
        self.fresh_time += self.window
        self.cnt = 0


class TimeoutSession(requests.Session):
    def __init__(self, timeout=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.timeout = timeout  # Default timeout for all requests

    def request(self, method, url, **kwargs):
        # Use the session's timeout if not specified in the request
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = self.timeout
        return super().request(method, url, **kwargs)


class MentionStream(AsyncStreamingClient):
    def __init__(self, on_response: Callable[[StreamResponse, str], None], **kwargs):
        super().__init__(**kwargs)
        self._on_response = on_response

    async def on_response(self, response: StreamResponse) -> None:
        cache_key = f"{self.agent_id}:{MENTIONS_TIMELINE_ID}"
        await self._on_response(response, cache_key)


class ContextBuilderAgent:
    """
    负责构建推文完整上下文的Agent，功能包括：
    1. 递归获取对话链
    2. 缓存管理
    3. 处理API限制和错误重试
    """

    def __init__(
        self,
        agent_id: str,
        twitter_client: TwitterClient,
        oauth: OAuth1,
        cache: Optional[CacheStore] = None,
        max_depth: int = 5,
        timeout: int = 30,
    ) -> None:
        self.agent_id = agent_id
        self.twitter = twitter_client
        self.twitter.session = TimeoutSession(timeout=timeout)
        self.user_auth = self.twitter.access_token_secret is not None
        self.oauth = oauth
        self.cache = cache
        self.max_depth = max_depth
        self.retry_limit = 2
        self.me: Optional[User] = None
        self.quota: Dict[str, RateLimit] = {
            "HOME_TIMELINE": RateLimit(5, 900),
            "MENTIONS_TIMELINE": RateLimit(10, 900),
            "GET_TWEET": RateLimit(15, 900),
            "POST_TWEET": RateLimit(100, 86400),
            "SAMPLING_QUOTE_TWEET": DailyRateLimit(2, 8),
        }
        self.recover_time: Optional[int] = None
        self.block_user_ids = json.loads(os.getenv("BLOCK_USER_IDS", "[]"))
        logger.error(f"block_user_ids: {self.block_user_ids}")
        self.white_user_ids = json.loads(os.getenv("MENTIONS_WHITE_USER_ID", "[]"))
        self.reply_freq_limit = int(os.getenv("MAX_REPLY_COUNT", "5"))

    def _remove_recover_time(self, description: str) -> (str, int):
        pattern = re.compile(r"(\s*Status: Off\s*Recovery time: \d+-\d+-\d+ \d+:\d+ UTC\+8\s*)")
        matches = pattern.findall(description)
        old_time: int = 0
        timezone = pytz.FixedOffset(480)
        for match in matches:
            description = description.replace(match, "")
            t = match.split()
            recover_time = timezone.localize(datetime.strptime(" ".join([t[-3], t[-2]]), "%Y-%m-%d %H:%M"))
            old_time = max(old_time, int(recover_time.timestamp()))
        return description, old_time

    async def unset_recover_time(self) -> (int, str):
        if self.recover_time is None:
            return 0, str(self.recover_time)
        if self.recover_time > int(time.time()):
            logger.info(f"service not available until {self.recover_time}")
            return 403, str(self.recover_time)
        self.recover_time = None
        return 0, str(self.recover_time)

    async def set_recover_time(self, recover_time: int) -> (int, str):
        if self.recover_time == recover_time:
            return 0, str(self.recover_time)
        elif recover_time <= int(time.time()):
            return 403, "recover time is already past"
        elif recover_time > self.recover_time:
            self.recover_time = recover_time
        return 0, str(self.recover_time)

    async def create_tweet(self, kwargs: Dict[str, Any]) -> (int, str):
        if not self.quota["POST_TWEET"].acquire_quota():
            recover_time = self.quota["POST_TWEET"].recover_time()
            logger.error(f"POST_TWEET has no quota, recover_time={recover_time}")
            await self.set_recover_time(recover_time)
            return 500, "POST_TWEET has no quota"
        await self.unset_recover_time()
        kwargs["user_auth"] = self.user_auth
        try:
            response = self.twitter.create_tweet(**kwargs)
            if "in_reply_to_tweet_id" in kwargs:
                self._mark_tweet_process(str(kwargs["in_reply_to_tweet_id"]))
            logger.info(f"create_tweet succeed. {response.data}")
            return 0, str(response.data["id"])
        except TweepyException as e:
            # we don't know whether fail posts costs twitter quota or not
            logger.error(f"create_tweet failed. {str(e)}")
            try:
                status_code = e.response.status_code
            except AttributeError:
                # response is an instance of aiohttp.ClientResponse
                status_code = e.response.status
            return status_code, e.args[0]
        except Exception as e:
            logger.error(f"create_tweet failed. {str(e)}")
            return 500, "Server Error"

    async def reply_tweet(self, reply_to: str, content: str) -> str:
        """
        a tool to post a new tweet or reply to an exist tweet.

        reply_to: string, the tweet ID of that reply to. This field is required, use empty string is no reply_to is found.
        content: string, the content to post
        """
        args: Dict[str, Any] = {"text": content}
        if len(reply_to) > 0:
            args["in_reply_to_tweet_id"] = int(reply_to)
        code, msg = await self.create_tweet(args)
        if code == 0:
            return f"""
            new tweet posted successfully:
            ```json
            {{
                "id": "{msg}",
                "content": "{content}"
            }}
            ```
            """
        else:
            return "failed to post tweet."

    async def sampling_quote_tweet(self, reply_to: str, sampling_quote: bool, content: str) -> str:
        """
        reply_to: string, the tweet ID of that reply to, empty string for orignal post
        sampling_quote: bool, whether quote this tweet or not
        content: string, the content to post
        """
        if not self.quote_tweet_or_not(reply_to, sampling_quote):
            # Do not quote this tweet, just reply it
            return await self.reply_tweet(reply_to, content)
        args: Dict[str, Any] = {"text": content, "quote_tweet_id": int(reply_to)}
        code, msg = await self.create_tweet(args)
        if code == 0:
            return f"""
            new tweet quoted successfully:
            ```json
            {{
                "id": "{msg}",
                "content": "{content}"
            }}
            ```
            """
        else:
            self.quota["SAMPLING_QUOTE_TWEET"].rollback()
            return "failed to quote tweet."

    async def subscribe(self, mention_stream: MentionStream) -> bool:
        try:
            rule = StreamRule(tag=f"{self.agent_id}:{MENTIONS_TIMELINE_ID}", value=f"@{self.me.username} -is:retweet")
            await mention_stream.add_rules(rule, dry_run=1)
            mention_stream.filter(
                tweet_fields=TWEET_FIELDS,
                expansions=EXPANSIONS,
                media_fields=MEDIA_FIELDS,
                poll_fields=POLL_FIELDS,
                user_fields=USER_FIELDS,
                place_fields=PLACE_FIELDS,
            )
            return True
        except TweepyException as e:
            logger.error(f"subscribe error: {e}")
            return False

    async def get_home_timeline_with_context(self) -> str:
        """
        Get the new tweets with full conversation context in home timeline since last time.
        Params: no parameters required
        Return: json string, which is a list of tweets
        """

        tweets: List[str] = []
        since_id: Optional[str] = None
        cache_key = f"{self.agent_id}:{HOME_TIMELINE_ID}"
        if self.quota["POST_TWEET"].remain_quota() == 0:
            if self.cache:
                self.cache.delete(cache_key)
            return "[]"
        # 未到服务恢复时间
        code, _ = await self.unset_recover_time()
        if code != 0:
            if self.cache:
                self.cache.delete(cache_key)
            return "[]"
        for attempt in range(self.retry_limit):
            try:
                if self.me is None:
                    response = self.twitter.get_me(
                        user_auth=self.user_auth,
                        user_fields=USER_FIELDS,
                    )
                    self.me = response.data
                if not self.quota["HOME_TIMELINE"].acquire_quota():
                    logger.warning(f"HOME_TIMELINE no quota, recover_time={self.quota['HOME_TIMELINE'].recover_time()}")
                    if self.cache:
                        self.cache.delete(cache_key)
                    break
                since_id = self.cache.get(cache_key) if self.cache else None
                response = self.twitter.get_home_timeline(
                    tweet_fields=TWEET_FIELDS,
                    expansions=EXPANSIONS,
                    media_fields=MEDIA_FIELDS,
                    poll_fields=POLL_FIELDS,
                    user_fields=USER_FIELDS,
                    place_fields=PLACE_FIELDS,
                    exclude=["replies", "retweets"],
                    since_id=int(since_id) if since_id else None,
                    max_results=MAX_RESULTS,
                    user_auth=self.user_auth,
                )
                tweet_list, next_token = await self.on_twitter_response(
                    response,
                    cache_key=cache_key,
                )
                if len(tweet_list) == 0:
                    break
                tweets.extend(tweet_list)
                return json.dumps(tweets, ensure_ascii=False, default=str)
            except Exception as e:
                logger.error(traceback.format_exc())
                logger.error(f"error get_home_timeline_with_context(attempt {attempt+1}): {str(e)}")
                if not isinstance(e, TwitterServerError):
                    break
            await asyncio.sleep(2**attempt)  # 指数退避
        return "[]"

    async def get_mentions_with_context(self) -> str:
        def filter_tweet(tweet: Dict[str, Any]) -> bool:
            return tweet["mentions_me"]

        """
        Get the new tweets with full conversation context that mentions me since last time.
        Params: no parameters required
        Return: json string, which is a list of tweets
        """
        tweets: List[Dict[str, Any]] = []
        since_id: Optional[str] = None
        next_token: Optional[str] = None
        cache_key = f"{self.agent_id}:{MENTIONS_TIMELINE_ID}"
        if self.quota["POST_TWEET"].remain_quota() < 10:
            if self.cache:
                self.cache.delete(cache_key)
            return "[]"
        # 未到服务恢复时间
        code, _ = await self.unset_recover_time()
        if code != 0:
            if self.cache:
                self.cache.delete(cache_key)
            return "[]"
        while True:
            for attempt in range(self.retry_limit):
                try:
                    if self.me is None:
                        response = self.twitter.get_me(
                            user_auth=self.user_auth,
                            user_fields=USER_FIELDS,
                        )
                        self.me = response.data
                    if not self.quota["MENTIONS_TIMELINE"].acquire_quota():
                        logger.warning(
                            f"MENTIONS_TIMELINE has no quota, recover_time={self.quota['MENTIONS_TIMELINE'].recover_time()}"
                        )
                        if self.cache:
                            self.cache.delete(cache_key)
                        next_token = None
                        break
                    since_id = self.cache.get(cache_key) if self.cache else None
                    response = self.twitter.get_users_mentions(
                        id=self.me.id,
                        tweet_fields=TWEET_FIELDS,
                        expansions=EXPANSIONS,
                        media_fields=MEDIA_FIELDS,
                        poll_fields=POLL_FIELDS,
                        user_fields=USER_FIELDS,
                        place_fields=PLACE_FIELDS,
                        since_id=int(since_id) if since_id else None,
                        max_results=MAX_RESULTS,
                        pagination_token=next_token,
                        user_auth=self.user_auth,
                    )
                    tweet_list, next_token = await self.on_twitter_response(
                        response, cache_key=cache_key, filter_func=filter_tweet
                    )
                    if len(tweet_list) == 0:
                        break
                    tweets.extend(tweet_list)
                    break
                except Exception as e:
                    logger.error(traceback.format_exc())
                    logger.error(f"error get_mentions_with_context(attempt {attempt+1}): {str(e)}")
                    if not isinstance(e, TwitterServerError):
                        next_token = None
                        break
                await asyncio.sleep(2**attempt)  # 指数退避
            if not next_token:
                # no mentions left
                break
        return json.dumps(tweets, ensure_ascii=False, default=str)

    async def on_twitter_response(
        self,
        response: Response | StreamResponse,
        cache_key: str,
        filter_func: Callable[[Dict[str, Any]], bool] = (lambda x: True),
    ) -> (List[Dict[str, Any]], Optional[int]):
        tweets: List[Dict[str, Any]] = []
        next_token = response.meta["next_token"] if "next_token" in response.meta else None
        if response.meta["result_count"] == 0 or response.data is None:
            assert next_token is None
            return tweets, next_token
        users: Dict[str, User] = self._build_users(response.includes)
        medias: Dict[str, Media] = self._build_medias(response.includes)
        all_tweets = self._get_all_tweets(response, users, medias)
        await self._cache_tweets(all_tweets)
        has_processed = False
        for tweet in all_tweets:
            is_processed = await self._check_tweet_process(tweet["id"])
            has_processed = has_processed or is_processed
            if (
                tweet["author_id"] == self.me.data["id"]
                or self.block_user_ids.count(int(tweet["author_id"])) != 0
                or is_processed
                or not filter_func(tweet)
                or (await self._get_freq(tweet) >= self.reply_freq_limit and self.white_user_ids.count(int(tweet["author_id"])) == 0)
            ):
                continue
            await self._increase_freq(tweet)
            await self._mark_tweet_process(tweet["id"])
            tweet = await self._normalize_tweet(tweet)
            tweets.append(tweet)
        if self.cache and not has_processed and response.meta["result_count"] == MAX_RESULTS:
            self.cache.delete(cache_key)
            next_token = None
        elif self.cache and "newest_id" in response.meta:
            self.cache.set(cache_key, str(response.meta["newest_id"]))
        return tweets, next_token

    async def build_context(self, tweet: Dict[str, Any]) -> str:
        """
        build the full conversation of given tweet
        :param tweet_id: last tweet ID
        :return: full conversation in plain text
        """
        # 获取缓存
        if "conversation_id" not in tweet:
            tweet["conversation_id"] = tweet["id"]
        conversation: List[Dict[str, Any]] = []
        await self._recursive_fetch(tweet, conversation, 0)
        text = "<conversation>\n"
        for tweet in conversation:
            text += f"<tweet>{tweet['text']}</tweet>\n"
        text += "</conversation>\n"
        return text

    async def build_mention_context(self, tweet: Dict[str, Any]) -> tuple[List[str], str]:
        """
        build the full conversation of given tweet
        :param tweet_id: last tweet ID
        :return: full conversation in plain text
        """
        # 获取缓存
        his = []
        if "conversation_id" not in tweet:
            tweet["conversation_id"] = tweet["id"]
        conversation: List[Dict[str, Any]] = []
        await self._recursive_fetch(tweet, conversation, 0)
        current_tweet = conversation[-1]['text']
        if len(conversation) > 1:
            for tweet in conversation[0:-1]:
                his.append(tweet['text'])
        return his,current_tweet

    async def _recursive_fetch(self, tweet: Dict[str, Any], conversation: List[Dict[str, Any]], depth: int) -> bool:
        """
        递归获取父级推文的核心逻辑
        """
        if depth > self.max_depth:
            logger.warning(f"max_depth {self.max_depth} exceeded")
            conversation.append(tweet)
            return True

        parent_id = (
            tweet["referenced_tweets"][0]["id"]
            if ("referenced_tweets" in tweet and len(tweet["referenced_tweets"]) > 0)
            else None
        )
        if parent_id is None:
            conversation.append(tweet)
            return True

        parent = await self._get_cached_tweet(parent_id)
        if not parent:
            parent = await self._fetch_tweet_with_retry(parent_id)
            if parent:
                await self._cache_tweets([parent])
        if not parent:
            conversation.append(tweet)
            return False
        is_success = await self._recursive_fetch(parent, conversation, depth + 1)
        conversation.append(tweet)
        return is_success

    async def _fetch_tweet_with_retry(self, tweet_id: str) -> Optional[Dict[str, Any]]:
        """带重试机制的API调用"""
        for attempt in range(self.retry_limit):
            try:
                response = self.twitter.get_tweet(
                    tweet_id,
                    tweet_fields=TWEET_FIELDS,
                    expansions=EXPANSIONS,
                    media_fields=MEDIA_FIELDS,
                    poll_fields=POLL_FIELDS,
                    user_fields=USER_FIELDS,
                    place_fields=PLACE_FIELDS,
                    user_auth=self.user_auth,
                )
                if not response.data:
                    logger.error(f"error get_tweet {str(response)}")
                    return None
                tweet: Dict[str, Any] = response.data.data
                users: Dict[str, User] = self._build_users(response.includes)
                medias: Dict[str, Media] = self._build_medias(response.includes)
                self._format_tweet_data(tweet, users, medias)
                return tweet
            except Exception as e:
                if isinstance(e, NotFound):
                    logger.warning(f"tweet not exists: {tweet_id}")
                    return None
                logger.error(f"error get_tweet(attempt {attempt+1}): {str(e)}")
                if not isinstance(e, TwitterServerError):
                    return None
                await asyncio.sleep(2**attempt)  # 指数退避
        return None

    async def _normalize_tweet(self, tweet: Dict[str, Any]) -> Dict[str, Any]:
        tweet["text"] = await self.build_context(tweet)
        history,last = await self.build_mention_context(tweet)
        tweet["histroy"] = history
        tweet["last_mention"] = last
        FILTER_FIELDS = [
            "id",
            "text",
            "author",
            "image_url",
            "public_metrics",
        ]
        simplified_tweet: Dict[str, Any] = {}
        for key, value in tweet.items():
            if key in FILTER_FIELDS:
                simplified_tweet[key] = value
        # generate necessary fields
        simplified_tweet["sampling_quote"] = "referenced_tweets" not in tweet or len(tweet["referenced_tweets"]) == 0
        return simplified_tweet

    async def reply_to_tweet_with_image(self, tweet_id, text, image_bytes):
        """
        Main function to reply to a tweet with an image

        Args:
            tweet_id: Target tweet ID to reply to
            text: Reply text content
            image_bytes: Image bytes

        Returns:
            dict: Tweet response data if successful, None otherwise
        """
        try:
            # Upload the image
            media_id = self.image_upload_with_v2(image_bytes)

            # Post the reply with media
            response = self.twitter.create_tweet(text=text, media_ids=[media_id], in_reply_to_tweet_id=tweet_id)
            data = response.data
            logger.debug(f"Twitter api tweet_creation result：{response}")

            if data and data["id"]:
                logger.info(f"Successfully posted tweet reply! Tweet ID: {data['id']}")
            return data
        except Exception as e:
            logger.error(f"Tweet with image post failed: {e}")
            raise e

    async def create_tweet_with_image(self, text, image_bytes):
        """
        Main function to reply to a tweet with an image

        Args:
            tweet_id: Target tweet ID to reply to
            text: Reply text content
            image_bytes: Image bytes

        Returns:
            dict: Tweet response data if successful, None otherwise
        """
        try:
            # Upload the image
            media_id = self.image_upload_with_v2(image_bytes)

            # Post the reply with media
            response = self.twitter.create_tweet(text=text, media_ids=[media_id])
            data = response.data
            logger.debug(f"Twitter api tweet_creation result：{response}")

            if data and data["id"]:
                logger.info(f"Successfully posted tweet reply! Tweet ID: {data['id']}")
            return data
        except Exception as e:
            logger.error(f"Tweet with image post failed: {e}")
            raise e

    def image_upload_with_v2(self, image_bytes) -> int:
        """
        Upload image using Twitter API V2 (avoid blocking)

        Args:
            image_bytes: Image bytes

        Returns:
            dict: Tweet response data if successful, None otherwise
        """
        try:
            # Step1: Initialize Media Upload
            initialize_url = "https://api.twitter.com/2/media/upload/initialize"

            # Create a multipart form with the image file using the correct content type
            payload = {
                "media_category": "tweet_image",
                "media_type": "image/png",
                "shared": False,
                "total_bytes": len(image_bytes),
            }

            initialize_response = requests.post(initialize_url, auth=self.oauth, json=payload)

            if initialize_response.status_code == 200:
                media_data = initialize_response.json()["data"]
                if "id" in media_data:
                    media_id = media_data["id"]
                else:
                    raise ValueError(f"unexpected response format from Twitter media upload: {media_data}")
            else:
                raise ValueError(
                    f"failed to initialize image upload. Status code: {initialize_response.status_code}, Response: {initialize_response.text}"
                )

            # Step2: Append media chunk of bytes using the APPEND command
            upload_url = f"https://api.twitter.com/2/media/upload/{media_id}/append"

            request_data = {
                "segment_index": 0
            }

            files = {
                "media": image_bytes,
            }

            append_response = requests.post(url=upload_url, data=request_data, files=files, auth=self.oauth)

            if append_response.status_code != 200:
                raise ValueError(
                    f"failed to append image to Twitter. Status code: {append_response.status_code}, Response: {append_response.text}"
                )

            # Step3: Finalize media upload
            finalize_url = f"https://api.twitter.com/2/media/upload/{media_id}/finalize"
            finalize_response = requests.post(finalize_url, auth=self.oauth)
            data = finalize_response.json()["data"]
            return int(data["id"])
        except Exception as e:
            logger.error(f"An unexpected error occurred: {e}")
            raise e

    def _format_tweet_data(self, tweet: Dict[str, Any], users: Dict[str, User], medias: Dict[str, Media]) -> None:
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

        tweet["mentions_me"] = False
        if "entities" in tweet and "mentions" in tweet["entities"]:
            for mention in tweet["entities"]["mentions"]:
                if mention["id"] == self.me.data["id"]:
                    tweet["mentions_me"] = True
                    break

        text = tweet["text"]
        if "display_text_range" in tweet:
            display_text_range: List[int] = tweet["display_text_range"]
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

    def _build_users(self, includes: Dict[str, Any]) -> Dict[str, User]:
        users: Dict[str, User] = {}
        if "users" in includes:
            for user in includes["users"]:
                users[str(user.id)] = user
        return users

    def _build_medias(self, includes: Dict[str, Any]) -> Dict[str, Media]:
        medias: Dict[str, Media] = {}
        if "media" in includes:
            for media in includes["media"]:
                medias[str(media.media_key)] = media
        return medias

    def _get_all_tweets(
        self, response: TwitterResponse, users: Dict[str, User], medias: Dict[str, Media]
    ) -> List[Dict[str, Any]]:
        all_tweets: List[Dict[str, Any]] = []
        for tweet in response.data:
            t = tweet.data
            self._format_tweet_data(t, users, medias)
            all_tweets.append(t)
        return all_tweets

    async def _check_tweet_process(self, tweet_id: str) -> bool:
        if self.cache is None:
            return False
        try:
            return self.cache.get(f"{self.agent_id}:{PROCESS_KEY_PREFIX}{tweet_id}") is not None
        except Exception:
            # regard it as processed if cache not available
            return True

    async def _mark_tweet_process(self, tweet_id: str) -> None:
        if self.cache is None:
            return
        try:
            self.cache.set(f"{self.agent_id}:{PROCESS_KEY_PREFIX}{tweet_id}", "")
        except Exception:
            pass

    async def _get_robot_freq(self, tweet: Dict[str, Any]) -> int:
        if self.cache is None or not tweet["is_robot"]:
            return -1
        try:
            freq = self.cache.get(f"{self.agent_id}:{FREQ_KEY_PREFIX}{tweet['conversation_id']}:{tweet['author_id']}")
            return int(freq) if freq else 0
        except Exception:
            return 0

    async def _get_freq(self, tweet: Dict[str, Any]) -> int:
        if self.cache is None:
            return -1
        try:
            freq = self.cache.get(f"{self.agent_id}:{FREQ_KEY_PREFIX}{tweet['conversation_id']}")
            return int(freq) if freq else 0
        except Exception:
            return 0

    async def _increase_robot_freq(self, tweet: Dict[str, Any]) -> None:
        if self.cache is None or not tweet["is_robot"]:
            return
        freq = await self._get_robot_freq(tweet)
        try:
            self.cache.set(
                f"{self.agent_id}:{FREQ_KEY_PREFIX}{tweet['conversation_id']}:{tweet['author_id']}", str(freq + 1)
            )
        except Exception:
            pass

    async def _increase_freq(self, tweet: Dict[str, Any]) -> None:
        if self.cache is None:
            return
        freq = await self._get_robot_freq(tweet)
        try:
            self.cache.set(
                f"{self.agent_id}:{FREQ_KEY_PREFIX}{tweet['conversation_id']}", str(freq + 1)
            )
        except Exception:
            pass

    async def _get_cached_conversation(self, conversation_id: str) -> List[Dict[str, Any]]:
        if self.cache:
            try:
                conversation = self.cache.get(f"{self.agent_id}:{CONVERSATION_KEY_PREFIX}{conversation_id}", "[]")
                return json.loads(conversation)
            except Exception as e:
                logger.error(f"error _get_cached_conversation: {e}")
        return []

    async def _cache_conversation(self, conversation: List[Dict[str, Any]]) -> None:
        if len(conversation) == 0 or self.cache is None:
            return
        try:
            conversation_id = str(conversation[0]["conversation_id"])
            self.cache.set(
                f"{self.agent_id}:{CONVERSATION_KEY_PREFIX}{conversation_id}",
                json.dumps(conversation, ensure_ascii=False, default=str),
            )
        except Exception as e:
            logger.error(f"error _cache_conversation: {e}")

    async def _get_cached_tweet(self, tweet_id: str) -> Optional[Dict[str, Any]]:
        if self.cache:
            try:
                tweet = self.cache.get(f"{self.agent_id}:{TWEET_KEY_PREFIX}{tweet_id}")
                if tweet:
                    return json.loads(tweet)
            except Exception as e:
                logger.error(f"error _get_cached_tweet: {e}")
        return None

    async def _cache_tweets(self, tweets: List[Dict[str, Any]]) -> None:
        if len(tweets) == 0 or self.cache is None:
            return
        for tweet in tweets:
            try:
                tweet_id = tweet["id"]
                self.cache.set(
                    f"{self.agent_id}:{TWEET_KEY_PREFIX}{tweet_id}", json.dumps(tweet, ensure_ascii=False, default=str)
                )
            except Exception as e:
                logger.error(f"error _cache_tweet[{tweet_id}]: {e}")

    def _check_quote_time(self) -> bool:
        timezone = pytz.FixedOffset(480)
        now = datetime.now(timezone)
        return now.hour >= 10 and now.hour < 23

    def _quote_sampling(self) -> bool:
        # 20点以后quota还没用完，加速消耗
        timezone = pytz.FixedOffset(480)
        now = datetime.now(timezone)
        if now.hour >= 20:
            return True
        return random.random() >= 0.5

    def quote_tweet_or_not(self, reply_to: str, sampling_quote: bool) -> bool:
        """
        determine whether to quote given tweet or not
        Params:
            reply_to: string, the tweet ID of that reply to, empty string for orignal post
            sampling_quote: bool, whether to given tweet can be quoted
        Return:
            bool: whether to quote given tweet
        """
        return (
            sampling_quote
            and len(reply_to) != 0
            and self._check_quote_time()
            and self._quote_sampling()
            and self.quota["SAMPLING_QUOTE_TWEET"].acquire_quota()
        )
