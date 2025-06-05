from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Dict, List, Union

from autogen_core import CancellationToken, ComponentBase
from autogen_core._image import Image
from autogen_core.memory import MemoryContent
from autogen_core.model_context import ChatCompletionContext
from pydantic import BaseModel, ConfigDict, field_serializer


class MemoryMimeType(Enum):
    """Supported MIME types for memory content."""

    TEXT = "text/plain"
    JSON = "application/json"
    MARKDOWN = "text/markdown"
    IMAGE = "image/*"
    BINARY = "application/octet-stream"


ContentType = Union[str, bytes, Dict[str, Any], Image]


class MemoryQueryResult(BaseModel):
    """Result of a memory :meth:`~sunagent_core.memory.Memory.query` operation."""

    results: List[MemoryContent]


class UpdateContextResult(BaseModel):
    """Result of a memory :meth:`~sunagent_core.memory.Memory.update_context` operation."""

    memories: MemoryQueryResult


class Memory(ABC, ComponentBase[BaseModel]):
    """Protocol defining the interface for memory implementations.

    A memory is the storage for data that can be used to enrich or modify the model context.

    A memory implementation can use any storage mechanism, such as a list, a database, or a file system.
    It can also use any retrieval mechanism, such as vector search or text search.
    It is up to the implementation to decide how to store and retrieve data.

    It is also a memory implementation's responsibility to update the model context
    with relevant memory content based on the current model context and querying the memory store.

    See :class:`~sunagent_core.memory.ListMemory` for an example implementation.
    """

    component_type = "memory"

    @abstractmethod
    async def update_context(
        self,
        model_context: ChatCompletionContext,
    ) -> UpdateContextResult:
        """
        Update the provided model context using relevant memory content.

        Args:
            model_context: The context to update.

        Returns:
            UpdateContextResult containing relevant memories
        """
        ...

    @abstractmethod
    async def query(
        self,
        query: str | MemoryContent,
        cancellation_token: CancellationToken | None = None,
        **kwargs: Any,
    ) -> MemoryQueryResult:
        """
        Query the memory store and return relevant entries.

        Args:
            query: Query content item
            cancellation_token: Optional token to cancel operation
            **kwargs: Additional implementation-specific parameters

        Returns:
            MemoryQueryResult containing memory entries with relevance scores
        """
        ...

    @abstractmethod
    async def add(self, content: MemoryContent, cancellation_token: CancellationToken | None = None) -> None:
        """
        Add a new content to memory.

        Args:
            content: The memory content to add
            cancellation_token: Optional token to cancel operation
        """
        ...

    @abstractmethod
    async def clear(self) -> None:
        """Clear all entries from memory."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Clean up any resources used by the memory implementation."""
        ...


class ContextMemory(ABC, ComponentBase[BaseModel]):
    """Protocol defining the interface for context memory implementations."""

    @abstractmethod
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
        ...

    @abstractmethod
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
        ...

    @abstractmethod
    async def clear(self) -> None:
        """Clear all entries from memory."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Clean up any resources used by the memory implementation."""
        ...
