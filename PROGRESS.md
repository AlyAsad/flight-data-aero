# Implementation savepoint

Status as of 2026-11-17. All six spec milestones are implemented. The app has been restructured for Vercel, and the same code runs locally.

**Architecture change from the spec:** fetching is driven by the browser rather than a server-side worker pool, and the live-update stream is gone. `/api/week` returns the jobs a view still needs; the browser runs them through `/api/fetch` (`frontend/src/lib/fetchQueue.ts`). The database is a local SQLite file, or Turso over HTTP when `TURSO_DATABASE_URL` is set (`backend/app/db.py`). Provider health, settings, SerpApi usage and per-city-day fetch claims all live in the database, so serverless instances share them.

Tests: `make test` runs 139 backend tests (the cache, fetcher and API tests run against both SQLite and a fake Turso server) and 38 frontend tests. `make lint` is clean.

Live checks:

- Local end-to-end (production build, headless browser, From UK → KHI centred on 31 Dec 2026): 77 route-days in 39 s via 56 `/api/fetch` calls. A reload made 0 fetch calls. No page errors.
- Vercel IP probe from London (`lhr1`): Google returned results for 12/20, then 15/20 searches. No hard block. The other responses were Google's transient error payload, which the adapter now retries twice and otherwise records as an error, not "no flights".

## Open items

- **First Vercel deploy:** not yet done (needs the user's Vercel and Turso accounts; steps in README). Things to confirm on it:
  - Vercel builds the frontend (`buildCommand` in `vercel.json`) and serves it.
  - The function finds `backend.app.main:app`.
  - Turso is reachable.
- **SerpApi's real airport limit** is at least 8. The "too many airports" error text is still unknown, so `TOO_MANY_AIRPORTS` in `providers/serpapi.py` is a heuristic.
