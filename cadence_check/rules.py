"""Judging a prose claim against a measurement.

You cannot assert that "Weekly" equals a number. That is the whole difficulty, and the way
round it is to stop trying: a label is given a **permitted band**, and the check is only
that the measurement does not fall outside it. The tool never tries to reproduce the label
from the data. It only ever says "this label and this ledger disagree."

Four rules do the work. Three of them exist because of a specific failure they would have
caught, which is the only reason worth adding a rule:

1. **Band.** The median gap between active days must sit inside the label's range. Caught a
   board reading "Continuous" against a median of 26 days.
2. **Staleness.** A label that promises activity cannot sit on a book that has not traded
   in `stale_days`, whatever its historical median says. Caught a strategy labelled
   "Continuous" whose last trade was ten weeks earlier -- its *median* was fine, because it
   had been busy and then stopped. The band alone would have passed it.
3. **Unknown label fails.** A label with no band is an error, not a default pass. Caught a
   strategy described as "Rare" -- a word nobody had ever assigned a meaning to, sitting on
   a book that traded every two days. A tool that shrugs at unfamiliar input would have
   said nothing.
4. **Empty books.** A book with no activity satisfies a band vacuously. Labels must opt in
   to permitting that, so "has never traded" cannot masquerade as "trades weekly".

Bands are deliberately **wide**. The job is catching a claim that is wrong by a factor of
five, not adjudicating whether eight days is weekly or multi-week.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .ledger import Activity


@dataclass(frozen=True)
class Band:
    """A label's permitted range, in calendar days between active days.

    `min`/`max` of None means unbounded on that side. `allow_empty` lets a label sit on a
    book with no activity at all -- appropriate for something event-gated that is waiting
    for conditions, and wrong for anything claiming a rhythm.
    `min_max_gap` asserts the book has a real quiet stretch, which is how you express a
    claim about *clustering* rather than about average frequency.
    """

    min: float | None = None
    max: float | None = None
    allow_empty: bool = False
    promises_activity: bool = True
    min_max_gap: int | None = None


#: A starting vocabulary. Replace it wholesale -- these words are only useful if they are
#: the words your own docs actually use. The point is that each one has a written-down
#: meaning, not that these are the right meanings.
DEFAULT_BANDS: dict[str, Band] = {
    "daily": Band(min=1, max=2),
    "continuous": Band(min=1, max=7),
    "clustered": Band(min=1, max=6, min_max_gap=10),
    "weekly": Band(min=3, max=11),
    "multi-week": Band(min=7, max=30, promises_activity=False, allow_empty=True),
    "monthly": Band(min=18, max=40, promises_activity=False),
    "quarterly": Band(min=60, max=120, promises_activity=False),
    "occasional": Band(min=10, promises_activity=False, allow_empty=True),
    "event-driven": Band(min=10, promises_activity=False, allow_empty=True),
}

DEFAULT_STALE_DAYS = 35


@dataclass
class Finding:
    subject: str
    label: str
    message: str

    def __str__(self) -> str:
        return f"{self.subject}: {self.message}"


@dataclass
class Result:
    subject: str
    label: str
    activity: Activity
    findings: list[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.findings


def check_one(
    subject: str,
    label: str,
    activity: Activity,
    *,
    bands: dict[str, Band] | None = None,
    stale_days: int = DEFAULT_STALE_DAYS,
) -> Result:
    """Judge one labelled subject against one ledger."""
    bands = bands if bands is not None else DEFAULT_BANDS
    result = Result(subject=subject, label=label, activity=activity)
    key = label.strip().lower()

    # Rule 3 first: without a band there is nothing to judge against, and guessing one
    # would reintroduce the silent pass this exists to prevent.
    band = bands.get(key)
    if band is None:
        known = ", ".join(sorted(bands)) or "(none configured)"
        result.findings.append(
            Finding(
                subject,
                label,
                f'cadence "{label}" has no band. Give it one, or use a label that has '
                f"one. Known labels: {known}. An unreviewed label is how a wrong one "
                f"survives.",
            )
        )
        return result

    # Rule 4.
    if activity.count == 0:
        if not band.allow_empty:
            result.findings.append(
                Finding(
                    subject,
                    label,
                    f'labelled "{label}" with no recorded activity at all. A book that '
                    f"has never traded cannot promise a rhythm. Set allow_empty on this "
                    f"label if that is genuinely expected.",
                )
            )
        return result

    # Rule 2. Checked before the band because a book that has stopped can still have a
    # perfectly ordinary historical median -- that is exactly how this one hides.
    since = activity.days_since_last
    if band.promises_activity and since is not None and since > stale_days:
        result.findings.append(
            Finding(
                subject,
                label,
                f'labelled "{label}" but its last activity was {since} days ago '
                f"({activity.days[-1]}), over the {stale_days}-day limit for a label that "
                f"promises activity.",
            )
        )

    # Rule 1.
    median = activity.median_gap
    if median is not None:
        if band.min is not None and median < band.min:
            result.findings.append(
                Finding(
                    subject,
                    label,
                    f'labelled "{label}" but it is active every {median:g} days on the '
                    f"median -- more often than that label allows "
                    f"({_fmt_band(band)}).",
                )
            )
        if band.max is not None and median > band.max:
            result.findings.append(
                Finding(
                    subject,
                    label,
                    f'labelled "{label}" but its median gap is {median:g} days, outside '
                    f"the band for that label ({_fmt_band(band)}).",
                )
            )

    # The clustering assertion.
    if band.min_max_gap is not None:
        longest = activity.max_gap
        if longest is not None and longest < band.min_max_gap:
            result.findings.append(
                Finding(
                    subject,
                    label,
                    f'labelled "{label}" but its longest quiet stretch is only {longest} '
                    f"days. Clustering needs a real tail, not a short even cadence.",
                )
            )

    return result


def _fmt_band(band: Band) -> str:
    lo = "any" if band.min is None else f"{band.min:g}"
    hi = "any" if band.max is None else f"{band.max:g}"
    return f"{lo}-{hi} days"


def check_all(
    subjects: Iterable[tuple[str, str, Activity]],
    *,
    bands: dict[str, Band] | None = None,
    stale_days: int = DEFAULT_STALE_DAYS,
) -> list[Result]:
    return [
        check_one(name, label, activity, bands=bands, stale_days=stale_days)
        for name, label, activity in subjects
    ]
