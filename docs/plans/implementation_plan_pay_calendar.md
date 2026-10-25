# Implementation Plan: The Pay Calendar

**The argument for deriving the pay calendar from the paydays.** Each step, finding and ruling is a
card in the tracker, `saltyreformed-labs/shekel-plan` (`CLAUDE.md`, Development Status): an open
step's specification is its card's body and the order is the board's. Sections 4, 5 and 7 held the
steps, the findings' pointer and this document's rules until the tracker took them.
**`R-PC2` also answers the `recurrence` arc's `F-10` fork**, a pay-period HOLE.

---

## 0. Sequencing against the other two arcs

Three arcs are live. **A step with no code yet has no measured file set**, and the first draft
claimed one for C1-C3 anyway. The surface `C4` had to cross was measured by AST over `app/` on
2026-08-08 and is a HISTORICAL figure: `.end_date` 35 files / 72 accesses (33 of them `PayPeriod`),
`.period_index` 21 files / 60 accesses. **`C4-c` crossed it**; the same census over `app/` today
returns ZERO reads of either name reached through a `budget.pay_periods` row, and every surviving
`.end_date` is a `DerivedPeriod`, `TrendPoint`, `SchedulePeriod` or `RecurrenceRule`.

C2 is not merely adjacent to the balance arc's **X-l**, it IS that step, and it is also recurrence
**R-F12**: three arcs asking for one total calendar. X-l's stated root -- "the pay calendar is a
PARTIAL function ... past the last one every consumer improvises" (the balance README's section 1,
item 3) -- is what this arc's C2 supplies by deriving the calendar from the paydays. C2 is the third
NAME for one value, not a third step.

**Both cross-arc collisions this section was written for are settled in CODE**, and each is kept to
one line because the commit is the record. C3 / `balance:X-ad`: `X-ad-a` deleted the registration
bootstrap payday and writes a `PaySchedule` row instead, so C4's **P8** backfill is not reopened by
the next signup. C3 / `recurrence:R7c`: `R7c-c` (`d9f5c1a48b73`) dropped
`recurrence_rules.offset_periods`, so the phase derives from the rule's first occurrence on every
read and no stored ordinal is left for an inserted payday to re-phase (row **P11**, closed).

---

## 1. Root cause

`budget.pay_periods` STORED three values per row and only one of them was a fact. **`C4-c` dropped
the other two on 2026-09-01 (`c703e1c7`), so this section is the arc's ROOT CAUSE as it stood, kept
because everything below is argued from it.** The table holds `start_date` and nothing else now.

| column | is it a fact? | what it actually was |
|---|---|---|
| `start_date` | **yes** | the day money arrived |
| `end_date` | no | `lead(start_date) - 1` -- the day before the NEXT payday |
| `period_index` | no | `row_number() - 1` over the user's paydays in date order |

`pay_period_service`'s own module docstring stated the derivation it then did not enforce
("end_date = day before next payday"); what the writer computed was `start_date + cadence_days - 1`,
which equals the definition only when the next batch happens to start at `start + cadence`.

**Two derived values stored beside the fact they derive from, with nothing reconciling them.** Every
symptom here is one disagreement: `end_date` BELOW the next `start_date` is a GAP, a day funded by
no paycheck (row P2); at or ABOVE it is an OVERLAP, a day funded by two; and `period_index` order
disagreeing with `start_date` order is the balance resolver walking money out of calendar order.

Because the schema cannot make them agree, the application grew
**five separate runtime fences that all police the same functional dependency**:

**FOUR OF THE FIVE ARE ALREADY GONE, and `C4-c` (`c703e1c7`) is what took them** -- which is this
section's own argument arriving, not a correction to it. Re-derive rather than re-read: the table
below once carried five line-range cites and every one of them had rotted by 2026-09-11.

| fence | where it stood | now |
|---|---|---|
| `_reject_overlapping_batch` -- one-directional, which IS row P2 | `pay_period_service.py` | DELETED; the file names it in prose only |
| `PeriodCalendar.__post_init__` -- the same property at the value boundary | `recurrence/_calendar.py` | the module MOVED to `pay_calendar/_calendar.py`; the guard survives |
| `_pp_assert_structure` -- the same property in the test suite | `tests/_test_helpers.py` | DELETED |
| `integrity_check` BA-03 / BA-04 -- the same property in weekly SQL | `scripts/integrity_check.py` | DELETED at `C4-c` and NOT replaced, which that file says at its own `:331` |
| `uq_pay_periods_user_index` + `ck_pay_periods_date_order` | the schema | both DROPPED; `uq_pay_periods_user_start` is what stands |

Regenerate with:

```bash
grep -rn '_reject_overlapping_batch\|_pp_assert_structure\|BA-03\|uq_pay_periods_user' \
     app/ tests/ scripts/
```

Not one of them would exist under the normalized model, because none would have a subject -- and the
four that have gone went exactly that way, by their subject being removed rather than by being
argued with.

## 2. Evidence

**Archived to `historical/pay_calendar_evidence_2026-08-11.md`.** The measurements and the rejected
options are a HISTORICAL RECORD: the rulings state what was decided and the code states what was
built. Cite the archive for how a decision came to be, never for what is true now.

## 3. Target model

**This is the END state. Which step creates each piece is marked.**

```sql
budget.pay_periods
  id            PK                              -- UNCHANGED: every inbound FK survives
  user_id       FK auth.users CASCADE  NOT NULL
  start_date    DATE NOT NULL                   -- THE PAYDAY.  the only fact in the row
  created_at
  UNIQUE (user_id, start_date)                  -- already live; the payday model's exact key

-- DROPPED [C4]: end_date, period_index
-- DROPPED [C4] with them: THREE constraints -- ck_pay_periods_date_order,
--                         ck_pay_periods_positive_index, uq_pay_periods_user_index

budget.pay_schedule            -- the OWNER's facts, one row per owner with a payday:
  user_id       UNIQUE          -- rolling_enabled, rolling_target_periods, history_opens_on
  -- DROPPED [C17-a]: cadence_days, shift_id, nominal_anchor -- the RHYTHM is an era's

budget.pay_eras                -- [C17-a] one row per "how I have been paid since"
  user_id       FK budget.pay_schedule.user_id RESTRICT  (fk_pay_eras_schedule)
  effective_from DATE NOT NULL  -- the era's first NOMINAL payday, and so the grid's PHASE
  kind_id       FK ref.pay_cadence_kinds          -- DROPPED [C17-d-2]: the kind is which parameter
                                                 -- columns are present (R-PC80)
  cadence_days  INT NULL        -- ck_pay_eras_cadence_range; fixed-days kind only [C17-d-2]
  nominal_day   SMALLINT NULL   -- 29..31 when the first month was too short [C17-d-2]
  other_day     SMALLINT NULL   -- semi-monthly's second day [C17-d-2]
  shift_id      FK ref.business_day_shifts        -- the convention (R-PC47)
  UNIQUE (user_id, effective_from)                -- uq_pay_eras_user_effective_from
```

An era governs from its `effective_from` to the next era's; the EARLIEST also runs backward below
the record, bounded by `history_opens_on` (**R-PC66**). Everything else is derived, once, by one
producer:

```text
period_index = row_number() over (partition by user_id order by start_date) - 1
end_date     = coalesce(lead(start_date) over (...) - 1,   -- the definition.  NOT
                                                           -- "- INTERVAL '1 day'", which
                                                           -- returns a timestamp
                        projected_payday(start_date, era, 1) - 1)  -- the open last one:
                                                           -- the day before the NEXT nominal
                                                           -- payday, displaced (C14-c, C14-e-3)
```

**The value type exists**: `PayCalendar` derives `(period_id, period_index, start_date, end_date)`
per period from the paydays alone. **But "consumers do not change shape" is FALSE for one class of
reader and saying so is the correction** (adversarial review, 2026-08-08): the hot path reads the
bounds off the ORM RELATIONSHIP, not off a calendar --
`period = txn.pay_period; attribution_date(txn.due_date, period.start_date, period.end_date)` on
every grid, dashboard and account render. ***That spelling no longer exists***: `C4-a-2` deleted
`utils.dates.attribution_date` for `DerivedPeriod.attribution_day`, so the shape a remaining reader
has is `period.end_date` off the relationship rather than a three-argument call. `PayPeriod.label`
(`models/pay_period.py:73-85`) is a model property built from `end_date`. Those callers hold a
`Transaction`, not a calendar, so C4 is a seam signature change for them and C1's oracle proves
nothing about it. ***The four sites this paragraph used to NAME are not the live set and two of them
had already migrated*** (AST census 2026-08-25): `calendar_service` takes a `DerivedPeriod` since
`C2-f2`, and `routes/transactions/_helpers.py:156` reads only `.user_id`, which survives C4.
**The measured census was `C4`'s, and section 0 states its outcome**, rather than a list here that
goes stale between the two. **Two constraints on the producer, both structural:**

1. **It is constructed ONLY from an owner's COMPLETE payday set.** A partial list makes the last
   row's end window-dependent (row P14). The partial-list constructor does not survive C2.
2. **A window is a VIEW over that calendar**, carrying the real ends, never a calendar rebuilt from
   a slice.

### What becomes impossible rather than checked

- **A gap** (consecutive dates define adjacent intervals -- no second column to disagree with),
  **an overlap** (intervals from distinct sorted dates never overlap), and
  **index order differing from date order** (it is the definition of the index).
- All five fences in section 1, and with them `PlacementOutcome.SCHEDULE_GAP`, `GenerationPlan.gaps`
  and `_recurrence_common.report_schedule_gaps` (C5, which ticks recurrence R-F10).

### The one question the model opens, and why it is its OWN step

A payday may now be inserted between two existing ones, splitting a period. **Ruled 2026-08-08:
refuse when the split period is locked by `classify_period_lock`, insert and re-derive otherwise.**
Adversarial review then showed "insert and re-derive" is a feature, not a clause, so it is **C6**.
Row **P10** carries the two halves that must be specified first: what happens to a row
`attribution_date` would now CLAMP into the wrong half (a silent RENDER on the wrong day, not an
orphan), and whether the split-off payday is repopulated (a monthly billed twice) or left empty
(income understated for the whole horizon).

### How a transaction's owner is proved -- RULED, BUILT, and closed

Finding **P75**, CLOSED at `C13-b`. **`budget.transactions` HAS a `user_id` since `C13-a`**, held
equal to both its account's owner and its paycheck's by a composite key each, and every door reads
it since `C13-b`. *This section opened with the sentence "`budget.transactions` carries no
`user_id`" until 2026-09-02 -- the same decay `C13-a` corrected in P75's own ledger cell.*

**What the ruling was.** `C2-f3e` left the question open and offered two answers: the owner's
CALENDAR (it holds one owner's schedule, so a foreign id is ABSENT) against a `user_id`-filtered
JOIN (one indexed query, refusing by never RETURNING the row).
**Ruling `R-PC32`, 2026-08-27, took NEITHER.** Both are ways of ASKING, and ruling for either would
have left two structural answers to one question on adjacent doors -- the denormalisation this arc
exists to remove. The third answer makes the question unanswerable by making the state
unconstructible. *The JOIN arm cited a live spelling, `Transaction.pay_period.has(user_id=owner_id)`
in `statement_match/_candidates.py`; that call no longer exists anywhere in `app/`, which is a
second reason the fork as recorded could not have been decided from this text.*

**All nineteen are RETIRED at `C13-b` (`e2c325dc`), and the PREDICATES are what say so.** A list of
line numbers in a planning document cannot survive the code -- these had already drifted twice -- so
what this section keeps is the two greps that REGENERATE the census, run last on 2026-09-03 and
returning prose only:

- **The RELATIONSHIP walk** -- `grep -rn "\.pay_period\.user_id" app/`, keeping the reads that
  REFUSE. Eleven after `C2-f3e`; ZERO live sites now, each one equality on `X.user_id`.
- **The PRIMARY-KEY refetch**, P51's literal wording -- a `PayPeriod` fetched by id and compared.
  Eight after `C2-f3e`; ZERO now. They went to the owner's CALENDAR rather than to the composite
  key, per ruling **R-PC46**: the key answers what may be STORED and a submitted id is a question
  about INPUT, which it does not answer.

**The SCOPES were weighed and REFUSED** -- `statement_match._candidates`' two period-set clauses and
`reconcile_service._rows`', which two comments had predicted this step would move. Each is also what
makes its span lookup TOTAL, so `Transaction.user_id` cannot replace it.

**The STAMP reads moved anyway** (`posting_service`, `loan_posting_service/_payments`,
`transaction_service/_settle`, `routes/transactions/_helpers`, `routes/entries`). They were never
P75's -- counting one as a check is how the first census reached three -- but a row's owner has ONE
home, and leaving them would have kept a hydration for a value the row carries.

## 6. Alternatives considered and rejected

**Archived to `historical/pay_calendar_evidence_2026-08-11.md`.** The measurements and the rejected
options are a HISTORICAL RECORD: the rulings state what was decided and the code states what was
built. Cite the archive for how a decision came to be, never for what is true now.
