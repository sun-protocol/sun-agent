import json
import logging
import os
import random
from io import BytesIO
from typing import (
    Any,
    Dict,
    List,
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
    ChatCompletionClient,
    SystemMessage,
    UserMessage,
)
from google import genai
from google.genai import types
from PIL import Image as PILImage

from sunagent_app.metrics import model_api_failure_count, model_api_success_count

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
        image_styles: List[str],
        image_model: str = "imagen-3.0-generate-002",
        width: int = 400,
        height: int = 400,
    ) -> None:
        super().__init__(name=name, description=description)
        self._model_client = model_client
        self._system_message = SystemMessage(content=system_message)
        self._image_model = genai.Client(api_key=os.getenv("GOOGLE_GEMINI_API_KEY"))
        self._model_name = image_model
        self._image_styles = image_styles
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
        """Process messages and launch token"""
        # 1. Extract tweet and info
        tweet, informations = self._extract_tweet_and_informations(messages)
        logger.info(f"TokenLaunchAgent Tweet: {tweet}, Informations: {informations}")
        # 2. Validate token launch permission
        permission_result = self._validate_token_launch_permission(tweet)
        if permission_result:
            return permission_result
        
        # 3. Check and complete missing info
        completion_result = await self._complete_missing_informations(
            informations, tweet, cancellation_token
        )
        if completion_result:
            return completion_result
        
        # 4. Check previous launch status
        status_result = await self._check_previous_launch_status(informations["username"])
        if status_result:
            return status_result
        
        # 5. Get or generate image
        image_result = await self._get_or_generate_image(tweet, informations)
        if isinstance(image_result, Response):
            return image_result
        
        # 6. Launch token
        return await self._launch_token(informations, image_result)

    def _extract_tweet_and_informations(self, messages: Sequence[ChatMessage]) -> tuple[Dict[str, Any], Dict[str, Optional[str]]]:
        """Extract tweet and related info from messages"""
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
        
        assert tweet is not None, "No valid tweet found in messages"
        informations["username"] = tweet["author"]
        
        return tweet, informations

    def _validate_token_launch_permission(self, tweet: Dict[str, Any]) -> Optional[Response]:
        """Validate if tweet allows new token launch"""
        can_launch = tweet.get("can_launch_new_token", "").strip().lower()
        if can_launch != "ok":
            logger.warning(f"Tweet does not allow token launch: {tweet['can_launch_new_token']}")
            return Response(
                chat_message=TextMessage(content=tweet["can_launch_new_token"], source=self.name)
            )
        return None

    async def _complete_missing_informations(
        self, 
        informations: Dict[str, Optional[str]], 
        tweet: Dict[str, Any], 
        cancellation_token: CancellationToken
    ) -> Optional[Response]:
        """Check and complete missing info"""
        missing_parameters = [key for key, value in informations.items() if value is None]
        
        # If critical info missing, ask user
        critical_params = {"name", "symbol", "description"}
        if critical_params.issubset(set(missing_parameters)):
            logger.warning(f"Critical info missing: {missing_parameters}")
            return Response(
                chat_message=TextMessage(
                    content=f"ask user for these informations {json.dumps(missing_parameters)}", 
                    source=self.name
                )
            )
        
        # Use AI model to fill missing info
        if missing_parameters:
            await self._generate_missing_informations(informations, tweet, cancellation_token)
            
        return None

    async def _generate_missing_informations(
        self, 
        informations: Dict[str, Optional[str]], 
        tweet: Dict[str, Any], 
        cancellation_token: CancellationToken
    ) -> None:
        """Use AI model to generate missing info"""
        try:
            result = await self._model_client.create(
                [
                    self._system_message,
                    UserMessage(content=f"```json\n{json.dumps(tweet, ensure_ascii=False)}\n```", source="user"),
                ],
                cancellation_token=cancellation_token,
            )
            logger.info(f"generated missing informations: {result}")
            model_api_success_count.inc()
            
            if isinstance(result.content, str):
                self._get_informations_from_message(
                    TextMessage(content=result.content, source=self.name), 
                    informations
                )
                logger.info(f"generated informations: {json.dumps(informations)}")
        except Exception as e:
            model_api_failure_count.inc()
            logger.error(f"Error generating missing informations: {e}")
            raise

    async def _check_previous_launch_status(self, username: Optional[str]) -> Optional[Response]:
        """Check user's previous token launch status"""
        try:
            status = await self._sunpump_service.query_launch_token_status_by_user(username)
            logger.info(f"Previous launch status: {status}")
            if status == "UPLOADED":
                return Response(
                    chat_message=TextMessage(
                        content="User can't launch a new token before the previous token launch job is completed",
                        source=self.name,
                    )
                )
            elif status not in ["NONE", "CREATED"]:
                return Response(chat_message=TextMessage(content=status, source=self.name))
                
        except Exception as e:
            logger.error(f"Error checking previous launch status: {e}")
            
        return None

    async def _get_or_generate_image(
        self, 
        tweet: Dict[str, Any], 
        informations: Dict[str, Optional[str]]
    ) -> PILImage.Image | Response:
        """Get or generate token image"""
        # Try to get image from tweet URL
        if "image_url" in tweet:
            logger.info(f"Fetching image from URL: {tweet['image_url']}")
            image = await self._fetch_image_from_url(tweet["image_url"])
            if image:
                return image
        
        # Generate new image
        return await self._generate_token_image(informations.get("image_description"))

    async def _fetch_image_from_url(self, image_url: str) -> Optional[PILImage.Image]:
        """Fetch image from URL"""
        try:
            raw_image = await fetch_url(image_url)
            if raw_image:
                return PILImage.open(BytesIO(raw_image))
        except Exception as e:
            logger.error(f"Error fetching image from URL {image_url}: {e}")
        return None

    async def _generate_token_image(self, image_description: Optional[str]) -> PILImage.Image | Response:
        """Generate token image"""
        prompt_text = ",".join([random.choice(self._image_styles), image_description or ""])
        logger.info(f"Generating image with prompt: {prompt_text}")
        try:
            response = self._image_model.models.generate_images(
                model=self._model_name,
                prompt=prompt_text,
                config=types.GenerateImagesConfig(
                    number_of_images=1,
                    person_generation=types.PersonGeneration.ALLOW_ADULT,
                ),
            )
            model_api_success_count.inc()
            
            raw_image = response.generated_images[0].image.image_bytes
            return PILImage.open(BytesIO(raw_image), formats=["JPEG", "PNG"])
            
        except Exception as e:
            model_api_failure_count.inc()
            logger.error(f"Error generating image: {e}")
            return Response(
                chat_message=TextMessage(
                    content="Image generate service is busy, tell user try again later",
                    source=self.name,
                )
            )

    async def _launch_token(
        self, 
        informations: Dict[str, Optional[str]], 
        image: PILImage.Image
    ) -> Response:
        # Resize and convert image
        resized_image = Image.from_pil(image.resize((self.width, self.height)))
            
        try:
            response = await self._sunpump_service.launch_new_token(
                str(informations["name"]),
                str(informations["symbol"]),
                str(informations["description"]),
                resized_image.to_base64(),
                informations.get("tweet_id", ""),
                str(informations["username"]),
            )
            logger.info(f"Token Launch Result: {response}")
            return Response(
                chat_message=TextMessage(content=f"Token Launch Result:\n{response}", source=self.name)
            )
        except Exception as e:
            logger.error(f"Error launching token: {e}")
            return Response(
                chat_message=TextMessage(
                    content=f"Failed to launch token: {str(e)}", 
                    source=self.name
                )
            )

    async def on_reset(self, cancellation_token: CancellationToken) -> None:
        """Reset the assistant agent to its initialization state."""
        pass
