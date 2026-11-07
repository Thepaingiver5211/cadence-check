"""Command line: read a config, measure the ledgers, report, exit non-zero on drift.

Exit codes are the point. This is built to sit in CI, where the only thing that reliably
gets anyone's attention is a red build -- a warning printed into a log nobody opens is the
same as no check at all.
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

from .ledger import Activity
from .rules import DEFAULT_BANDS, DEFAULT_STALE_DAYS, Band, check_one


def load_config(path: Path) -> dict:
    with open(path, "rb") as handle:
        return tomllib.load(handle)


def bands_from_config(cfg: dict) -> dict[str, Band]:
    """Config bands REPLACE the defaults rather than extending them.

    Deliberate: the defaults are a starting vocabulary, not a standard. Silently merging
    them in would mean a typo'd label quietly matching a built-in you forgot about, which
    is rule 3 defeating itself.
    """
    raw = cfg.get("bands")
    if not raw:
        return DEFAULT_BANDS
    out: dict[str, Band] = {}
    for name, spec in raw.items():
        if not isinstance(spec, dict):
            raise SystemExit(f"config: band '{name}' must be a table, got {type(spec).__name__}")
        unknown = set(spec) - {"min", "max", "allow_empty", "promises_activity", "min_max_gap"}
        if unknown:
            raise SystemExit(f"config: band '{name}' has unknown keys: {', '.join(sorted(unknown))}")
        out[name.strip().lower()] = Band(**spec)
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="cadence-check",
        description="Fail when a published cadence claim disagrees with the ledger behind it.",
    )
    p.add_argument("config", type=Path, help="TOML file describing the claims")
    p.add_argument("--quiet", action="store_true", help="print only failures")
    p.add_argument("--days", type=str, action="append", default=[],
                   help="also list every active day for this subject (repeatable)")
    p.add_argument("--measure", action="store_true",
                   help="print the measurements and exit 0 without judging. Use this to "
                        "set bands from your own ledgers instead of guessing.")
    args = p.parse_args(argv)

    if not args.config.exists():
        print(f"cadence-check: no such config: {args.config}", file=sys.stderr)
        return 2

    cfg = load_config(args.config)
    root = args.config.parent
    bands = bands_from_config(cfg)
    defaults = cfg.get("defaults", {})
    stale_days = int(defaults.get("stale_days", DEFAULT_STALE_DAYS))

    # Which keys count as activity. Defaults over-collect on purpose (see ledger.py). Narrow
    # it if your ledgers open and close on adjacent days and you only care about entries --
    # but narrow it knowingly, because a key you leave out of a shape you forgot about reads
    # as silence, and silence passes any claim that tolerates it.
    reader_kwargs = {}
    if "activity_keys" in defaults:
        reader_kwargs["activity_keys"] = tuple(defaults["activity_keys"])
    if "skip_keys" in defaults:
        reader_kwargs["skip_keys"] = frozenset(defaults["skip_keys"])

    entries = cfg.get("strategy") or cfg.get("subject") or []
    if not entries:
        print("cadence-check: config declares no [[strategy]] entries", file=sys.stderr)
        return 2

    rows, failures = [], []
    for entry in entries:
        for required in ("name", "ledger", "cadence"):
            if required not in entry:
                print(f"cadence-check: an entry is missing '{required}'", file=sys.stderr)
                return 2
        ledger_path = (root / entry["ledger"]).resolve()
        if not ledger_path.exists():
            print(f"cadence-check: {entry['name']}: no such ledger: {ledger_path}", file=sys.stderr)
            return 2

        activity = Activity.from_file(ledger_path, **reader_kwargs)
        result = check_one(entry["name"], entry["cadence"], activity,
                           bands=bands, stale_days=stale_days)
        rows.append(result)
        failures.extend(result.findings)

    if args.measure:
        _report(rows, args.days)
        print("\n--measure: reporting only, nothing judged. Set your bands from these numbers.")
        return 0

    if not args.quiet:
        _report(rows, args.days)

    if failures:
        print(f"\nCADENCE DRIFT — {len(failures)} claim(s) contradicted by the ledgers:\n")
        for f in failures:
            print(f"  FAIL: {f}")
        print("\nFix the published label, or the band, whichever is actually wrong.")
        return 1

    print(f"ok: all {len(rows)} cadence claims agree with their ledgers")
    return 0


def _report(rows, detail_for: list[str]) -> None:
    head = f"{'subject':<22} {'label':<14} {'n':>4} {'med':>6} {'max':>5} {'ago':>5}"
    print(head)
    print("-" * len(head))
    for r in rows:
        a = r.activity
        print(
            f"{r.subject:<22} {r.label:<14} {a.count:>4} "
            f"{'-' if a.median_gap is None else format(a.median_gap, 'g'):>6} "
            f"{'-' if a.max_gap is None else a.max_gap:>5} "
            f"{'-' if a.days_since_last is None else a.days_since_last:>5}"
            + ("" if r.ok else "   <-- see below")
        )
        if r.subject in detail_for and a.days:
            print(f"    active days: {', '.join(a.days)}")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
