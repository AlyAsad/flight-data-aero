"""Foreign-airport autocomplete backed by the `airportsdata` package."""

from __future__ import annotations

from functools import lru_cache

import airportsdata
from pydantic import BaseModel


class Airport(BaseModel):
    iata: str
    name: str
    city: str
    country: str


@lru_cache
def _airports() -> tuple[Airport, ...]:
    data = airportsdata.load("IATA")
    return tuple(
        Airport(iata=code, name=a["name"], city=a["city"], country=a["country"])
        for code, a in data.items()
        if code and len(code) == 3
    )


def get_airport(iata: str) -> Airport | None:
    iata = iata.upper()
    return next((a for a in _airports() if a.iata == iata), None)


def search_airports(query: str, limit: int = 10) -> list[Airport]:
    """Rank: exact IATA, IATA prefix, city prefix, name prefix, then substring matches."""
    q = query.strip().lower()
    if not q:
        return []
    scored: list[tuple[int, str, Airport]] = []
    for a in _airports():
        code, city, name = a.iata.lower(), a.city.lower(), a.name.lower()
        if code == q:
            score = 0
        elif code.startswith(q):
            score = 1
        elif city.startswith(q):
            score = 2
        elif name.startswith(q):
            score = 3
        elif q in city or q in name:
            score = 4
        else:
            continue
        # Prefer "International" airports within the same score band.
        tiebreak = 0 if "international" in name else 1
        scored.append((score * 2 + tiebreak, a.name, a))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [a for _, _, a in scored[:limit]]
