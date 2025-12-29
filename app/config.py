from functools import lru_cache
import json
from typing import Any, List

import yaml
from pydantic import BaseSettings, Field, validator

from .utils import config_path, parse_admin_usernames


class Settings(BaseSettings):
    telegram_token: str = Field(..., env="TELEGRAM_TOKEN")
    admin_usernames: List[str] = Field(default_factory=list, env="ADMIN_USERNAMES")
    locale: str = Field(default="en", env="LOCALE")

    enable_admin_web: bool = Field(default=False, env="ENABLE_ADMIN_WEB")
    admin_web_host: str = Field(default="0.0.0.0", env="ADMIN_WEB_HOST")
    admin_web_port: int = Field(default=8000, env="ADMIN_WEB_PORT")
    basic_auth_username: str = Field(default="admin", env="ADMIN_BASIC_AUTH_USERNAME")
    basic_auth_password: str = Field(default="admin", env="ADMIN_BASIC_AUTH_PASSWORD")

    database_url: str = Field(
        default="sqlite:///./anonchatbot.db",
        env="DATABASE_URL",
        description="SQLAlchemy compatible database URL.",
    )

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False

        @classmethod
        def customise_sources(cls, init_settings, env_settings, file_secret_settings):
            return (
                init_settings,
                env_settings,
                cls._yaml_settings_source,
                file_secret_settings,
            )

        @classmethod
        def _yaml_settings_source(cls, settings: BaseSettings) -> dict[str, Any]:
            return settings.__class__.load_from_yaml()

        @classmethod
        def parse_env_var(cls, field_name: str, raw_val: str) -> Any:
            if field_name == "admin_usernames":
                try:
                    loaded = json.loads(raw_val)
                except json.JSONDecodeError:
                    return Settings._parse_admin_usernames(raw_val)
                return Settings._parse_admin_usernames(loaded)
            return json.loads(raw_val)

    @validator("admin_usernames", pre=True)
    def validate_admin_usernames(cls, value: str | List[str] | None) -> List[str]:  # type: ignore[override]
        return parse_admin_usernames(value)

    @classmethod
    def _parse_admin_usernames(cls, value: str | List[str] | None) -> List[str]:
        return parse_admin_usernames(value)

    @property
    def admin_username_set(self) -> set[str]:
        return {name.lower() for name in self.admin_usernames}

    @classmethod
    def load_from_yaml(cls) -> dict[str, Any]:
        path = config_path()
        if not path:
            return {}
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return data


@lru_cache(None)
def get_settings() -> Settings:
    return Settings()
