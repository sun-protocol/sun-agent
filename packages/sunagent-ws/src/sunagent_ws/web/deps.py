# api/deps.py
import logging
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

import aiofiles
import yaml
from fastapi import Depends, HTTPException, status

from ..agent.pump_tools import create_tools
from ..database import DatabaseManager
from ..teammanager import TeamManager
from .config import LOGGER_NAME, settings
from .managers.connection import WebSocketManager

logger = logging.getLogger(LOGGER_NAME)

# Global manager instances
_db_manager: Optional[DatabaseManager] = None
_websocket_manager: Optional[WebSocketManager] = None
_team_manager: Optional[TeamManager] = None

# Context manager for database sessions


@contextmanager
def get_db_context():
    """Provide a transactional scope around a series of operations."""
    if not _db_manager:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database manager not initialized"
        )
    try:
        yield _db_manager
    except Exception as e:
        logger.error(f"Database operation failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database operation failed"
        ) from e


# Dependency providers


async def get_db() -> DatabaseManager:
    """Dependency provider for database manager"""
    if not _db_manager:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database manager not initialized"
        )
    return _db_manager


async def get_websocket_manager() -> WebSocketManager:
    """Dependency provider for connection manager"""
    if not _websocket_manager:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Connection manager not initialized"
        )
    return _websocket_manager


async def get_team_manager() -> TeamManager:
    """Dependency provider for team manager"""
    if not _team_manager:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Team manager not initialized")
    return _team_manager


# Authentication dependency

# Manager initialization and cleanup


async def init_managers(database_uri: str, app_root: str | Path) -> None:
    """Initialize all manager instances"""
    global _db_manager, _websocket_manager, _team_manager

    logger.info("Initializing managers...")

    try:
        # Initialize database manager
        logger.info(f"Initializing managers... {database_uri}")
        _db_manager = DatabaseManager(engine_uri=database_uri)
        _db_manager.initialize_database(auto_upgrade=settings.UPGRADE_DATABASE)
        logging_config = os.getenv("LOGGING_CONFIG", "logging_config.yaml")
        async with aiofiles.open(logging_config, "r") as f:
            content = await f.read()
            config = yaml.safe_load(content)
            logging.config.dictConfig(config)
        logger.info("Database manager initialized")
        tools = await create_tools()
        _team_manager = TeamManager(tools)
        logger.info("Team manager initialized")
        # Initialize connection manager
        _websocket_manager = WebSocketManager(db_manager=_db_manager, team_manager=_team_manager)
        logger.info("Connection manager initialized")
        # Initialize team manager

    except Exception as e:
        logger.error(f"Failed to initialize managers: {str(e)}")
        await cleanup_managers()  # Cleanup any partially initialized managers
        raise


async def cleanup_managers() -> None:
    """Cleanup and shutdown all manager instances"""
    global _db_manager, _websocket_manager, _team_manager

    logger.info("Cleaning up managers...")

    # TeamManager doesn't need explicit cleanup since WebSocketManager handles it
    _team_manager = None

    # Cleanup database manager last
    if _db_manager:
        try:
            await _db_manager.close()
        except Exception as e:
            logger.error(f"Error cleaning up database manager: {str(e)}")
        finally:
            _db_manager = None

    logger.info("All managers cleaned up")


# Utility functions for dependency management


def get_manager_status() -> dict:
    """Get the initialization status of all managers"""
    return {
        "database_manager": _db_manager is not None,
        "websocket_manager": _websocket_manager is not None,
        "team_manager": _team_manager is not None,
    }


# Combined dependencies


async def get_managers():
    """Get all managers in one dependency"""
    return {"db": await get_db(), "connection": await get_websocket_manager(), "team": await get_team_manager()}


# Error handling for manager operations


class ManagerOperationError(Exception):
    """Custom exception for manager operation errors"""

    def __init__(self, manager_name: str, operation: str, detail: str):
        self.manager_name = manager_name
        self.operation = operation
        self.detail = detail
        super().__init__(f"{manager_name} failed during {operation}: {detail}")


# Dependency for requiring specific managers


def require_managers(*manager_names: str):
    """Decorator to require specific managers for a route"""

    async def dependency():
        manager_status = get_manager_status()  # Different name
        missing = [name for name in manager_names if not manager_status.get(f"{name}_manager")]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,  # Now this refers to the imported module
                detail=f"Required managers not available: {', '.join(missing)}",
            )
        return True

    return Depends(dependency)
