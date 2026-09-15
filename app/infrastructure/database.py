"""Database connection management, session lifecycle, and WAL pragmas for Reachout CRM.

Uses SQLAlchemy with SQLite in Write-Ahead Logging (WAL) mode for concurrency,
foreign key enforcement, and transactional integrity.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = ROOT_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

DEFAULT_DB_PATH = DATA_DIR / "reachout.db"
DB_URL = os.environ.get("DATABASE_URL", f"sqlite:///{DEFAULT_DB_PATH.as_posix()}")


class Base(DeclarativeBase):
    """Declarative base for all SQLAlchemy ORM models."""

    pass


@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    """Enable SQLite WAL mode, foreign keys, and sensible busy timeouts on each connection."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


def create_db_engine(db_url: str = DB_URL) -> Engine:
    """Create configured SQLAlchemy engine."""
    connect_args = {"check_same_thread": False, "timeout": 30} if db_url.startswith("sqlite") else {}
    return create_engine(
        db_url,
        connect_args=connect_args,
        echo=False,
    )


engine = create_db_engine()
SessionFactory = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_session() -> Generator[Session, None, None]:
    """Yield a transactional database session."""
    session = SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db(target_engine: Engine = engine) -> None:
    """Create all database tables if they do not exist."""
    # Import all models to ensure they are registered with Base.metadata
    import app.infrastructure.models  # noqa: F401

    Base.metadata.create_all(bind=target_engine)
