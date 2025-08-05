import logging
import random
import traceback
from io import BytesIO
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
    SystemMessage,
    UserMessage,
)
from autogen_ext.models.openai import AzureOpenAIChatCompletionClient
from google.genai import Client, types
from PIL import Image as PILImage
from sunagent_app._constants import LOGGER_NAME
from sunagent_app.metrics import model_api_failure_count, model_api_success_count

from ._prompts import PROMPT_FOR_IMAGE_PROMPT

logger = logging.getLogger(LOGGER_NAME)


def extract_message_content(message: ChatMessage) -> str:
    if isinstance(message, TextMessage):
        return message.content
    elif isinstance(message, MultiModalMessage):
        text_parts = [item for item in message.content if isinstance(item, str)]
        return " ".join(text_parts)
    else:
        content = getattr(message, 'content', "")
        return str(content) if content and not isinstance(content, str) else content or ""


class ImagePromptAgent(BaseChatAgent):
    _image_styles = [
        "Studio Ghibli style, magical atmosphere, hand-drawn look, soft colors",
        "dynamic cartoon-style illustration",
        "papercraft, kirigami style, layered paper, paper quilling, diorama, made of paper, 3D paper art",
        "claymation character, stop-motion animation style, made of plasticine, fingerprint details, in the style of Aardman Animations",
    ]

    def __init__(
        self,
        name: str,
        text_model_client: AzureOpenAIChatCompletionClient,
        *,
        prompt_for_image_prompt: str = PROMPT_FOR_IMAGE_PROMPT,
        description: str = "Responsible for analyzing content and generating optimized image prompts",
    ) -> None:
        super().__init__(name=name, description=description)
        self.text_model_client = text_model_client
        self._prompt_for_image_prompt = prompt_for_image_prompt

    @property
    def produced_message_types(self) -> Sequence[type[ChatMessage]]:
        return (TextMessage,)

    async def on_messages(self, messages: Sequence[ChatMessage], cancellation_token: CancellationToken) -> Response:
        try:
            image_prompt = await self._generate_prompt(messages)
            if not image_prompt:
                return self._create_error_response("Failed to generate image prompt")
            return Response(
                chat_message=TextMessage(
                    content=image_prompt,
                    source=self.name,
                )
            )

        except Exception as e:
            logger.error(f"ImagePromptAgent error: {e}")
            logger.error(traceback.format_exc())
            return self._create_error_response("Unexpected error")

    async def _generate_prompt(self, messages: Sequence[ChatMessage]) -> Optional[str]:
        try:
            last_message = messages[-1]
            message_content = extract_message_content(last_message)

            image_style = random.choice(self._image_styles)
            prompt_text = self._prompt_for_image_prompt.format(image_style=image_style)
            logger.info(f"Generating prompt with image style: {image_style}")
            response = await self.text_model_client.create(
                [
                    SystemMessage(content=prompt_text),
                    UserMessage(
                        content=message_content, source=self.name
                    ),
                ],
            )
            model_api_success_count.inc()
            return response.content
        except Exception as e:
            model_api_failure_count.inc()
            logger.error(f"Error generating image prompt: {e}")
            return None

    def _create_error_response(self, error_message: str) -> Response:
        return Response(
            chat_message=TextMessage(
                content=f"{error_message}, EARLY_TERMINATE",
                source=self.name,
            )
        )

    async def on_reset(self, cancellation_token: CancellationToken) -> None:
        pass


class ImageGenerateAgent(BaseChatAgent):
    """Agent responsible for generating images based on prompts. Receives a prompt and generates the corresponding image."""

    def __init__(
        self,
        name: str,
        image_model_client: Client,
        *,
        description: str = "Generate images based on prompts",
        image_model_name: str = "imagen-3.0-generate-002",
        width: int = 400,
        height: int = 400,
    ) -> None:
        super().__init__(name=name, description=description)
        self.image_model_client = image_model_client
        self._image_model_name = image_model_name
        self.width = width
        self.height = height

    @property
    def produced_message_types(self) -> Sequence[type[ChatMessage]]:
        return (MultiModalMessage, TextMessage)

    async def on_messages(self, messages: Sequence[ChatMessage], cancellation_token: CancellationToken) -> Response:
        try:
            # Get the last message as the image prompt
            last_message = messages[-1]
            image_prompt = extract_message_content(last_message)
            
            if not image_prompt:
                return self._create_error_response("No image prompt provided")
            # Generate image
            image = await self._generate_image(image_prompt)
            if not image:
                return self._create_error_response("Image generation failed, please try again later")
            return self._create_image_response(image)
        except Exception as e:
            logger.error(f"ImageGenerateAgent error: {e}")
            logger.error(traceback.format_exc())
            return self._create_error_response(f"Unexpected error: {e}")

    async def _generate_image(self, image_prompt: str) -> Optional[PILImage.Image]:
        """Generate an image based on the image prompt"""
        try:
            logger.info(f"Generating image with prompt: {image_prompt}")
            response = self.image_model_client.models.generate_images(
                model=self._image_model_name,
                prompt=image_prompt,
                config=types.GenerateImagesConfig(number_of_images=1),
            )
            model_api_success_count.inc()
            raw_image = response.generated_images[0].image.image_bytes
            image = PILImage.open(BytesIO(raw_image), formats=["JPEG", "PNG"])
            return image.resize((self.width, self.height))
        except Exception as e:
            logger.error(f"Error generating image: {e}")
            model_api_failure_count.inc()
            logger.error(traceback.format_exc())
            return None

    def _create_error_response(self, error_message: str) -> Response:
        return Response(
            chat_message=TextMessage(
                content=f"{error_message}, EARLY_TERMINATE",
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
        pass
