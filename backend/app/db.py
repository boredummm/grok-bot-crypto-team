"""SQLite engine, decimal columns, and a single-writer session scope."""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterator

from sqlalchemy import String, Text, TypeDecorator, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings


class Base(DeclarativeBase):
    pass


class DecimalString(TypeDecorator):
    impl = String(80)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, float):
            raise TypeError("monetary float refused")
        return format(Decimal(value), "f")

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return Decimal(value)


class UTCDateTime(TypeDecorator):
    impl = String(40)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)


class JSONText(TypeDecorator):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return json.dumps(value, default=str, separators=(",", ":"))

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return json.loads(value)


_write_lock = threading.RLock()
_engine: Engine | None = None
_Session: sessionmaker[Session] | None = None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def init_engine(settings: Settings) -> Engine:
    global _engine, _Session
    url = settings.database_url
    kwargs: dict = {"connect_args": {"check_same_thread": False}}
    if url.endswith(":memory:") or url.endswith(":memory:?cache=shared"):
        kwargs["poolclass"] = StaticPool
    engine = create_engine(url, **kwargs)

    @event.listens_for(engine, "connect")
    def _pragma(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
        except Exception:
            pass
        cursor.close()

    _engine = engine
    _Session = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    return engine


def get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError("database engine is not initialized")
    return _engine


@contextmanager
def session_scope() -> Iterator[Session]:
    if _Session is None:
        raise RuntimeError("database engine is not initialized")
    with _write_lock:
        session = _Session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
