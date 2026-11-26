import os
from pathlib import Path
from typing import List, Optional
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field

BASE_DIR_DEFAULT = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR_DEFAULT / ".env", override=False)


def _parse_origins(raw: str) -> List[str]:
    if not raw or not raw.strip():
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise ValueError(f"Environment variable {name} must be an integer, got {raw!r}.") from exc


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    val = raw.strip().lower()
    if val in ("1", "true", "yes", "on"):
        return True
    if val in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"Environment variable {name} must be a boolean, got {raw!r}.")


def _default_data_dir() -> Path:
    raw = os.getenv("DATA_DIR")
    return Path(raw.strip()) if (raw and raw.strip()) else (BASE_DIR_DEFAULT / "data")


DATA_DIR_DEFAULT = _default_data_dir()


class Settings(BaseModel):
    model_config = ConfigDict(validate_assignment=True, arbitrary_types_allowed=True)

    APP_NAME: str = Field(default_factory=lambda: os.getenv("APP_NAME", "WhereIsMyStuff (WIMS) Inventory"))
    APP_ENV: str = Field(default_factory=lambda: os.getenv("APP_ENV", "production"))
    BASE_DIR: Path = BASE_DIR_DEFAULT
    DATA_DIR: Path = DATA_DIR_DEFAULT
    UPLOAD_DIR: Path = DATA_DIR_DEFAULT / "uploads"
    ORIGINALS_DIR: Path = DATA_DIR_DEFAULT / "uploads" / "originals"
    THUMBNAILS_DIR: Path = DATA_DIR_DEFAULT / "uploads" / "thumbnails"
    ATTACHMENTS_DIR: Path = DATA_DIR_DEFAULT / "uploads" / "attachments"
    BACKUP_DIR: Path = DATA_DIR_DEFAULT / "backups"
    LOG_DIR: Path = BASE_DIR_DEFAULT / "logs"

    DATABASE_URL: str = Field(
        default_factory=lambda: os.getenv(
            "DATABASE_URL", f"sqlite:///{(DATA_DIR_DEFAULT / 'inventory.db').as_posix()}"
        )
    )
    HOST: str = Field(default_factory=lambda: os.getenv("HOST", "0.0.0.0"))
    PORT: int = Field(default_factory=lambda: _env_int("PORT", 8000))

    MAX_IMAGE_UPLOAD_SIZE_MB: int = Field(default_factory=lambda: _env_int("MAX_IMAGE_UPLOAD_SIZE_MB", 15))
    MAX_IMAGE_DIMENSION: int = Field(default_factory=lambda: _env_int("MAX_IMAGE_DIMENSION", 1600))
    THUMBNAIL_MAX_DIMENSION: int = Field(default_factory=lambda: _env_int("THUMBNAIL_MAX_DIMENSION", 400))
    MAX_IMAGE_PIXELS: int = Field(default_factory=lambda: _env_int("MAX_IMAGE_PIXELS", 36000000))

    LOG_LEVEL: str = Field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    BACKUP_RETENTION_COUNT: int = Field(default_factory=lambda: _env_int("BACKUP_RETENTION_COUNT", 10))
    AUTO_BACKUP_ENABLED: bool = Field(default_factory=lambda: _env_bool("AUTO_BACKUP_ENABLED", True))
    AUTO_BACKUP_INTERVAL_HOURS: int = Field(default_factory=lambda: _env_int("AUTO_BACKUP_INTERVAL_HOURS", 24))
    CORS_ALLOWED_ORIGINS: List[str] = Field(
        default_factory=lambda: _parse_origins(os.getenv("CORS_ALLOWED_ORIGINS", ""))
    )
    PUBLIC_BASE_URL: Optional[str] = Field(default_factory=lambda: os.getenv("PUBLIC_BASE_URL") or None)
    WIMS_ACCESS_PIN: Optional[str] = Field(
        default_factory=lambda: (os.getenv("WIMS_ACCESS_PIN") or "").strip() or None
    )
    WIMS_AUTH_BYPASS_LOCALHOST: bool = Field(
        default_factory=lambda: _env_bool("WIMS_AUTH_BYPASS_LOCALHOST", False)
    )
    WIMS_SESSION_SECRET: Optional[str] = Field(
        default_factory=lambda: (os.getenv("WIMS_SESSION_SECRET") or "").strip() or None
    )
    WIMS_SESSION_TTL_SECONDS: int = Field(
        default_factory=lambda: _env_int("WIMS_SESSION_TTL_SECONDS", 14 * 24 * 3600)
    )
    RATE_LIMIT_ENABLED: bool = Field(
        default_factory=lambda: _env_bool("RATE_LIMIT_ENABLED", True)
    )
    RATE_LIMIT_AUTH_PER_MIN: int = Field(default_factory=lambda: _env_int("RATE_LIMIT_AUTH_PER_MIN", 10))
    RATE_LIMIT_VISION_PER_MIN: int = Field(default_factory=lambda: _env_int("RATE_LIMIT_VISION_PER_MIN", 15))
    RATE_LIMIT_SYSTEM_PER_MIN: int = Field(default_factory=lambda: _env_int("RATE_LIMIT_SYSTEM_PER_MIN", 10))
    RATE_LIMIT_UPLOAD_PER_MIN: int = Field(default_factory=lambda: _env_int("RATE_LIMIT_UPLOAD_PER_MIN", 60))
    VISION_PROVIDER: str = Field(default_factory=lambda: os.getenv("VISION_PROVIDER", "local"))
    OLLAMA_BASE_URL: str = Field(default_factory=lambda: os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"))
    OLLAMA_VISION_MODEL: str = Field(default_factory=lambda: os.getenv("OLLAMA_VISION_MODEL", "qwen2.5vl:7b"))
    GEMINI_API_KEY: Optional[str] = Field(default_factory=lambda: os.getenv("GEMINI_API_KEY") or None)
    GEMINI_VISION_MODEL: str = Field(default_factory=lambda: os.getenv("GEMINI_VISION_MODEL", "gemini-2.5-flash"))
    OPENAI_API_KEY: Optional[str] = Field(default_factory=lambda: os.getenv("OPENAI_API_KEY") or None)
    OPENAI_VISION_MODEL: str = Field(default_factory=lambda: os.getenv("OPENAI_VISION_MODEL", "gpt-4o-mini"))

    @property
    def sqlite_db_path(self) -> Optional[Path]:
        if not self.DATABASE_URL.startswith("sqlite:///"):
            return None
        raw = self.DATABASE_URL[len("sqlite:///"):]
        if raw == ":memory:":
            return None
        return Path(raw)

    def init_directories(self) -> None:
        for directory in (
            self.DATA_DIR,
            self.UPLOAD_DIR,
            self.ORIGINALS_DIR,
            self.THUMBNAILS_DIR,
            self.ATTACHMENTS_DIR,
            self.BACKUP_DIR,
            self.LOG_DIR,
        ):
            directory.mkdir(parents=True, exist_ok=True)


settings = Settings()
