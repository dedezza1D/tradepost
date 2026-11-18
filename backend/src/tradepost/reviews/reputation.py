"""Seller reputation: a level computed from the last 90 days, explained publicly.

The inputs are facts the platform records itself (completed sales, cancellations by the seller,
shipping delays, refunds after a dispute, ratings), so a seller cannot inflate them. Computing on
read keeps it always current; at this scale it is a handful of aggregate queries.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

WINDOW_DAYS = 90
SHIP_WITHIN_DAYS = 3

Level = Literal["new", "top", "good", "attention"]


@dataclass(frozen=True)
class Reputation:
    level: Level
    completed_sales: int
    cancelled_sales: int
    late_shipments: int
    shipments: int
    rating_avg: float | None
    rating_count: int

    @property
    def cancel_rate(self) -> float:
        total = self.completed_sales + self.cancelled_sales
        return self.cancelled_sales / total if total else 0.0

    @property
    def late_rate(self) -> float:
        return self.late_shipments / self.shipments if self.shipments else 0.0


_STATS = text(
    """
    SELECT
      count(*) FILTER (WHERE o.status = 'delivered' AND o.delivered_at > :since),
      count(*) FILTER (
        WHERE o.status = 'refunded' AND o.closed_at > :since
          AND EXISTS (SELECT 1 FROM order_events e
                       WHERE e.order_id = o.id AND e.to_status = 'refunded'
                         AND e.actor <> 'system')),
      count(*) FILTER (
        WHERE o.paid_at > :since
          AND ((o.shipped_at IS NOT NULL AND o.shipped_at - o.paid_at > :ship)
               OR (o.shipped_at IS NULL AND o.status = 'paid' AND o.paid_at < now() - :ship))),
      count(*) FILTER (
        WHERE o.paid_at > :since
          AND (o.shipped_at IS NOT NULL OR (o.status = 'paid' AND o.paid_at < now() - :ship)))
    FROM orders o
    WHERE o.seller_id = :seller
    """
)

_RATINGS = text(
    "SELECT avg(rating), count(*) FROM reviews WHERE seller_id = :seller AND created_at > :since"
)


def level_for(
    completed: int, cancel_rate: float, late_rate: float, rating_avg: float | None
) -> Level:
    """The published rules (also shown on the reputation help page)."""
    if completed < 3:
        return "new"
    rating_ok = rating_avg is None or rating_avg >= 4.5
    if completed >= 10 and cancel_rate <= 0.02 and late_rate <= 0.05 and rating_ok:
        return "top"
    if cancel_rate <= 0.05 and late_rate <= 0.15 and (rating_avg is None or rating_avg >= 4.0):
        return "good"
    return "attention"


async def seller_reputation(db: AsyncSession, seller_id: uuid.UUID) -> Reputation:
    params = {
        "seller": seller_id,
        "since": datetime.now(UTC) - timedelta(days=WINDOW_DAYS),
        "ship": timedelta(days=SHIP_WITHIN_DAYS),
    }
    completed, cancelled, late, shipments = (await db.execute(_STATS, params)).one()
    avg, count = (await db.execute(_RATINGS, params)).one()
    rating = round(float(avg), 2) if avg is not None else None
    total = completed + cancelled
    cancel_rate = cancelled / total if total else 0.0
    late_rate = late / shipments if shipments else 0.0
    return Reputation(
        level=level_for(completed, cancel_rate, late_rate, rating),
        completed_sales=int(completed),
        cancelled_sales=int(cancelled),
        late_shipments=int(late),
        shipments=int(shipments),
        rating_avg=rating,
        rating_count=int(count),
    )
