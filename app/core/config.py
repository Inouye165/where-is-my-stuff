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


class Settings(BaseModel):
    model_config = ConfigDict(validate_assignment=True, arbitrary_types_allowed=True)

    APP_NAME: str = Field(default_factory=lambda: os.getenv("APP_NAME", "WhereIsMyStuff (WIMS) Inventory"))
    APP_ENV: str = Field(default_factory=lambda: os.getenv("APP_ENV", "production"))
    BASE_DIR: Path = BASE_DIR_DEFAULT
    DATA_DIR: Path = BASE_DIR_DEFAULT / "data"
    UPLOAD_DIR: Path = BASE_DIR_DEFAULT / "data" / "uploads"
    ORIGINALS_DIR: Path = BASE_DIR_DEFAULT / "data" / "uploads" / "originals"
    THUMBNAILS_DIR: Path = BASE_DIR_DEFAULT / "data" / "uploads" / "thumbnails"
    BACKUP_DIR: Path = BASE_DIR_DEFAULT / "data" / "backups"
    LOG_DIR: Path = BASE_DIR_DEFAULT / "logs"

    DATABASE_URL: str = Field(
        default_factory=lambda: os.getenv(
            "DATABASE_URL", f"sqlite:///{(BASE_DIR_DEFAULT / 'data' / 'inventory.db').as_posix()}"
        )
    )
    HOST: str = Field(default_factory=lambda: os.getenv("HOST", "0.0.0.0"))
    PORT: int = Field(default_factory=lambda: int(os.getenv("PORT", "8000")))

    MAX_IMAGE_UPLOAD_SIZE_MB: int = Field(default_factory=lambda: int(os.getenv("MAX_IMAGE_UPLOAD_SIZE_MB", "15")))
    MAX_IMAGE_DIMENSION: int = Field(default_factory=lambda: int(os.getenv("MAX_IMAGE_DIMENSION", "1600")))
    THUMBNAIL_MAX_DIMENSION: int = Field(default_factory=lambda: int(os.getenv("THUMBNAIL_MAX_DIMENSION", "400")))
    MAX_IMAGE_PIXELS: int = Field(default_factory=lambda: int(os.getenv("MAX_IMAGE_PIXELS", "36000000")))

    LOG_LEVEL: str = Field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    BACKUP_RETENTION_COUNT: int = Field(default_factory=lambda: int(os.getenv("BACKUP_RETENTION_COUNT", "10")))
    CORS_ALLOWED_ORIGINS: List[str] = Field(
        default_factory=lambda: _parse_origins(os.getenv("CORS_ALLOWED_ORIGINS", ""))
    )
    PUBLIC_BASE_URL: Optional[str] = Field(default_factory=lambda: os.getenv("PUBLIC_BASE_URL") or None)
    GEMINI_API_KEY: Optional[str] = Field(default_factory=lambda: os.getenv("GEMINI_API_KEY") or None)

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
            self.BACKUP_DIR,
            self.LOG_DIR,
        ):
            directory.mkdir(parents=True, exist_ok=True)


settings = Settings()
