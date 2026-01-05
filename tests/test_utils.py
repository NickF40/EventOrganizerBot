from app import config, const, utils


def test_resolve_from_directory_prefers_first_filename(tmp_path):
    first = tmp_path / const.CONFIG_FILENAMES[0]
    second = tmp_path / const.CONFIG_FILENAMES[1]
    second.write_text("telegram_token: second")
    first.write_text("telegram_token: first")

    assert utils.resolve_from_directory(tmp_path) == first


def test_resolve_from_directory_returns_none_when_missing(tmp_path):
    assert utils.resolve_from_directory(tmp_path) is None


def test_load_from_yaml_returns_empty_when_missing(monkeypatch):
    monkeypatch.setattr(config, "config_path", lambda: None)

    assert config.Settings.load_from_yaml() == {}


def test_config_path_prefers_env_file(tmp_path, monkeypatch):
    config_file = tmp_path / const.CONFIG_FILENAMES[0]
    config_file.write_text("telegram_token: env")
    monkeypatch.setenv(utils.CONFIG_ENV_VAR, str(config_file))

    assert utils.config_path() == config_file


def test_config_path_resolves_env_directory(tmp_path, monkeypatch):
    config_file = tmp_path / const.CONFIG_FILENAMES[1]
    config_file.write_text("telegram_token: env-dir")
    monkeypatch.setenv(utils.CONFIG_ENV_VAR, str(tmp_path))

    assert utils.config_path() == config_file


def test_config_path_falls_back_to_search_paths(tmp_path, monkeypatch):
    config_file = tmp_path / const.CONFIG_FILENAMES[0]
    config_file.write_text("telegram_token: search")
    monkeypatch.delenv(utils.CONFIG_ENV_VAR, raising=False)
    monkeypatch.setattr(utils, "CONFIG_SEARCH_PATHS", (tmp_path,))

    assert utils.config_path() == config_file


def test_config_path_returns_none_when_unresolved(monkeypatch):
    monkeypatch.delenv(utils.CONFIG_ENV_VAR, raising=False)
    monkeypatch.setattr(utils, "CONFIG_SEARCH_PATHS", ())

    assert utils.config_path() is None


def test_parse_admin_usernames_handles_none():
    assert utils.parse_admin_usernames(None) == []
