from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from config.settings import settings

# Support both PostgreSQL and SQLite (for dev/dry-run without Docker)
_db_url = settings.database_url
_kwargs: dict = {}
if "sqlite" in _db_url:
    # SQLite needs special handling for async
    _kwargs = {"connect_args": {"check_same_thread": False}}
else:
    # pool_pre_ping cheaply verifies a connection before checkout (SELECT 1).
    # Without it, long-running background tasks (e.g. conversation_monitor)
    # crash with `InterfaceError: connection is closed` when a Postgres /
    # pgbouncer-side idle timeout cycles the underlying socket between scans.
    # pool_recycle proactively rotates connections older than this many
    # seconds so we hit it less often in the first place.
    _kwargs = {
        "pool_size": 20,
        "max_overflow": 10,
        "pool_pre_ping": True,
        "pool_recycle": 1800,  # 30 min — well under typical idle timeouts
    }

engine = create_async_engine(_db_url, echo=settings.debug, **_kwargs)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_session() -> AsyncSession:
    async with async_session() as session:
        yield session


async def create_tables():
    """Create all tables (for SQLite dev mode)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
