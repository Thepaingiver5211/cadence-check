"""Tests. Each rule's test is named for the real failure it would have caught.

A rule with no test is a rule nobody has shown the value of, and this tool exists because
of four specific misses -- so each one gets reproduced here rather than described.
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

import pytest

from cadence_check import Activity, Band, activity_days, check_one

ROOT = Path(__file__).resolve().parents[1]


def days_ago(*offsets: int) -> list[str]:
    today = dt.date.today()
    return sorted((today - dt.timedelta(days=n)).isoformat() for n in offsets)


def activity(*offsets: int) -> Activity:
    return Activity(days_ago(*offsets))


# --------------------------------------------------------------------- the ledger reader


def test_finds_dates_whatever_shape_the_ledger_uses():
    """One scan, four shapes. A reader with a branch per shape misses the one it has
    not met, and returns zero -- which reads as 'quiet' and passes."""
    ledger = {
        "open_positions": {"AAA": {"entry_date": "2026-01-02"}},          # dict of dicts
        "closed_trades": [{"entry_date": "2026-01-05", "exit_date": "2026-01-06"}],
        "position": {"opened_at": "2026-01-09T14:30:00-05:00"},            # singular, datetime
        "trades": [{"date": "2026-01-12", "side": "buy"}],                 # bare `date`
    }
    assert activity_days(ledger) == ["2026-01-02", "2026-01-05", "2026-01-06",
                                     "2026-01-09", "2026-01-12"]


def test_equity_history_is_skipped():
    """An equity curve has a date per day the process ran. Counting it would make every
    strategy look daily -- the exact false pass this tool is for."""
    ledger = {
        "trades": [{"date": "2026-03-02"}],
        "equity_history": [{"date": f"2026-03-{d:02d}"} for d in range(1, 29)],
    }
    assert activity_days(ledger) == ["2026-03-02"]


def test_a_null_position_is_not_activity():
    assert activity_days({"position": None, "closed_trades": []}) == []


def test_garbage_dates_are_ignored_not_crashed_on():
    ledger = {"trades": [{"date": "not-a-date"}, {"date": "2026-13-45"},
                         {"date": ""}, {"date": "2026-02-10"}]}
    assert activity_days(ledger) == ["2026-02-10"]


def test_activity_keys_can_be_narrowed():
    """For ledgers that open and close on adjacent days, counting only entries gives a
    cleaner cadence. Opt-in, because narrowing is the direction that can hide a shape."""
    ledger = {"closed_trades": [{"entry_date": "2026-01-05", "exit_date": "2026-01-06"}]}
    assert activity_days(ledger, activity_keys=("entry_date",)) == ["2026-01-05"]


def test_measurements():
    a = activity(30, 20, 10, 0)
    assert a.count == 4
    assert a.gaps == [10, 10, 10]
    assert a.median_gap == 10
    assert a.max_gap == 10
    assert a.days_since_last == 0


def test_median_of_an_even_number_of_gaps_is_the_mean_of_the_middle_two():
    a = Activity(["2026-01-01", "2026-01-02", "2026-01-10", "2026-01-30"])  # gaps 1, 8, 20
    assert a.gaps == [1, 8, 20]
    assert a.median_gap == 8
    a2 = Activity(["2026-01-01", "2026-01-03", "2026-01-13"])               # gaps 2, 10
    assert a2.median_gap == 6


def test_a_single_active_day_has_no_gaps_and_does_not_crash():
    a = activity(5)
    assert a.gaps == [] and a.median_gap is None and a.max_gap is None
    assert a.days_since_last == 5


# ------------------------------------------------------------------------------- rule 1


def test_band_catches_continuous_against_a_monthly_median():
    """The original catch: a board read 'Continuous' against a 26-day median."""
    r = check_one("Trend", "continuous", activity(104, 78, 52, 26, 0))
    assert not r.ok
    assert "median gap" in r.findings[0].message


def test_band_catches_a_label_that_is_too_slow_for_the_book():
    r = check_one("Fast", "monthly", activity(4, 3, 2, 1, 0))
    assert not r.ok
    assert "more often than that label allows" in r.findings[0].message


def test_a_label_that_matches_passes():
    assert check_one("Weekly thing", "weekly", activity(28, 21, 14, 7, 0)).ok


# ------------------------------------------------------------------------------- rule 2


def test_staleness_catches_a_book_that_stopped():
    """The catch a band alone cannot make. This book's median is a healthy 7 days -- it
    was busy and then stopped ten weeks ago, and the median still says 'busy'."""
    r = check_one("Rotation", "continuous", activity(91, 84, 77, 70))
    assert not r.ok
    assert any("last activity was" in f.message for f in r.findings)


def test_a_label_that_does_not_promise_activity_is_exempt_from_staleness():
    bands = {"occasional": Band(min=10, promises_activity=False)}
    assert check_one("Hedge", "occasional", activity(200, 150, 100), bands=bands).ok


def test_stale_days_is_configurable():
    a = activity(60, 50, 40)
    assert not check_one("X", "weekly", a, stale_days=35).ok
    assert check_one("X", "weekly", a, stale_days=90).ok


# ------------------------------------------------------------------------------- rule 3


def test_an_unknown_label_fails_rather_than_passing():
    """The sharpest catch. 'Rare' was never a defined label, and sat on a book that traded
    every two days. Anything that shrugs at unfamiliar input would have said nothing."""
    r = check_one("Spike", "Rare", activity(6, 4, 2, 0))
    assert not r.ok
    assert "has no band" in r.findings[0].message


def test_labels_are_matched_case_and_space_insensitively():
    assert check_one("X", "  Weekly  ", activity(21, 14, 7, 0)).ok


# ------------------------------------------------------------------------------- rule 4


def test_an_empty_book_cannot_claim_a_rhythm():
    r = check_one("Never traded", "weekly", Activity([]))
    assert not r.ok
    assert "no recorded activity" in r.findings[0].message


def test_an_empty_book_is_fine_for_a_label_that_allows_it():
    bands = {"event-driven": Band(min=10, allow_empty=True, promises_activity=False)}
    assert check_one("Crisis hedge", "event-driven", Activity([]), bands=bands).ok


# ------------------------------------------------------------- the clustering assertion


def test_clustered_requires_a_real_quiet_stretch():
    """Clustering is a claim about variance, not about the average. A book with an even
    short cadence is not clustered however fast it is."""
    bands = {"clustered": Band(min=1, max=6, min_max_gap=10)}
    even = check_one("Even", "clustered", activity(8, 6, 4, 2, 0), bands=bands)
    assert not even.ok
    assert "longest quiet stretch" in even.findings[0].message

    bursty = check_one("Bursty", "clustered",
                       activity(60, 59, 58, 30, 29, 28, 2, 1, 0), bands=bands)
    assert bursty.ok


# ------------------------------------------------------------------------- the whole CLI


def test_the_example_config_fails_for_exactly_one_reason():
    """The shipped example must demonstrate the lesson and nothing else. If this starts
    failing for a second subject, the example drifted."""
    out = subprocess.run([sys.executable, "-m", "cadence_check", "examples/claims.toml"],
                         cwd=ROOT, capture_output=True, text=True)
    assert out.returncode == 1, out.stdout + out.stderr
    assert "Rotation B" in out.stdout
    failing = {line.split(":")[1].strip() for line in out.stdout.splitlines()
               if line.strip().startswith("FAIL:")}
    assert failing == {"Rotation B"}, f"unexpected subjects failing: {failing}"


def test_measure_mode_reports_without_judging():
    out = subprocess.run([sys.executable, "-m", "cadence_check", "examples/claims.toml",
                          "--measure"], cwd=ROOT, capture_output=True, text=True)
    assert out.returncode == 0
    assert "nothing judged" in out.stdout


def test_a_missing_ledger_is_an_error_not_a_pass(tmp_path):
    cfg = tmp_path / "claims.toml"
    cfg.write_text('[[strategy]]\nname = "X"\nledger = "nope.json"\ncadence = "weekly"\n',
                   encoding="utf-8")
    out = subprocess.run([sys.executable, "-m", "cadence_check", str(cfg)],
                         cwd=ROOT, capture_output=True, text=True)
    assert out.returncode == 2
    assert "no such ledger" in out.stderr


def test_declaring_bands_replaces_the_defaults(tmp_path):
    """So a typo cannot silently match a built-in you had forgotten about."""
    (tmp_path / "l.json").write_text(json.dumps({"trades": [{"date": "2026-01-01"}]}),
                                     encoding="utf-8")
    cfg = tmp_path / "claims.toml"
    cfg.write_text(
        "[bands]\nmine = { min = 1, max = 5, allow_empty = true }\n\n"
        '[[strategy]]\nname = "X"\nledger = "l.json"\ncadence = "weekly"\n',
        encoding="utf-8",
    )
    out = subprocess.run([sys.executable, "-m", "cadence_check", str(cfg)],
                         cwd=ROOT, capture_output=True, text=True)
    assert out.returncode == 1
    assert "has no band" in out.stdout


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
