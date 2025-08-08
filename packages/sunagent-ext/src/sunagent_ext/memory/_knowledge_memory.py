import os

from sunagent_ext.memory import Mem0xMemory, Mem0xMemoryConfig
from sunagent_ext.secret_management.config import Config


async def get_knowledge_memory(config: Config) -> list[Mem0xMemory]:
    """
    Knowledge related
    Return:
     Return the knowledge related memory
    """
    memo_config = Mem0xMemoryConfig(
        name="knowledge",
        header="Knowledge",
        url=await config.get_env("MEM0X_URL") or "http://localhost:19527",
        table_name="knowledge",
        agent_id=await config.get_env("MEMORY_AGENT_ID") or "sunlumi",
        limit=int(await config.get_env("MEMORY_LIMIT", "50")),
        score_threshold=float(await config.get_env("MEMORY_SCORE_THRESHOLD", "0.5")),
    )

    return [Mem0xMemory(memo_config)]
