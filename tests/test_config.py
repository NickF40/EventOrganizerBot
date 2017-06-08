import pytest
from app.config import Settings, get_settings


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

    settings = get_settings()

    assert settings.telegram_token == "test-token"
    assert settings.admin_ids == [123]
    assert 123 in settings.admin_id_set

    monkeypatch.delenv("CONFIG_FILE", raising=False)
