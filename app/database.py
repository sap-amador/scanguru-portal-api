"""SQLAlchemy engine, session, and FastAPI dependency."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from app.config import settings

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    """FastAPI dependency: yields a session, closes on exit."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
