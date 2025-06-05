import asyncio
import logging
from typing import Optional

import aiohttp

from .._constants import LOGGER_NAME

logger = logging.getLogger(LOGGER_NAME)


async def fetch_url(url) -> Optional[bytes]:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                response.raise_for_status()
                return await response.content.read()
    except aiohttp.ClientError as e:
        logger.error(f"Error fetching {url}: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error fetching {url}: {e}")
        return None
