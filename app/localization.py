import json
import logging

from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any

DEFAULT_LOCALE = "en"
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Localizer:
    """Format localized strings using dotted keys."""

    _messages: dict[str, Any]
    _fallback: "Localizer | None" = None

    def _lookup(self, key: str) -> Any:
        parts = key.split(".")
        current: Any = self._messages
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                if self._fallback is not None:
                    return self._fallback._lookup(key)
                return key
        if isinstance(current, dict):
            if self._fallback is not None:
                fallback_value = self._fallback._lookup(key)
                if isinstance(fallback_value, str):
                    return fallback_value
            return key
        return current

    def get(self, key: str) -> str:
        value = self._lookup(key)
        if not isinstance(value, str):
            return str(value)
        return value

    def format(self, key: str, /, **kwargs: Any) -> str:
        template = self.get(key)
        try:
            return template.format(**kwargs)
        except KeyError:
            return template


def _load_messages(locale: str) -> dict[str, Any]:
    package = resources.files("app.locales")
    path = package / f"{locale}.json"
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


@lru_cache(maxsize=None)
def get_localizer(locale: str) -> Localizer:
    logger.info('get_localizer: locale chosen is %s', locale)
    try:
        messages = _load_messages(locale)
    except FileNotFoundError:
        if locale == DEFAULT_LOCALE:
            raise
        logger.error('Locale %s not found, using default one', locale)
        return get_localizer(DEFAULT_LOCALE)

    fallback = get_localizer(DEFAULT_LOCALE) if locale != DEFAULT_LOCALE else None
    return Localizer(messages, fallback)
