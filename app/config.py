from typing import Any, List
from functools import lru_cache

import yaml
from pydantic import BaseSettings, Field
from .utils import config_path


class Settings(BaseSettings):
    telegram_token: str = Field(..., env="TELEGRAM_TOKEN")
    admin_ids: List[int] = Field(default_factory=list, env="ADMIN_IDS")

    database_url: str = Field(
        default="sqlite:///./anonchatbot.db",
        env="DATABASE_URL",
        description="SQLAlchemy compatible database URL.",
    )
    event_name: str = Field(default="Community Event", env="EVENT_NAME")
    attendee_limit: int = Field(default=150, env="ATTENDEE_LIMIT")

    web_host: str = Field(default="0.0.0.0", env="WEB_HOST")
    web_port: int = Field(default=8000, env="WEB_PORT")

    basic_auth_username: str = Field(..., env="ADMIN_USERNAME")
    basic_auth_password: str = Field(..., env="ADMIN_PASSWORD")

    scheduler_interval_seconds: int = Field(default=60, env="SCHEDULER_INTERVAL_SECONDS")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False

    @staticmethod
    def _parse_admin_ids(value: str | List[int] | None) -> List[int]:
        if value is None:
            return []
        if isinstance(value, list):
            return [int(v) for v in value]
        cleaned = [v.strip() for v in value.split(",") if v.strip()]
        return [int(v) for v in cleaned]

    @property
    def admin_id_set(self) -> set[int]:
        return set(self.admin_ids)

    @classmethod
    def _load_from_yaml(cls) -> dict[str, Any]:
        path = config_path()
        if not path:
            return {}
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls._cast_admin_ids(data)

    @classmethod
    def _cast_admin_ids(cls, settings: dict[str, Any]) -> dict[str, Any]:
        if "admin_ids" in settings:
            settings["admin_ids"] = cls._parse_admin_ids(settings["admin_ids"])  # type: ignore[arg-type]
        return settings

    @classmethod
    def customise_sources(cls, init_settings, env_settings, file_secret_settings):  # type: ignore[override]
        def cast_admin_ids(settings: dict[str, object]) -> dict[str, object]:
            if "admin_ids" in settings:
                settings["admin_ids"] = cls._parse_admin_ids(settings["admin_ids"])  # type: ignore[arg-type]
            return settings

        def settings_source():
            settings = env_settings()
            return cast_admin_ids(settings)

        def init_source():
            settings = init_settings()
            return cast_admin_ids(settings)

        def yaml_source():
            return cls._load_from_yaml()

        return init_source, yaml_source, settings_source, file_secret_settings


@lru_cache()
def get_settings() -> Settings:
    return Settings()
