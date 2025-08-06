import os
from typing import List

from autogen_core.memory import Memory
from sunagent_ext.memory import Mem0xMemory, Mem0xMemoryConfig


def get_knowledge_memory() -> List[Memory]:
    """
    Knowledge related
    Return:
     Return the knowledge related memory
    """
    config = Mem0xMemoryConfig(agent_id=os.getenv("MEMORY_AGENT_ID"))
    name = os.getenv("MEM0X_NAME")
    if name is not None:
        config.name = name
    header = os.getenv("MEM0X_HEAD")
    if header is not None:
        config.header = header
    url = os.getenv("MEM0X_URL")
    if url is not None:
        config.url = url
    table_name = os.getenv("MEM0X_TABLE_NAME")
    if table_name is not None:
        config.table_name = table_name
    limit = os.getenv("MEMORY_LIMIT")
    if limit is not None:
        config.limit = int(limit)
    score_threshold = os.getenv("MEMORY_SCORE_THRESHOLD")
    if score_threshold is not None:
        config.score_threshold = float(score_threshold)
    knowledge_memory = Mem0xMemory(config)

    return [knowledge_memory]
