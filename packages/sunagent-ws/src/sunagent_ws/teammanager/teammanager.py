import asyncio
import datetime
import logging
import os
import time
from typing import Any, AsyncGenerator, Awaitable, Callable, List, Optional, Sequence, Union

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.base import TaskResult
from autogen_agentchat.conditions import SourceMatchTermination, TextMentionTermination
from autogen_agentchat.messages import AgentEvent, ChatMessage
from autogen_agentchat.teams import BaseGroupChat
from autogen_core import CancellationToken
from autogen_core.logging import LLMCallEvent
from autogen_core.tools import BaseTool
from autogen_ext.models.openai import AzureOpenAIChatCompletionClient, OpenAIChatCompletionClient
from templates.agent_template import CHAT_TEMPLATE, FORMAT_TEMPLATE, PUMP_TEMPLATE, SELECT_PROMPT

from ..agent._select_sunagent_group_chat import SelectorSunagentGroupChat
from ..agent.transfer_agent import TransferAgent
from ..datamodel.types import LLMCallEventMessage, TeamResult
from ..memory._profile_memory import get_sunagent_profile_memory
from ..web.config import LOGGER_NAME

logger = logging.getLogger(LOGGER_NAME)


class RunEventLogger(logging.Handler):
    """Event logger that queues LLMCallEvents for streaming"""

    def __init__(self):
        super().__init__()
        self.events = asyncio.Queue()

    def emit(self, record: logging.LogRecord):
        if isinstance(record.msg, LLMCallEvent):
            self.events.put_nowait(LLMCallEventMessage(content=str(record.msg)))


class TeamManager:
    """Manages team operations including loading configs and running teams"""

    def __init__(
        self,
        pump_tools: List[BaseTool[Any, Any] | Callable[..., Any] | Callable[..., Awaitable[Any]]] | None = None,
    ):
        self.teams: dict[str, BaseGroupChat] = {}
        self.pump_tools = pump_tools
        self.model_client_g = OpenAIChatCompletionClient(
            model="gemini-2.0-flash",
            api_key=os.getenv("GOOGLE_GEMINI_API_KEY"),
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            model_info={
                "vision": True,
                "function_calling": True,
                "json_output": True,
            },
            temperature=0,
        )

        self.model_client = AzureOpenAIChatCompletionClient(
            model=os.getenv("OPENAI_MODEL"),
            azure_deployment=os.getenv("OPENAI_DEPLOYMENT"),
            api_version=os.getenv("OPENAI_API_VERSION"),
            azure_endpoint=os.getenv("OPENAI_ENDPOINT"),
            api_key=os.getenv("OPENAI_API_KEY"),
            temperature=0,
        )

    async def _create_team(
        self,
        transfer_func: Optional[Callable],
    ) -> BaseGroupChat:
        # Load env vars into environment if provided
        # 交易agent
        transfer_agent = TransferAgent(
            name="transfer", model_client=self.model_client, start_transfer_func=transfer_func
        )
        chat_agent = AssistantAgent(
            name="chat",
            description=CHAT_TEMPLATE["description"],
            system_message=CHAT_TEMPLATE["prompt"],
            model_client=self.model_client,
            memory=get_sunagent_profile_memory(),
        )
        format_agent = AssistantAgent(
            name="format",
            description=FORMAT_TEMPLATE["description"],
            system_message=FORMAT_TEMPLATE["prompt"],
            model_client=self.model_client_g,
        )
        # pump agent
        # tools = await create_tools()
        pump_agent = AssistantAgent(
            name="pump",
            description=PUMP_TEMPLATE["description"],
            system_message=PUMP_TEMPLATE["prompt"],
            tools=self.pump_tools,
            model_client=self.model_client,
        )

        termination_condition = SourceMatchTermination(["transfer", "format", "chat"]) | TextMentionTermination(
            "TERMINATE"
        )
        team: BaseGroupChat = SelectorSunagentGroupChat(
            participants=[
                chat_agent,
                transfer_agent,
                pump_agent,
                format_agent,
            ],
            model_client=self.model_client,
            termination_condition=termination_condition,
        )
        return team

    async def run_stream(
        self,
        session_id: str,
        transfer_func: Optional[Callable],
        task: str | ChatMessage | Sequence[ChatMessage] | None = None,
        cancellation_token: Optional[CancellationToken] = None,
    ) -> AsyncGenerator[Union[AgentEvent | ChatMessage | LLMCallEvent, ChatMessage, TeamResult], None]:
        """Stream team execution results"""
        start_time = time.time()
        # Setup logger correctly
        llm_event_logger = RunEventLogger()
        team: BaseGroupChat = self.teams.get(session_id, None)
        # 若果已经有team 则不再创建
        logger.info(f"Starting team session {session_id}  task {task}")
        s1 = datetime.datetime.now()
        logger.info(f"mem0x spend : {datetime.datetime.now() - s1}")
        if not team:
            team = await self._create_team(transfer_func)
        else:
            await team.reset()
            logger.info("reset team status")
        try:
            logger.info(f"Starting team run {session_id}  task {task}")
            async for message in team.run_stream(task=task, cancellation_token=cancellation_token):
                if cancellation_token and cancellation_token.is_cancelled():
                    break
                if isinstance(message, TaskResult):
                    yield TeamResult(task_result=message, usage="", duration=time.time() - start_time)
                else:
                    yield message
                # Check for any LLM events
                while not llm_event_logger.events.empty():
                    event = await llm_event_logger.events.get()
                    yield event
        finally:
            # Cleanup - remove our handler
            if llm_event_logger in logger.handlers:
                logger.handlers.remove(llm_event_logger)

            # Ensure cleanup happens
            if team and hasattr(team, "_participants"):
                for agent in team._participants:
                    if hasattr(agent, "close"):
                        await agent.close()

    async def stop_team(self, session_id: str):
        team = self.teams[session_id]
        if team:
            await team.reset()
