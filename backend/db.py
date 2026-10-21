from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from backend.config import get_settings


class Base(DeclarativeBase):
    pass


def make_engine(url: str | None = None):
    engine = create_engine(url or get_settings().database_url, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()

    return engine


engine = None
SessionLocal = sessionmaker(autoflush=False, expire_on_commit=False)


def init_db(url: str | None = None):
    global engine
    from backend import models  # noqa: F401  (register tables)

    engine = make_engine(url)
    SessionLocal.configure(bind=engine)
    Base.metadata.create_all(engine)
    return engine


def get_session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session
