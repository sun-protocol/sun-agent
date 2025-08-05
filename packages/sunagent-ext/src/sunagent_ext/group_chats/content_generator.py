import asyncio
from typing import Sequence, List

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.conditions import SourceMatchTermination, TextMentionTermination
from autogen_agentchat.teams import RoundRobinGroupChat
from autogen_core import ComponentModel
from autogen_core.memory import Memory
from autogen_ext.models.openai import AzureOpenAIChatCompletionClient
from openai import BaseModel
from _prompts import CONTENT_GUARD_PROMPT,CONTENT_GENERATOR_PROMPT,FORMATTER_PROMPT,ORIGINAL_GUARD_PROMPT


class ContentGenerator(RoundRobinGroupChat):
    def __init__(
        self,
        model_client: AzureOpenAIChatCompletionClient,
        original_guard_prompt: str = ORIGINAL_GUARD_PROMPT,
        content_generator_prompt: str = CONTENT_GENERATOR_PROMPT,
        content_guard_prompt: str = CONTENT_GUARD_PROMPT,
        formatter_prompt: str = FORMATTER_PROMPT,
        memory: Sequence[Memory] | None = None,
    ):

        original_guard = AssistantAgent(
            name="original_content_guard",
            system_message=original_guard_prompt,
            model_client=model_client,
        )

        content_generator = AssistantAgent(
            name="content_generator",
            system_message=content_generator_prompt,
            model_client=model_client,
            memory=memory,
        )

        content_guard = AssistantAgent(
            name="content_guard",
            system_message=content_guard_prompt,
            model_client=model_client,
        )

        formatter = AssistantAgent(
            name="formatter",
            system_message=formatter_prompt,
            model_client=model_client,
        )

        super().__init__(
            participants=[original_guard, content_generator, content_guard, formatter],
            termination_condition=(
                SourceMatchTermination(["formatter"])
                | TextMentionTermination("EARLY_TERMINATE")
            ),
        )
