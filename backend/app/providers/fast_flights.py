"""fast-flights adapter (unofficial Google Flights scraper, pinned to 3.1.0).

fast-flights builds the query (protobuf `tfs` URL). The page payload is parsed
here rather than with `fast_flights.parser.parse`, because 3.1.0's parser has
two problems, verified against live responses:

* It reads only `payload[3]` ("other flights") and skips `payload[2]` ("best
  flights"), which often holds the cheapest itineraries.
* It drops each segment's flight number (`segment[22] = [carrier, number, _, name]`).

Field positions follow the library's parser. Price 0 means "price unavailable".
Not available from Google's payload: baggage, self-transfer flag.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime
from typing import Any

from fast_flights import FlightQuery, create_query
from fast_flights.parser import _parse_time
from primp import Client
from selectolax.lexbor import LexborHTMLParser

from ..models import FlightOption
from .base import ProviderError, Segment, build_flight_option

log = logging.getLogger(__name__)

NAME = "fast_flights"
GOOGLE_FLIGHTS_URL = "https://www.google.com/travel/flights"
# Google serves a cookie-consent interstitial to UK/EU IPs. These cookies record
# "consent given" so the results page is returned instead (owner-approved).
CONSENT_COOKIES = {
    "SOCS": "CAESHAgBEhJnd3NfMjAyMzA4MTAtMF9SQzIaAmVuIAEaBgiAo_CmBg",
    "CONSENT": "YES+",
}
RESULT_SECTIONS = (2, 3)  # best flights, other flights


def _dt(day: list[int], time: list[int | None] | None) -> datetime:
    hh, mm = _parse_time(time)
    return datetime(day[0], day[1], day[2], hh, mm)


def _flight_number(seg: list[Any]) -> str | None:
    info = seg[22] if len(seg) > 22 else None
    if isinstance(info, list) and len(info) > 1 and info[0] and info[1]:
        return f"{info[0]} {info[1]}"
    return None


def _map_itinerary(item: list[Any]) -> FlightOption:
    flight, price = item[0], item[1][0][1]
    segments = [
        Segment(
            origin=seg[3],
            destination=seg[6],
            departure=_dt(seg[20], seg[8]),
            arrival=_dt(seg[21], seg[10]),
            duration_minutes=seg[11],
            airline=(seg[22][3] if isinstance(seg[22], list) and len(seg[22]) > 3 else None),
            flight_number=_flight_number(seg),
            aircraft=seg[17] or None,
        )
        for seg in flight[2]
    ]
    return build_flight_option(
        segments,
        provider=NAME,
        price_gbp=float(price) if price else None,
        airlines=list(flight[1]) or None,
    )


def map_payload(payload: list[Any]) -> list[FlightOption]:
    """Map both result sections, skipping malformed itineraries and duplicates."""
    options: dict[str, FlightOption] = {}
    for idx in RESULT_SECTIONS:
        section = payload[idx] if len(payload) > idx else None
        if not section or not section[0]:
            continue
        for item in section[0]:
            try:
                option = _map_itinerary(item)
            except (IndexError, KeyError, TypeError, ValueError) as exc:
                log.warning("skipping malformed Google Flights itinerary: %s", exc)
                continue
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
    script = LexborHTMLParser(html).css_first(r"script.ds\:1")
    if script is None:
        raise ProviderError("could not find flight data in Google Flights page")
    js = script.text()
    try:
        data = js.split("data:", 1)[1].rsplit(",", 1)[0]
        if data.endswith("errorHasStatus: true"):
            return []  # Google's "no flights found" response
        payload = json.loads(data)
    except (IndexError, ValueError) as exc:
        raise ProviderError(f"could not parse Google Flights page: {exc}") from exc
    return map_payload(payload)


class FastFlightsProvider:
    name = NAME

    def __init__(self, send_consent_cookie: bool = True):
        self._send_consent_cookie = send_consent_cookie

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
        # Same client settings as fast_flights.fetch_flights_html, plus consent cookies.
        client = Client(
            impersonate="chrome_145", impersonate_os="macos", referer=True, cookie_store=True
        )
        res = client.get(
            GOOGLE_FLIGHTS_URL,
            params={**query.params(), "gl": "uk"},
            cookies=CONSENT_COOKIES if self._send_consent_cookie else None,
        )
        if res.status_code >= 400:
            raise ProviderError(f"Google Flights HTTP {res.status_code}")
        return res.text

    async def search_one_way(self, origin: str, destination: str, date: date) -> list[FlightOption]:
        try:
            html = await asyncio.to_thread(self._fetch_html, origin, destination, date)
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"fast-flights request failed: {exc}") from exc
        return parse_html(html)
