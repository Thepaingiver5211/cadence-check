"""cadence-check — fail when a published cadence claim disagrees with the ledger behind it.

    from cadence_check import Activity, check_one

    activity = Activity.from_file("ledgers/trend_follower.json")
    result = check_one("Trend Follower", "weekly", activity)
    if not result.ok:
        for finding in result.findings:
            print(finding)
"""

from .ledger import ACTIVITY_KEYS, SKIP_KEYS, Activity, activity_days
from .rules import (
    DEFAULT_BANDS,
    DEFAULT_STALE_DAYS,
    Band,
    Finding,
    Result,
    check_all,
    check_one,
)

__version__ = "0.1.0"

__all__ = [
    "ACTIVITY_KEYS",
    "SKIP_KEYS",
    "Activity",
    "activity_days",
    "Band",
    "DEFAULT_BANDS",
    "DEFAULT_STALE_DAYS",
    "Finding",
    "Result",
    "check_all",
    "check_one",
    "__version__",
]
