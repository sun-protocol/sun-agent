from typing import Any, List

from autogen_core import CancellationToken, Component
from autogen_core.memory import Memory, MemoryContent, MemoryQueryResult, UpdateContextResult
from autogen_core.model_context import ChatCompletionContext
from autogen_core.models import SystemMessage
from pydantic import BaseModel
from typing_extensions import Self


class ProfileListMemoryConfig(BaseModel):
    """Configuration for ListMemory component."""

    name: str | None = None
    """Optional identifier for this memory instance."""
    header: str | None = None
    """Header of Profile"""
    memory_contents: List[MemoryContent] = []
    """List of memory contents stored in this memory instance."""


class ProfileListMemory(Memory, Component[ProfileListMemoryConfig]):
    """Simple profile list-based memory implementation.
    Args:
        name: Optional identifier for this memory instance

    """

    component_type = "memory"
    component_provider_override = "sunagent_core.memory.ListMemory"
    component_config_schema = ProfileListMemoryConfig

    def __init__(
        self, name: str | None = None, header: str | None = None, memory_contents: List[MemoryContent] | None = None
    ) -> None:
        self._name = name or "default_list_memory"
        self._header = header or ""
        self._contents: List[MemoryContent] = memory_contents if memory_contents is not None else []

    @property
    def name(self) -> str:
        """Get the memory instance identifier.

        Returns:
            str: Memory instance name
        """
        return self._name

    @property
    def content(self) -> List[MemoryContent]:
        """Get the current memory contents.

        Returns:
            List[MemoryContent]: List of stored memory contents
        """
        return self._contents

    @content.setter
    def content(self, value: List[MemoryContent]) -> None:
        """Set the memory contents.

        Args:
            value: New list of memory contents to store
        """
        self._contents = value

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

        if not self._contents:
            return UpdateContextResult(memories=MemoryQueryResult(results=[]))

        memory_strings = [f"{i}. {str(memory.content)}" for i, memory in enumerate(self._contents, 1)]

        if memory_strings:
            memory_context = "\n" + self._header + ":\n" + "\n".join(memory_strings) + "\n"
            await model_context.add_message(SystemMessage(content=memory_context))

        return UpdateContextResult(memories=MemoryQueryResult(results=self._contents))

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
        _ = query, cancellation_token, kwargs
        return MemoryQueryResult(results=self._contents)

    async def add(self, content: MemoryContent, cancellation_token: CancellationToken | None = None) -> None:
        """Add new content to memory.

        Args:
            content: Memory content to store
            cancellation_token: Optional token to cancel operation
        """
        self._contents.append(content)

    async def clear(self) -> None:
        """Clear all memory content."""
        self._contents = []

    async def close(self) -> None:
        """Cleanup resources if needed."""
        pass

    @classmethod
    def _from_config(cls, config: ProfileListMemoryConfig) -> Self:
        return cls(name=config.name, header=config.header, memory_contents=config.memory_contents)

    def _to_config(self) -> ProfileListMemoryConfig:
        return ProfileListMemoryConfig(name=self.name, header=self._header, memory_contents=self._contents)
