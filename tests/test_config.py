import pytest
import yaml
from app import utils
from app.config import Settings, get_settings


def test_parse_admin_usernames_from_string():
    assert Settings._parse_admin_usernames("Admin, @Owner") == ["admin", "owner"]


@pytest.fixture(autouse=True)
def reset_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_settings_loaded_from_yaml(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
telegram_token: test-token
admin_usernames:
  - admin
  - "@Owner"
        """
    )
    monkeypatch.setenv("CONFIG_FILE", str(config_path))
    monkeypatch.setenv("TELEGRAM_TOKEN", "test-token")

    settings = get_settings()

    assert settings.telegram_token == "test-token"
    assert settings.admin_usernames == ["admin", "owner"]
    assert settings.admin_username_set == {"admin", "owner"}

    monkeypatch.delenv("CONFIG_FILE", raising=False)


def test_config_path_accepts_directory(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_file = config_dir / "config.yaml"
    config_file.write_text("telegram_token: x")

    monkeypatch.setenv("CONFIG_FILE", str(config_dir))

    try:
        assert utils.config_path() == config_file
    finally:
        monkeypatch.delenv("CONFIG_FILE", raising=False)


def test_config_path_falls_back_to_search_paths(tmp_path, monkeypatch):
    config_file = tmp_path / "config.yml"
    config_file.write_text("telegram_token: x")

    monkeypatch.delenv("CONFIG_FILE", raising=False)
    monkeypatch.setattr(utils, "CONFIG_SEARCH_PATHS", (tmp_path,))

    assert utils.config_path() == config_file


def test_settings_respects_admin_usernames_env(monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAMES", '["first", "Second"]')
    settings = Settings(telegram_token="token")
    assert settings.admin_usernames == ["first", "second"]


def test_settings_timezone_validation():
    with pytest.raises(ValueError):
        Settings(telegram_token="token", timezone="Not/AZone")


def test_set_timezone_persists_when_config_exists(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("telegram_token: test-token\n")
    monkeypatch.setenv("CONFIG_FILE", str(config_path))

    settings = Settings(telegram_token="token")
    assert settings.set_timezone("UTC") is True

    persisted = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert persisted["timezone"] == "UTC"
