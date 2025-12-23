import importlib

import pytest
from sqlalchemy import text

import app.config as config


def _reload_database(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", "token")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    config.get_settings.cache_clear()

    import app.database as database
    import app.models as models

    database = importlib.reload(database)
    models = importlib.reload(models)
    return database, models


def test_ensure_schema_sets_version_and_tables(tmp_path, monkeypatch):
    database, _ = _reload_database(tmp_path, monkeypatch)

    database.ensure_schema()

    with database.engine.begin() as connection:
        version = connection.execute(text("SELECT version FROM schema_version")).scalar()
        tables = {
            row[0]
            for row in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).all()
        }

    assert version == database.SCHEMA_VERSION
    assert {"users", "feedback", "message_templates", "admin_state", "event_state"}.issubset(tables)

    config.get_settings.cache_clear()


def test_session_scope_commits_and_rolls_back(tmp_path, monkeypatch):
    database, models = _reload_database(tmp_path, monkeypatch)
    database.ensure_schema()

    with database.session_scope() as session:
        initial_count = session.query(models.SchemaVersion).count()
        session.add(models.SchemaVersion(version=2))

    with database.session_scope() as session:
        assert session.query(models.SchemaVersion).count() == initial_count + 1

    class DummyError(Exception):
        pass

    with pytest.raises(DummyError):
        with database.session_scope() as session:
            session.add(models.SchemaVersion(version=3))
            raise DummyError

    with database.session_scope() as session:
        assert session.query(models.SchemaVersion).count() == initial_count + 1

    config.get_settings.cache_clear()
