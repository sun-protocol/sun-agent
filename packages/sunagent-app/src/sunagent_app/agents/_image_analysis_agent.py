import json
import logging
from typing import (
    Optional,
    Sequence,
)

from autogen_agentchat.agents import BaseChatAgent
from autogen_agentchat.base import Response
from autogen_agentchat.messages import (
    ChatMessage,
    MultiModalMessage,
    TextMessage,
)
from autogen_core import CancellationToken, Image
from autogen_core.models import (
    UserMessage,
)
from autogen_ext.models.openai import AzureOpenAIChatCompletionClient

from sunagent_app._constants import LOGGER_NAME
from sunagent_app.agents._markdown_utils import extract_json_from_string

logger = logging.getLogger(LOGGER_NAME)


class ImageAnalysisAgent(BaseChatAgent):
    """An agent that analyzes the quality of an image based on the description in the tweet context.
    An image generation agent must be called before this agent.
    """

    def __init__(
        self,
        name: str,
        model_client: AzureOpenAIChatCompletionClient,
        *,
        description: str = """
        An agent that analyzes the quality of an image based on the description in the tweet context.
        An Image Generation agent must be called before this agent.
        """,
        system_message: str = "",
    ) -> None:
        super().__init__(name=name, description=description)
        self.system_message = system_message
        self._model_client = model_client

    @property
    def produced_message_types(self) -> Sequence[type[ChatMessage]]:
        """The types of messages that the this agent produces."""
        return (MultiModalMessage, TextMessage)

    async def on_messages(self, messages: Sequence[ChatMessage], cancellation_token: CancellationToken) -> Response:
        image: Optional[Image] = None
        image_description: Optional[str] = None
        for message in messages:
            if message.source == "ImageAdvisor":
                reply_msg = extract_json_from_string(message.content)
                description = {"last_tweet": reply_msg["last_tweet"], "content": reply_msg["content"]}
                image_description = json.dumps(description, ensure_ascii=False)
            elif message.source == "ImageGenerator":
                image = message.content[0]

        if image is None:
            return Response(chat_message=TextMessage(content="no image provided", source=self.name))
        try:
            image_analysis_result = await self._model_client.create(
                [
                    UserMessage(
                        content=[self.system_message, image_description, image],
                        source="user",
                    ),
                ],
            )
            extracted_json = extract_json_from_string(image_analysis_result.content)
            if extracted_json["score"] > 0.7:
                logger.info(f"Image passed check with score {extracted_json["score"]}, {extracted_json["reason"]}")
                return Response(chat_message=MultiModalMessage(content=[image], source=self.name))
            else:
                return Response(
                    chat_message=TextMessage(
                        content=f"image quality is not good enough TERMINATE, reason: {extracted_json["reason"]}",
                        source=self.name,
                    )
                )
        except Exception as e:
            logger.error(f"error analyzing the given image, {e}")
            return Response(
                chat_message=TextMessage(content=f"error analyzing image: {e}. TERMINATE", source=self.name)
            )

    async def on_reset(self, cancellation_token: CancellationToken) -> None:
        """Reset the assistant agent to its initialization state."""
        pass
