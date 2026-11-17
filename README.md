# Aero

<img src="frontend/public/favicon.svg" width="48" alt="Aero logo">

Aero is a web app that compares one-way economy flights between one international airport and eight UK city groups, a week at a time, so you can pick the cheapest or most convenient UK airport. It shows data only. It does not book or link to booking sites.

- **From UK**: every enabled UK city → one destination (e.g. → Karachi KHI)
- **To UK**: one origin → every enabled UK city

See [spec.md](spec.md) for the full specification.

## Quick start

Prerequisites: Python 3.12, [uv](https://docs.astral.sh/uv/getting-started/installation/), Node 20+. Install uv with `curl -LsSf https://astral.sh/uv/install.sh | sh`. Use the standalone installer rather than `pip install uv` under pyenv, because pyenv shims can clash with uv.

```bash
cp .env.example .env        # optionally add SERPAPI_API_KEY
make install                # uv sync + npm install
make dev                    # API on :8000, UI on :5173 (opens browser)
```

`make start` builds the frontend and serves everything from FastAPI on `http://localhost:8000` as a single process. The server binds to `127.0.0.1` only.

Run `make help` to list every target.

## Using it

1. Choose a direction and type the foreign airport (name, city or IATA code).
2. Pick a date. The view covers 3 days before to 3 days after it. Use ◂ ▸ to move a week at a time.
3. The **sidebar** shows each city's cheapest fare that week. The **heatmap** shows the cheapest fare per city per day (green is cheap, red is expensive).
4. Click a city or cell to load its **day tabs** and **flight list**. Click any column header to sort. Click a row to see layovers, flight numbers, aircraft, baggage and provider.

Results fill in as they arrive. The status line shows progress ("Loading 23 of 77"), the active provider and any warnings. Hover a cell or tab to see when its data was fetched.

| Marker | Meaning |
| --- | --- |
| `£410*` | Partial: from a SerpApi overview search. Select the city to load the full list. |
| dashed cell / "stale" | Expired data, shown because a newer fetch hasn't succeeded yet |
| ⚠ | The fetch failed. It is retried after 15 minutes, or sooner with Refresh. |
| hatched cell | Date is in the past, or not yet on sale (more than ~330 days out) |

**⟳ menu**: Refresh week, Refresh all cache (marks everything expired, then re-fetches the view), Clear cache.
**⚙ settings**: provider mode, SerpApi cap and usage, concurrency, request delay (in ms, minimum 100) and which UK cities to include. Cache expiry is server configuration only (`CACHE_TTL_DAYS`). Enabling or disabling a city updates the sidebar and heatmap straight away.

## Data providers

| Provider | Cost | Notes |
| --- | --- | --- |
| [fast-flights](https://github.com/AWeirdDev/fast-flights) 3.1.0 (default) | Free | Unofficial Google Flights scraper, one request per UK airport per day. Includes flight numbers. No baggage or self-transfer info. |
| [SerpApi](https://serpapi.com/google-flights-api) (optional) | 250 searches/month free | Richer data. Searches a whole city group in one request. Usage is tracked and never exceeds the cap (default 230). |

Provider modes:

- `fast_flights` (default): fast-flights only.
- `auto`: uses fast-flights. After 3 consecutive failures it switches to SerpApi for 30 minutes, if a key is set. When fast-flights finds no flights for some airports in a city on a given day, one SerpApi query re-checks just those airports. That costs 1 search per empty city-day, stays within the cap, and isn't repeated until the cache expires.
- `serpapi`: SerpApi only.

In SerpApi mode the other cities are filled by **overview** queries, which batch up to 7 airports per request (`SERPAPI_BATCH_MAX_AIRPORTS`). If SerpApi rejects a batch as too large, the batch size is halved and the lower size is remembered. The selected city gets a complete **detail** query per day. SerpApi accepted 8 airports in testing, so 7 is a conservative default. Overview results really are partial: in a 7-airport test, Luton was crowded out.

Switching provider mode also applies to work already queued. Switch to `serpapi` while a week is loading and the rest of that week is fetched with SerpApi.

**fast-flights notes**

- Google shows a cookie-consent page to UK/EU IPs. The adapter sends consent cookies (`GOOGLE_CONSENT_COOKIE=true`) so results come back instead.
- fast-flights 3.1.0's own parser skips Google's "best flights" section and drops flight numbers, so the adapter parses the page payload itself and uses fast-flights only to build the query.
- For some less common routes, such as Bristol or Leeds Bradford to Karachi, Google's page holds no results even though SerpApi finds itineraries (usually a coach or rail leg to a hub airport). In `auto` mode the SerpApi double-check fills these in. In `fast_flights` mode they show "—".

## Configuration

`.env` values seed settings on first run. After that, settings live in SQLite and are edited in the UI.

| Variable | Default | |
| --- | --- | --- |
| `SERPAPI_API_KEY` | (empty) | Enables SerpApi |
| `PROVIDER_MODE` | `fast_flights` | `fast_flights`, `auto` or `serpapi` |
| `CACHE_TTL_DAYS` | `1` | Cache expiry, 1–5 days. Set here only, not in the UI. Applied to existing entries on restart; nothing is re-fetched until it expires or you refresh. |
| `SERPAPI_MONTHLY_CAP` | `230` | Hard cap, never exceeded |
| `SERPAPI_BATCH_MAX_AIRPORTS` | `7` | Max airports per overview query |
| `FETCH_CONCURRENCY` | `3` | Parallel fetch workers, max 8 |
| `FETCH_DELAY_SECONDS` | `0.5-1` | Random delay between requests, in seconds (0.1–60). Doubles per consecutive fast-flights failure, up to 30 s. |
| `GOOGLE_CONSENT_COOKIE` | `true` | Send Google consent cookies with fast-flights requests |
| `ADMIN_PASSWORD` | (empty) | If set, changing settings or cities, Refresh all cache and Clear cache need this password. Leave empty for local use. |

UK city groups and their airports are defined in [backend/config/uk_airports.json](backend/config/uk_airports.json), and its `"enabled"` values are the defaults. Turn cities on or off in ⚙ settings; that choice is saved in the database and overrides the JSON. Edit the JSON (and restart) only to add a city or change its airports. Liverpool and East Midlands are off by default.

## Deploying (free, on Koyeb)

The repo's `Dockerfile` builds the frontend and serves everything from FastAPI on `$PORT` (default 8000). Koyeb builds it straight from GitHub and redeploys on every push to `main`.

1. Push the repo to GitHub, then in [Koyeb](https://app.koyeb.com) choose **Create Web Service → GitHub** and pick this repo, branch `main`.
2. **Builder:** Dockerfile (detected automatically; no build or run command needed).
3. **Instance:** Free. **Region:** Frankfurt (closest to the UK).
4. **Environment variables:** add these (use **Secret** for the two secrets):

   | Name | Value |
   | --- | --- |
   | `ADMIN_PASSWORD` | Secret: a long password only you know |
   | `SERPAPI_API_KEY` | Secret, optional: needed only if you'll switch to `auto`/`serpapi` |
   | `PROVIDER_MODE` | `fast_flights` |
   | `CACHE_TTL_DAYS` | `2` |

5. **Ports:** `8000`, protocol HTTP, route `/`. **Health check:** HTTP, path `/api/status`, port `8000`.
6. Name the service `aero` and deploy. The site appears at `https://aero-<your-org>.koyeb.app`.

What to expect on the free instance (512 MB RAM, 0.1 vCPU):

- **Plenty of memory:** a local test peaked at about 125 MB while fetching a full week (77 requests).
- **Sleeping:** the app sleeps after 1 hour without traffic. The first visit afterwards takes roughly 10–20 seconds while Python starts on the small CPU share. A week of fetching takes a little longer than locally.
- **Wiped disk:** the disk is cleared on every restart or redeploy. The flight cache refills on demand. Settings changed in the UI return to the environment variables above, so put your defaults there. The SerpApi usage counter is re-synced from SerpApi's free Account API at startup and every 30 minutes, so the monthly cap still holds.
- **Google and cloud IPs:** Google may treat cloud IPs more strictly. If the status line shows fast-flights failing, unlock admin mode in ⚙ and switch to `auto` so SerpApi fills the gaps.

**Admin mode:** with `ADMIN_PASSWORD` set, visitors can browse, search and use Refresh day/week. ⚙ settings, city toggles, Refresh all cache and Clear cache need the password. It's checked on the server and kept only in that browser tab's session.

To run the same image locally: `docker build -t aero . && docker run -p 8000:8000 --env-file .env aero`.

## Development

```bash
make test       # pytest + vitest (offline, uses recorded fixtures)
make lint       # ruff + tsc + eslint
make smoke      # one live NCL → KHI search per provider
make smoke ARGS=--record   # re-capture the live fixtures in backend/tests/fixtures/*_live.*
```

```text
backend/
  app/main.py            FastAPI app, static serving
  app/api/routes.py      REST + SSE endpoints (/api/...)
  app/providers/         base.py (interface), fast_flights.py, serpapi.py
  app/fetcher.py         priority queue, worker pool, provider switching
  app/cache.py           SQLite cache, settings, SerpApi usage
  app/summary.py         week / day summaries from cache entries
  app/models.py          FlightOption, summaries, settings
  config/uk_airports.json
  tests/                 pytest suite; fixtures/ holds provider responses
frontend/src/
  components/            TopBar, CitySidebar, Heatmap, DayTabs, FlightTable, SettingsDialog, Footer, Logo
  hooks/                 TanStack Query hooks, SSE subscription
  lib/                   API client, URL state, formatting
data/                    SQLite database (git-ignored)
```

### How fetching works

The unit of caching is a **route-day**: one UK airport, one foreign airport, one date, one direction. Opening a week view queues the missing or expired route-days in this order:

1. The selected city on the selected day
2. The selected city's other days, nearest first
3. Every other city for the week

Navigating to another view re-prioritises the queue: the new view's jobs run first. Pending jobs from up to 2 other views opened in the last 10 minutes (another tab, say) wait behind them, and older views' jobs are dropped. Running jobs always finish. Cached, unexpired data is never re-fetched unless you refresh, so reloading within the TTL makes no provider calls.
