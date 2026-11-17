# Provider fixtures

Offline responses used by the adapter tests (the suite makes no live calls).

| File | What it is |
| --- | --- |
| `fast_flights_ncl_khi.html` | A Google Flights page in the shape the pinned fast-flights 3.1.0 parser reads (`script.ds:1` payload). |
| `fast_flights_consent.html` | Google's cookie-consent interstitial, which can be served instead of results from UK/EU IPs. |
| `serpapi_lon_khi.json` | A SerpApi `google_flights` one-way response for `LHR,LGW,STN,LTN → KHI`. |
| `serpapi_no_results.json` | SerpApi's "no results" error body. |

These were **built by hand** to match the library's parser indices and SerpApi's
documented schema, not captured from live traffic. To replace them with real
responses, run `make smoke ARGS=--record` from the repo root. It writes live
responses next to these files with a `.live` suffix so you can compare before
swapping them in.
