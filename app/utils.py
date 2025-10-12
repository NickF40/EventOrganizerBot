import os
from pathlib import Path
from typing import List

from app.const import CONFIG_ENV_VAR, CONFIG_FILENAMES, CONFIG_SEARCH_PATHS


def resolve_from_directory(directory: Path) -> Path | None:
    for filename in CONFIG_FILENAMES:
        candidate = directory / filename
        if candidate.is_file():
            return candidate
    return None


def config_path() -> Path | None:
    """Return the best effort path to the configuration file.

    The resolver first honours the ``CONFIG_FILE`` environment variable, which can
    either reference the configuration file directly or a directory that contains
    one of the supported filenames. When not present, we probe common locations so
    Docker volume mounts (such as ``/config``) are automatically detected.
    """

    env_path = os.getenv(CONFIG_ENV_VAR)
    if env_path:
        candidate = Path(env_path)
        if candidate.is_file():
            return candidate
        if candidate.is_dir():
            resolved = resolve_from_directory(candidate)
            if resolved:
                return resolved

    for base in CONFIG_SEARCH_PATHS:
        resolved = resolve_from_directory(base)
        if resolved:
            return resolved

    return None


def parse_admin_ids(value: str | List[int] | None) -> List[int]:
    if value is None:
        return []
    if isinstance(value, list):
        return [int(v) for v in value]

    cleaned = [v.strip() for v in value.split(",") if v.strip()]
    return [int(v) for v in cleaned]
