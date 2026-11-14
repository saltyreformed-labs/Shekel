# The test suite's two clocks and its calendar

How the suite defends against time-dependent failures, what the two gates actually check, and the
pitfalls that cost real debugging time on 2026-08-01. Written after five separate defects whose only
trigger was *when* the suite ran.

The short version of the rules is in `.claude/rules/testing.md`; the standards entry is
`docs/testing-standards.md` ("The ambient clock and the ambient calendar"). **This document is the
depth: why the gates exist, what they cannot see, and how to read a failure from either of them.**

---

## 1. Why this exists

Between 2026-07-31 and 2026-08-01, five defects surfaced. Every one of them was found by CI blocking
a merge, never by a test, and every one had a single trigger -- the moment the suite ran:

| finding | trigger | what it did |
|---|---|---|
| N-131 | last days of a month | six cross-page tests failed, ~12 days a year |
| N-132 | any day | fixtures separated events by HOURS against a rule that reads civil DAYS |
| R8 | a "day before today" default | an offset silently became a duplicate of its sibling |
| 2026-08-01 (two loan tests) | the 1st, and the days before the month's first Monday | a fixture promised a "clean past" the calendar decided |
| 2028-02-29 / 2027-06-01 | a leap day; a hard-coded future date going stale | a `ValueError` crash; a pill that silently stops rendering |

Three more were reported as "flaky" and were nothing of the kind. **A test that passes on some days
and fails on others is not flaky. It is a broken test with a schedule.** Treating one as flaky is
how it survives to block a merge at the worst possible moment.

---

## 2. The two clocks

```text
date.today()        -> the PROCESS timezone  (whatever TZ the interpreter inherited)
display_today()     -> America/New_York      (app/utils/dates.py, DISPLAY_TIMEZONE)
```

The application's civil day is `display_today()`. The process clock agrees with it
**only because the deployment pins the timezone**: the image sets `ENV TZ=America/New_York`
(`Dockerfile`, step plan#3812), `deploy/docker-compose.prod.yml` and `docker-compose.dev.yml` both
set `TZ: America/New_York` until step plan#2166 deletes them, and the prod file says why in its own
comment --

> the default is UTC, which flipped `date.today()` to the next day at 20:00 America/New_York [...]
> The app's date logic is naive process-local time throughout, so the process timezone IS the app
> timezone.

That is the whole hazard in one sentence. **Correctness rests on an environment variable**, and any
environment that does not set it computes different calendar dates. CI is exactly such an
environment.

### Where it bites

Any value a test builds that the application will later compare against *its own* "today":

- an `entry_date` posted to a route -- `entry_service._reject_future_entry_date` refuses anything
  after `display_today()` (ruling R-M), so a process date one day ahead is simply a **400**;
- a pay-period window that must contain the day `get_current_period` looks for;
- an anchor's `observed_on`;
- any assertion on a date the app derived.

### The rule

> **Whenever a test builds a date the application will compare against its own "today", use
> `display_today()`.**

### The pitfall that makes it invisible

The two clocks agree in a dev shell (the host is Eastern) and in production (TZ pinned). They
disagreed in CI **only between 00:00 and 03:59 UTC** -- four hours a night. So the failure was
unreproducible by anyone who did not happen to run at 20:00-23:59 Eastern, which is precisely why
three of them were filed as flakes.

### Anti-fix

**Do not pin CI's timezone to `America/New_York`.** It makes the symptom vanish and leaves the
coupling in place, ready for the next environment that differs. The fix is always to use the app's
clock in the test.

---

## 3. Gate 1 -- the clock gate (per PR, free)

`.github/workflows/ci.yml`'s `lint-and-test` job sets:

```yaml
TZ: Pacific/Kiritimati
```

Kiritimati is UTC+14, so the process date runs a day ahead of the app's for
**eighteen hours out of every twenty-four**. A `date.today()` / `display_today()` mix now fails on
most runs instead of on a four-hour nightly window. No second job, no extra minutes.

Reproduce locally:

```bash
TZ=Pacific/Kiritimati ./scripts/test.sh
```

**It is not 100%.** For the remaining six hours the two dates coincide and the property is not
exercised. No single timezone can disagree with another all day; this is the best a free, single-run
gate can do. Treat a green run as strong evidence, not proof.

---

## 4. Gate 2 -- the calendar sweep (weekly)

`.github/workflows/calendar-sweep.yml` runs the whole suite as if today were each of five awkward
positions, `fail-fast: false` so one red date does not hide the others:

| date | why |
|---|---|
| 2028-02-29 | leap day -- `date.replace(year=+1)` raises here |
| 2026-12-31 | last day of a year |
| 2027-01-01 | first day of a year -- a 13-month window fails to reach the next year from here |
| 2026-11-30 | month end (finding N-131's shape) |
| 2026-09-01 | first of a month -- an installment falls on today |

Locally, one date at a time:

```bash
SHEKEL_FAKE_TODAY=2028-02-29 ./scripts/test.sh
```

The mechanism is a session-scoped autouse fixture in `tests/conftest.py` (`_calendar_sweep`), a
**no-op unless the variable is set**, wrapping the session in `time_machine.travel(..., tick=True)`.

- `tick=True`, not frozen: a frozen clock makes every `created_at` in a session identical, and the
  fold's assertion ordering (ruling R-DH -- two assertions sharing a civil day apply in *recording*
  order) then has no order to read.
- The instant is built at **midday in `DISPLAY_TIMEZONE`**, so the faked civil day is unambiguous
  and cannot straddle midnight in either direction.

### Two fixture shapes that shipped, and their fixes

- **Never `date.replace(year=...)`.** On 29 February it raises
  `ValueError: day 29 must be in range 1..28 for month 2 in year 2029`. Use
  `app.utils.dates.add_months(d, 12)`, which clamps to 2029-02-28. (`app/` has never used
  `.replace(year=`; this was test-only.)
- **Never hard-code a date because it is "in the future".** `target_date=date(2027, 6, 1)` stops
  being in the future on 2027-06-01, and the test depending on it starts failing that morning.
  Derive it: `add_months(display_today(), 10)`.

---

## 5. What the sweep CANNOT see -- the two-clock artifact

**`time-machine` moves Python's clock. It cannot move PostgreSQL's.**

```text
created_at / updated_at   server_default=db.func.now()   app/models/mixins.py
entry_date                server_default CURRENT_DATE    app/models/transaction_entry.py
```

A third row used to sit here -- `paid_at`, stamped by `db.func.now()` in
`app/services/status_seam.py` -- and it is gone twice over. Ruling **R-EC** (plan step X-f1)
replaced that instant with the civil `settled_on`, which the seam writes from `display_today()`:
Python's clock, so `time-machine` does move it. And plan step X-au-c3 made `status_seam` a package,
so the path named no file either.

Both remaining rows are evaluated **in the database**, which knows nothing about the fake. So under
a faked date a server-stamped row carries the **real** instant, and any test comparing it against a
Python-derived date fails by the offset between them.

Measured directly, under `SHEKEL_FAKE_TODAY=2026-09-02`:

```text
opening created_at  (POSTGRES clock) = 2026-08-01 12:39 UTC
opening observed_on (PYTHON clock)   = 2026-09-01
```

An impossible production state, produced entirely by the instrument.

### The marker

Those tests carry `@pytest.mark.server_clock` (registered in `pytest.ini`), and the sweep job
deselects them with `-m "not docker and not server_clock"`. Four of them assert the database's clock
**on purpose**, which no amount of Python faking can satisfy: the `CURRENT_DATE` server default, the
audit trigger's `executed_at`, and the stamp arm and the up-then-down round trip in
`test_a_transfer_side_knows_its_own_day_migration.py`, which grade a Paid press's day against its
own audit row's `executed_at`.

> **The marker is a statement about the instrument, never a way to quiet a failure.** Every marked
> test still runs in ordinary CI. A test earns the marker only after its failure has been traced to
> the two clocks.

### The trap -- read this before adding a marker

**"It fails at some dates but not others" is NOT sufficient evidence that a failure is real.**

`test_two_same_day_trueups_reconcile` fails at 2026-09-07 and passes at 2026-09-01, which looks
exactly like a genuine calendar bomb. It is not. Its fixture computes `origin + 30 days` from the
**Postgres-stamped** `created_at` while the opening's business date comes from the **faked Python**
clock; the two only diverge once the faked date moves far enough, so the failure is
*date-dependent and still an artifact*. This cost real time, and the pattern-based shortcut is what
caused it.

**Trace every sweep failure to the two clocks before classifying it.** The tell is a date offset
equal to the distance between the faked date and the real one.

---

## 6. Operational pitfalls

### The test template goes stale across branches

The template carries the **current branch's** migration head, and a stale one produces a flood of
errors that look nothing like a migration problem:

```text
psycopg2.errors.UndefinedColumn: column "observed_on" of relation "account_anchor_history"
    does not exist
```

This nearly produced a wrong conclusion: a sweep run reporting *4,979 errors* was read as "the
Postgres-clock gap makes the instrument unusable", when it was a stale template and the real number
was 20.

**Since `balance:X-br-4` there is nothing to rebuild by hand.** The template is baked into a tagged
image whose cache key covers every input the build reads -- the migrations AND the in-code trigger
definitions applied after them -- and `./scripts/test.sh` re-verifies the image on every invocation,
so a branch switch that crosses a migration rebuilds at the door. The flood above is still worth
recognising, because a run that somehow reaches a stale template still fails this way; it is no
longer something a branch switch can cause through the wrapper.

### Comparing against `main`

Use `git worktree add`, never `git checkout` -- a checkout reverts the working tree and discards
uncommitted work. The worktree needs nothing else: `./scripts/test.sh` builds that checkout's own
image, starts its own cluster and exports its own DSNs, so the two trees' suites cannot see each
other and can run at the same time. Before `balance:X-br-4` this paragraph had to name a separate
template and explicit DSNs per worktree, because one postmaster served them all.

### Environment

- `IDLE_TIMEOUT_MINUTES=720` when a concurrent session has left `.env` at `10080`; otherwise
  `test_stale_activity_rejected` fails suite-wide.
- Do not run the full suite concurrently with anything else against the same cluster -- the
  per-worker DB names collide (see `docs/testing-standards.md`).

---

## 7. How to read a failure

```text
Test fails in CI but not locally
  └─ Is the CI job's TZ skewed?  (yes -- Pacific/Kiritimati, by design)
     └─ Does the test build a date the app compares against display_today()?
        └─ YES  -> real defect. Use display_today() in the test.
        └─ NO   -> does it sign in, then freeze_today on a date read from the wall clock?
           └─ YES -> the idle timeout measures against the clock the freeze moves, so the
                     session expires and the route answers 302 /login while the freeze sits
                     over 12 hours past the sign-in. 01:00 UTC on date.today() does that
                     from 10:00 to 13:00 UTC a day under Kiritimati (plan#2925), so it
                     passes on most runs. Sign in again after the freeze (log_in_seed_user).
           └─ NO  -> keep going; it is not the clock gate.

Test fails in the calendar sweep
  └─ Does it compare a server-stamped timestamp against a Python-derived date?
     └─ YES  -> instrument artifact. Trace it, then ask what supplies the Python-side
                date, and take that remedy (R-BAL197, recurrence:R-R121, R-BAL198):
        └─ a second stamp the code writes from Python's clock -> freeze both stamps
           to one instant, freeze_today before the write (BAL-494's _one_clock, R-BAL197)
        └─ the test's own reading of "today" -> read today from the database's
           clock (REC-532's fix, recurrence:R-R121)
        └─ the database stamps it itself (a trigger) -> @pytest.mark.server_clock
           (R-BAL198)
     └─ NO   -> real calendar coupling. Make the fixture construct the property
                it needs on every calendar day, rather than deriving it from today.

Thousands of errors, not failures
  └─ Almost certainly a stale test template. Rebuild it before reading anything else.
```

---

## 8. Current state

- Full suite: **7,687 passed / 0 failed**, on the normal clock and under `TZ=Pacific/Kiritimati`.
- Calendar sweep: **7,662 passed / 0 failed** at 2028-02-29, 2027-01-01 and 2026-11-30 (the
  `server_clock` tests deselected, every one of which still runs in ordinary CI).
- `app/` carries no `.replace(year=` and was never exposed to the leap-day crash.

### Known gaps, stated so they are not mistaken for covered

1. **The clock gate covers ~18 hours of every 24**, not all of it (section 3).
2. **Only the forward skew is exercised.** `TZ=Pacific/Kiritimati` puts the process date *ahead* of
   the app's. The reverse -- process date *behind* -- has never been run, and a coupling that only
   breaks in that direction would not be caught.
3. **The sweep cannot fake PostgreSQL** (section 5). Closing that would need `libfaketime` inside
   the postgres container the suite runs against -- since `balance:X-br-4` a throwaway one per run,
   started from a baked image -- which is invasive. Three remedies stand in for it, each where it
   fits (section 7): freeze both stamps where the code writes a second one from Python's clock
   (BAL-494's, **R-BAL197**), read "today" from the database where the Python side is the test's own
   reading of today (REC-532's, **recurrence:R-R121**), and the marker where the database stamps the
   row itself (**R-BAL198**).
4. **Five calendar positions, not all of them.** A coupling keyed to something else -- a specific
   weekday, a DST transition -- would need its date added to the matrix.
