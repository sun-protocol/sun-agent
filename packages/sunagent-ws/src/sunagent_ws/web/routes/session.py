import logging
from typing import Dict

from fastapi import APIRouter, Depends

from sunagent_ws.database import DatabaseManager
from sunagent_ws.datamodel import RowRunMessage
from sunagent_ws.web.config import LOGGER_NAME
from sunagent_ws.web.deps import get_db

router = APIRouter()
logger = logging.getLogger(LOGGER_NAME)


@router.get("/{session_id}/messages")
async def get_session_history(session_id: str, db: DatabaseManager = Depends(get_db)) -> Dict:
    logger.info(f"Get session history for session_id: {session_id}")
    response = db.get(RowRunMessage, filters={"session_id": session_id}, return_json=False)
    logger.info(f"Get session history for session_id: {session_id}, response: {response}")
    res = {"code": 0, "data": []}
    if response.status and response.data:
        res["data"] = response.data
    return res
