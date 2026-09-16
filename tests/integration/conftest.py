"""Shared fixtures for integration tests."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.infrastructure.database import Base


@pytest.fixture
def isolated_db(tmp_path):
    """Yields an isolated temporary SQLite engine and SessionFactory."""
    db_file = tmp_path / "verification_isolated.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    SessionFactory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return engine, SessionFactory
