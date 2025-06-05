# from dataclasses import Field
import enum
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from autogen_agentchat.base import TaskResult
from autogen_agentchat.messages import BaseChatMessage
from pydantic import BaseModel, Field


class MessageConfig(BaseModel):
    source: str
    content: str
    message_type: Optional[str] = "text"


class TeamResult(BaseModel):
    task_result: TaskResult
    usage: str
    duration: float


class RowMessageType(enum.Enum):
    MESSAGE = "message"
    CANCEL = "cancel"


class MessageSource(enum.Enum):
    USER = "user"
    AGENT = "agent"


class MessageType(enum.Enum):
    FUNCTION = "Function"
    TEXT = "Text"


class Message(BaseModel):
    source: str
    content: str
    type: MessageType
    datetime: float = Field(default_factory=lambda: datetime.now(timezone.utc).timestamp())


class RowMessage(BaseModel):
    code: int
    msg: str
    data: Optional[Message] = None
    type: RowMessageType = RowMessageType.MESSAGE


class WsMessageType(enum.Enum):
    SUN_AGENT = "sunagentMsg"


class LLMCallEventMessage(BaseChatMessage):
    source: str = "llm_call_event"
    content: str


class WsExtraFieldsDto(BaseModel):
    agentContextId: str
    userAddress: str
    ip: str = ""


class WsMessage(BaseModel):
    messageType: WsMessageType = WsMessageType.SUN_AGENT
    content: RowMessage
    wsExtraFieldsDto: Optional[WsExtraFieldsDto]


class Response(BaseModel):
    message: str
    status: bool
    data: Optional[Any] = None
