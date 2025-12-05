import base64
import json
import logging
import random
import traceback
from io import BytesIO
from typing import (
    Any,
    Dict,
    List,
    Literal,
    Optional,
    Protocol,
    Sequence,
    cast,
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
from google.genai import types
from PIL import Image as PILImage

from sunagent_app._constants import LOGGER_NAME
from sunagent_app.metrics import model_api_failure_count, model_api_success_count

from ._markdown_utils import extract_json_from_string

logger = logging.getLogger(LOGGER_NAME)

# Allowed sizes for Azure OpenAI images.generate
_OpenAIImageSize = Literal[
    "auto",
    "1024x1024",
    "1536x1024",
    "1024x1536",
    "256x256",
    "512x512",
    "1792x1024",
    "1024x1792",
]


class _OpenAIImagesClient(Protocol):
    def generate(self, model: str, prompt: str, *, size: _OpenAIImageSize | None = None) -> Any: ...


class _OpenAIClient(Protocol):
    images: _OpenAIImagesClient


class _GoogleModelsClient(Protocol):
    def generate_images(self, *, model: str, prompt: str, config: Any) -> Any: ...


class _GoogleClient(Protocol):
    models: _GoogleModelsClient


class ImageGenerateAgent(BaseChatAgent):
    """An agent that generate an image based on the description in the tweet.
    An information extraction agent must be called before this agent.
    """

    def __init__(
        self,
        name: str,
        text_model_client: AzureOpenAIChatCompletionClient,
        image_model_client: _GoogleClient | _OpenAIClient,
        *,
        description: str = """
        An agent that extract image attachment of given tweet, or generate an image according to the image description in the tweet.
        Image is base64 encoded.
        An information extraction agent must be called before this agent.
        """,
        system_message: str,
        image_styles: List[str],
        image_model_name: str = "imagen-3.0-generate-002",
        image_provider: Literal["google", "openai"] = "google",
        image_path: str = "generated_image.png",
        width: int = 400,
        height: int = 400,
    ) -> None:
        super().__init__(name=name, description=description)
        self.system_message = system_message
        self.image_styles = image_styles
        self._image_model_name = image_model_name
        self._image_provider = image_provider
        self._image_path = image_path
        self.image_model_client = image_model_client
        self.text_model_client = text_model_client
        self.width = width
        self.height = height

    @property
    def produced_message_types(self) -> Sequence[type[ChatMessage]]:
        """The types of messages that the this agent produces."""
        return (MultiModalMessage, TextMessage)

    async def on_messages(self, messages: Sequence[ChatMessage], cancellation_token: CancellationToken) -> Response:
        try:
            # 1. Extract the metadata required for image generation
            image_metadata = self.get_image_generation_metadata(messages)
            if not image_metadata:
                return self._create_no_image_response()

            # 2. Generate an optimized image prompt
            image_prompt = await self._generate_image_prompt(image_metadata)
            if not image_prompt:
                return self._create_error_response("Failed to generate image prompt")

            # 3. Generate the image
            image = await self._generate_image(image_prompt)
            if not image:
                return self._create_error_response("Failed to generate image, please try again later")

            # 4. Return the response containing the image
            return self._create_image_response(image)
        except Exception as e:
            logger.error(f"Error in on_messages: {e}")
            logger.error(traceback.format_exc())
            return self._create_error_response(f"Unexpected error: {e}")

    def get_image_generation_metadata(self, messages: Sequence[ChatMessage]) -> Optional[Dict[str, str]]:
        """Extract the metadata required for image generation from the messages."""
        for message in messages:
            if message.source == "ImageAdvisor" and isinstance(message, TextMessage):
                try:
                    reply_msg = extract_json_from_string(message.content)
                    if reply_msg is not None and reply_msg.get("need_image"):
                        return {
                            "last_tweet": reply_msg.get("last_tweet", ""),
                            "content": reply_msg.get("content", ""),
                            "image_style": random.choice(self.image_styles),
                        }
                except Exception as e:
                    logger.error(f"Error extracting image metadata: {e}")
        return None

    async def _generate_image_prompt(self, image_metadata: Dict[str, str]) -> Optional[str]:
        """Generate an optimized image prompt."""
        try:
            logger.info(f"Generating image with style: {image_metadata['image_style']}")
            prompt_text = self.system_message.format(
                last_tweet=image_metadata["last_tweet"],
                content=image_metadata["content"],
                image_style=image_metadata["image_style"],
            )
            response = await self.text_model_client.create(
                [
                    UserMessage(
                        content=prompt_text,
                        source="user",
                    ),
                ]
            )
            assert isinstance(response.content, str)
            return response.content
        except Exception as e:
            logger.error(f"Error generating image prompt: {e}")
            return None

    async def _generate_image(self, image_prompt: str) -> Optional[PILImage.Image]:
        """Generate an image based on the image prompt."""
        try:
            logger.info(f"Generating image with prompt: {image_prompt}")
            if self._image_provider == "openai":
                image_client = cast(_OpenAIClient, self.image_model_client)
                size_str = f"{self.width}x{self.height}"
                allowed_sizes: set[_OpenAIImageSize] = {
                    "auto",
                    "1024x1024",
                    "1536x1024",
                    "1024x1536",
                    "256x256",
                    "512x512",
                    "1792x1024",
                    "1024x1792",
                }
                size_literal: _OpenAIImageSize = (
                    cast(_OpenAIImageSize, size_str) if size_str in allowed_sizes else "1024x1024"
                )
                response = image_client.images.generate(
                    model=self._image_model_name,
                    prompt=image_prompt,
                    size=size_literal,
                )
                data = getattr(response, "data", None)
                if not data or not isinstance(data, list):
                    logger.error("OpenAI image response missing data")
                    return None
                first = data[0]
                b64_json = getattr(first, "b64_json", None)
                if not isinstance(b64_json, str):
                    logger.error("OpenAI image response missing b64_json")
                    return None
                raw_image = base64.b64decode(b64_json)
                image = PILImage.open(BytesIO(raw_image), formats=["PNG", "JPEG"])
            else:
                google_client = cast(_GoogleClient, self.image_model_client)
                response = google_client.models.generate_images(
                    model=self._image_model_name,
                    prompt=image_prompt,
                    config=types.GenerateImagesConfig(number_of_images=1),
                )
                if (
                    response.generated_images is None
                    or len(response.generated_images) == 0
                    or response.generated_images[0].image is None
                    or response.generated_images[0].image.image_bytes is None
                ):
                    logger.error("Failed to generate image")
                    return None
                raw_image = response.generated_images[0].image.image_bytes
                image = PILImage.open(BytesIO(raw_image), formats=["PNG"])

            model_api_success_count.inc()
            return image.resize((self.width, self.height))
        except Exception as e:
            logger.error(f"Error generating image: {e}")
            model_api_failure_count.inc()
            logger.error(traceback.format_exc())
        return None

    def _create_no_image_response(self) -> Response:
        return Response(
            chat_message=TextMessage(
                content="don't need image generation OR image_prompt is not provided, TERMINATE",
                source=self.name,
            )
        )

    def _create_error_response(self, error_message: str) -> Response:
        return Response(
            chat_message=TextMessage(
                content=f"{error_message}, TERMINATE",
                source=self.name,
            )
        )

    def _create_image_response(self, image: PILImage.Image) -> Response:
        return Response(
            chat_message=MultiModalMessage(
                content=[Image.from_pil(image)],
                source=self.name,
            )
        )

    async def on_reset(self, cancellation_token: CancellationToken) -> None:
        """Reset the assistant agent to its initialization state."""
        pass
