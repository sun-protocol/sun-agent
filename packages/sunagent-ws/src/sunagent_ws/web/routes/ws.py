import asyncio
import json
import logging

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from ...datamodel.db import SessionStatus
from ...datamodel.types import RowMessageType, WsExtraFieldsDto, WsMessageType
from ..config import LOGGER_NAME
from ..deps import get_websocket_manager
from ..managers import WebSocketManager

logger = logging.getLogger(LOGGER_NAME)

router = APIRouter()


@router.websocket("/agent/{agentContextId}")
async def run_websocket(
    websocket: WebSocket, agentContextId: str, ws_manager: WebSocketManager = Depends(get_websocket_manager)
):
    """WebSocket endpoint for run communication"""
    # Verify run exists and is in valid state
    # Connect websocket
    await websocket.accept()
    ws_message = None
    session_id = agentContextId
    await ws_manager.init_connect_session(websocket, session_id)
    ws_extra_fields_dto = None
    try:
        while True:
            try:
                text = await websocket.receive_text()
                if text == "PING":
                    await websocket.send_text("PONG")
                    continue
                logger.info(f"Received message: {text}")
                ws_message = json.loads(text)
                # ping pong

                ws_extra_fields_dto = WsExtraFieldsDto(**ws_message["wsExtraFieldsDto"])
                session_id = ws_extra_fields_dto.agentContextId
                await ws_manager.connect_session(websocket, ws_extra_fields_dto)
                if ws_message["content"]["type"] == RowMessageType.MESSAGE.value:
                    input_flag = ws_manager.get_input_flag(session_id)
                    if input_flag:
                        content = ws_message["content"]["data"]["content"]
                        logger.info(f"Received input response for session {session_id}")
                        await ws_manager.handle_input_response(session_id, content)
                    else:
                        if ws_manager.get_session_state(session_id) == SessionStatus.ACTIVE:
                            pass
                        else:
                            content = ws_message["content"]["data"]["content"]
                            logger.info(f"Received message for session {session_id}")
                            asyncio.create_task(ws_manager.run_stream(session_id, content))
                elif ws_message["content"]["type"] == RowMessageType.CANCEL.value:
                    # // 终止流程
                    logger.info(f"Received stop request for session {session_id}")
                    reason = "User requested stop/cancellation"
                    await ws_manager.stop_run(session_id, reason=reason)
                    break
                else:
                    logger.warning(f"Invalid message type: {ws_message["content"]["type"]}")
            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON received: {ws_message}")
                await send_error_message(websocket, "Invalid message format")
    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for run {session_id}")
        await send_error_message(websocket, "webSocket disconnected")
    except Exception as e:
        await send_error_message(websocket, str(e))
        logger.error(f"WebSocket error: {str(e)}", e)
    finally:
        await ws_manager.disconnect(session_id)


async def send_error_message(websocket: WebSocket, error_message: str):
    try:
        ms = {
            "messageType": WsMessageType.SUN_AGENT.value,
            "content": {
                "code": 500,
                "msg": f"error {error_message}",
            },
            "wsExtraFieldsDto": None,
        }
        await websocket.send_json(ms)
        logger.error(f"Error message sent: {error_message}")
        return True
    except Exception as e:
        logger.error(f"WebSocket error: {str(e)}", e)
