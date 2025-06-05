# defines how core data types in sunagentstudio are serialized and stored in the database

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlmodel import Column, DateTime, Field, SQLModel, func


class SessionStatus(str, Enum):
    CREATED = "created"
    FINISHED = "finished"
    ACTIVE = "active"
    STOPPED = "stopped"


class Session(SQLModel, table=True):
    __table_args__ = {"sqlite_autoincrement": True}
    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: Optional[str] = Field(default=None)
    created_at: datetime = Field(
        default_factory=datetime.now,
        sa_column=Column(DateTime(timezone=True), server_default=func.now()),
    )  # pylint: disable=not-callable
    updated_at: datetime = Field(
        default_factory=datetime.now,
        sa_column=Column(DateTime(timezone=True), onupdate=func.now()),
    )  # pylint: disable=not-callable
    user_id: Optional[str] = None
    status: SessionStatus = Field(default=None)


# 用户能见到的消息
class RowRunMessage(SQLModel, table=True):
    __table_args__ = {"sqlite_autoincrement": True}
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: Optional[str] = None
    session_id: Optional[str] = Field(default=None)
    source: Optional[str] = Field(default=None)
    content: Optional[str] = Field(default=None)
    created_at: datetime = Field(
        default_factory=datetime.now,
        sa_column=Column(DateTime(timezone=True), server_default=func.now()),
    )  #
