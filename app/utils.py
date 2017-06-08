import os
from pathlib import Path


def config_path() -> Path | None:
    env_path = os.getenv("CONFIG_FILE")
    if env_path:
        path = Path(env_path)
        if path.exists():
            return path
    default_path = Path("config.yaml")
    if default_path.exists():
        return default_path
    return None
