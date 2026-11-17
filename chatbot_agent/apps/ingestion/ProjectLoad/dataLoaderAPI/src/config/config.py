from pathlib import Path

import yaml
from loguru import logger
from pydantic_settings import BaseSettings, SettingsConfigDict

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

# Resolved from this file so the app works from any working directory
CONFIG_DIR = Path(__file__).resolve().parent
REPO_ROOT = CONFIG_DIR.parents[min(5, len(CONFIG_DIR.parents) - 1)]  # .../dataLoaderAPI/src/config -> repo root (shallower in Docker)
ROOT_ENV = str(REPO_ROOT / ".env")  # shared secrets for all services
DEV_CONFIG = str(CONFIG_DIR / "dev.env")  # optional service-local overrides
YAML_CONFIG_PATH = str(CONFIG_DIR / "dev_config.yaml")

if load_dotenv is not None:
    # Real environment variables win, then service-local dev.env, then the root .env
    load_dotenv(dotenv_path=DEV_CONFIG, override=False)
    load_dotenv(dotenv_path=ROOT_ENV, override=False)


class BatchConfig(BaseSettings):
    """Settings required to fetch documents from the source API."""

    SOURCE_BASE_URL: str = "http://localhost:8000"
    SOURCE_SEARCH_URL: str = "/search"
    SOURCE_DOWNLOAD_URL: str = "/download"
    API_AUTH_KEY: str = ""  # if set, clients must send it as the X-API-Key header
    ALLOWED_ORIGINS: str = "*"  # comma-separated CORS origins
    MAX_UPLOAD_MB: int = 25
    CATEGORY: str = ""
    FILE_FORMAT: str = ""
    
    model_config = SettingsConfigDict(
        env_file=(ROOT_ENV, DEV_CONFIG),
        env_file_encoding="utf-8",
        extra="ignore",
    )


def _load_config() -> tuple[BatchConfig, dict]:
    batch_config = BatchConfig()
    
    with open(YAML_CONFIG_PATH) as f:
        chunking_config_data = yaml.safe_load(f)

    logger.info("Source API and chunking configuration loaded successfully.")
    return batch_config, chunking_config_data


batch_config, chunking_config_data = _load_config()
