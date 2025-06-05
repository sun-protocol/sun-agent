import logging
import os
import traceback
from io import BytesIO
from typing import (
    Any,
    Dict,
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
    SystemMessage,
)
from google import genai
from google.genai import types
from PIL import Image as PILImage

from .._constants import LOGGER_NAME
from ._http_utils import fetch_url
from ._markdown_utils import extract_markdown_json_blocks, extract_tweets_from_markdown_json_blocks

logger = logging.getLogger(LOGGER_NAME)


class ImageGenerateAgent(BaseChatAgent):
    """An agent that generate an image based on the description in the tweet.
    An information extraction agent must be called before this agent.
    """

    def __init__(
        self,
        name: str,
        *,
        description: str = """
        An agent that extract image attachment of given tweet, or generate an image according to the image description in the tweet.
        Image is base64 encoded.
        An information extraction agent must be called before this agent.
        """,
        system_message: str = """
        Flat illustration, cyberpunk style, bright colors, a sense of technology,
        Game CG style, American cartoon style, 3D rendering,
        Pop art style, bright colors, exaggerated geometric patterns and color blocks,
        Hyperrealistic style, future technology, surrealism, fluorescent color,
        """,
        model_name: str = "imagen-3.0-generate-002",
        width: int = 400,
        height: int = 400,
    ) -> None:
        super().__init__(name=name, description=description)
        self._model_client = genai.Client(api_key=os.getenv("GOOGLE_GEMINI_API_KEY"))
        self._model_name = model_name
        self._system_message = SystemMessage(content=system_message)
        self.width = width
        self.height = height

    @property
    def produced_message_types(self) -> Sequence[type[ChatMessage]]:
        """The types of messages that the this agent produces."""
        return (MultiModalMessage, TextMessage)

    async def on_messages(self, messages: Sequence[ChatMessage], cancellation_token: CancellationToken) -> Response:
        tweet: Optional[Dict[str, Any]] = None
        image_description: Optional[str] = None
        for message in messages:
            if message.source == "user":
                tweets = extract_tweets_from_markdown_json_blocks(message.content)
                tweet = tweets[0] if len(tweets) > 0 else None
            elif isinstance(message, TextMessage):
                blocks = extract_markdown_json_blocks(message.content)
                for block in blocks:
                    if isinstance(block, Dict) and "symbol" in block and "image_description" in block:
                        image_description = block["image_description"]
        assert tweet is not None
        image: Optional[PILImage] = None
        if "image_url" in tweet:
            raw_image = await fetch_url(tweet["image_url"])
            if raw_image is not None:
                image = PILImage.open(BytesIO(raw_image))
        else:
            # generate image
            if image_description is None:
                return Response(chat_message=TextMessage(content="ask user for image_description", source=self.name))
            try:
                prompt = ",".join([self._system_message.content, image_description])
                response = self._model_client.models.generate_images(
                    model=self._model_name,
                    prompt=prompt,
                    config=types.GenerateImagesConfig(
                        number_of_images=1,
                        person_generation=types.PersonGeneration.ALLOW_ADULT,
                    ),
                )
                raw_image = response.generated_images[0].image.image_bytes
                image = PILImage.open(BytesIO(raw_image), formats=["JPEG", "PNG"])
            except Exception as e:
                logger.error(traceback.format_exc())
                logger.error(f"Error generate image, {e}")

        if image is None:
            return Response(chat_message=TextMessage(content="tell user to try again later", source=self.name))
        image = image.resize((self.width, self.height))
        return Response(
            chat_message=MultiModalMessage(content=["Here is the token image", Image.from_pil(image)], source=self.name)
        )

    async def on_reset(self, cancellation_token: CancellationToken) -> None:
        """Reset the assistant agent to its initialization state."""
        pass
