import json
import logging
import math
from datetime import datetime
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Sequence,
    cast,
)

from autogen_agentchat.agents import BaseChatAgent
from autogen_agentchat.base import Response
from autogen_agentchat.messages import (
    ChatMessage,
    TextMessage,
    ToolCallSummaryMessage,
)
from autogen_core import CancellationToken
from autogen_core.models import (
    ChatCompletionClient,
    SystemMessage,
    UserMessage,
)

from .._constants import LOGGER_NAME
from ._markdown_utils import extract_scores_from_markdown_json_blocks, extract_tweets_from_markdown_json_blocks

logger = logging.getLogger(LOGGER_NAME)


class TweetAnalysisAgent(BaseChatAgent):
    """An agent that extracts tweets found in received messages.
    It evaluate the quality of each tweet, and output the tweet with the best quality.
    """

    def __init__(
        self,
        name: str,
        *,
        description: str = """
        An agent that extracts tweets found in received messages.
        It evaluate the quality of each tweet, and output the tweet ID with the best quality.
        """,
        model_client: Optional[ChatCompletionClient] = None,
        system_message: str = """
        Your task is to evaluate the quality of the following tweet
        and return a float score between 0.0 to 1.0.
        """,
        evaluate_func: Optional[
            Callable[[List[Dict[str, Any]], Optional[ChatCompletionClient], Optional[CancellationToken]], List[float]]
        ] = None,
        batch_size: int = 50,
    ) -> None:
        super().__init__(name=name, description=description)
        self._model_client = model_client
        self._system_message = SystemMessage(content=system_message)
        self._evaluate_func = evaluate_func
        self._batch_size = batch_size

    @property
    def produced_message_types(self) -> Sequence[type[ChatMessage]]:
        """The types of messages that this agent produces."""
        return (TextMessage,)

    async def on_messages(self, messages: Sequence[ChatMessage], cancellation_token: CancellationToken) -> Response:
        tweets: List[Dict[str, Any]] = []
        for msg in messages:
            if isinstance(msg, TextMessage) or isinstance(msg, ToolCallSummaryMessage):
                tweets.extend(extract_tweets_from_markdown_json_blocks(msg.content))
        if len(tweets) == 0:
            logger.info("Unable to extract tweets from the JSON string, or there are no tweets present")
            return Response(chat_message=TextMessage(content="EARLY_TERMINATE", source=self.name))
        scores: List[float] = []
        for i in range(0, len(tweets), self._batch_size):
            batch = tweets[i : i + self._batch_size]
            if self._evaluate_func is not None:
                scores.extend(self._evaluate_func(batch, self._model_client, cancellation_token))
            else:
                scores.extend(await self._evaluate_tweet(batch, cancellation_token))
        if len(tweets) != len(scores):
            logger.error("Failed in evaluating the tweets")
            return Response(chat_message=TextMessage(content="EARLY_TERMINATE", source=self.name))
        best: int = scores.index(max(scores))
        if scores[best] - 0 < 0.01:
            logger.info("Scores of the tweets (judged by their popularity and semantics) are too low to pass the check")
            return Response(chat_message=TextMessage(content="EARLY_TERMINATE", source=self.name))
        return Response(
            chat_message=TextMessage(
                content=f"Chosen tweet:\n```json\n{json.dumps(tweets[best], ensure_ascii=False)}\n```\n",
                source=self.name,
            )
        )

    async def on_reset(self, cancellation_token: CancellationToken) -> None:
        """Reset the assistant agent to its initialization state."""
        pass

    async def _evaluate_tweet(self, tweets: List[Dict[str, Any]], cancellation_token: CancellationToken) -> List[float]:
        SCORE_WEIGHTS = {"sementic": 0.7, "timeliness": 0.0, "popularity": 0.3}
        scores: List[float] = []
        for tweet in tweets:
            scores.append(self._get_popularity(tweet))
        sementic_scores: List[float] = []
        if self._model_client is not None:
            sementic_scores = await self._calc_sementic_score(tweets, cancellation_token)
        else:
            sementic_scores = [0] * len(tweets)
        for i, tweet in enumerate(tweets):
            sementic_score = sementic_scores[i]
            popularity = scores[i]
            if sementic_score >= 0.3:
                scores[i] = sementic_score * SCORE_WEIGHTS["sementic"] + popularity * SCORE_WEIGHTS["popularity"]
            else:
                scores[i] = 0.0
            logger.debug(f"""
tweet: {tweet}
evaluate result: sementic: {sementic_score} popularity: {popularity} total_score: {scores[i]}
""")
        return scores

    async def _calc_sementic_score(
        self, tweets: List[Dict[str, Any]], cancellation_token: CancellationToken
    ) -> List[float]:
        assert self._model_client is not None
        prompt = f"evalute thsese tweet: {json.dumps([{'id': tweet['id'], 'content': tweet['text']} for tweet in tweets], ensure_ascii=False)}"
        try:
            result = await self._model_client.create(
                [self._system_message, UserMessage(content=prompt, source="user")],
                cancellation_token=cancellation_token,
            )
            assert isinstance(result.content, str)
            scores = extract_scores_from_markdown_json_blocks(result.content)
            if len(scores) != len(tweets):
                logger.error(f"expect {len(tweets)} but got {len(scores)}, {result}")
            if len(scores) >= len(tweets):
                return scores[: len(tweets)]
        except Exception as e:
            logger.error(f"error calculate sementic score, {e}")
        return [0] * len(tweets)

    def _get_popularity(self, tweet: Dict[str, Any]) -> float:
        if "public_metrics" not in tweet:
            return 0

        log_popularity: float = math.log1p(
            tweet["public_metrics"]["like_count"] + tweet["public_metrics"]["retweet_count"] * 2
        )
        tweet.pop("public_metrics")
        return min(1.0, log_popularity / 5)

    def _calc_timeliness(self, tweet: Dict[str, Any]) -> float:
        if "create_at" not in tweet:
            return 0
        hours_old: float = (datetime.now() - tweet["created_at"]).total_seconds() / 3600
        return max(0, 1 - hours_old / 72)
