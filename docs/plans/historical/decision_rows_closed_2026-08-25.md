> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Four decision-gated findings, ruled and shipped (2026-08-25)

**Read-only history. Nothing here governs anything.** It exists so `ledger.md` can drop four rows
under `conventions.md` rule 5 without losing what each one measured or why the developer chose the
remedy he did. A FIFTH was ruled the same day and is NOT closed -- see the last section. Cite
this for how a decision came to be, never for what is true now.

These five had no step owner. They sat under `developer-decision` / `operator` -- the ledger's
spelling for "nobody has answered yet" -- and the answer, not the code, was what they were waiting
on. All five were re-measured against the tree at `dc39a559` before a remedy was put. **One census was
wrong -- F-15's -- and the re-measure of it was wrong three more times**, which is why that row
stayed open; N-299's and N-300's were checked and held. An earlier draft of this paragraph said
"three of the five censuses were wrong" and named none of the other two, which was a number nobody
had measured.

| arc | id | ruled | shipped |
|---|---|---|---|
| pay_calendar | P74 | a ranking's key must totally order its input, in Python | `_breakdown.py`, `_surprises.py` |
| — | — | *(and `_build_changes`, whose key was cited as the exemplar and was not total)* | `_breakdown.py` |
| pay_calendar | P67 | both years on a straddle, the end year alone otherwise | `utils/dates.pay_period_range_label` |
| balance | N-299 | store the entered day; `created_at` keeps ordering | migration `e5b2c8a17d34` |
| balance | N-300 | derive the child's date from the REAL clock | `test_seed_redaction.py` |

## What the re-measure corrected

**N-299 was diagnosed correctly and its remedy was the open question.** The card captions a row as
back-dated when the day a balance was TRUE differs from the day it was ENTERED. `observed_on`
defaults to the application's `display_today()`; the entered day was derived from `created_at`,
which is `server_default=db.func.now()` and therefore PostgreSQL's. Two clocks, one comparison.

**N-300's census -- "exactly ONE test" -- held.** `test_seed_user.py:499` sets the same env var from
`display_today()` but runs the script IN-PROCESS through `run_seed_user()`, so the faked clock
applies to both the setter and the validator. Only the subprocess caller is affected.

## The rulings, and what was rejected

**P74.** In Python, not SQL: an `ORDER BY` pins an order the reducers do not own and changes the
query plan, and the reducers re-sort anyway. Not a per-type ordering either -- `_build_changes`
ranks the same shape by a different rule, so one ordering per type would be the wrong abstraction.
The keys are `(-amount, group_name)`, `(-amount, item_name)` and `(-abs(delta), transaction_id)`;
`item_name` is total within a group by the `(user_id, group_name, item_name)` unique constraint, and
the Uncategorized bucket is alone in its own group.

**N-299.** Option C of four. Rejected: marking the two tests `@pytest.mark.server_clock`, which the
sweep deselects -- so a DATE-SENSITIVE caption would be graded at no calendar position at all, which
is the one thing the sweep exists to do; freezing the database clock, which the sweep deliberately
does not do (`tick=True` keeps `created_at` a recording order); and making the shared
`CreatedAtMixin` application-supplied, whose blast radius is every append-only table.

The column's default is context-sensitive: a caller that pins `created_at` is building a historical
row and gets that instant's display day, and a caller that does not -- every production write -- gets
`display_today()`. That is what stops the second column from being a rule maintained by remembering
at twenty fixture sites. A bulk `UPDATE` bypasses it, and `tests/conftest.py`'s re-anchoring step
sets both columns for that reason.

**N-300.** The bug is in the test, not the script: `seed_user.py` validates a payday against the only
clock it has. Rejected: marking it, which loses credential-redaction coverage on five calendar dates;
and propagating the fake into the child, which puts test-only machinery in a production provisioning
script.

## What it measured, and what it cost

The sweep had been red on all five matrix dates since 2026-08-10 (last green), reproduced at run
`32703452775` on 2026-08-24. Both N-299 tests fail at `dc39a559` under `SHEKEL_FAKE_TODAY` and pass
after; the seed-redaction test fails there with *"payday cannot be 2026-12-31: that day has not
happened yet"*.

The N-299 backfill is `(created_at AT TIME ZONE 'America/New_York')::date`, the derivation it
replaces, verbatim: measured on the developer's dev database, 82 rows and **0** disagreeing with the
old derived value, so no rendered caption moved. The downgrade is value-lossless for every row the
migration backfilled and lossy only for rows written after it -- exactly the rows whose two clocks
disagreed.

**Three defects in this session's own work, each caught by something later than the author.**
`escape_hatch` raises `ValueError("Not currently time-travelling.")` outside a travel context, so the
first N-300 fix turned a sweep-only failure into an every-run one; the full suite caught it and
`is_travelling()` gates it now. The first surprises tie-break test was a TAUTOLOGY -- driven through
`compute_spending_report` the rows arrive in id order anyway, so an unstable sort produced the same
answer as a total one and the test passed against the defect it named; it feeds the reducer a
reversed list now, and fails on the mutation. And the first `recorded_on` default was a plain
callable, which silently re-dated every fixture-built historical assertion.

## The fifth: `recurrence:F-15`, ruled and NOT closed

The developer ruled it the same day -- convert every site to `utils.dates.month_name` rather than
pin `LANG` in the container, on the ground that pinning would rest a correctness property on an
environment variable nobody reading the code can see, which is the shape `balance:N-191` already
records for the civil day at 113 call sites. **That ruling stands and the row does not close**,
because the census it was put against was wrong.

The row said FOUR month-name producers. The correction written into `ledger.md` earlier the same
day said ELEVEN. An AST census taken while converting them found **11 in `app/*.py` and 83 in
`app/templates/`** -- 94. The second miss was a `grep` for `strftime("` with double quotes only,
which sees neither `strftime('%b %d')` nor any template, and it was made by the same pass that had
just corrected `balance:N-212` for a line-based grep. **The failure mode survived being named.**

TEN sites are converted and stay converted -- four `calendar.month_name` in `routes/analytics.py`
and `routes/analytics_view.py`, six `strftime` in `debt_strategy`, `loan/_helpers`,
`analytics_view`, `investment_dashboard_service/_chart` (x2) and `escrow_calculator`. Byte-identical
output under the container's C locale, verified for all twelve months against the stdlib.

What remains is a display-wide sweep of a different size, and it needs something that does not exist
yet: the 83 template sites format WHOLE DATES (`%b %-d, %Y`), so the `month_name` Jinja filter does
not answer them -- a date-formatting filter does. That is a step, not a leaf, and the row now says
so.

## What adversarial review changed, after the fixes were built

Six neutral reviews ran before any of this was committed. They did not confirm the work; they
rewrote a third of it.

**F-15's whole conversion was REVERTED.** Its premise -- that a container under a different
`LC_TIME` renders month names in another language -- is false. Measured with `create_app()` fully
executed under `LC_ALL=LANG=LC_TIME=de_DE.UTF-8`: `strftime('%b')` -> `Dec`,
`calendar.month_name[12]` -> `December`. CPython never calls `setlocale` and nothing in `app/` does;
the sole mention in the tree is `recurrence/_describe.py:188`, a docstring recording that an EARLIER
adversarial review had already corrected this same claim. The partial conversion also manufactured
the defect it names -- `analytics_view:137` (converted) and `:393` (not) render on ONE response via
`analytics.py:361`/`:364` -- and wrote one expression inline in five places.

**`_build_changes` was not immune.** Both the ledger row and this document cited it as the total-key
exemplar. Its key carries neither group nor id over a FLAT list, so `Home: Insurance` and
`Auto: Insurance` -- which the unique constraint permits by design -- collide outright. More
reachable than either case the pass set out to fix.

**The item key was not total either.** It ended at `item_name` on the argument that the
Uncategorized bucket is alone in its group. Nothing enforces that: the bucket is synthesised for a
null-category row, and `CategoryCreateSchema` bounds `group_name` only by length.

**The context-sensitive column default was deleted.** 1,807 tests passed with its pinned branch
removed, so it was ungraded; `Insert.from_select` renders a Python default with no row context, so a
future backfill would have silently stamped every row with the deploy date; and its only
beneficiaries were three test fixtures. Those fixtures state the value themselves now.

**`_restamp_assertion` was left behind** -- 54 call sites across 12 modules restamping the instant
and the observed day while the entered day kept its insert-time value. Measured on `seed_periods`: a
row asserting a balance true on Jan 5 and typed on Jan 2, three days before the fact it records.

**Two claims in the migration and the model were false.** "Nothing in a test process can fake
Postgres" -- `_freeze_db_clock` exists for exactly this class and `test_frozen_db_clock.py` pins it
on this very column; what is true is that the SWEEP declines to freeze it, for a stated reason. And
"the two cannot be built disagreeing" holds only for the fixture branch; in production the two
clocks disagreeing is the row the column exists to make representable.

**Three numbers in `dc39a559` were wrong.** N-212's "all 103 money doors" is 74 (`places=2`); the
other 29 are rate fields. N-329's "six on a user-supplied id" is four, and its claim that the silent
set had GROWN was withdrawn outright -- the same 13 callers exist at both commits and the original
row was correct for the population it named. Correcting a row is not the same as measuring it.
