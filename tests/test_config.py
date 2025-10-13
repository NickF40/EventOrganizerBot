import pytest

from app.config import Settings, get_settings
from app import utils


def test_parse_admin_ids_from_string():
    assert Settings._parse_admin_ids("1, 2,3") == [1, 2, 3]


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
admin_ids:
  - 123
basic_auth_username: admin
basic_auth_password: secret
        """
    )
    monkeypatch.setenv("CONFIG_FILE", str(config_path))
    monkeypatch.setenv("TELEGRAM_TOKEN", "test-token")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "secret")

    settings = get_settings()

    assert settings.telegram_token == "test-token"
    assert settings.admin_ids == [123]
    assert 123 in settings.admin_id_set

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


def test_set_timezone_respects_read_only_file(tmp_path, monkeypatch):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("timezone: UTC\n")
    config_file.chmod(0o400)

    monkeypatch.setenv("CONFIG_FILE", str(config_file))
    settings = Settings(
        telegram_token="token",
        basic_auth_username="admin",
        basic_auth_password="secret",
    )

    persisted = settings.set_timezone("Europe/London")

    assert persisted is False
    assert settings.timezone == "Europe/London"
