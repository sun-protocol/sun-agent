import json
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
    UserMessage,
    SystemMessage,
)
from autogen_ext.models.openai import AzureOpenAIChatCompletionClient
from google.genai import Client, types
from PIL import Image as PILImage

from sunagent_app._constants import LOGGER_NAME
from sunagent_app.metrics import model_api_failure_count, model_api_success_count

logger = logging.getLogger(LOGGER_NAME)


class ImagePromptAgent(BaseChatAgent):
    """负责生成图像提示词的Agent。分析内容并生成优化的图像描述。"""

    _image_styles = [
        "Studio Ghibli style, magical atmosphere, hand-drawn look, soft colors",
        "dynamic cartoon-style illustration",
        "papercraft, kirigami style, layered paper, paper quilling, diorama, made of paper, 3D paper art",
        "claymation character, stop-motion animation style, made of plasticine, fingerprint details, in the style of Aardman Animations",
    ]
    _prompt_template = """
    # Your Task
    Generate a vivid, detailed image prompt based on the input content. The prompt should:
    - Accurately capture the main idea of the content.
    - Be directly usable by a text-to-image model.
    - Output ONLY the English prompt, do NOT include your thought process.

    # Image Style
    {image_style}

    # Three-Step Method

    ## Step 1:  Extract Core Theme
    Extract 3-5 important keywords from the content, such as:
    - Read the content carefully.
    - Identify the most essential theme, object, action, or emotion.
    - Ignore irrelevant details, hashtags, and secondary information.

    ## Step 2: Visualize It
    - Translate the core theme into one or two simple, recognizable visual symbols.
    - Use imagination: consider metaphorical, creative, or bold representations—not just literal objects.
    - Choose the most vivid and original symbols that capture the content’s essence.

    ## Step 3: Compose the Prompt
    Use the following structure:
    - [Design Style], [Core Visual Elements], [Composition & Background], [Key Modifiers]
    Guidelines:
    - Use clear, concise English keywords.
    - Focus on the central subject of the icon.
    - Specify the background: "on a white background", "on a simple background", or "on a transparent background".
    - Add key modifiers: app icon, vector logo, UI icon, clean, modern, vibrant colors.
    """

    def __init__(
        self,
        name: str,
        text_model_client: AzureOpenAIChatCompletionClient,
        *,
        description: str = "负责分析内容并生成优化的图像提示词",
    ) -> None:
        super().__init__(name=name, description=description)
        self.text_model_client = text_model_client

    @property
    def produced_message_types(self) -> Sequence[type[ChatMessage]]:
        return (TextMessage,)

    async def on_messages(self, messages: Sequence[ChatMessage], cancellation_token: CancellationToken) -> Response:
        try:
            # 提取图像生成元数据
            image_metadata = self._extract_metadata(messages)
            # 生成图像提示词
            image_prompt = await self._generate_prompt(image_metadata)
            if not image_prompt:
                return self._create_error_response("生成图像提示词失败")
            return Response(
                chat_message=TextMessage(
                    content=image_prompt,
                    source=self.name,
                )
            )
            
        except Exception as e:
            logger.error(f"ImagePromptAgent错误: {e}")
            logger.error(traceback.format_exc())
            return self._create_error_response(f"意外错误: {e}")

    def _extract_metadata(self, messages: Sequence[ChatMessage]) -> dict:
        """从消息中提取图像生成所需的元数据"""
        last_message = messages[-1]
        return {
            "content": last_message.get("content", ""),
            "image_style": random.choice(self._image_styles),
        }

    async def _generate_prompt(self, image_metadata: dict) -> Optional[str]:
        """生成优化的图像提示词"""
        try:
            logger.info(f"使用图像风格生成提示词: {image_metadata['image_style']}")
            prompt = self._prompt_template.format(image_style=image_metadata['image_style'])
            response = await self.text_model_client.create(
                [
                    SystemMessage(content=self._prompt_template),
                    UserMessage(
                        content=f"```json\n{json.dumps(image_metadata, ensure_ascii=False)}\n```{prompt}", 
                        source=self.name
                    ),
                ],
            )
            return response.content
        except Exception as e:
            logger.error(f"生成图像提示词时出错: {e}")
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
            image_prompt = last_message.get("content", "")
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