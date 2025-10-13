from datetime import datetime
from functools import lru_cache
from typing import Any, List
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseSettings, Field, validator

from .utils import config_path, parse_admin_ids


def detect_default_timezone() -> str:
    """Return the host timezone name or UTC if it cannot be determined."""

    try:
        tzinfo = datetime.now().astimezone().tzinfo
        if tzinfo is None:
            return "UTC"

        key = getattr(tzinfo, "key", None)
        if key:
            return key

        name = tzinfo.tzname(None)
        if name:
            try:
                ZoneInfo(name)
            except ZoneInfoNotFoundError:
                pass
            else:
                return name
    except Exception:
        pass

    return "UTC"


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
    timezone: str = Field(default_factory=detect_default_timezone, env="TIMEZONE")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False

    @validator("admin_ids", pre=True)
    def validate_admin_ids(cls, value: str | List[int] | None) -> List[int]:  # type: ignore[override]
        return parse_admin_ids(value)

    @classmethod
    def _parse_admin_ids(cls, value: str | List[int] | None) -> List[int]:
        return parse_admin_ids(value)

    @property
    def admin_id_set(self) -> set[int]:
        return set(self.admin_ids)

    @property
    def tzinfo(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError:
            return ZoneInfo("UTC")

    @property
    def can_persist_timezone(self) -> bool:
        return config_path() is not None

    def set_timezone(self, timezone_name: str) -> bool:
        try:
            ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown timezone '{timezone_name}'") from exc

        object.__setattr__(self, "timezone", timezone_name)
        return self._persist_value("timezone", timezone_name)

    def _persist_value(self, key: str, value: Any) -> bool:
        path = config_path()
        if not path:
            return False

        data = self.load_from_yaml()
        data[key] = value
        try:
            path.write_text(
                yaml.safe_dump(data, sort_keys=True, allow_unicode=True),
                encoding="utf-8",
            )
        except OSError:
            return False
        return True

    @classmethod
    def load_from_yaml(cls) -> dict[str, Any]:
        path = config_path()
        if not path:
            return {}
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return data


@lru_cache(None)
def get_settings() -> Settings:
    yaml_values = Settings.load_from_yaml()
    return Settings(**yaml_values)
