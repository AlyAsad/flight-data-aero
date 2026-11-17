# UK Multi-Airport Flight Finder — Spec

Nov 16, 2026 · @Aly

## Overview

A locally run web app that shows one-way flight options between a fixed international airport and a set of UK airports, one week at a time, so the user can pick the cheapest or most convenient UK airport.

Two directions are supported:

- **From UK:** any enabled UK city group → one fixed destination (e.g. Newcastle, Manchester, London… → Karachi KHI).
- **To UK:** one fixed origin → any enabled UK city group (e.g. Karachi → Edinburgh, Birmingham…).

The non-UK side is always a single airport chosen by the user. Only the UK side fans out across cities.

The app is for decision support only. It shows flight data; it does not book or link out to booking sites.

## Scope

**In scope**

- One-way flights, 1 adult, economy, prices in GBP.
- 7-day view centred on the selected date (3 days before, 3 after), scrollable 7 days backward or forward.
- UK city sidebar with the cheapest price per city for the shown week.
- City × day heatmap of cheapest prices.
- Day tabs with the cheapest price per day for the selected city.
- Sortable flight list with full per-flight detail (see Data model).
- Local cache with configurable expiry (1–5 days), visible fetch times, and manual refresh.
- Data from fast-flights by default, SerpApi as an optional fallback.
- Runs locally from a cloned repo with a few commands.

**Out of scope**

- Return trips, multiple passengers, cabin classes other than economy.
- Filters of any kind (all data is shown; sorting only).
- Train data, home city, or travel-to-airport estimates.
- Booking, deep links to airlines or Google Flights.
- Hosting, authentication, multi-user support.
- Non-UK multi-airport expansion.

## UK airport groups

Eight city groups are enabled by default. They live in `config/uk_airports.json` so cities and airports can be toggled without code changes. Sheffield is dropped (no operating airport). Liverpool and East Midlands are disabled by default because they are mostly short-haul and rarely cheaper for long-haul.

| Group id | Display name | Airports (IATA) | Enabled by default |
| --- | --- | --- | --- |
| LON | London | LHR, LGW, STN, LTN | Yes |
| MAN | Manchester | MAN | Yes |
| BHX | Birmingham | BHX | Yes |
| EDI | Edinburgh | EDI | Yes |
| GLA | Glasgow | GLA | Yes |
| NCL | Newcastle | NCL | Yes |
| BRS | Bristol | BRS | Yes |
| LBA | Leeds Bradford | LBA | Yes |
| LPL | Liverpool | LPL | No |
| EMA | East Midlands | EMA | No |

A group's flights are the union of its airports' flights. Each flight row shows its actual airport, so London results show whether a flight uses Heathrow or Stansted.

Config shape:

```json
[{"id": "LON", "name": "London", "airports": ["LHR", "LGW", "STN", "LTN"], "enabled": true}]
```

## Search model and data flow

The unit of fetching and caching is one **route-day**: one UK airport, one foreign airport, one date, one direction. Everything in the UI is assembled from route-days.

A week view for 8 groups (11 airports) needs up to 77 route-days. To keep the first screen fast and avoid rate limits, the backend fetches in priority order:

1. Selected city, selected day.
2. Selected city, rest of the week.
3. All other enabled cities, the same week (fills sidebar and heatmap).
4. Nothing else. Adjacent weeks are fetched only when the user navigates to them.

Rules:

- Cached, unexpired route-days are never re-fetched unless the user refreshes.
- Dates in the past are skipped. Dates beyond \~330 days out are shown as "not yet on sale".
- When the user changes the selected city or week, the queue is re-prioritised; already-running jobs finish.
- A global worker pool fetches with limited concurrency (default 2) and a jittered delay (default 1–3 s) between requests.
- The frontend polls (or uses SSE) for progress and fills cells as route-days complete.
- If a fetch fails and an expired cache entry exists, show the expired data with a "stale" badge rather than nothing.
- The week picker accepts any date and centres the week around that day (for example, if a date is selected which is on a Wednesday, the information for that date will be fetched along with 3 days before and 3 days after that date).

## Data providers

fast-flights is the default provider; SerpApi is an optional fallback. Both sit behind one interface so the rest of the app never knows which one answered.

```python
class FlightProvider(Protocol):
    name: str
    async def search_one_way(self, origin: str, destination: str, date: date) -> list[FlightOption]: ...
```

**fast-flights (default)**

- Unofficial Python library that scrapes Google Flights. Free, no key, no quota.
- Pin an exact version in `pyproject.toml`. The library's API and output fields change between versions, so wrap it in an adapter and verify which fields it actually returns before mapping.
- It may not return flight numbers, layover details or baggage. Map what exists; leave the rest null.
- One request per airport per day.

**SerpApi Google Flights (fallback)**

- Key in `.env` as `SERPAPI_API_KEY`. Free plan: 250 searches/month ([pricing](https://serpapi.com/pricing)).
- Params: `engine=google_flights`, `type=2` (one-way), `adults=1`, `currency=GBP`, `gl=uk`, `hl=en`.
- `departure_id` / `arrival_id` accept comma-separated airports, so a whole UK group (e.g. `LHR,LGW,STN,LTN`) costs one search per day. Used for detail queries (see Batched overview queries below).
- Combine `best_flights` and `other_flights`. Richer data: flight numbers, layovers, overnight flags, aircraft, legroom, and extensions text that sometimes mentions baggage.
- Track monthly usage in the database; show "SerpApi: 37 / 250 used this month" in settings. Enforce a configurable hard cap (default 230) and never exceed it.

**Batched overview queries (SerpApi only)**

In SerpApi mode, the sidebar and heatmap are filled by a few multi-airport queries per day instead of one per city. Google Flights appears to cap a multi-airport field at about 7 airports. This is unconfirmed for SerpApi, so verify it in milestone 5 and make the size configurable (`SERPAPI_BATCH_MAX_AIRPORTS=7`).

The 11 default airports fit in 2 batches. Batches are built automatically from the enabled groups: groups are packed greedily and never split, so London's 4 airports stay together. Toggling groups re-packs the batches, and a larger set may need 3.

| Batch | Airports | Count |
| --- | --- | --- |
| A | LHR, LGW, STN, LTN, BHX, BRS | 6 |
| B | MAN, NCL, LBA, EDI, GLA | 5 |

If SerpApi rejects a batch for having too many airports, split it in half, retry, and store the lower batch size.

Two kinds of query:

- **Overview:** one per batch per day (14 per week-view). Fills the sidebar and heatmap. Results are partial: Google returns one ranked list per query, so a smaller airport can be crowded out by busier ones.
- **Detail:** one per group per day, for the selected city only (7 per week-view). Gives that city's complete list and replaces its overview data.

A typical week-view costs 21 searches instead of 56, about 11 week-views a month on the free plan. Other cities get detail queries only when selected.

fast-flights mode is unchanged: per-airport queries, all complete.

**Provider mode** (setting, default `auto`):

| Mode | Behaviour |
| --- | --- |
| `auto` | fast-flights first. After 3 consecutive fast-flights failures, use SerpApi (if a key exists) for the next 30 min, then retry fast-flights. |
| `fast_flights` | fast-flights only. |
| `serpapi` | SerpApi only, subject to the cap. |

In `auto` mode with SerpApi active, overview queries run automatically for the current week; detail queries run only for the selected city. Modes should be able to be selected in the website UI.

## Caching

All fetched data is stored in a local SQLite file, `data/flights.db` (git-ignored), with a TTL the user sets between 1 and 5 days (default 2) in the website UI, updating it doesn't refresh the data until the user presses the refresh button.

- Cache key: `(direction, uk_airport, foreign_airport, date)`. Group results are assembled from airport entries.
- A SerpApi group search returns flights for several airports at once; split them by actual UK airport and store one entry per airport. An airport with no flights is stored as an empty result, not a miss, except after an overview query, where absence may only mean it was crowded out. Each entry has a \`completeness\` field: \`full\` (per-airport or per-group query) or \`partial\` (overview query). Full data always replaces partial data.
- Each entry stores `fetched_at`, `expires_at`, `provider`, `status` (`ok`, `empty`, `error`) and the flight list as JSON.
- Changing the TTL recomputes `expires_at` for existing entries from their `fetched_at`.
- Errors are cached for 15 minutes only, so a temporary failure is retried soon.

**Refresh controls in the UI**

| Control | Effect |
| --- | --- |
| Refresh day | Re-fetch the selected city and day. |
| Refresh week | Re-fetch every route-day in the current view. |
| Refresh all cache | Mark every entry expired (data stays visible as stale until replaced), then re-fetch the current view. Asks for confirmation. |
| Clear cache | Delete all entries. Asks for confirmation. |

Cache times are always visible: each day tab and heatmap cell has a tooltip with "fetched 14 h ago", and the flight list header shows "Fetched 29 Dec 09:12 · expires in 34 h".

## Data model

Every field the provider gives is kept; fields a provider lacks are `null` and shown as "—". The UI never hides a flight because data is missing.

**FlightOption**

| Field | Type | Notes |
| --- | --- | --- |
| id | string | Stable hash of airports, times, flight numbers. |
| origin\_airport, destination\_airport | IATA string | Actual airports, not group ids. |
| departure\_local, arrival\_local | ISO datetime | Local time at each airport. |
| arrival\_day\_offset | int | +1, +2 for next-day arrivals. |
| duration\_minutes | int | Total including layovers. |
| stops | int | 0 = direct. |
| layovers | list | Each: airport, duration\_minutes, overnight (bool), airport\_change (bool). |
| airlines | list\[string\] | All marketing carriers on the itinerary. |
| flight\_numbers | list\[string\] or null |  |
| aircraft | list\[string\] or null |  |
| price\_gbp | number or null | Null if the provider shows "price unavailable". |
| baggage | object or null | carry\_on and checked, each `included`, `fee`, or `unknown`, plus raw text. |
| is\_self\_transfer | bool or null | Separate tickets, if the provider says so. |
| provider | string | `fast_flights` or `serpapi`. |
| fetched\_at | datetime | From the cache entry. |

**RouteDaySummary** (computed, not stored): group id, date, cheapest price, cheapest flight id, number of options, status, fetched\_at, is\_stale, is\_partial. Partial prices show with an asterisk ("£410\*") and a tooltip: "From an overview search; select this city for the full list."

**WeekSummary**: for each enabled group, 7 RouteDaySummaries plus the group's cheapest price for the week.

## Backend API

REST endpoints under `/api`, served by FastAPI on `localhost:8000`. `direction` is `from_uk` or `to_uk`; `foreign` is the non-UK IATA code; `center_date` is the selected date, and the view covers 3 days before to 3 days after it (`YYYY-MM-DD`).

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/airports?q=kara` | Autocomplete for the foreign airport (name, city, country, IATA) from a bundled airport dataset. |
| GET | `/uk-groups` | Enabled UK groups from config. |
| GET | `/week?direction&foreign&center_date&selected_group` | WeekSummary from cache. Enqueues missing or expired route-days in priority order. Returns `pending_count`. |
| GET | `/flights?direction&foreign&group&date` | All FlightOptions for one group and day, plus cache metadata. |
| GET | `/flights/week?direction&foreign&group&center_date` | All FlightOptions for one group across the week (for the "Whole week" tab). |
| GET | `/events` | Server-sent events: `route_day_done`, `route_day_error`, `queue_size`, `provider_switched`. |
| POST | `/refresh` | Body `{scope: "day" or "week", direction, foreign, group?, date?, center_date?}`. |
| POST | `/cache/expire-all` | Refresh all cache. |
| DELETE | `/cache` | Clear cache. |
| GET / PUT | `/settings` | `ttl_days` (1–5), `provider_mode`, `serpapi_monthly_cap`, `concurrency`, `delay_seconds`. |
| GET | `/status` | Provider health, SerpApi usage this month, queue size. |

Settings are stored in SQLite; `.env` values are defaults on first run.

## UI

One page, desktop-first, usable on a narrow window. All state (direction, foreign airport, week, group, day, sort) lives in the URL query string so a reload or bookmark restores the view.

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ [From UK ▸ | ◂ To UK]   Karachi (KHI) ▾     ◂  29 Dec – 4 Jan 2027  ▸   ⟳ ⚙ │
├──────────────┬───────────────────────────────────────────────────────────────┤
│ UK cities    │ HEATMAP          Mon   Tue   Wed   Thu   Fri   Sat   Sun     │
│              │ London           £412  £398  £455  £430  £510  £388  £402    │
│ London  £388 │ Manchester       £395  £401  £420  …                          │
│▸Manchest £395│ Newcastle        £520  £498  …                                │
│ Birming. £410│ …                                                             │
│ Newcastle£498├───────────────────────────────────────────────────────────────┤
│ Edinburgh£470│ Manchester → Karachi                                          │
│ Glasgow  £455│ [Mon £395][Tue £401][Wed £420]…[Sun £433][Whole week]         │
│ Bristol  £512│ Fetched 29 Dec 09:12 · expires in 34 h   [Refresh day]        │
│ Leeds    £530│ Dep ▾ │ Arr │ Duration │ Stops │ Airline │ Price │ Bags      │
│              │ rows… (click to expand)                                       │
└──────────────┴───────────────────────────────────────────────────────────────┘
```

**Top bar**

- Direction toggle. The label reads "From UK to …" or "From … to UK".
- Foreign airport autocomplete.
- Week navigator: ◂ / ▸ arrows plus a date picker. The 7-day view is centred on the picked date; arrows move it by 7 days. Defaults to today + 10 days (so the view starts a week out).
- ⟳ menu: Refresh week, Refresh all cache, Clear cache. ⚙ opens settings (TTL slider 1–5 days, provider mode, SerpApi cap and usage, concurrency).
- Small status line: queue progress ("Loading 23 of 77"), active provider, warnings like "fast-flights failing, using SerpApi".

**UK city sidebar**

- One row per enabled group with its cheapest price for the week; spinner while loading, "—" if no flights, ⚠ on error.
- Sort toggle: by price or alphabetical. Cheapest city gets a subtle highlight.
- Clicking a city selects it for the flight list.

**Heatmap (city × day)**

- Cell = cheapest price for that city and day. Colour scale green → red, relative to the week's min and max across all cells.
- Empty, error, stale, and not-yet-on-sale cells are visually distinct. Tooltip shows the fetch time.
- Clicking a cell selects that city and day.
- Collapsible, to give the flight list more room.

**Day tabs**

- Seven tabs for the selected city, each showing the day, date and cheapest price as a subheading, plus a "Whole week" tab listing all 7 days' flights together.

**Flight list**

- Columns: date (whole-week tab only), UK airport, departure, arrival (+1 marker), duration, stops, airlines, price, baggage icons.
- Every column sortable ascending/descending. Default sort: price.
- Row expands to show layovers (airport, duration, overnight, airport change), flight numbers, aircraft, self-transfer flag, raw baggage text, provider and fetch time.
- Highlight the cheapest and the shortest flight on the day.
- Times are local to each airport, labelled with the airport code.

## Tech stack and local setup

Python is required because fast-flights is a Python library, so the backend is FastAPI and the frontend is a separate React app.

| Layer | Choice |
| --- | --- |
| Backend | Python 3.12, FastAPI, Uvicorn, httpx, Pydantic v2, uv for dependencies |
| Flight data | fast-flights (pinned), SerpApi over HTTP |
| Storage | SQLite via SQLModel or plain sqlite3, file in `data/` |
| Airport dataset | `airportsdata` package or a bundled OurAirports CSV (large and medium airports with IATA codes) |
| Frontend | React + TypeScript + Vite, TanStack Query for fetching, TanStack Table for sorting, Tailwind CSS |
| Tooling | Ruff, pytest, Vitest, a `Makefile` |

**.env.example**

```text
SERPAPI_API_KEY=
PROVIDER_MODE=auto
CACHE_TTL_DAYS=2
SERPAPI_MONTHLY_CAP=230
FETCH_CONCURRENCY=2
FETCH_DELAY_SECONDS=1-3
```

**Running locally** (prerequisites: Python 3.12, uv, Node 20+):

```bash
git clone <repo> && cd <repo>
cp .env.example .env        # optionally add SERPAPI_API_KEY
make install                # venv + uv sync + npm install
make dev                    # backend :8000 + frontend :5173, opens browser
```

`make start` builds the frontend and has FastAPI serve it, so only one process runs on `localhost:8000`.&#32;

The server binds to `127.0.0.1` only, so the key and quota stay on the user's machine.

## Repo structure, testing and milestones

```text
backend/
  app/main.py            FastAPI app, static serving
  app/api/               routes
  app/providers/         base.py, fast_flights.py, serpapi.py
  app/fetcher.py         priority queue, worker pool, provider switching
  app/cache.py           SQLite cache and settings
  app/models.py          FlightOption, summaries
  config/uk_airports.json
  tests/                 fixtures/ with recorded provider responses
frontend/
  src/components/        TopBar, CitySidebar, Heatmap, DayTabs, FlightTable, SettingsDialog
data/                    git-ignored SQLite file
Makefile  .env.example  README.md
```

**Testing**

- Provider adapters are tested against recorded responses in `tests/fixtures`; no live calls in the test suite.
- A `make smoke` command does one live search (NCL → KHI, a date 30 days out) per provider to check they still work.
- Unit tests for cache expiry, priority ordering, group merging, and SerpApi cap enforcement.

**Milestones**

1. Backend skeleton, fast-flights adapter, `make smoke` returns flights for NCL → KHI.
2. SQLite cache, fetch queue, `/week` and `/flights` endpoints.
3. Frontend: top bar, city sidebar, day tabs, sortable flight list.
4. Heatmap and SSE progress.
5. SerpApi adapter, `auto` mode switching, usage tracking.
6. Settings dialog, refresh controls, stale handling, README.

**Acceptance check:** searching From UK → KHI for 7 days centred on 31 Dec 2026 shows all 8 cities with weekly cheapest prices, a filled heatmap, day-tab prices for the selected city, and a sortable list. Reloading within the TTL makes no new provider calls.
