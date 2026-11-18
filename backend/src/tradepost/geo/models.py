from sqlalchemy import Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from tradepost.db import Base


class Municipality(Base):
    """Brazilian municipalities (IBGE code, name, state, centroid). Loaded by a migration from
    the public dataset in ``migrations/data``. Listings and users point at a municipality, never
    at an exact address."""

    __tablename__ = "municipalities"
    __table_args__ = (Index("ix_municipalities_name_key", "name_key", "state"),)

    ibge_code: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    # Lower-case, unaccented name used for matching and autocomplete.
    name_key: Mapped[str] = mapped_column(String(80), nullable=False)
    state: Mapped[str] = mapped_column(String(2), nullable=False)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lng: Mapped[float] = mapped_column(Float, nullable=False)
