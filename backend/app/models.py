"""Domain and API models."""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from .config import parse_delay_range


class Direction(StrEnum):
    FROM_UK = "from_uk"
    TO_UK = "to_uk"


class ProviderMode(StrEnum):
    AUTO = "auto"
    FAST_FLIGHTS = "fast_flights"
    SERPAPI = "serpapi"


class EntryStatus(StrEnum):
    OK = "ok"
    EMPTY = "empty"
    ERROR = "error"


class Completeness(StrEnum):
    FULL = "full"
    PARTIAL = "partial"


class DayStatus(StrEnum):
    """Status of a route-day summary as shown in the UI."""

    OK = "ok"
    EMPTY = "empty"
    ERROR = "error"
    PENDING = "pending"
    PAST = "past"
    NOT_ON_SALE = "not_on_sale"


# --------------------------------------------------------------------------- flights

BagStatus = Literal["included", "fee", "unknown"]


class Layover(BaseModel):
    airport: str
    duration_minutes: int | None = None
    overnight: bool = False
    airport_change: bool = False


class Baggage(BaseModel):
    carry_on: BagStatus = "unknown"
    checked: BagStatus = "unknown"
    raw: str | None = None


class FlightOption(BaseModel):
    id: str
    origin_airport: str
    destination_airport: str
    departure_local: datetime
    arrival_local: datetime
    arrival_day_offset: int = 0
    duration_minutes: int | None = None
    stops: int = 0
    layovers: list[Layover] = Field(default_factory=list)
    airlines: list[str] = Field(default_factory=list)
    flight_numbers: list[str] | None = None
    aircraft: list[str] | None = None
    price_gbp: float | None = None
    baggage: Baggage | None = None
    is_self_transfer: bool | None = None
    provider: str
    fetched_at: datetime | None = None


# --------------------------------------------------------------------------- groups


class UKGroup(BaseModel):
    id: str
    name: str
    airports: list[str]
    enabled: bool = True


# --------------------------------------------------------------------------- summaries


class RouteDaySummary(BaseModel):
    group_id: str
    date: Date
    cheapest_price: float | None = None
    cheapest_flight_id: str | None = None
    option_count: int = 0
    status: DayStatus
    fetched_at: datetime | None = None
    expires_at: datetime | None = None
    is_stale: bool = False
    is_partial: bool = False
    loading: bool = False
    error: str | None = None


class GroupWeek(BaseModel):
    group_id: str
    name: str
    airports: list[str]
    days: list[RouteDaySummary]
    cheapest_price: float | None = None
    status: DayStatus


class WeekSummary(BaseModel):
    direction: Direction
    foreign: str
    center_date: Date
    dates: list[Date]
    groups: list[GroupWeek]
    pending_count: int
    min_price: float | None = None
    max_price: float | None = None


class CacheMeta(BaseModel):
    status: DayStatus
    fetched_at: datetime | None = None
    expires_at: datetime | None = None
    is_stale: bool = False
    is_partial: bool = False
    providers: list[str] = Field(default_factory=list)
    error: str | None = None


class FlightsResponse(BaseModel):
    direction: Direction
    foreign: str
    group: str
    date: Date
    flights: list[FlightOption]
    meta: CacheMeta


class DayFlights(BaseModel):
    date: Date
    flights: list[FlightOption]
    meta: CacheMeta


class WeekFlightsResponse(BaseModel):
    direction: Direction
    foreign: str
    group: str
    center_date: Date
    days: list[DayFlights]


# --------------------------------------------------------------------------- settings


class AppSettings(BaseModel):
    ttl_days: int = Field(default=2, ge=1, le=5)
    provider_mode: ProviderMode = ProviderMode.AUTO
    serpapi_monthly_cap: int = Field(default=230, ge=0, le=250)
    concurrency: int = Field(default=2, ge=1, le=8)
    delay_seconds: tuple[float, float] = (0.5, 1.0)

    @field_validator("delay_seconds", mode="before")
    @classmethod
    def _parse_delay(cls, v: object) -> tuple[float, float]:
        return parse_delay_range(v)  # type: ignore[arg-type]


class SettingsUpdate(BaseModel):
    """Partial settings update (PUT /settings)."""

    ttl_days: int | None = Field(default=None, ge=1, le=5)
    provider_mode: ProviderMode | None = None
    serpapi_monthly_cap: int | None = Field(default=None, ge=0, le=250)
    concurrency: int | None = Field(default=None, ge=1, le=8)
    delay_seconds: tuple[float, float] | None = None

    @field_validator("delay_seconds", mode="before")
    @classmethod
    def _parse_delay(cls, v: object) -> tuple[float, float] | None:
        return None if v is None else parse_delay_range(v)  # type: ignore[arg-type]


class UKGroupsUpdate(BaseModel):
    enabled: list[str]


class RefreshRequest(BaseModel):
    scope: Literal["day", "week"]
    direction: Direction
    foreign: str
    group: str | None = None
    date: Date | None = None
    center_date: Date | None = None
