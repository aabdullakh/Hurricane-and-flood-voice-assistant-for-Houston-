"""Database models. SQLite by default (zero setup); Postgres in Docker via DATABASE_URL."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, Text, UniqueConstraint, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .config import settings

_is_sqlite = settings.database_url.startswith("sqlite")
if _is_sqlite:
    settings.data_dir.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_db(dt: datetime) -> datetime:
    """Store naive UTC."""
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def from_db(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc)


class Base(DeclarativeBase):
    pass


class GaugeReading(Base):
    """One gauge reading. Every row keeps the ORIGINAL observation time, not just when we fetched it."""

    __tablename__ = "gauge_readings"
    __table_args__ = (UniqueConstraint("site_id", "observed_at", name="uq_site_time"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    site_id: Mapped[str] = mapped_column(String(32), index=True)
    site_name: Mapped[str] = mapped_column(String(255))
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    level_ft: Mapped[float] = mapped_column(Float)
    observed_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime)


class Profile(Base):
    """Consented household facts. Keyed by a hash of the phone number, never the number itself."""

    __tablename__ = "profiles"

    caller_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    language: Mapped[str] = mapped_column(String(16), default="en")
    name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    needs: Mapped[list] = mapped_column(JSON, default=list)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    consent_at: Mapped[datetime] = mapped_column(DateTime)
    backboard_assistant_id: Mapped[str | None] = mapped_column(String(100), nullable=True)


class CallSummary(Base):
    """Post-call triage. The transcript itself is NOT stored."""

    __tablename__ = "call_summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    language: Mapped[str] = mapped_column(String(16), default="en")
    needs_help: Mapped[bool] = mapped_column(Boolean, default=False)
    urgency: Mapped[str] = mapped_column(String(16), default="low")
    categories: Mapped[list] = mapped_column(JSON, default=list)
    summary: Mapped[str] = mapped_column(Text, default="")


def init_db() -> None:
    Base.metadata.create_all(engine)


def get_session():
    with SessionLocal() as session:
        yield session
