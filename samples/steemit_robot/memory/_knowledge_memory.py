import os

from sunagent_ext.memory import Mem0xMemory, Mem0xMemoryConfig


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
        limit=int(os.getenv("MEMORY_LIMIT", "50")),
        score_threshold=float(os.getenv("MEMORY_SCORE_THRESHOLD", "0.5")),
    )
    knowledge_memory = Mem0xMemory(config)

    return [knowledge_memory]
