# Implementation Plan: Recurrence Rule Redesign

**The argument for the two-axis recurrence model and the cash-date / installment-date split.** R1-R4
and the R7c cutover are ARCHIVED; the closed pattern set is GONE, which is what this arc was for
(R-R16 / R-R18 / R-R27). Each step, finding and ruling is a card in the tracker,
`saltyreformed-labs/shekel-plan` (`CLAUDE.md`, Development Status): an open step's specification is
its card's body and the order is the board's.

**Ten decisions were archived on 2026-08-08** to `historical/recurrence_as_built_2026-08-08.md`: the
`Once` retirement, R2 and R4 sequencing, the wrong stored paycheck, bound semantics, the
`Monthly First` and period-unit anchors, write-door enforcement, and the two `R-R12` superseded.

---

## 0. Why this arc splits, and the one ruling still owed

**The ORDER is the board's and is not restated here.** This section holds the one question the
split's measurement could not answer; the measurement itself, and the "R6 ships with X-an"
contradiction it exposed, are in `historical/recurrence_evidence_2026-08-11.md`, archived
2026-08-26.

**What the measurement said, and it is unchanged.** The ENGINE CORE (R1-R4) touches no file the
balance arc's anchor half edits, so it constrains nothing there. The DATE work does: `R5` and `R6`
sit on all four of `X-an`'s surfaces and one file of `X-f4`'s deletion set, which is why they are a
separate half and why they wait where they do. This arc asks which date IS the contractual
installment while X-an asks which date decides a payment already HAPPENED.

**RULED 2026-09-03 (R-R52)**: `R5` re-points behind `balance:X-bi-4`, not `X-f4` -- the X-f4 gate
rested on one shared file where R5's touch is a docstring line, merge hygiene and not a
dependency -- and `R6`, `R8-b`, `R8-c`, `R8-d` and `balance:X-k` follow R5 and inherit the move. R6
stays behind R5 (it reads `due_on`); the split-off accessor option was not taken.

**Consequence for Half A:** it must leave the `due_date` contract byte-identical so the R1 oracle
stays green, so no step before R5 touches the column. The transaction-template form's "Due Day of
Month" field went at `R5-a` (**R-R96**): a recurring due day that differs from the payment day is a
CONTRACT term, and it lives on the loan.

---

## 1. Root cause

**Archived to `historical/recurrence_evidence_2026-08-11.md` on 2026-08-26**, beside section 2's
evidence and for the same reason: the closed pattern set, the wide sparse table and the reverse
`_match_*` generation are the state this arc DELETED (R7c-a..R7c-c, rulings R-R16 / R-R18 / R-R27),
and the section still described them in the PRESENT tense with five citations into a module that is
now a package. **The one-line root cause that survives**: one cadence family got a knob and the
other got the interval baked into an enum NAME, so "every other month" had nowhere to live -- which
is a missing AXIS, never a missing enum member. Section 3 states what replaced it.

## 2. Evidence

**Archived to `historical/recurrence_evidence_2026-08-11.md`.** The measurements and the rejected
options are a HISTORICAL RECORD: the rulings state what was decided and the code states what was
built. Cite the archive for how a decision came to be, never for what is true now.

## 3. Target model

**This is the END state. Which step creates each piece is marked; see R2.**

```sql
ref.recurrence_units        -- PERIOD, WEEK, MONTH, YEAR                    [R2a, DONE]
ref.period_placements       -- CONTAINING_DATE, PERIOD_STARTING_ON_OR_AFTER [R2a, DONE]
ref.business_day_shifts     -- NONE, PRIOR, NEXT                            [R2a, DONE]

budget.recurrence_rules
  id               PK
  user_id          FK auth.users CASCADE          NOT NULL
  interval_n       INT   NOT NULL  CHECK (interval_n > 0)
  -- All five were COMPUTED until R7c and are authored columns since; the
  -- expand / backfill / tighten it took is in that step's own archive.
  unit_id          FK ref.recurrence_units RESTRICT   NOT NULL           [R7c-b]
  starts_on        DATE  NOT NULL   -- the rule's FIRST OCCURRENCE.  ONE
                                    -- meaning for every unit, and its
                                    -- position in the cycle IS the phase,
                                    -- so no month or day column survives
                                    -- beside it.  R-R16               [R7c-a]
  nominal_day      SMALLINT NULL  CHECK (nominal_day IS NULL OR (
                     nominal_day BETWEEN 29 AND 31
                     AND nominal_day > EXTRACT(day FROM starts_on)))
                                    -- the day the rule MEANS when
                                    -- starts_on's own month was too short
                                    -- to hold it.  29-31, not 1-31: a day
                                    -- the date already carries would be a
                                    -- second statement of it.  R-R3 [R7c-a]
  placement_id     FK ref.period_placements RESTRICT  NOT NULL           [R7c-b]
  shift_id         FK ref.business_day_shifts RESTRICT NOT NULL          [R7c-b]
  end_date         DATE  NULL   CHECK (end_date IS NULL OR end_date >= starts_on)  [CHECK at R7c-b]
  max_occurrences  INT   NULL   CHECK (max_occurrences IS NULL OR max_occurrences > 0)  [R2b, DONE]
  created_at
  CHECK (end_date IS NULL OR max_occurrences IS NULL)   -- at most one end bound

-- ONE subtype survives R-R13.  It carries a surrogate ``id`` PK plus
-- ``UNIQUE (recurrence_rule_id)``, NOT ``recurrence_rule_id`` as the PK: it is
-- audited, and ``system.audit_trigger_func`` assigns ``v_row_id := NEW.id`` --
-- on a table without that column every INSERT dies with
-- ``record "new" has no field "id"`` (measured on a probe table, R2b).
-- UNIQUE over a NOT NULL column enforces the identical 0-or-1 cardinality.

budget.recurrence_weekday_anchors    [R2b created it, EMPTY; R8 is the first writer]
                                     -- 0..1 per rule; nth-weekday-of-month rules.
                                     -- A REAL subtype: two fields with their own
                                     -- domain, not a repair for a lossy encoding.
  id                  PK
  recurrence_rule_id  FK -> budget.recurrence_rules ON DELETE CASCADE, UNIQUE
  nth_week            INT NOT NULL
                      CHECK (nth_week BETWEEN -1 AND 5 AND nth_week <> 0)  -- -1 = last
  weekday             INT NOT NULL CHECK (weekday BETWEEN 0 AND 6)  -- date.weekday(), 0=Mon

-- DROPPED by R-R13, unwritten: budget.recurrence_month_anchors.  Its whole
-- content was "the day I actually meant", present iff a DATE anchor had lost
-- it; ``nominal_day`` above holds it once instead.
-- NEVER CREATED (R-R12): budget.recurrence_due_dates.  A loan's recurring
-- due day is a CONTRACT term on the loan (``loan_params.payment_day``,
-- R-R96), not a fact about the rule.

budget.transactions / budget.transfers          [R5-b, rulings R-R12, R-R94]
  occurs_on        DATE  NOT NULL   -- the date the CADENCE names
  pay_period_id    FK                -- the funding.  Already exists.
  due_on           DATE  NULL        -- a date someone STATED that the
                                     -- occurrence does not give (R-R94).
  UNIQUE (template_id, scenario_id, occurs_on) WHERE ...   -- re-keyed off the paycheck
```

**`end_date >= starts_on` lands at R7c, with the column it names.** `end_date` is user-authored and
live; 14 live rules RESOLVE to a bound in the future, so setting an earlier end date -- exactly what
the field invites -- would become a `CheckViolation` out of `update_template`'s autoflush, which
nothing catches: the user could not stop an annual bill and the projection would keep charging it.
R7c adds it together with the Marshmallow validator that refuses the pair at the door.

**Zero conditionally-meaningless columns, and since R-R13 zero conditionally-MEANING-SHIFTING
ones.** `nominal_day` is NULL exactly when the unit does not fire on a day of the month, so absence
is the discriminator and no second table repairs a lossy one.

`placement` is well-defined for all four units, and **INERT under the PERIOD unit** -- a claim this
document retired and R3 restored by measurement. The retirement read `anchor_date` as the emitted
occurrence; R3 does not emit it. A pay-period-space rule emits the qualifying PAYCHECK's own
`start_date`, and both placements carry a period start back to that same period. Emitting the payday
is also what reproduces the current row's DATE: `compute_due_date` returns `period.start_date` for a
day-less rule, so emitting a mid-period bound would move it.

**The axis is short one value, and it is NOT the one this paragraph used to name.** It said both
members fund on or AFTER the occurrence, so a bill that must be funded in ADVANCE is inexpressible,
and named "the last paycheck on or before the occurrence" as the missing member (ledger row D20).
Plan step R8-a measured both halves false. `CONTAINING_DATE` funds from the paycheck whose span
COVERS the occurrence, so its payday is on or before that date by construction, and the member D20
named is that same rule under another name on a calendar whose periods tile.
**D20 CLOSED on that measurement.** What is genuinely inexpressible is a LEAD: funding from a
paycheck EARLIER than the containing one, so rent due 1 August is paid from the 17 July paycheck
rather than the 31 July one. Ledger row **D40**, plan step **R11**, which opens with the ruling of
what the lead is measured in.

**Only the `budget` tables are audited.** An earlier draft of this section said "all three new
tables go into `AUDITED_TABLES`"; measured against that list's own inclusion criteria
(`app/audit_infrastructure.py:46-64`) that is wrong for the `ref` tables -- the `ref` schema is
excluded with exactly one exception, the multi-tenant `ref.account_types`, and adding read-only seed
catalogues would both drown the trail in seed noise and move `EXPECTED_TRIGGER_COUNT`, which the
container entrypoint asserts at start. So: `ref.recurrence_units` / `ref.period_placements` /
`ref.business_day_shifts` are NOT audited (pinned by `TestNotAudited` in
`tests/test_models/test_recurrence_ref_tables_migration.py`), while
`budget.recurrence_weekday_anchors` and `budget.recurrence_month_anchors` went into `AUDITED_TABLES`
in R2b. **R7c drops the second of those with its table (R-R13), so it leaves the list in the same
migration** -- and `EXPECTED_TRIGGER_COUNT` moves DOWN by one, which the container entrypoint
asserts at start and which R7c must therefore update in the same commit.

### Where the old columns went

| old | new |
|---|---|
| `day_of_month` | `starts_on.day`, plus `nominal_day` on the one shape a short month loses (R-R16) |
| `month_of_year` | `starts_on.month` -- the FIRST OCCURRENCE's, which is in the cycle's residue class where the bound never was (R-R16, row D28) |
| `offset_periods` | `starts_on` (a date survives a schedule rebuild; an index does not) -- kills D1 |
| `start_period_id` (weak, bypassable) | deleted; `starts_on` is the start and is applied unconditionally -- kills D2 |
| `start_date` (strong, loan-sync only) | `starts_on` at R7c-c, which NARROWS D6 rather than closing it: for a loan rule that fires on a day of the month -- both live loans, and what `routes/loan/payment_transfer.py` sets up -- the first contractual installment IS an occurrence, so the fold is exact; for a DAY-LESS loan rule (row D27's unenforced precondition) `starts_on` is the payday of the paycheck that installment falls in, which selects the same paycheck and generates identically but does not keep the installment DATE. Nothing is lost that the app cannot re-derive: `rate_period_engine.first_installment_date(origination_date, payment_day)` answers it from the loan |
| `due_day_of_month` + implicit next-month rule | dropped at `R5-a` (R-R96): a recurring due day that differs from the payment day is the loan's contract term, `loan_params.payment_day` |
| `Once` pattern | deleted; `recurrence_rule_id IS NULL` for both template kinds |

### Generation becomes one function

**Built at R3.** The signature below is the shipped one, not the drafted one -- the draft read
`occurrences(rule, window)`, which cannot serve the PERIOD unit at all (finding D9, closed):

```text
occurrences(resolved, calendar, *, through) -> Iterator[date]      # forward, by unit
place(occurrence, calendar, placement) -> SchedulePeriod | None    # bisect, NOT total
occurrence_placements(resolved, calendar, *, through=None) -> tuple[...]
```

Forward generation plus placement is explicit -- a date the schedule cannot host is a stated "no
period" rather than one nobody looked for. This kills D3 structurally, at any cadence, and R4
retires all five `_match_*` helpers.

**It is not TOTAL, and the claim that it is was measured false.** This paragraph read "periods are
contiguous by construction (`pay_period_service.py:190`), so every date has exactly one period".
Contiguity holds WITHIN a generated batch and not across batches: `_reject_overlapping_batch` only
requires a new batch to start after the latest existing `end_date`, so `latest_end + 5 days` is
accepted and leaves a gap -- and registration bootstraps a 14-day period 0 that any later real
schedule starts after. `place()` answers "no period" and the composition REPORTS the unplaced
occurrence; generation LOGS it and skips it (ruled 2026-08-08, built at R4b-2, row D7 closed).
Closing the WRITER that permits a gapped batch is finding F-10.
