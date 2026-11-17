"""fast-flights adapter (unofficial Google Flights scraper, pinned to 3.1.0).

Verified fields in fast-flights 3.1.0 (`fast_flights.model`):
  Flights: type, price (int), airlines (names), flights (segments), carbon
  SingleFlight: from_airport/to_airport (code, name), departure/arrival
                (date tuple, time tuple; local), duration (min), plane_type
Not available: flight numbers, baggage, self-transfer flag, total duration
(derived from segments + layovers).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime

from fast_flights import FlightQuery, FlightsNotFound, create_query, fetch_flights_html
from fast_flights.model import Flights, SimpleDatetime
from fast_flights.parser import parse

from ..models import FlightOption
from .base import ProviderError, Segment, build_flight_option

log = logging.getLogger(__name__)

NAME = "fast_flights"


def _to_datetime(value: SimpleDatetime) -> datetime:
    y, m, d = value.date
    hh, mm = value.time
    return datetime(y, m, d, hh, mm)


def map_results(results: list[Flights]) -> list[FlightOption]:
    """Map fast-flights results to FlightOptions, skipping malformed itineraries."""
    options: dict[str, FlightOption] = {}
    for item in results:
        try:
            segments = [
                Segment(
                    origin=s.from_airport.code,
                    destination=s.to_airport.code,
                    departure=_to_datetime(s.departure),
                    arrival=_to_datetime(s.arrival),
                    duration_minutes=s.duration,
                    aircraft=s.plane_type or None,
                )
                for s in item.flights
            ]
            option = build_flight_option(
                segments,
                provider=NAME,
                price_gbp=float(item.price) if item.price else None,
                airlines=list(item.airlines),
            )
        except (TypeError, ValueError, AttributeError) as exc:
            log.warning("skipping malformed fast-flights itinerary: %s", exc)
            continue
        # Google sometimes lists the same itinerary twice; keep the cheapest.
        prev = options.get(option.id)
        if prev is None or (option.price_gbp or 1e12) < (prev.price_gbp or 1e12):
            options[option.id] = option
    return list(options.values())


def parse_html(html: str) -> list[FlightOption]:
    head = html[:4000]
    if "consent.google.com" in head or "<title>Before you continue" in head:
        raise ProviderError(
            "Google returned a cookie-consent page instead of results "
            "(common for UK/EU IP addresses)"
        )
    try:
        results = parse(html)
    except FlightsNotFound:
        return []
    except Exception as exc:  # parser relies on undocumented page structure
        raise ProviderError(f"could not parse Google Flights page: {exc}") from exc
    return map_results(results)


class FastFlightsProvider:
    name = NAME

    def _fetch_html(self, origin: str, destination: str, day: date) -> str:
        query = create_query(
            flights=[
                FlightQuery(date=day.isoformat(), from_airport=origin, to_airport=destination)
            ],
            trip="one-way",
            seat="economy",
            language="en-GB",
            currency="GBP",
        )
        return fetch_flights_html(query)

    async def search_one_way(self, origin: str, destination: str, date: date) -> list[FlightOption]:
        try:
            html = await asyncio.to_thread(self._fetch_html, origin, destination, date)
        except Exception as exc:
            raise ProviderError(f"fast-flights request failed: {exc}") from exc
        return parse_html(html)
