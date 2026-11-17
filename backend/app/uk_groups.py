"""UK airport groups loaded from `config/uk_airports.json`, plus SerpApi batch packing."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from .cache import SettingsStore
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


class GroupStore:
    """UK groups from the JSON config, with enabled flags overridable from the UI.

    Overrides live in SQLite (`settings` table) so the tracked config file is
    never rewritten; the JSON `enabled` values are the defaults.
    """

    KEY = "uk_group_enabled"

    def __init__(self, settings: SettingsStore, base: Sequence[UKGroup]):
        self._settings = settings
        self._base = list(base)

    def _overrides(self) -> dict[str, bool]:
        raw = self._settings.get_value(self.KEY)
        return json.loads(raw) if raw else {}

    def all(self) -> list[UKGroup]:
        overrides = self._overrides()
        return [
            g.model_copy(update={"enabled": overrides.get(g.id, g.enabled)}) for g in self._base
        ]

    def enabled(self) -> tuple[UKGroup, ...]:
        return tuple(g for g in self.all() if g.enabled)

    def set_enabled(self, enabled_ids: Sequence[str]) -> list[UKGroup]:
        known = {g.id for g in self._base}
        unknown = set(enabled_ids) - known
        if unknown:
            raise ValueError(f"unknown UK group(s): {', '.join(sorted(unknown))}")
        if not enabled_ids:
            raise ValueError("at least one UK group must be enabled")
        wanted = set(enabled_ids)
        self._settings.set_value(self.KEY, json.dumps({g.id: g.id in wanted for g in self._base}))
        return self.all()


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
