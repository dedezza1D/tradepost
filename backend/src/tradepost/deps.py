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


# scope="function": the session commits when the endpoint returns, *before* the response is
# sent. With FastAPI's default ("request") the commit would run after the client already got its
# 200: a follow-up read could see the old data, and a failed commit would go unreported.
DbSession = Annotated[AsyncSession, Depends(get_db, scope="function")]
AppSettings = Annotated[Settings, Depends(get_settings)]
