"""Shipping quotes from a price table: distance between the two cities and package weight.

``ShippingQuoter`` is the port; ``TableShipping`` is the only adapter for now. A carrier
aggregator (Melhor Envio, for instance) would be a second adapter with the same ``quote`` method,
with this table kept as the fallback when the carrier API does not answer.
"""

import math
from dataclasses import dataclass
from typing import Literal, Protocol

Service = Literal["standard", "express"]
SERVICES: tuple[Service, ...] = ("standard", "express")
CARRIER = "tradepost Envios"
# Cubic weight: carriers bill the larger of the real weight and length * width * height / 6000.
VOLUMETRIC_DIVISOR = 6000


@dataclass(frozen=True)
class Parcel:
    weight_grams: int
    length_cm: int
    width_cm: int
    height_cm: int

    def billable_kg(self, quantity: int) -> float:
        real = self.weight_grams * quantity / 1000
        cubic = self.length_cm * self.width_cm * self.height_cm * quantity / VOLUMETRIC_DIVISOR
        return max(real, cubic)


@dataclass(frozen=True)
class Quote:
    service: Service
    carrier: str
    price_cents: int
    days: int


class ShippingQuoter(Protocol):
    def quote(self, distance_km: float, parcel: Parcel, quantity: int) -> list[Quote]: ...


class TableShipping:
    """Base price, plus a price per km and per kg above the first; express costs more and
    takes about half the time. Prices are simulated, which the README states."""

    base_cents = 1490
    per_km_cents = 1.5
    per_extra_kg_cents = 450
    same_city_km = 15

    def quote(self, distance_km: float, parcel: Parcel, quantity: int) -> list[Quote]:
        kg = parcel.billable_kg(quantity)
        extra_kg = max(math.ceil(kg) - 1, 0)
        if distance_km <= self.same_city_km:
            standard = 990 + extra_kg * self.per_extra_kg_cents
            days = 1
        else:
            standard = round(
                self.base_cents
                + distance_km * self.per_km_cents
                + extra_kg * self.per_extra_kg_cents
            )
            days = 2 + math.ceil(distance_km / 400)
        return [
            Quote("standard", CARRIER, standard, days),
            Quote("express", CARRIER, round(standard * 1.7), max(1, math.ceil(days / 2))),
        ]


def distance_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance; roads are longer, which the per-km price absorbs."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


quoter: ShippingQuoter = TableShipping()
