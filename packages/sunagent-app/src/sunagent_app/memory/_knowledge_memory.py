import os

from sunagent_ext.memory import Mem0xMemory, Mem0xMemoryConfig


def get_knowledge_memory():
    """
    Knowledge related
    Return:
     Return the knowledge related memory
    """
    config = Mem0xMemoryConfig(
        name=os.getenv("MEM0X_NAME"),
        header=os.getenv("MEM0X_HEAD"),
        url=os.getenv("MEM0X_URL"),
        table_name=os.getenv("MEM0X_TABLE_NAME"),
        agent_id=os.getenv("MEMORY_AGENT_ID"),
        limit=int(os.getenv("MEMORY_LIMIT")),
        score_threshold=float(os.getenv("MEMORY_SCORE_THRESHOLD")),
    )
    knowledge_memory = Mem0xMemory(config)

    return [knowledge_memory]
