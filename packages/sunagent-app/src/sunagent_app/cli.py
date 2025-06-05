#!/usr/bin/env python
# coding=utf-8

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.messages import TextMessage
from autogen_core import CancellationToken
from autogen_ext.models.openai import OpenAIChatCompletionClient


async def test_model(user_input: str) -> str:
    agent = AssistantAgent("assistant", OpenAIChatCompletionClient(model="gemini-2.0-flash"))
    response = await agent.on_messages([TextMessage(content=user_input, source="user")], CancellationToken())
    assert isinstance(response.chat_message, TextMessage)
    return response.chat_message.content
