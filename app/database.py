from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from app.config import get_settings

settings = get_settings()
connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}

if settings.database_url.startswith("sqlite"):
    url = make_url(settings.database_url)
    if url.database:
        db_path = Path(url.database)
        if not db_path.is_absolute():
            db_path = Path.cwd() / db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(settings.database_url, connect_args=connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

Base = declarative_base()

SCHEMA_VERSION = 3


def ensure_schema() -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE IF NOT EXISTS schema_version ("
                "id INTEGER PRIMARY KEY, "
                "version INTEGER NOT NULL)"
            )
        )
        result = connection.execute(text("SELECT version FROM schema_version LIMIT 1"))
        current_version = result.scalar()
        if current_version is None:
            connection.execute(text("INSERT INTO schema_version (version) VALUES (0)"))
            current_version = 0

    if current_version < SCHEMA_VERSION:
        Base.metadata.drop_all(bind=engine)
        from app import models  # noqa: F401

        Base.metadata.create_all(bind=engine)
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM schema_version"))
            connection.execute(
                text("INSERT INTO schema_version (version) VALUES (:version)"),
                {"version": SCHEMA_VERSION},
            )


@contextmanager
def session_scope() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    with session_scope() as session:
        yield session
