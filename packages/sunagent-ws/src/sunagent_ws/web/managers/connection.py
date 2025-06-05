import asyncio
import json
import logging
import re
import traceback
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from autogen_agentchat.messages import ChatMessage, TextMessage
from autogen_core import CancellationToken
from fastapi import WebSocket, WebSocketDisconnect

from ...database import DatabaseManager
from ...datamodel import (
    RowMessage,
    TeamResult,
)
from ...datamodel.db import RowRunMessage, Session, SessionStatus
from ...datamodel.types import Message, MessageSource, MessageType, RowMessageType, WsExtraFieldsDto, WsMessageType
from ...teammanager import TeamManager
from ..config import LOGGER_NAME

logger = logging.getLogger(LOGGER_NAME)


def convert_result(res: str):
    pattern = r"```json\n(.*?)```"
    match = re.search(pattern, res, re.DOTALL)
    if match:
        json_str = match.group(1)
        dic = json.loads(json_str)
        if "data" in dic:
            if "tokens" in dic["data"]:
                tokens = dic["data"]["tokens"]
                dic["data"] = tokens
        return dic
    else:
        try:
            dic = json.loads(res)
            if "data" in dic:
                if "tokens" in dic["data"]:
                    tokens = dic["data"]["tokens"]
                    dic["data"] = tokens
            return dic
        except Exception as e:
            logger.error(f"cant parse llm result  {e}")
            return {"name": "chat", "text": res}


class WebSocketManager:
    """Manages WebSocket connections and message streaming for team task execution"""

    def __init__(self, db_manager: DatabaseManager, team_manager: TeamManager):
        self.db_manager = db_manager
        self.team_manager = team_manager
        self._connections: Dict[str, WebSocket] = {}
        self._connections_extras: Dict[str, WsExtraFieldsDto] = {}
        self._cancellation_tokens: Dict[str, CancellationToken] = {}
        # Track explicitly closed connections
        self._closed_connections: set[str] = set()
        self._input_responses: Dict[str, asyncio.Queue] = {}
        self._input_response_flags: Dict[str, bool] = {}
        self._sessions: Dict[str, Session] = {}
        self._chat_messages: Dict[str, List[Message]] = {}

    def _get_stop_message(self, reason: str):
        return

    def get_input_flag(self, session_id: str):
        return self._input_response_flags[session_id]

    async def init_connect_session(self, websocket: WebSocket, session_id: str):
        self._connections[session_id] = websocket
        self._closed_connections.discard(session_id)
        self._closed_connections.discard(session_id)
        # # Initialize input queue for this connection

    async def connect_session(self, websocket: WebSocket, wsExtraFieldsDto: WsExtraFieldsDto) -> bool:
        session_id = wsExtraFieldsDto.agentContextId
        try:
            self._input_responses[session_id] = asyncio.Queue()
            self._input_response_flags[session_id] = False
            self._connections_extras[session_id] = wsExtraFieldsDto
            self._chat_messages[session_id] = []
            if session_id not in self._sessions:
                session = Session(
                    session_id=session_id, status=SessionStatus.CREATED, user_id=wsExtraFieldsDto.userAddress
                )
                self._sessions[session_id] = session
                self.db_manager.upsert(session)
            self._connections[session_id] = websocket
            return True
        except Exception as e:
            logger.error(f"Connection error for run {session_id}: {e}", e)
            return False

    def get_session_state(self, session_id) -> SessionStatus:
        return self._sessions[session_id].status

    async def run_stream(self, session_id: str, task: str) -> None:
        """Start streaming task execution with proper run management"""
        if session_id not in self._connections or session_id in self._closed_connections:
            raise ValueError(f"No active connection for run {session_id}")
        if session_id in self._cancellation_tokens:
            # 终止上一个session
            self._cancellation_tokens[session_id].cancel()
        cancellation_token = CancellationToken()
        self._cancellation_tokens[session_id] = cancellation_token
        final_result = None
        history: List[ChatMessage] = self._get_history_message(session_id)
        new_message = TextMessage(content=task, source="user")
        history.append(new_message)
        logger.info(f"Starting stream for history {history}")
        self._chat_messages[session_id].append(Message(source="user", type=MessageType.TEXT, content=task))
        # 更新session
        session = self._sessions[session_id]
        session.status = SessionStatus.ACTIVE
        session.updated_at = datetime.now(timezone.utc)

        start = datetime.now()
        try:
            s1 = datetime.now()
            async for message in self.team_manager.run_stream(
                session_id=session_id,
                transfer_func=self.create_transfer_input_func(session_id),
                task=history,
                cancellation_token=cancellation_token,
            ):
                logger.info(f"spend for {datetime.now() - s1} message {message}")
                s1 = datetime.now()
                if cancellation_token.is_cancelled() or session_id in self._closed_connections:
                    logger.info(f"Stream cancelled or connection closed for run {session_id}")
                    break
                    # Capture final result if it's a TeamResult
                if isinstance(message, TeamResult):
                    final_result = message
                    logger.info(f"Final result captured for run {session_id}: {final_result}")
            if not cancellation_token.is_cancelled() and session_id not in self._closed_connections:
                if final_result:
                    logger.info(f"Sending final result for run {session_id}: {final_result}")
                    last_message = final_result.task_result.messages[-1]
                    ms = convert_result(last_message.content)
                    # 兼容处理
                    if ms["name"] != "chat" and ms["name"] != "transfer":
                        ms["name"] = "market"
                    message = Message(
                        source=last_message.source,
                        type=MessageType.TEXT,
                        content=json.dumps(ms, ensure_ascii=False),
                    )
                    self._chat_messages[session_id].append(message)
                    row_message = RowMessage(
                        code=0,
                        msg="success",
                        data=message,
                    )
                    await self._send_message(session_id, row_message)
                    # 调试时取消历史消息内容
                    await self.save_messages(session_id)
                    session = self._sessions[session_id]
                    session.status = SessionStatus.FINISHED
                    session.updated_at = datetime.now(timezone.utc)
                    self.db_manager.upsert(session)
                else:
                    logger.warning(f"No final result captured for completed run {session_id}")
                    logger.info(f"Sending final result for run {session_id}: {final_result}")
                    pass
            else:
                logger.warning(f"No final result captured for completed run {session_id}")
                logger.info(f"Sending final result for run {session_id}: {final_result}")
                pass

        except Exception as e:
            logger.error(f"Stream error for run {session_id}: {e}")
            traceback.print_exc()
            await self._handle_stream_error(session_id, e)
        finally:
            self._cancellation_tokens.pop(session_id, None)
            logger.info(f"spend time {datetime.now() - start} for query {task}")

    def _get_history_message(self, session_id: str) -> List[ChatMessage]:
        """
        获取会话历史消息
        """
        history = []
        response = self.db_manager.get(RowRunMessage, filters={"session_id": session_id}, return_json=False)
        if response.status and response.data:
            for message in response.data:
                if message.source == "user":
                    history.append(TextMessage(source=message.source, content=message.content))
                else:
                    history.append(TextMessage(source="assistant", content=message.content))
        history = list(reversed(history))
        # 只要最后四条
        if len(history) > 4:
            history = history[-4:]
        return history

    def create_transfer_input_func(self, session_id: str) -> Callable:
        """Creates an input function for a specific run"""

        async def input_handler(prompt: str = "", cancellation_token: Optional[CancellationToken] = None) -> str:
            try:
                logger.info(f"Handling input for session {session_id}: {prompt}")
                message = Message(content=prompt, source="transfer", type=MessageType.FUNCTION)
                row_message = RowMessage(code=0, msg="", data=message)
                # Send input request to client
                await self._send_message(
                    session_id,
                    row_message,
                )
                self._input_response_flags[session_id] = True
                logger.info(
                    f"Waiting for input response for session {session_id}。{self._input_response_flags[session_id]}"
                )
                # Wait for response
                if session_id in self._input_responses:
                    logger.info(
                        f"Waiting for input response for session {session_id}。{self._input_response_flags[session_id]}"
                    )
                    response = await self._input_responses[session_id].get()
                    return response
                else:
                    raise ValueError(f"No input queue for session {session_id}")
            except Exception as e:
                logger.error(f"Error handling input for session {session_id}: {e}")
                raise

        return input_handler

    async def handle_input_response(self, session_id: str, response: str) -> None:
        """Handle input response from client"""
        logger.info(f"Handling input response for session {session_id}: {response}")
        self._input_response_flags[session_id] = False
        if session_id in self._input_responses:
            await self._input_responses[session_id].put(response)
        else:
            logger.warning(f"Received input response for inactive run {session_id}")

    async def stop_run(self, session_id: str, reason: str) -> None:
        if session_id in self._cancellation_tokens:
            try:
                # Finally cancel the token
                self._cancellation_tokens[session_id].cancel()
                self._input_response_flags[session_id] = False
                self._chat_messages.pop(session_id, [])
                await self.team_manager.stop_team(session_id)
                session = self._sessions[session_id]
                session.status = SessionStatus.STOPPED
                session.updated_at = datetime.now(timezone.utc)
                self.db_manager.upsert(session)

            except Exception as e:
                logger.error(f"Error stopping session {session_id}: {e}")
                # We might want to force disconnect here if db update failed
                await self.disconnect(session_id)  # Optional

    async def disconnect(self, session_id: str) -> None:
        # Mark as closed before cleanup to prevent any new messages
        await self.stop_run(session_id, "Connection closed")
        self._closed_connections.add(session_id)
        self._connections.pop(session_id, None)
        self._cancellation_tokens.pop(session_id, None)
        self._input_responses.pop(session_id, None)
        self._input_response_flags.pop(session_id, None)
        self._chat_messages.pop(session_id, None)
        self._connections_extras.pop(session_id, None)
        self._sessions.pop(session_id, None)
        logger.info(f"Disconnecting session {session_id}")
        # Cancel any running tasks

    async def _send_message(self, session_id: str, message: RowMessage) -> None:
        """Send a message through the WebSocket with connection state checking

        Args:
            run_id: id of the run
            message: Message dictionary to send
        """
        if session_id in self._closed_connections:
            logger.warning(f"Attempted to send message to closed connection for session {session_id}")
            return
        ws_extra_fields_dto = self._connections_extras[session_id]
        data = {}
        if message.data:
            data = {"source": message.data.source, "content": message.data.content, "text": message.data.type.value}
        ms = {
            "messageType": WsMessageType.SUN_AGENT.value,
            "content": {"code": 0, "msg": "success", "type": RowMessageType.MESSAGE.value, "data": data},
            "wsExtraFieldsDto": ws_extra_fields_dto.model_dump(),
        }

        try:
            if session_id in self._connections:
                websocket = self._connections[session_id]
                await websocket.send_json(ms)
        except WebSocketDisconnect as e:
            logger.warning(f"WebSocket disconnected while sending message for run {session_id}", e)
            await self.disconnect(session_id)
        except Exception as e:
            logger.error(f"Error sending message for run {session_id}: {e}", e)
            await self.disconnect(session_id)

    async def save_messages(self, session_id: str):
        extras = self._connections_extras[session_id]
        logger.info(f"Saving messages for session {session_id}")
        for message in self._chat_messages[session_id]:
            message = RowRunMessage(
                user_id=extras.userAddress,
                session_id=session_id,
                source=message.source,
                content=message.content,
                created_at=datetime.fromtimestamp(message.datetime),
            )
            self.db_manager.upsert(message)
        self._chat_messages[session_id] = []

    async def _handle_stream_error(self, session_id: str, error: Exception) -> None:
        """Handle stream errors with proper run updates"""
        if session_id not in self._closed_connections:
            err = RowMessage(code=500, msg=f"An error occurred {error}")
            await self._send_message(
                session_id,
                err,
            )
            await self.team_manager.stop_team(session_id)

    @property
    def active_connections(self) -> set[str]:
        """Get set of active run IDs"""
        return set(self._connections.keys()) - self._closed_connections
