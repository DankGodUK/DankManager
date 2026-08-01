import os
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///albion_bot.db")

# echo=False in production; flip to True to debug SQL
engine = create_async_engine(DATABASE_URL, echo=False)

async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


from sqlalchemy import text

async def init_db():
    """Create all tables. Safe to call every startup (no-ops if tables exist)."""
    from db.models import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
        # Add new column if it doesn't exist
        try:
            await conn.execute(text("ALTER TABLE preset_slot ADD COLUMN role_type VARCHAR NOT NULL DEFAULT 'DPS'"))
        except Exception:
            pass
