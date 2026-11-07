"""Read activity dates out of trade ledgers whose shape you do not control.

The problem this solves is boring and is most of the work: every strategy writes its
ledger a little differently. One keeps `open_positions` as a dict and `closed_trades` as a
list; another has `completed_trades` with `entry_date`; a third stores a single position
under a singular `position` key that is null when flat; a fourth records trades under a
bare `date`.

A reader with a branch per shape looks tidy and fails in the worst possible way: it
returns **zero** for a shape it has not met, and zero reads as "this strategy is quiet",
which is exactly the claim you were trying to check. A silent zero is worse than a crash.

So this scans recursively for anything that looks like an activity date and deliberately
**over-collects**. Over-collecting makes a book look busier than it is, which fails a
"quiet" claim rather than excusing a loud one. That is the safe direction: the tool's job
is to raise its hand, and a false alarm costs a minute while a false pass costs you the
thing you built it for.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from pathlib import Path
from typing import Any, Iterable

# Keys whose value is a date on which the book DID something.
#
# `date` is in here on purpose and it is the reason this measures ACTIVE days rather than
# strictly entries: plenty of ledgers record a trade as {"date": ..., "side": "buy"} with
# no entry_date at all. Dropping it would report those books as empty, and an "empty" book
# trivially satisfies any claim that permits inactivity -- a pass for entirely the wrong
# reason. The cost is that a book recording both an open and a close counts two active
# days, which is the right unit anyway: a day the strategy made you do something.
ACTIVITY_KEYS = ("entry_date", "entry_time", "opened_at", "closed_at", "exit_date", "date")

# Containers holding a per-session snapshot rather than a trade. An equity curve carries a
# `date` for every day the process ran, so scanning it would make every strategy look like
# it trades daily -- the exact false pass this module exists to avoid.
SKIP_KEYS = frozenset(
    {
        "equity_history",
        "equity_curve",
        "daily_equity",
        "nav_history",
        "balance_history",
        "corrections",
        "metadata",
    }
)

_ISO_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def _as_date(value: Any) -> str | None:
    """The ISO date at the front of a value, or None. Accepts dates and datetimes."""
    if not isinstance(value, str):
        return None
    m = _ISO_DATE.match(value.strip())
    if not m:
        return None
    try:
        _dt.date.fromisoformat(m.group(1))
    except ValueError:
        return None
    return m.group(1)


def activity_days(
    obj: Any,
    *,
    activity_keys: Iterable[str] = ACTIVITY_KEYS,
    skip_keys: Iterable[str] = SKIP_KEYS,
) -> list[str]:
    """Every distinct ISO date on which this ledger shows activity, sorted.

    Shape-agnostic by design -- see the module docstring for why that is a feature and not
    laziness.
    """
    wanted = frozenset(activity_keys)
    skipped = frozenset(skip_keys)
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in skipped:
                    continue
                if key in wanted:
                    day = _as_date(value)
                    if day is not None:
                        found.add(day)
                        continue
                walk(value)
        elif isinstance(node, (list, tuple)):
            for item in node:
                walk(item)

    walk(obj)
    return sorted(found)


class Activity:
    """What a ledger says about when a strategy was busy.

    Deliberately holds no opinion about whether that is good. Measurement and judgement are
    separated so you can look at the numbers without the rules arguing with you.
    """

    def __init__(self, days: list[str], *, today: _dt.date | None = None) -> None:
        self.days = days
        self._today = today or _dt.date.today()

    @classmethod
    def from_file(cls, path: str | Path, **kwargs: Any) -> "Activity":
        with open(path, encoding="utf-8") as handle:
            return cls(activity_days(json.load(handle), **kwargs))

    @property
    def count(self) -> int:
        return len(self.days)

    @property
    def dates(self) -> list[_dt.date]:
        return [_dt.date.fromisoformat(d) for d in self.days]

    @property
    def gaps(self) -> list[int]:
        """Calendar days between consecutive active days.

        Calendar, not trading days, and the README says so where a user will see it. Using
        a market calendar would mean a dependency and a venue assumption for a tool whose
        whole pitch is that it runs anywhere with no setup.
        """
        d = self.dates
        return [(b - a).days for a, b in zip(d, d[1:])]

    @property
    def median_gap(self) -> float | None:
        g = sorted(self.gaps)
        if not g:
            return None
        mid = len(g) // 2
        return float(g[mid]) if len(g) % 2 else (g[mid - 1] + g[mid]) / 2

    @property
    def max_gap(self) -> int | None:
        return max(self.gaps) if self.gaps else None

    @property
    def days_since_last(self) -> int | None:
        return (self._today - self.dates[-1]).days if self.days else None

    def __repr__(self) -> str:  # pragma: no cover - debugging convenience
        return (
            f"Activity(count={self.count}, median_gap={self.median_gap}, "
            f"max_gap={self.max_gap}, days_since_last={self.days_since_last})"
        )
