# api/config.py

from pydantic_settings import BaseSettings

LOGGER_NAME = "sunagent_ws"


class Settings(BaseSettings):
    DATABASE_URI: str = "sqlite:///./sunagent.db"
    CLEANUP_INTERVAL: int = 300  # 5 minutes
    SESSION_TIMEOUT: int = 3600  # 1 hour
    UPGRADE_DATABASE: bool = False
    model_config = {"env_prefix": "AUTOGENSTUDIO_"}


settings = Settings()
