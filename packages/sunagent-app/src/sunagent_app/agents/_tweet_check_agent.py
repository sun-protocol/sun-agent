import json
import logging
import re
from typing import (
    Dict,
    List,
    Optional,
    Sequence,
)

from autogen_agentchat.agents import BaseChatAgent
from autogen_agentchat.base import Response
from autogen_agentchat.messages import ChatMessage, TextMessage
from autogen_core import CancellationToken
from autogen_core.models import (
    ChatCompletionClient,
    SystemMessage,
    UserMessage,
)

from .._constants import LOGGER_NAME
from ._markdown_utils import extract_markdown_json_blocks, extract_tweets_from_markdown_json_blocks

logger = logging.getLogger(LOGGER_NAME)


class TweetCheckAgent(BaseChatAgent):
    """An agent that evaluate a tweet and output the result whether the tweet is content safe."""

    def __init__(
        self,
        name: str,
        *,
        description: str = """
        An agent that evaluate a tweet and output the result whether the tweet is content safe.
        """,
        model_client: ChatCompletionClient,
        system_message: str = """
        Your task is to evaluate the reply for content safety.
        Make sure the tweet comply with the Twitter Community Guidelines.
        """,
        block_patterns: Optional[Dict[str, List[str]]] = None,
        skip_task_description: bool = False,
    ) -> None:
        super().__init__(name=name, description=description)
        self._model_client = model_client
        self._system_message = SystemMessage(content=system_message)
        self._block_patterns = block_patterns if block_patterns else {}
        self.skip_task_description = skip_task_description

    @property
    def produced_message_types(self) -> Sequence[type[ChatMessage]]:
        """The types of messages that the code executor agent produces."""
        return (TextMessage,)

    async def on_messages(self, messages: Sequence[ChatMessage], cancellation_token: CancellationToken) -> Response:
        message = messages[-1]
        assert isinstance(message, TextMessage)
        safe, reason = self._validate(message.content)
        if not safe and self.skip_task_description and len(messages) > 1:
            message = messages[-2]
            assert isinstance(message, TextMessage)
            safe, reason = self._validate(message.content)
        if not safe:
            return Response(
                chat_message=TextMessage(
                    content=f"""
Evaluate result:
```json
{{
    "safe": false,
    "reason": "{reason}"
}}
```
""",
                    source=self.name,
                )
            )
        result = await self._evaluate_tweet(message, cancellation_token)
        return Response(chat_message=TextMessage(content=result, source=self.name))

    async def on_reset(self, cancellation_token: CancellationToken) -> None:
        """Reset the assistant agent to its initialization state."""
        pass

    def _validate(self, content: str) -> (bool, str):
        tweets = extract_markdown_json_blocks(content)
        if len(tweets) != 1:
            return False, "content not found"
        for reason, patterns in self._block_patterns.items():
            for pattern in patterns:
                if re.search(pattern, str(tweets[0]), re.IGNORECASE):
                    return False, reason
        return True, ""

    async def _evaluate_tweet(self, message: TextMessage, cancellation_token: CancellationToken) -> str:
        prompt = f"check this tweet: {message.content}"
        try:
            result = await self._model_client.create(
                [self._system_message, UserMessage(content=prompt, source=message.source)],
                cancellation_token=cancellation_token,
            )
            assert isinstance(result.content, str)
            blocks = extract_markdown_json_blocks(result.content)
            for block in blocks:
                if not isinstance(block, Dict) or "score" not in block:
                    continue
                block["safe"] = block["score"] < 0.7
                return f"Evaluate result:\n```json\n{json.dumps(block, ensure_ascii=False)}\n```"
        except Exception as e:
            logger.error(f"error evaluate tweet, {e}")
        reject_message = "Evaluate result:\n```json\n{ 'score': false, 'reason': 'evaluate error' }\n```"
        return reject_message
