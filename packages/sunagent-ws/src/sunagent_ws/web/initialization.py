# api/initialization.py
import os
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger
from pydantic import BaseModel

from .config import Settings


class _AppPaths(BaseModel):
    """Internal model representing all application paths"""

    app_root: Path
    static_root: Path
    user_files: Path
    database_uri: str


class AppInitializer:
    """Handles application initialization including paths and environment setup"""

    def __init__(self, settings: Settings, app_path: str):
        """
        Initialize the application structure.

        Args:
            settings: Application settings
            app_path: Path to the application code directory
        """
        self.settings = settings
        self._app_path = Path(app_path)
        self._paths = self._init_paths()
        self._load_environment()
        logger.info(f"Initializing application data folder: {self.app_root} ")

    def _get_app_root(self) -> Path:
        """Determine application root directory"""
        if app_dir := os.getenv("SUN_AGENT_ROOT"):
            return Path(app_dir)
        return Path.home() / ".sunagent"

    def _get_database_uri(self, app_root: Path) -> str:
        """Generate database URI based on settings or environment"""
        if db_uri := os.getenv("DATABASE_URI"):
            return db_uri
        return self.settings.DATABASE_URI.replace("./", str(app_root) + "/")

    def _init_paths(self) -> _AppPaths:
        """Initialize and return AppPaths instance"""
        app_root = self._get_app_root()
        return _AppPaths(
            app_root=app_root,
            static_root=app_root / "files",
            user_files=app_root / "files" / "user",
            database_uri=self._get_database_uri(app_root),
        )

    def _load_environment(self) -> None:
        """Load environment variables from .env file if it exists"""
        env_file = self.app_root / ".env"
        if env_file.exists():
            # logger.info(f"Loading environment variables from {env_file}")
            load_dotenv(str(env_file))

    # Properties for accessing paths
    @property
    def app_root(self) -> Path:
        """Root directory for the application"""
        return self._paths.app_root

    @property
    def static_root(self) -> Path:
        """Directory for static files"""
        return self._paths.static_root

    @property
    def user_files(self) -> Path:
        """Directory for user files"""
        return self._paths.user_files

    @property
    def config_dir(self) -> Path:
        """Directory for configuration files"""
        return self._paths.config_dir

    @property
    def database_uri(self) -> str:
        """Database connection URI"""
        return self._paths.database_uri
