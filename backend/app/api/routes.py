"""REST + SSE endpoints under /api."""

from __future__ import annotations

import asyncio
import hmac
from datetime import date as Date
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sse_starlette.sse import EventSourceResponse

from ..airports import Airport, search_airports
from ..dates import week_dates
from ..models import (
    AppSettings,
    DayFlights,
    Direction,
    FlightsResponse,
    RefreshRequest,
    SettingsUpdate,
    UKGroup,
    UKGroupsUpdate,
    WeekFlightsResponse,
    WeekSummary,
)
from ..state import AppState, get_state

router = APIRouter(prefix="/api")

State = Annotated[AppState, Depends(get_state)]
IATA = Annotated[str, Query(min_length=3, max_length=3, pattern=r"^[A-Za-z]{3}$")]


FAILED_LOGIN_DELAY_SECONDS = 1.0  # slows down password guessing


async def require_admin(
    state: State, x_admin_password: Annotated[str | None, Header()] = None
) -> None:
    """Guard for shared settings. A no-op when ADMIN_PASSWORD is not configured."""
    expected = state.env.admin_password
    if not expected:
        return
    if x_admin_password and hmac.compare_digest(x_admin_password.encode(), expected.encode()):
        return
    await asyncio.sleep(FAILED_LOGIN_DELAY_SECONDS)
    raise HTTPException(
        401, "admin password required" if not x_admin_password else "wrong admin password"
    )


Admin = Depends(require_admin)


def _group(state: AppState, group_id: str) -> UKGroup:
    group = next((g for g in state.groups if g.id == group_id), None)
    if group is None:
        raise HTTPException(404, f"unknown or disabled UK group: {group_id}")
    return group


# ---------------------------------------------------------------------- lookups


@router.get("/airports")
def airports(q: Annotated[str, Query(min_length=1, max_length=64)]) -> list[Airport]:
    return search_airports(q)


@router.get("/uk-groups")
def uk_groups(state: State, include_disabled: bool = False) -> list[UKGroup]:
    return state.group_store.all() if include_disabled else list(state.groups)


@router.put("/uk-groups", dependencies=[Admin])
def set_uk_groups(state: State, body: UKGroupsUpdate) -> list[UKGroup]:
    """Enable exactly the given groups (all others are disabled)."""
    try:
        groups = state.group_store.set_enabled(body.enabled)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    state.fetcher.set_groups(state.groups)
    return groups


# ---------------------------------------------------------------------- flights


@router.get("/week")
def week(
    state: State,
    direction: Direction,
    foreign: IATA,
    center_date: Date,
    selected_group: str | None = None,
    selected_date: Date | None = None,
) -> WeekSummary:
    foreign = foreign.upper()
    if selected_group is not None:
        _group(state, selected_group)
    state.fetcher.plan_view(direction, foreign, center_date, selected_group, selected_date)
    return state.summaries.week(
        direction, foreign, center_date, state.groups, state.fetcher.queued_keys()
    )


@router.get("/flights")
def flights(
    state: State, direction: Direction, foreign: IATA, group: str, date: Date
) -> FlightsResponse:
    foreign = foreign.upper()
    g = _group(state, group)
    [(day, items, meta, _)] = state.summaries.group_days(
        direction, foreign, g, [date], state.fetcher.queued_keys()
    )
    return FlightsResponse(
        direction=direction, foreign=foreign, group=group, date=day, flights=items, meta=meta
    )


@router.get("/flights/week")
def flights_week(
    state: State, direction: Direction, foreign: IATA, group: str, center_date: Date
) -> WeekFlightsResponse:
    foreign = foreign.upper()
    g = _group(state, group)
    rows = state.summaries.group_days(
        direction, foreign, g, week_dates(center_date), state.fetcher.queued_keys()
    )
    return WeekFlightsResponse(
        direction=direction,
        foreign=foreign,
        group=group,
        center_date=center_date,
        days=[DayFlights(date=d, flights=items, meta=meta) for d, items, meta, _ in rows],
    )


@router.get("/events")
async def events(request: Request, state: State) -> EventSourceResponse:
    async def stream():
        async for event, data in state.bus.subscribe():
            if await request.is_disconnected():
                break
            yield {"event": event, "data": data}

    return EventSourceResponse(stream(), ping=15)


# ---------------------------------------------------------------------- refresh / cache


@router.post("/refresh")
def refresh(state: State, body: RefreshRequest) -> dict:
    foreign = body.foreign.upper()
    if body.scope == "day":
        if not body.group or not body.date:
            raise HTTPException(422, "scope=day requires group and date")
        queued = state.fetcher.refresh_day(
            body.direction, foreign, _group(state, body.group), body.date
        )
        return {"queued": int(queued)}
    if not body.center_date:
        raise HTTPException(422, "scope=week requires center_date")
    n = state.fetcher.plan_view(
        body.direction, foreign, body.center_date, body.group, body.date, force=True
    )
    return {"queued": n}


@router.post("/cache/expire-all", dependencies=[Admin])
def expire_all(state: State) -> dict:
    return {"expired": state.cache.expire_all()}


@router.delete("/cache", dependencies=[Admin])
def clear_cache(state: State) -> dict:
    state.fetcher.clear_queue()
    return {"deleted": state.cache.clear()}


# ---------------------------------------------------------------------- settings / status


@router.post("/admin/check", dependencies=[Admin])
def admin_check() -> dict:
    """Lets the UI verify an admin password before unlocking settings."""
    return {"ok": True}


@router.get("/settings")
def get_settings(state: State) -> AppSettings:
    return state.settings.get()


@router.put("/settings", dependencies=[Admin])
def put_settings(state: State, body: SettingsUpdate) -> AppSettings:
    current = state.settings.get()
    updated = current.model_copy(update=body.model_dump(exclude_none=True))
    updated = AppSettings.model_validate(updated.model_dump())
    state.settings.save(updated)
    if updated.concurrency != current.concurrency:
        state.fetcher.set_concurrency(updated.concurrency)
    if updated.provider_mode != current.provider_mode:
        state.providers.reset()
    return updated


@router.get("/status")
def status(state: State) -> dict:
    settings = state.settings.get()
    p = state.providers
    return {
        "admin_protected": bool(state.env.admin_password),
        "active_provider": p.peek_active_name(),
        "provider_mode": settings.provider_mode,
        "switch_reason": p.switch_reason(),
        "serpapi_until": p.serpapi_until,
        "providers": {
            "fast_flights": {
                "available": True,
                "consecutive_failures": p.consecutive_ff_failures,
                "last_error": p.last_error.get("fast_flights"),
            },
            "serpapi": {
                "available": p.serpapi.available,
                "last_error": p.last_error.get("serpapi"),
            },
        },
        "serpapi_usage": {"used": state.usage.used(), "cap": settings.serpapi_monthly_cap},
        "serpapi_batch_max_airports": state.fetcher.batch_size,
        "queue": {
            "pending": state.fetcher.queue_size,
            "total": state.fetcher.progress.total,
            "done": min(state.fetcher.progress.done, state.fetcher.progress.total),
        },
    }
