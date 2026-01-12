import pytest

from app.db.base import get_engine
from app.db.models import Base


@pytest.fixture(autouse=True, scope="function")
async def fresh_db():
    """Drop and recreate all tables before each integration test."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
