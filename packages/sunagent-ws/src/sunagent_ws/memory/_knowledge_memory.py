import logging
import os

from sunagent_ext.memory import Mem0xMemory, Mem0xMemoryConfig

from sunagent_ws.web.config import LOGGER_NAME

logger = logging.getLogger(LOGGER_NAME)


def get_knowledge_memory():
    """
    Knowledge related
    Return:
     Return the knowledge related memory
    """
    config = Mem0xMemoryConfig(
        name="knowledge",
        header="Knowledge",
        url=os.getenv("MEM0X_URL") or "http://localhost:19527",
        table_name="knowledge",
        agent_id=os.getenv("MEMORY_AGENT_ID") or "sunlumi",
    )
    knowledge_memory = Mem0xMemory._from_config(config)

    return [knowledge_memory]
