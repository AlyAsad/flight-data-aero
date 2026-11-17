"""Live smoke test: one NCL → KHI search, 30 days out, per provider.

Usage: uv run python -m scripts.smoke [--record]

--record overwrites the live-captured fixtures in tests/fixtures/*_live.*
(the test suite asserts on their contents, so re-check tests after recording). SerpApi is skipped
when SERPAPI_API_KEY is not set; a SerpApi run uses 1 search of your quota.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import httpx

from app.cache import Database, SettingsStore, UsageStore
from app.config import get_env
from app.providers.base import ProviderError
from app.providers.fast_flights import FastFlightsProvider, parse_html
from app.providers.serpapi import ENDPOINT, parse_response

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
ORIGIN, DEST = "NCL", "KHI"


def _strip_page(html: str) -> str:
    """Keep only the data script the parser reads (the full page is ~2 MB)."""
    from selectolax.lexbor import LexborHTMLParser

    script = LexborHTMLParser(html).css_first(r"script.ds\:1")
    if script is None:
        return html
    return (
        "<!doctype html><html><head><title>Google Flights</title></head><body>"
        f'<script class="ds:1">{script.text()}</script></body></html>'
    )


def report(name: str, flights) -> None:
    print(f"[{name}] {len(flights)} flights")
    for f in sorted(flights, key=lambda f: f.price_gbp or 1e9)[:5]:
        price = f"£{f.price_gbp:.0f}" if f.price_gbp is not None else "—"
        print(
            f"   {price:>6}  {f.departure_local:%d %b %H:%M} → {f.arrival_local:%H:%M}"
            f"{'+' + str(f.arrival_day_offset) if f.arrival_day_offset else ''}"
            f"  {f.stops} stop(s)  {', '.join(f.airlines)}"
        )


async def smoke_fast_flights(day: date, record: bool) -> bool:
    provider = FastFlightsProvider()
    try:
        html = await asyncio.to_thread(provider._fetch_html, ORIGIN, DEST, day)
        if record:
            (FIXTURES / "fast_flights_ncl_khi_live.html").write_text(_strip_page(html))
        flights = parse_html(html)
    except ProviderError as exc:
        print(f"[fast_flights] FAILED: {exc}")
        return False
    report("fast_flights", flights)
    return bool(flights)


async def smoke_serpapi(day: date, record: bool) -> bool | None:
    env = get_env()
    if not env.serpapi_api_key:
        print("[serpapi] skipped (SERPAPI_API_KEY not set)")
        return None
    db = Database(env.db_path)
    usage, settings = UsageStore(db), SettingsStore(db, env)
    if not usage.try_reserve(settings.get().serpapi_monthly_cap):
        print("[serpapi] skipped (monthly cap reached)")
        return None
    params = {
        "engine": "google_flights",
        "type": "2",
        "adults": "1",
        "currency": "GBP",
        "gl": "uk",
        "hl": "en",
        "departure_id": ORIGIN,
        "arrival_id": DEST,
        "outbound_date": day.isoformat(),
        "api_key": env.serpapi_api_key,
    }
    async with httpx.AsyncClient(timeout=60) as client:
        data = (await client.get(ENDPOINT, params=params)).json()
    if record:
        data.pop("search_metadata", None)
        (FIXTURES / "serpapi_ncl_khi_live.json").write_text(json.dumps(data, indent=2))
    try:
        flights = parse_response(data)
    except ProviderError as exc:
        print(f"[serpapi] FAILED: {exc}")
        return False
    report("serpapi", flights)
    return bool(flights)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", action="store_true")
    args = parser.parse_args()
    day = date.today() + timedelta(days=30)
    print(f"Smoke test {ORIGIN} → {DEST} on {day}")
    results = [await smoke_fast_flights(day, args.record), await smoke_serpapi(day, args.record)]
    return 0 if all(r is not False for r in results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
