"""UK airport groups loaded from `config/uk_airports.json`, plus SerpApi batch packing."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from .config import UK_AIRPORTS_CONFIG
from .models import UKGroup


def load_groups(path: Path = UK_AIRPORTS_CONFIG) -> list[UKGroup]:
    with path.open(encoding="utf-8") as fh:
        raw = json.load(fh)
    groups = [UKGroup.model_validate(item) for item in raw]
    ids = [g.id for g in groups]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate group ids in uk_airports.json")
    return groups


@lru_cache
def enabled_groups() -> tuple[UKGroup, ...]:
    return tuple(g for g in load_groups() if g.enabled)


def get_group(group_id: str) -> UKGroup | None:
    return next((g for g in enabled_groups() if g.id == group_id), None)


def group_for_airport(airport: str, groups: tuple[UKGroup, ...] | None = None) -> UKGroup | None:
    for g in groups if groups is not None else enabled_groups():
        if airport in g.airports:
            return g
    return None


def pack_batches(
    groups: list[UKGroup] | tuple[UKGroup, ...], max_airports: int
) -> list[list[UKGroup]]:
    """Pack groups into batches of at most `max_airports` airports, never splitting a group.

    First-fit decreasing: largest groups first, each placed into the first batch
    with room. A group larger than `max_airports` gets a batch of its own (it
    cannot be split; the caller handles rejection by lowering the size).
    """
    batches: list[list[UKGroup]] = []
    sizes: list[int] = []
    ordered = sorted(groups, key=lambda g: (-len(g.airports), g.id))
    for group in ordered:
        n = len(group.airports)
        for i, used in enumerate(sizes):
            if used + n <= max_airports:
                batches[i].append(group)
                sizes[i] += n
                break
        else:
            batches.append([group])
            sizes.append(n)
    return batches
