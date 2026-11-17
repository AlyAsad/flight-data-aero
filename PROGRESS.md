# Implementation savepoint

Status as of 2026-11-17. All six spec milestones are implemented and checked against live providers.

| Milestone | State |
| --- | --- |
| 1. Backend, fast-flights adapter, `make smoke` | Done. Smoke passes for both providers. |
| 2. SQLite cache, fetch queue, `/week`, `/flights` | Done |
| 3. Frontend: top bar, sidebar, day tabs, sortable list | Done |
| 4. Heatmap, SSE progress | Done |
| 5. SerpApi adapter, auto switching, usage tracking | Done. Live detail query verified. Batch of 8 airports accepted. |
| 6. Settings, refresh controls, stale handling, README | Done |

Tests: `make test` gives 70 backend tests and 28 frontend tests. `make lint` is clean.

Live acceptance check (From UK → KHI, centred 31 Dec 2026, fast-flights): the week filled in about 2.75 minutes with 0 errors, and a reload within the TTL made 0 provider calls. Six of the eight cities have prices from fast-flights. Bristol and Leeds Bradford are filled in by the `auto`-mode SerpApi double-check (verified live: BRS from £562, LBA from £596).

Rate test at the 500–1000 ms default delay (concurrency 2): 2 bursts of about 75 requests each, at about 1.25 requests/s. 148 of 150 succeeded; the 2 failures were one-off and not rate limiting.

## Open items

- **SerpApi's real airport limit** is at least 8. The "too many airports" error text is still unknown, so `TOO_MANY_AIRPORTS` in `providers/serpapi.py` is a heuristic.
- **Skyscanner-only fares** (for example Pegasus or self-transfer combinations): would need a third-party provider (SearchApi.io `skyscanner_flights`). Not implemented; needs owner decision.
