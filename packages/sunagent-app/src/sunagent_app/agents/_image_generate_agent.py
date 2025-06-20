import json
import logging
import traceback
from io import BytesIO
from typing import (
    Optional,
    Sequence,
)

from google.genai import Client, types
from PIL import Image as PILImage
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
from ._markdown_utils import extract_json_from_string

logger = logging.getLogger(LOGGER_NAME)

OptimizeImagePromptTemplate = """
Create a vivid image prompt based on the Twitter conversation

Original Tweet: "{last_tweet}"
Reply: "{content}"

Instructions:
1. Identify 3-5 key visual elements (focus on nouns: objects, places, symbols)
2. Note 1-2 emotions/moods from the conversation
3. Select appropriate style based on content (examples below)
4. Combine into 20-30 word (40 words maximum) description

Style Suggestions (adapt to context):
- "Playful cartoon with exaggerated features"
- "Watercolor with soft blending"
- "Isometric tech illustration"
- "Minimalist line art"
- "Surreal collage effect"
- "Neon cyberpunk aesthetic"
- "Claymation texture"
- "Retro futuristic"

Rules:
- No specific people/faces
- Include important elements from the last_tweet and content

Output ONLY the generated prompt.
"""


class ImageGenerateAgent(BaseChatAgent):
    """An agent that generate an image based on the description in the tweet.
    An information extraction agent must be called before this agent.
    """

    def __init__(
        self,
        name: str,
        text_model_client: AzureOpenAIChatCompletionClient,
        image_model_client: Client,
        *,
        description: str = """
        An agent that extract image attachment of given tweet, or generate an image according to the image description in the tweet.
        Image is base64 encoded.
        An information extraction agent must be called before this agent.
        """,
        image_model_name: str = "imagen-3.0-generate-002",
        image_path: str = "generated_image.png",
        width: int = 400,
        height: int = 400,
    ) -> None:
        super().__init__(name=name, description=description)
        self._image_model_name = image_model_name
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
        image_description: Optional[str] = None
        image: Optional[PILImage] = None
        for message in messages:
            if message.source == "ImageAdvisor":
                reply_msg = extract_json_from_string(message.content)
                if reply_msg["need_image"]:
                    description = {"last_tweet": reply_msg["last_tweet"], "content": reply_msg["content"]}
                    image_description = json.dumps(description, ensure_ascii=False)
                    try:
                        image_generation_prompt = await self.text_model_client.create(
                            [
                                UserMessage(
                                    content=[OptimizeImagePromptTemplate, image_description],
                                    source="user",
                                ),
                            ],
                        )
                        image_description = image_generation_prompt.content
                    except Exception as e:
                        logger.error(f"error analyzing the given image, {e}")
                        return Response(
                            chat_message=TextMessage(content=f"error analyzing image: {e}. TERMINATE", source=self.name)
                        )

        # generate image
        if image_description is None:
            return Response(
                chat_message=TextMessage(
                    content="don't need image generation OR image_description is not provided, TERMINATE",
                    source=self.name,
                )
            )
        try:
            response = self.image_model_client.models.generate_images(
                model=self._image_model_name,
                prompt=image_description,
                config=types.GenerateImagesConfig(
                    number_of_images=1,
                ),
            )
            raw_image = response.generated_images[0].image.image_bytes
            image = PILImage.open(BytesIO(raw_image), formats=["PNG"])
        except Exception as e:
            logger.error(traceback.format_exc())
            logger.error(f"Error generate image, {e}")

        if image is None:
            return Response(
                chat_message=TextMessage(content="tell user to try again later, TERMINATE", source=self.name)
            )
        image = image.resize((self.width, self.height))
        return Response(chat_message=MultiModalMessage(content=[Image.from_pil(image)], source=self.name))

    async def on_reset(self, cancellation_token: CancellationToken) -> None:
        """Reset the assistant agent to its initialization state."""
        pass
