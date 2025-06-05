import asyncio
import json
import logging
import os
import random
import traceback
from io import BytesIO
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
    MultiModalMessage,
    TextMessage,
)
from autogen_core import CancellationToken, Image
from autogen_core.models import (
    ChatCompletionClient,
    SystemMessage,
    UserMessage,
)
from google import genai
from google.genai import types
from PIL import Image as PILImage

from .._constants import LOGGER_NAME
from ..sunpump_service import SunPumpService
from ._http_utils import fetch_url
from ._markdown_utils import extract_markdown_json_blocks, extract_tweets_from_markdown_json_blocks

logger = logging.getLogger(LOGGER_NAME)


class TokenLaunchAgent(BaseChatAgent):
    """An agent that launch a token based on the description in the tweet.
    An information extraction agent must be called before this agent.
    """

    def __init__(
        self,
        name: str,
        *,
        description: str = """An agent that launch a token based on the description in the tweet.
        An information extraction agent must be called before this agent.
        """,
        sunpump_service: SunPumpService,
        model_client: ChatCompletionClient,
        system_message: str,
        image_prompts: List[str],
        image_model: str = "imagen-3.0-generate-002",
        width: int = 400,
        height: int = 400,
    ) -> None:
        super().__init__(name=name, description=description)
        self._model_client = model_client
        self._system_message = SystemMessage(content=system_message)
        self._image_model = genai.Client(api_key=os.getenv("GOOGLE_GEMINI_API_KEY"))
        self._model_name = image_model
        self._image_prompts = image_prompts
        self.width = width
        self.height = height
        self._sunpump_service = sunpump_service

    @property
    def produced_message_types(self) -> Sequence[type[ChatMessage]]:
        """The types of messages that the this agent produces."""
        return (MultiModalMessage, TextMessage)

    def _get_informations_from_message(self, message: TextMessage, informations: Dict[str, Optional[str]]) -> None:
        blocks = extract_markdown_json_blocks(message.content)
        for block in blocks:
            if isinstance(block, Dict) and "symbol" in block:
                for parameter in informations.keys():
                    if parameter in block and block[parameter]:
                        value = block[parameter].strip()
                        if len(value) > 0:
                            informations[parameter] = value

    async def on_messages(self, messages: Sequence[ChatMessage], cancellation_token: CancellationToken) -> Response:
        tweet: Optional[Dict[str, Any]] = None
        informations: Dict[str, Optional[str]] = {
            "name": None,
            "symbol": None,
            "image_description": "",
            "description": None,
            "tweet_id": None,
            "username": None,
        }
        for message in messages:
            if message.source == "user":
                tweets = extract_tweets_from_markdown_json_blocks(message.content)
                tweet = tweets[0] if len(tweets) > 0 else None
            elif isinstance(message, TextMessage):
                self._get_informations_from_message(message, informations)
        assert tweet is not None
        informations["username"] = tweet["author"]
        if tweet["can_launch_new_token"].strip().lower() != "ok":
            return Response(chat_message=TextMessage(content=tweet["can_launch_new_token"], source=self.name))

        missing_parameters: List[str] = []
        for key, value in informations.items():
            if value is None:
                missing_parameters.append(key)
        if "name" in missing_parameters and "symbol" in missing_parameters and "description" in missing_parameters:
            return Response(
                chat_message=TextMessage(
                    content=f"ask user for these informations {json.dumps(missing_parameters)}", source=self.name
                )
            )
        if len(missing_parameters) > 0:
            result = await self._model_client.create(
                [
                    self._system_message,
                    UserMessage(content=f"```json\n{json.dumps(tweet, ensure_ascii=False)}\n```", source="user"),
                ],
                cancellation_token=cancellation_token,
            )
            assert isinstance(result.content, str)
            self._get_informations_from_message(TextMessage(content=result.content, source=self.name), informations)
            logger.info(f"generated informations: {json.dumps(informations)}")

        # check previous launch status
        response = await self._sunpump_service.query_launch_token_status_by_user(informations["username"])
        if response == "UPLOADED":
            return Response(
                chat_message=TextMessage(
                    content="User can't launch a new token before the previous token launch job is completed",
                    source=self.name,
                )
            )
        elif response not in ["NONE", "CREATED"]:
            return Response(chat_message=TextMessage(content=response, source=self.name))

        image: Optional[PILImage] = None
        if "image_url" in tweet:
            raw_image = await fetch_url(tweet["image_url"])
            if raw_image is not None:
                image = PILImage.open(BytesIO(raw_image))
        else:
            # generate image
            try:
                image_prompt = self._image_prompts[random.randint(0, len(self._image_prompts) - 1)]
                prompt = ",".join(
                    [image_prompt, informations["name"], informations["description"], informations["image_description"]]
                )
                response = self._image_model.models.generate_images(
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
            return Response(
                chat_message=TextMessage(
                    content="Image generate service is busy, tell user try again later",
                    source=self.name,
                )
            )
        image = Image.from_pil(image.resize((self.width, self.height)))

        response = await self._sunpump_service.launch_new_token(
            informations["name"],
            informations["symbol"],
            informations["description"],
            image.to_base64(),
            informations["tweet_id"],
            informations["username"],
        )
        return Response(chat_message=TextMessage(content=f"Token Launch Result:\n{response}", source=self.name))

    async def on_reset(self, cancellation_token: CancellationToken) -> None:
        """Reset the assistant agent to its initialization state."""
        pass
