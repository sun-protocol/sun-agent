# api/app.py
import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import yaml
from fastapi import FastAPI

from ..version import VERSION
from .config import LOGGER_NAME, settings
from .deps import cleanup_managers, init_managers
from .initialization import AppInitializer
from .routes import session, ws

# Initialize application
app_file_path = os.path.dirname(os.path.abspath(__file__))
initializer = AppInitializer(settings, app_file_path)

logging_config = os.getenv("LOGGING_CONFIG", "logging_config.yaml")
with open(logging_config, "r") as f:
    config = yaml.safe_load(f.read())
    logging.config.dictConfig(config)

logger = logging.getLogger(LOGGER_NAME)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Lifecycle manager for the FastAPI application.
    Handles initialization and cleanup of application resources.
    """
    try:
        # Initialize managers (DB, Connection, Team)
        await init_managers(initializer.database_uri, initializer.app_root)

        # Any other initialization code
        logger.info("Application startup complete. Navigate to ws://0.0.0.0:8081/api/ws/agent")

    except Exception as e:
        logger.error(f"Failed to initialize application: {str(e)}")
        raise

    yield  # Application runs here

    # Shutdown
    try:
        logger.info("Cleaning up application resources...")
        await cleanup_managers()
        logger.info("Application shutdown complete")
    except Exception as e:
        logger.error(f"Error during shutdown: {str(e)}")


# Create FastAPI application
app = FastAPI(lifespan=lifespan, debug=True)
api = FastAPI(
    root_path="/api",
    title="SunAgent API",
    version=VERSION,
    description="sunagent",
)

api.include_router(
    ws.router,
    prefix="/ws",
    tags=["websocket"],
    responses={404: {"description": "Not found"}},
)

api.include_router(
    session.router,
    tags=["api"],
    responses={404: {"description": "Not found"}},
)


@api.get("/health")
async def health_check() -> dict[str, object]:
    """API health check endpoint"""
    return {
        "status": True,
        "message": "Service is healthy",
    }


# Mount static file directories
app.mount("/api", api)
# Error handlers


@app.exception_handler(500)
async def internal_error_handler(request, exc):
    logger.error(f"Internal error: {str(exc)}")
    return {
        "status": False,
        "message": "Internal server error",
        "detail": "Internal server error",
    }


def create_app() -> FastAPI:
    """
    Factory function to create and configure the FastAPI application.
    Useful for testing and different deployment scenarios.
    """
    return app
