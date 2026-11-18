"""Block checks shared by every module that shows one user's content to another."""

import uuid
from typing import Any

from sqlalchemy import ColumnElement, and_, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from tradepost.social.models import Block


def blocked_either_way(a: Any, b: Any) -> ColumnElement[bool]:
    """True when ``a`` blocked ``b`` or ``b`` blocked ``a``. Arguments are ids or columns."""
    return exists(
        select(Block.blocker_id).where(
            or_(
                and_(Block.blocker_id == a, Block.blocked_id == b),
                and_(Block.blocker_id == b, Block.blocked_id == a),
            )
        )
    )


async def is_blocked(db: AsyncSession, a: uuid.UUID, b: uuid.UUID) -> bool:
    return bool((await db.execute(select(blocked_either_way(a, b)))).scalar_one())
