import asyncio
from typing import List, Optional, Sequence

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.conditions import SourceMatchTermination, TextMentionTermination
from autogen_agentchat.teams import RoundRobinGroupChat
from autogen_core.memory import Memory
from autogen_ext.models.openai import AzureOpenAIChatCompletionClient

from sunagent_ext.group_chats._prompts import (
    CONTENT_GENERATOR_PROMPT,
    CONTENT_GUARD_PROMPT,
    FORMATTER_PROMPT,
    ORIGINAL_GUARD_PROMPT,
)


class ContentGenerator(RoundRobinGroupChat):
    """
    Example:
        cg = ContentGenerator(
            model_client=my_client,
            agent_list=["original_guard", "content_generator", "content_guard", "formatter"],
            prompts={
                "original_guard": "...",  # optional
                "content_guard": "...",
                "content_generator": "...",
                "formatter": "...",
            }
        )
    """

    def __init__(
        self,
        model_client: AzureOpenAIChatCompletionClient,
        agent_list: List[str],  # 必填：选择要启用的 agent 名称
        prompts: Optional[dict] = None,  # 外部 prompt 字典
        memory: Optional[Sequence[Memory]] = None,
    ):
        # 默认 prompt 兜底

        _prompts = {
            "original_guard": (prompts.get("original_guard") or ORIGINAL_GUARD_PROMPT),
            "content_guard": (prompts.get("content_guard") or CONTENT_GENERATOR_PROMPT),
            "content_generator": (prompts.get("content_generator") or CONTENT_GUARD_PROMPT),
            "formatter": (prompts.get("formatter") or FORMATTER_PROMPT),
        }
        # 根据 agent_list 动态创建 participant
        available_agents = {
            "original_guard": AssistantAgent(
                name="original_guard",
                system_message=_prompts["original_guard"],
                model_client=model_client,
            ),
            "content_generator": AssistantAgent(
                name="content_generator",
                system_message=_prompts["content_generator"],
                model_client=model_client,
                memory=memory,
            ),
            "content_guard": AssistantAgent(
                name="content_guard",
                system_message=_prompts["content_guard"],
                model_client=model_client,
            ),
            "formatter": AssistantAgent(
                name="formatter",
                system_message=_prompts["formatter"],
                model_client=model_client,
            ),
        }

        participants = [available_agents[name] for name in agent_list]

        super().__init__(
            participants=participants,
            termination_condition=(SourceMatchTermination(["formatter"]) | TextMentionTermination("EARLY_TERMINATE")),
        )
