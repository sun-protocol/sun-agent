import json
import logging
import re
from typing import (
    Any,
    Dict,
    List,
    cast,
)

from .._constants import LOGGER_NAME

logger = logging.getLogger(LOGGER_NAME)


def extract_markdown_json_blocks(markdown_text: str) -> List[Any]:
    pattern = re.compile(r"```(?:\s*([\w\+\-]+))?\n([\s\S]*?)```")
    matches = pattern.findall(markdown_text)
    blocks: List[Any] = []
    for match in matches:
        language = match[0].strip() if match[0] else ""
        if language != "json":
            continue
        try:
            blocks.append(json.loads(match[1]))
        except Exception:
            logger.warning(f"skip block {match[1]}")
            continue
    return blocks


def extract_scores_from_markdown_json_blocks(markdown_text: str) -> List[float]:
    blocks = extract_markdown_json_blocks(markdown_text)
    scores: List[float] = []
    for block in blocks:
        if isinstance(block, Dict) and "scores" in block and isinstance(block["scores"], List):
            for score in cast(List[float], block["scores"]):
                scores.append(score)
    return scores


def extract_tweets_from_markdown_json_blocks(markdown_text: str) -> List[Dict[str, Any]]:
    tweets: List[Dict[str, Any]] = []
    blocks = extract_markdown_json_blocks(markdown_text)
    for block in blocks:
        if isinstance(block, Dict) and "id" in block and "text" in block:
            tweets.append(cast(Dict[str, Any], block))
        elif isinstance(block, List):
            for obj in cast(List[Dict[str, Any]], block):
                if isinstance(obj, Dict) and "id" in obj and "text" in obj:
                    tweets.append(obj)
    return tweets


def extract_json_from_string(raw_str):
    pattern = r"\{[\s\S]*\}"
    match = re.search(pattern, raw_str)
    if match:
        json_str = match.group(0)
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            logger.error("No valid JSON found in the string")

    pattern = r"\{.*\}"
    match = re.search(pattern, raw_str, re.DOTALL)
    if match:
        json_str = match.group(0)
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            logger.error("No valid JSON found in the string")

    logger.error("No valid JSON found in the string")
