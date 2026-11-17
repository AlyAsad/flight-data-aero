# Implementation savepoint

Status as of 2026-11-17. All six spec milestones are implemented.

| Milestone | State |
| --- | --- |
| 1. Backend skeleton, fast-flights adapter, `make smoke` | Done. Smoke runs, but live fast-flights is blocked by Google's consent page on this network (see Open items). |
| 2. SQLite cache, fetch queue, `/week`, `/flights` | Done |
| 3. Frontend: top bar, sidebar, day tabs, sortable list | Done |
| 4. Heatmap, SSE progress | Done |
| 5. SerpApi adapter, auto switching, usage tracking | Done (offline-tested only, no key available) |
| 6. Settings dialog, refresh controls, stale handling, README | Done |

Tests: `make test` gives 54 backend tests and 25 frontend tests, all passing. `make lint` is clean.

## Open items

- **fast-flights consent wall**: from a UK IP, Google serves a cookie-consent page and fast-flights 3.1.0 gets no results. The adapter detects this and reports it. A fix (for example sending a consent cookie) needs the owner's approval.
- **Fixtures are hand-built**, not captured live (see `backend/tests/fixtures/README.md`). Replace them with `make smoke ARGS=--record` once live calls work.
- **SerpApi batch limit (7 airports)** is unverified against the live API (milestone 5 asks for this). Halve-and-retry is implemented. The "too many airports" error is matched heuristically in `providers/serpapi.py` (`TOO_MANY_AIRPORTS`), so check the real error text.
- **Acceptance check** (From UK → KHI, centred 31 Dec 2026) is unverified with live data for the same reason.
