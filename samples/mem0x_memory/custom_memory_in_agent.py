import asyncio
import os

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.base import TaskResult
from autogen_core.memory import MemoryContent
from autogen_ext.models.openai import AzureOpenAIChatCompletionClient
from dotenv import load_dotenv
from sunagent_ext.memory import Mem0xMemory, Mem0xMemoryConfig, ProfileListMemory, ProfileListMemoryConfig

# 加载 .env 文件
load_dotenv()  # 默认加载当前目录下的 .env 文件


def get_profile_memory():
    config = ProfileListMemoryConfig(
        name="profile_memory",
        header="about_demo",
        memory_contents=[
            MemoryContent(
                content="today is a good day",
                mime_type="text/plain",
            ),
            MemoryContent(
                content="your name is sunAgent",
                mime_type="text/plain",
            ),
        ],
    )
    memory = ProfileListMemory._from_config(config)
    return [memory]


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


llm_client = AzureOpenAIChatCompletionClient(
    model=os.getenv("OPENAI_MODEL"),
    azure_deployment=os.getenv("OPENAI_DEPLOYMENT"),
    api_version=os.getenv("OPENAI_API_VERSION"),
    azure_endpoint=os.getenv("OPENAI_ENDPOINT"),
    api_key=os.getenv("OPENAI_API_KEY"),
    temperature=0,
)

agent = AssistantAgent(
    name="demo",
    description="demo for mem0x memory",
    system_message="reply with your memory",
    model_client=llm_client,
    memory=get_profile_memory(),
    # memory=get_knowledge_memory(),
    reflect_on_tool_use=True,
)


