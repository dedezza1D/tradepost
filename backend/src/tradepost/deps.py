from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from tradepost.config import Settings, get_settings
from tradepost.db import Database


def get_database(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    async for session in get_database(request).session():
        yield session


DbSession = Annotated[AsyncSession, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]
