from typing import Any, List

import aiohttp
from autogen_core import CancellationToken, Component
from autogen_core.memory import Memory, MemoryContent, MemoryQueryResult, UpdateContextResult
from autogen_core.model_context import ChatCompletionContext
from autogen_core.models import SystemMessage
from pydantic import BaseModel, Field
from typing_extensions import Self

from sunagent_ext.memory._base_memory import ContextMemory


class Mem0xMemoryConfig(BaseModel):
    """Configuration for Mem0xMemory component."""

    name: str = Field(default="default_mem0x_memory", description="Name of the Memory")

    url: str = Field(default="http://localhost:19527", description="url of Mem0x Server")

    header: str = Field(default="", description="header of memory")

    table_name: str = Field(default="default", description="table name for record memory")

    user_id: str | None = Field(default=None, description="the user id for record memory")

    agent_id: str | None = Field(default="default_agent", description="the agent id for record memory")

    run_id: str | None = Field(default=None, description="the run id for record memory")

    limit: int = Field(default=100, description="the max return items")

    score_threshold: float | None = Field(default=None, description="the max item score")


class Mem0xMemory(Memory, Component[Mem0xMemoryConfig]):
    """Mem0x memory implementation

    This memory implementation stores contents in mem0x server.
    It has an `update_context` method that updates model contexts
    by appending all stored memories.

    The memory content can be directly accessed and modified through mem0x server api,
    allowing external applications to manage memory contents directly.

    Args:
        name: Optional identifier for this memory instance

    """

    component_type = "memory"
    component_provider_override = "sunagent_ext.memory.Mem0xMemory"
    component_config_schema = Mem0xMemoryConfig

    def __init__(self, config: Mem0xMemoryConfig | None = None) -> None:
        self._config = config if config else Mem0xMemoryConfig()

    @property
    def name(self) -> str:
        """Get the memory instance identifier.

        Returns:
            str: Memory instance name
        """
        return self._config.name

    def set_run_id(self, run_id: str) -> None:
        """Set the run id

        Returns:
           None
        """
        self._config.run_id = run_id

    def set_user_id(self, user_id: str) -> None:
        """Set the user id

        Returns:
          None
        """
        self._config.user_id = user_id

    async def update_context(
        self,
        model_context: ChatCompletionContext,
    ) -> UpdateContextResult:
        """Update the model context by appending memory content.

        This method mutates the provided model_context by adding all memories as a
        SystemMessage.

        Args:
            model_context: The context to update. Will be mutated if memories exist.

        Returns:
            UpdateContextResult containing the memories that were added to the context
        """
        query_result = MemoryQueryResult(results=[])

        contents: List[str] = []
        for msg in await model_context.get_messages():
            # knowledge of system message is static, don't need query
            if not isinstance(msg, SystemMessage):
                contents.append(str(msg.content))

        memory_content = MemoryContent(content="\n".join(contents), mime_type="text/plain")
        query_result = await self.query(query=memory_content)

        memory_strings = [f"{i}. {str(memory.content)}" for i, memory in enumerate(query_result.results, 1)]
        if memory_strings:
            memory_context = "\n" + self._config.header + ":\n" + "\n".join(memory_strings) + "\n"
            await model_context.add_message(SystemMessage(content=memory_context))

        return UpdateContextResult(memories=query_result)

    async def query(
        self,
        query: str | MemoryContent = "",
        cancellation_token: CancellationToken | None = None,
        **kwargs: Any,
    ) -> MemoryQueryResult:
        """Return all memories without any filtering.

        Args:
            query: Ignored in this implementation
            cancellation_token: Optional token to cancel operation
            **kwargs: Additional parameters (ignored)

        Returns:
            MemoryQueryResult containing all stored memories
        """
        payload = {
            "query": query.content if isinstance(query, MemoryContent) else query,
            "user_id": self._config.user_id,
            "agent_id": self._config.agent_id,
            "table_name": self._config.table_name,
            "run_id": self._config.run_id,
            "limit": self._config.limit,
        }
        url = self._config.url + "/longterm_search"

        results: List[MemoryContent] = []
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, headers={"Content-Type": "application/json"}) as resp:
                    response_data = await resp.json()
                    if response_data["status"]:
                        for item in response_data["data"]["results"]:
                            ### Facts
                            if self._config.score_threshold and item["score"] > self._config.score_threshold:
                                continue
                            results.append(MemoryContent(content=item["memory"], mime_type="text/plain"))
        except Exception:
            pass

        return MemoryQueryResult(results=results)

    async def add(self, content: MemoryContent, cancellation_token: CancellationToken | None = None) -> None:
        """Add new content to memory.

        Args:
            content: Memory content to store
            cancellation_token: Optional token to cancel operation
        """
        pass

    async def clear(self) -> None:
        """Clear all memory content"""
        pass

    async def close(self) -> None:
        """Cleanup resources if needed."""
        pass

    @classmethod
    def _from_config(cls, config: Mem0xMemoryConfig) -> Self:
        return cls(config)

    def _to_config(self) -> Mem0xMemoryConfig:
        return self._config


class Mem0xContextMemoryConfig(BaseModel):
    """Configuration for Mem0xMemory component."""

    name: str = Field(default="default_mem0x_context_memory", description="Name of the Memory")

    url: str = Field(default="http://localhost:19527", description="url of Mem0x Server")

    header: str = Field(default="", description="header of memory")

    table_name: str = Field(default="default", description="table name for record memory")


class Mem0xContextMemory(ContextMemory, Component[Mem0xContextMemoryConfig]):
    """Mem0x context memory implementation"""

    component_type = "context_memory"
    component_provider_override = "sunagent_ext.memory.Mem0xContextMemory"
    component_config_schema = Mem0xContextMemoryConfig

    def __init__(self, config: Mem0xContextMemoryConfig | None = None) -> None:
        self._config = config if config else Mem0xContextMemoryConfig()

    @property
    def name(self) -> str:
        """Get the memory instance identifier.

        Returns:
            str: Memory instance name
        """
        return self._config.name

    async def add(
        self,
        user_id: str | None = None,
        agent_id: str | None = None,
        run_id: str | None = None,
        content: MemoryContent | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> None:
        """
        Add a new content to memory.

        Args:
            user_id: The user id
            agent_id: The agent id
            run_id: The run id
            content: The memory content to add
            cancellation_token: Optional token to cancel operation
        """
        if content is None:
            return

        payload = {
            "content": {"text": content.content},
            "user_id": user_id,
            "agent_id": agent_id,
            "run_id": run_id,
            "table_name": self._config.table_name,
        }
        url = self._config.url + "/shortterm_add"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, headers={"Content-Type": "application/json"}) as resp:
                    await resp.json()
        except Exception:
            pass

    async def query(
        self,
        user_id: str | None = None,
        agent_id: str | None = None,
        run_id: str | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> str:
        """
        Query the memory store and return context memory.

        Args:
            query: Query content item
            user_id: The user id
            agent_id: The agent id
            run_id: The run id
            cancellation_token: Optional token to cancel operation

        Returns:
            str Return the context memory
        """

        payload = payload = {
            "run_id": run_id,
            "table_name": self._config.table_name,
        }
        url = self._config.url + "/shortterm_get"

        results: List[MemoryContent] = []
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, headers={"Content-Type": "application/json"}) as resp:
                    response_data = await resp.json()
                    if response_data["status"]:
                        for item in response_data["data"]["results"]:
                            ### Conversation List
                            chat_text = item["user_id"] + ": " + item["content"]["text"]
                            results.append(MemoryContent(content=chat_text, mime_type="text/plain"))
        except Exception:
            pass

        memory_strings = [f"{i} {str(memory.content)}" for i, memory in enumerate(results, 1)]
        if memory_strings:
            return "\n" + self._config.header + ":\n" + "\n".join(memory_strings) + "\n"
        return "\n" + self._config.header + ":\n" + "\n"

    async def clear(self) -> None:
        """Clear all entries from memory."""
        pass

    async def close(self) -> None:
        """Clean up any resources used by the memory implementation."""
        pass

    @classmethod
    def _from_config(cls, config: Mem0xContextMemoryConfig) -> Self:
        return cls(config)

    def _to_config(self) -> Mem0xContextMemoryConfig:
        return self._config
