from app import config, const, utils


def test_resolve_from_directory_prefers_first_filename(tmp_path):
    first = tmp_path / const.CONFIG_FILENAMES[0]
    second = tmp_path / const.CONFIG_FILENAMES[1]
    second.write_text("telegram_token: second")
    first.write_text("telegram_token: first")

    assert utils.resolve_from_directory(tmp_path) == first


def test_load_from_yaml_returns_empty_when_missing(monkeypatch):
    monkeypatch.setattr(config, "config_path", lambda: None)

    assert config.Settings.load_from_yaml() == {}
