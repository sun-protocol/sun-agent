import base64
import io
import logging
import re

from openpyxl.drawing.image import PILImage
from autogen_core import Image
from autogen_core.models import UserMessage

from .._constants import LOGGER_NAME
from ._http_utils import fetch_url

logger = logging.getLogger(LOGGER_NAME)

TweetImageExtraction = """
You are ImageContentExtractor, an AI visual analysis assistant specialized in blockchain and technology imagery.
You will be given an image.
Your job is to extract technical/actionable insights from the image. Do not provide generic descriptions.

## Technical Analysis Stage
When valid to proceed:
1. Language selection:
   - Use specified language, or English if language is not specified.
   - Strictly monolingual output

2. Content requirements:
   - Try to extract these priority elements:
     a) Cryptocurrency symbols/price data (e.g., "BTC $42,150 ↑3.2%")
     b) Technical patterns (e.g., "Head-and-Shoulders formation")
     c) Infrastructure components (e.g., "zkProver module")
     d) Visible metrics (e.g., "TPS: 2,000")
     e) Event markers (e.g., "Mainnet launch countdown: 3 days")
   - If no priority elements exists, just explain what's in the image, and don't need to mention crypto related information

3. Value-add features:
   - Flag potential trading signals (e.g., "BREAKOUT: ETH/USD $1,850 resistance")
   - Identify protocol versions (e.g., "EIP-4844 implementation")

## Domain Expertise
Prioritize these elements:
1. Crypto market charts
2. Blockchain architecture diagrams
3. Tech conference slides
4. Project roadmap graphics
5. Whitepaper illustrations

# Output Format
** Your reply should be within one line and no more than 100 words **:
"""


async def append_image_info(tweet: dict, model_client):
    if "image_url" in tweet:
        url_info = tweet["image_url"]
        image_description = await generate_image_description(url_info, model_client)
        if image_description is not None:
            tweet["image_description"] = image_description


async def generate_image_description(source: str, model_client) -> str:
    result = ""
    url_type = _is_image_url_or_base64(source)
    if url_type == "url":
        image_bytes = await fetch_url(source)
        image_description = await extract_image_description(image_bytes, model_client)
        if image_description is not None:
            result = image_description
    elif url_type == "base64":
        if "," in source:
            # if a prefix like 'data/image...' exists, split and extract the base64 part
            source = source.split(",")[1]
        image_bytes_list = source.split("|")
        for idx, encoded_image_bytes in enumerate(image_bytes_list):
            image_bytes = base64.b64decode(encoded_image_bytes)
            image_description = await extract_image_description(image_bytes, model_client)
            if image_description is not None:
                result += f"Image{idx + 1}: {image_description}; "
    return result


async def extract_image_description(image_bytes: bytes, model_client) -> str:
    try:
        pil_image = PILImage.open(io.BytesIO(image_bytes))
        extracted_image = Image.from_pil(pil_image)
        image_info = await model_client.create(
            [
                UserMessage(
                    content=[TweetImageExtraction, extracted_image],
                    source="user",
                ),
            ],
        )
        logger.info(f"Image Description: {image_info}")
        return image_info.content
    except Exception as e:
        logger.error(f"error extracting images, {e}")
        raise e


def _is_image_url_or_base64(image_str: str) -> str:
    if image_str.startswith("data:image/"):
        return "base64"

    url_pattern = re.compile(r"^https?://")
    if url_pattern.match(image_str):
        return "url"

    if "." in image_str.split("/")[-1]:
        return "url"

    return "unknown"
