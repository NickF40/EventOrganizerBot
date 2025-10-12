from pathlib import Path

CONFIG_ENV_VAR = "CONFIG_FILE"

CONFIG_FILENAMES: tuple[str, ...] = ("config.yaml", "config.yml")

CONFIG_SEARCH_PATHS: tuple[Path, ...] = (
    Path("."),
    Path("/config"),
    Path("/app"),
)

