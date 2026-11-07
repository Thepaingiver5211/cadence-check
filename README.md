# cadence-check

**Your strategy descriptions go stale and nothing tells you.** This reads your trade
ledgers and fails the build when a description disagrees with them.

Zero dependencies, Python 3.11+, one TOML file.

---

## The problem

You write down how often each strategy trades — on a dashboard, in a README, on a page
you sell from. Then the strategies keep running and the descriptions sit there, slowly
becoming fiction. Nothing breaks. Nothing warns you. The words were true when you typed
them.

Here is what that looked like in the project this came out of. Four of fifteen strategies
were described in a way the ledgers flatly contradicted:

| Described as | Actually |
|---|---|
| `Rare` — *"most weeks it does nothing"* | traded every **2 days**; longest quiet stretch ever was 20 |
| `Continuous` | median gap **26.5 days** |
| `Continuous` | **had not traded in ten weeks** |
| `Daily` | median gap **6 days** |

Two of those contradicted another sentence *on the same page*. The one labelled "Rare" was
being used to talk buyers out of it — "if a strategy that does nothing for six weeks would
make you cancel, this is the wrong one for you" — which was not true of a strategy that
had just traded three days running.

Every other published figure in that project was checked by something. Cadence was the one
field nobody could check, because **it was prose**.

## What it does

```console
$ python -m cadence_check claims.toml
subject                label             n    med   max   ago
-------------------------------------------------------------
Mean Revert A          daily            40      1     1     0
Rotation B             continuous        3     13    13    71   <-- see below
Rebalancer C           monthly           4     27    28    22
Fade D                 clustered        11      1    24     6
Crisis Hedge E         event-driven      0      -     -     -

CADENCE DRIFT — 2 claim(s) contradicted by the ledgers:

  FAIL: Rotation B: labelled "continuous" but its last activity was 71 days ago
        (2026-08-28), over the 35-day limit for a label that promises activity.
  FAIL: Rotation B: labelled "continuous" but its median gap is 13 days, outside
        the band for that label (1-7 days).

Fix the published label, or the band, whichever is actually wrong.
$ echo $?
1
```

Exit code 1, so CI goes red. A warning in a log nobody reads is the same as no check.

## The idea that makes it possible

**You cannot assert that "Weekly" equals a number.** That is the whole difficulty, and the
way round it is to stop trying.

Each label gets a **permitted band**, and the only question asked is whether the
measurement falls outside it. The tool never tries to derive the label from the data. It
only ever says *this label and this ledger disagree*. Bands are deliberately wide — the job
is catching a claim that is wrong by a factor of five, not adjudicating whether eight days
is weekly or multi-week.

Four rules, and three exist because of a specific thing they caught:

**1 · Band.** Median gap between active days must sit inside the label's range.
<br>*Caught `Continuous` against a 26-day median.*

**2 · Staleness.** A label promising activity cannot sit on a book that has stopped,
whatever its historical median says.
<br>*Caught a `Continuous` strategy whose last trade was ten weeks old. Its median was a
healthy 7 days — it had been busy, then stopped. **A band alone passes this one**, which is
why staleness is a separate rule.*

**3 · An unknown label fails.** A label with no band is an error, not a default pass.
<br>*Caught `Rare` — a word nobody had ever assigned a meaning to, sitting on a book that
traded every two days. Anything that shrugs at unfamiliar input says nothing here.*

**4 · Empty books.** No activity satisfies any band vacuously, so labels must opt in to
permitting it. Otherwise "has never traded" passes as "trades weekly".

## Reading ledgers you didn't design

Most of the work, and the least interesting part, is that every strategy writes its ledger
differently. One keeps `open_positions` as a dict and `closed_trades` as a list. Another
has `completed_trades` with `entry_date`. A third stores a single position under a
singular `position` key that is `null` when flat. A fourth records a bare `date`.

A reader with a branch per shape looks tidy and fails in the worst way: it returns **zero**
for a shape it has not met, and zero reads as *"this strategy is quiet"* — which is the
claim you were trying to check.

So the scan is recursive and deliberately **over-collects**. Over-collecting makes a book
look busier than it is, which fails a quiet claim rather than excusing a loud one. A false
alarm costs a minute. A false pass costs you the thing you built this for.

It skips `equity_history` and friends, because an equity curve has a date for every day
the process ran and counting it would make everything look daily.

## Quick start

```bash
git clone https://github.com/Thepaingiver5211/cadence-check
cd cadence-check
python -m cadence_check examples/claims.toml     # see it fail on purpose
```

Point it at your own ledgers:

```toml
# claims.toml
[defaults]
stale_days = 35

[bands]
daily      = { min = 1, max = 2 }
weekly     = { min = 3, max = 11 }
monthly    = { min = 18, max = 40, promises_activity = false }
dormant    = { min = 10, promises_activity = false, allow_empty = true }

[[strategy]]
name    = "Trend Follower"
ledger  = "state/trend_follower.json"
cadence = "weekly"
```

**Set your bands from measurements, not intuition:**

```bash
python -m cadence_check claims.toml --measure   # prints the numbers, judges nothing, exits 0
```

Then in CI:

```yaml
- run: python -m cadence_check claims.toml
```

### Band options

| key | meaning |
|---|---|
| `min` / `max` | permitted median gap, in calendar days. `None`/omitted = unbounded |
| `allow_empty` | may this label sit on a book with no activity at all? |
| `promises_activity` | does this label imply the strategy is currently active? Controls rule 2 |
| `min_max_gap` | require a quiet stretch at least this long — how you express *clustered* |

Declaring any `[bands]` table **replaces** the built-in vocabulary rather than extending it,
so a typo cannot silently match a default you had forgotten about.

## As a library

```python
from cadence_check import Activity, check_one

activity = Activity.from_file("state/trend_follower.json")
result = check_one("Trend Follower", "weekly", activity)
for finding in result.findings:
    print(finding)
```

`Activity` is pure measurement and holds no opinion — `count`, `gaps`, `median_gap`,
`max_gap`, `days_since_last`. Judgement lives in `rules.py`. They are separate so you can
look at the numbers without the rules arguing with you.

## Limitations, stated plainly

- **Calendar days, not trading days.** A market calendar means a dependency and a venue
  assumption, for a tool whose pitch is that it runs anywhere with no setup. Your bands
  absorb it — a daily strategy lands around 1, not 1.0.
- **Open-and-close counts as two active days.** If your strategies exit the day after they
  enter, medians skew low. Narrow the keys with
  `activity_keys = ["entry_date"]` under `[defaults]`, knowing that narrowing is the
  direction that can hide a shape.
- **JSON ledgers only.** CSV and SQLite would both be reasonable additions.
- **It checks cadence, not correctness.** It knows nothing about whether a strategy is any
  good, and deliberately computes no P&L.

## Why this exists

It came out of a website where every published number was derived from something — capital
requirements summed from the strategies, a savings table computed from the roster — except
the cadence column, which was typed by hand and had been wrong for months.

The general lesson is not about trading. **Any claim a human types next to data that keeps
moving will eventually disagree with it**, and the ones that hurt are the ones nothing can
check. If you have a field like that, it is worth finding a way to check it even when the
field is prose.

## License

MIT — see [LICENSE](LICENSE).
