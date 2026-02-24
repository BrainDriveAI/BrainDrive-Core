import logging
import sqlite3
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import NullPool
from sqlalchemy.engine.url import make_url

import contextlib
import asyncio
from app.core.config import settings
from app.core.json_storage import JSONStorage

logger = logging.getLogger(__name__)

# Create base class for SQLAlchemy models
Base = declarative_base()

class DatabaseFactory:
    def __init__(self):
        self.engine = self.get_engine()
        self.session_factory = self.get_session_factory()

    def get_engine(self):
        """Create and return a database engine based on configuration."""
        try:
            if settings.USE_JSON_STORAGE:
                logger.info("Using JSON storage")
                return None
            else:
                # Parse and validate the database URL
                url = make_url(settings.DATABASE_URL)
                if settings.DATABASE_TYPE == "sqlite" and url.drivername == "sqlite":
                    url = url.set(drivername="sqlite+aiosqlite")
                
                logger.info("Creating async database engine", driver=url.drivername, database=url.database)
                connect_args = {}
                if settings.DATABASE_TYPE == "sqlite":
                    connect_args = {"check_same_thread": False, "timeout": 30}
                engine = create_async_engine(
                    url,
                    echo=settings.DEBUG and getattr(logging, settings.SQL_LOG_LEVEL.upper(), logging.WARNING) <= logging.DEBUG,
                    poolclass=NullPool,
                    connect_args=connect_args,
                )

                # Enable SQLite PRAGMAs for better concurrency.
                if settings.DATABASE_TYPE == "sqlite":
                    @event.listens_for(engine.sync_engine, "connect")
                    def set_sqlite_pragma(dbapi_connection, connection_record):
                        if isinstance(dbapi_connection, sqlite3.Connection):
                            cursor = dbapi_connection.cursor()
                            cursor.execute("PRAGMA journal_mode=WAL")
                            cursor.execute("PRAGMA synchronous=NORMAL")
                            cursor.execute("PRAGMA busy_timeout=5000")
                            cursor.execute("PRAGMA foreign_keys=ON")
                            cursor.close()
                            logger.info("SQLite pragmas set (WAL/busy_timeout)")

                return engine
        except Exception as e:
            logger.error(f"Error creating database engine: {e}")
            raise

    def get_session_factory(self):
        """Create and return a session factory."""
        try:
            if settings.USE_JSON_STORAGE:
                return lambda: JSONStorage(settings.JSON_DB_PATH)
            else:
                return sessionmaker(
                    self.engine,
                    class_=AsyncSession,
                    expire_on_commit=False,
                    autocommit=False,
                    autoflush=False
                )
        except Exception as e:
            logger.error(f"Error creating session factory: {e}")
            raise

db_factory = DatabaseFactory()

async def get_db():
    """Dependency for getting database session."""
    if settings.USE_JSON_STORAGE:
        db = db_factory.session_factory()
        try:
            yield db
        finally:
            pass  # No cleanup needed for JSON storage
    else:
        db = db_factory.session_factory()
        try:
            yield db
            await db.commit()
        except Exception as e:
            await db.rollback()
            raise
        finally:
            # Swallow cancellation during shutdown/reload and log close issues without raising
            with contextlib.suppress(asyncio.CancelledError):
                try:
                    await db.close()
                except Exception as close_error:
                    # Log but don't raise close errors to avoid masking the real error
                    logger.warning(f"Error closing database session: {close_error}")
