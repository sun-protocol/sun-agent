import json
import logging
import random
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
# Your Task
Generate a vivid, detailed image prompt based on the Twitter conversation below. The prompt should:
- Accurately capture the main idea of the tweet.
- Depict a scene that is realistic and physically plausible.
- Be directly usable by a text-to-image model.
- Output ONLY the English prompt, do NOT include your thought process.

Original Tweet: "{last_tweet}"
Reply: "{content}"

# Image Style
{image_style}

# Three-Step Method

## Step 1: Identify Key Elements
Extract 3-5 important keywords from the tweet, such as:
- People/Companies (e.g., Vitalik Buterin, Robinhood)
- Events/Themes (e.g., Fireside Chat, Crypto's Next Chapter)
- Locations/Times (e.g., Cannes, July 1st)

## Step 2: Visualize It
Transform these keywords into visual elements:
- Turn companies or people into symbolic characters or objects.
- Represent events as scenes or actions.
- Use slogans or themes as banners, signs, or text in the image.

## Step 3: Compose the Prompt
Describe the image using the following template:
- **Style:** One sentence describing the art style.
- **Scene:** One sentence summarizing the overall scene.
- **Details:** Bullet points listing the main elements and their symbolic meanings.

# Output Format Example
- Style: [Describe the art style]
- Scene: [Describe the main scene]
- Details:
    1. [Key element 1 and its meaning]
    2. [Key element 2 and its meaning]
    3. [Additional relevant details]
    
# Examples
## Example 1
1. tweet content:
    Hong Kong's Cai Wensheng acquires a 35% stake in China Financial Leasing at a 13.4% premium, aiming to pivot towards AI, Web3, and digital assets. Could this signal a stronger focus on tech incubation and decentralized industries in the region? A move worth watching for blockchain and AI enthusiasts. 
2. output: 
    - Style:
    A vibrant and dynamic retro-futuristic cartoon poster illustration, characterized by bold outlines and a high-contrast color palette.
    -Scene:
    A visionary Asian businessman plants a flag for a new technological era in front of Hong Kong's iconic Victoria Harbour skyline at dawn.
    -Details:
    The Visionary: A stylized character representing Cai Wensheng, dressed in a sharp suit, confidently planting a large flag.
    The Tech Flag: The flag is emblazoned with the glowing words "AI" & "WEB3", symbolizing the strategic pivot.
    The Transformation: In the background, a classic, stone financial building (symbolizing "China Financial Leasing") is visibly transforming, with glowing blue circuits and futuristic panels growing over it.
    The Backdrop: A detailed, recognizable Hong Kong skyline, including the Bank of China Tower and IFC, bathed in the optimistic light of a new day.
    Floating Symbols: Holographic icons representing artificial intelligence (like a glowing brain) and blockchain (interlinked digital cubes) float in the air.
    The Billboard: A large, retro-style billboard in the sky reads: "HONG KONG'S NEW FRONTIER: FROM FINANCE TO FUTURE TECH".

## Example 2
1. tweet content:
    Matrixdock, Matrixport's RWA tokenization platform, is adding silver, platinum, and palladium tokens to its offering. With silver up 25% and platinum surging 44% this year, they're bridging traditional commodities with blockchain. Will tokenized precious metals reshape on-chain investment strategies?
2. output:
    - Style:
    Vibrant retro-futuristic comic book illustration, featuring bold, saturated colors (like reds, yellows, and blues), thick black outlines, and a dynamic, energetic composition.
    -Scene:
    A massive, powerful minting machine, the "Matrixdock Minter," serves as the centerpiece, actively transforming physical precious metals into digital assets on a grand scale.
    -Details:
    The Minter Machine (Matrixdock): A large, industrial-yet-high-tech machine with visible gears, pipes, and glowing lights. On its side, the name "MATRIXDOCK" is printed in bold lettering.
    The Input (Commodities): On the left, a conveyor belt feeds shiny, physical bars of silver, platinum, and palladium into an intake chute of the machine.
    The Output (Tokens): On the right, the machine dispenses a cascade of glowing, hexagonal digital tokens. Each token is translucent and clearly marked with a chemical symbol: "Ag," "Pt," and "Pd."
    The Blockchain River: The newly minted tokens fall onto a luminous, digital river made of interconnected, glowing blocks that flows towards the viewer, representing the blockchain.
    The Billboard Sign: In the background, a large yellow sign, similar to the one in the reference image, reads: "OLD METALS. NEW MONEY. Silver +25% | Platinum +44%" to highlight the value and transformation.

"""
image_styles = [
    # 吉卜力工作室风格
    "Studio Ghibli style, magical atmosphere, hand-drawn look, soft colors",
    # 动态卡通风格插画
    "dynamic cartoon-style illustration",
    # 立体纸艺/剪纸风 (3D Papercraft / Kirigami Style)
    "papercraft, kirigami style, layered paper, paper quilling, diorama, made of paper, 3D paper art",
    # 等轴体素艺术 (Isometric Voxel Art)
    "isometric voxel art of [object], a tiny room made of voxels, pixel art, 3D pixel, clean edges, video game aesthetic",
    # 黏土动画/定格动画风 (Claymation / Stop-Motion Style)
    "claymation character, stop-motion animation style, made of plasticine, fingerprint details, in the style of Aardman Animations",
]

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
                    # 1. 根据推文生成image 的文本描述
                    image_style = random.choice(image_styles)
                    logger.info(f"generate image with image_style: {image_style}")
                    description = {"last_tweet": reply_msg["last_tweet"], "content": reply_msg["content"], "image_style": image_style}
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
            logger.info(f"generate image with prompt: {image_description}")
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
