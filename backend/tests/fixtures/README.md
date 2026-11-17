# Provider fixtures

Responses used by the adapter tests (the suite makes no live calls).

| File | Source | What it is |
| --- | --- | --- |
| `fast_flights_ncl_khi_live.html` | Live, 2026-11-17 | Google Flights NCL → KHI (17 Dec 2026), reduced to the `ds:1` data script. |
| `serpapi_ncl_khi_live.json` | Live, 2026-11-17 | SerpApi response for the same search (no API key inside). |
| `fast_flights_ncl_khi.html` | Hand-built | Edge cases: zero price, leading-zero hour, overnight layover. |
| `fast_flights_consent.html` | Hand-built | Google's cookie-consent interstitial. |
| `serpapi_lon_khi.json` | Hand-built | Multi-airport London group: baggage text, self-transfer, airport change, missing price. |
| `serpapi_no_results.json` | Hand-built | SerpApi's "no results" error body. |

`make smoke ARGS=--record` re-captures the two live fixtures. Tests assert on
their contents, so update the assertions in `test_providers.py` after recording.
