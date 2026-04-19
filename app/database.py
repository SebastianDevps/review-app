"""Database session factory and initialization."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.models import Base

# Sync engine (for Celery workers — no async needed there)
_sync_url = settings.database_url.replace("+asyncpg", "+psycopg2").replace("postgresql+asyncpg", "postgresql")
engine = create_engine(_sync_url, pool_pre_ping=True, pool_size=5, max_overflow=10)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db() -> None:
    """Create all tables. Called on app startup."""
    Base.metadata.create_all(bind=engine)


def get_db():
    """FastAPI dependency — yields a DB session and closes it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
