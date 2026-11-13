"""
Database connection, session management, and table initialization.
Supports PostgreSQL and SQLite seamlessly.
"""
from __future__ import annotations

import csv
from contextlib import contextmanager
from typing import Generator
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from solution.config import DATABASE_URL, SCHEMAS_DIR
from solution.models import Base, DispositionMetadata

_ENGINE = None
_SESSION_FACTORY = None


def get_engine(database_url: str | None = None):
    """Get or create the SQLAlchemy engine."""
    global _ENGINE, _SESSION_FACTORY
    target_url = database_url or DATABASE_URL

    if _ENGINE is None or (database_url and str(_ENGINE.url) != target_url):
        connect_args = {}
        if target_url.startswith("sqlite"):
            connect_args["check_same_thread"] = False
            connect_args["timeout"] = 30

        _ENGINE = create_engine(
            target_url,
            connect_args=connect_args,
            pool_pre_ping=True
        )
        _SESSION_FACTORY = sessionmaker(bind=_ENGINE, autoflush=False, expire_on_commit=False)

    return _ENGINE


def init_db(database_url: str | None = None) -> None:
    """Initialize all tables and populate the disposition catalogue."""
    engine = get_engine(database_url)
    Base.metadata.create_all(engine)
    load_disposition_catalogue(database_url)


@contextmanager
def get_session(database_url: str | None = None) -> Generator[Session, None, None]:
    """Context manager yielding a transactional database session."""
    engine = get_engine(database_url)
    global _SESSION_FACTORY
    if _SESSION_FACTORY is None or _SESSION_FACTORY.kw["bind"] != engine:
        _SESSION_FACTORY = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    session: Session = _SESSION_FACTORY()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def load_disposition_catalogue(database_url: str | None = None) -> None:
    """Load dispositions.csv into the database if not already present."""
    csv_path = SCHEMAS_DIR / "dispositions.csv"
    if not csv_path.exists():
        return

    with get_session(database_url) as session:
        count = session.query(DispositionMetadata).count()
        if count > 0:
            return  # Already loaded

        with open(csv_path, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                disp = DispositionMetadata(
                    workflow_id=row["workflow_id"].strip(),
                    code=row["code"].strip(),
                    label=row["label"].strip(),
                    is_terminal=row["is_terminal"].strip().lower() == "true",
                    is_success=row["is_success"].strip().lower() == "true",
                )
                session.merge(disp)

