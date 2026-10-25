> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Archived evidence and rejected alternatives: the pay calendar

**Lifted out of `implementation_plan_pay_calendar.md` on 2026-08-11**, unchanged. Sections 2 and 6 of that
document as they stood; their headings are kept so an existing citation still resolves.

## 2. Evidence

### The table is ALREADY the paydays table

`uq_pay_periods_user_start UNIQUE (user_id, start_date)` is live on production -- the payday model's
exact key, already enforced. The two derived columns are pure redundancy on a correctly-keyed fact
table, which is why C4 is two `DROP COLUMN`s and not a rewrite: row `id` never moves, so all four
inbound FKs (`transactions`, `transfers`, `journal_entries`, `recurrence_rules`) are untouched.

### Live census, `shekel-prod-db`, 2026-08-08, re-verified 2026-08-10

```text
owner user 1 : 61 paydays, FIRST 2026-03-26, LAST 2028-07-13 (the last stored end_date
               is 2028-07-26; quoting that as the range's top would be the very
               conflation this document argues against).
               0 non-contiguous pairs, 0 periods of a length other than 14
               pay_schedule: cadence 14, rolling ON, target 52
user 2       : companion, 0 paydays -- CORRECT by design (it reads the owner's).
recurrence   : 46 rules, interval_n > 1 on ZERO, offset_periods <> 0 on ZERO
derivation   : row_number()-1 and coalesce(lead(start_date)-1, start+cadence-1)
               reproduce the two stored columns on 61 of 61 rows, 0 mismatches.
```

**Production has no hole today**, so this arc is entirely about what the writer PERMITS.

### P2 -- the writer accepts a hole, and only two paths can pass it one

**ONE `PayPeriod` constructor now exists in `app/` and `scripts/`** -- `generate_pay_periods`. *This
paragraph named a second, `auth_service`'s registration bootstrap, until `balance:X-ad-a` deleted
it; re-measured 2026-08-10.* Of its `app/` callers, `extend_pay_periods` cannot gap (it starts at
the last end + 1), `reset_pay_periods` cannot (it wipes first), and `top_up_rolling_window` inherits
extend's safety. **The gap-bearing paths are the two that take a free date from a form**:
`/pay-periods/generate` and `regenerate_pay_periods`. Cost when a hole exists is `-$140.63`, cited
from balance **N-128** rather than measured here: production is contiguous and no gapped clone
exists.

### P3 -- a new owner cannot enter their real first payday

**CLOSED by `balance:X-ad-a` (`2a4eb477`).** Kept as EVIDENCE: under the payday model that input
needed no code at all, so the special case existed only to keep two columns honest.

### P4 / P5 -- BOTH gates inherit the write door's blind spot

`integrity_check`'s BA-03/BA-04 and the suite's `_pp_assert_structure` each police OVERLAP and say
nothing about a HOLE, so neither could have caught P2. BA-04 is additionally off by one. Both rows
carry the predicates and their line cites.

### P8 / P12 / P29 -- the cadence, read circularly and written by accident

Three defects on `budget.pay_schedule.cadence_days`, all sharpened by the payday model because the
column becomes an INPUT to the last period's derived end. **P8**: `resolve_cadence` infers the
cadence from the last period's LENGTH, which after C4 reads back the value it produces (its
write-door half shipped at `balance:X-ad-a`). **P12**: a batch that creates NOTHING still rewrites
the stored cadence. **P29**, found 2026-08-10, is P12 in the mirror: the extend door generates at a
cadence it never persists. C3-b's one rule closes both. All three rows carry the traces.

### P9 -- a legal schedule the CHECK forbids

`ck_pay_periods_date_order CHECK (start_date < end_date)` makes a one-day pay period illegal, and
two paydays one day apart legitimately produce one. An artifact of `end_date` being authored.

### P13 -- `period_index` was the wire key of a destructive form

**CLOSED by `C3-a`.** Kept as EVIDENCE: a user-supplied ORDINAL selected which rows a CASCADE
destroyed, across a browser round trip, and was stable only while nothing renumbers.

### P6 -- SEVEN implementations, not three, which is what C2 is sized against

An AST census found **six**, not the three this row claimed until 2026-08-10, and a review of C2-a
found a SEVENTH the census structurally could not see -- it keyed on the predicate. Row **P6**
carries the site list and the lesson; the six `pay_period_service` readers carry **66** call sites.

### P14 -- the derivation is window-dependent where the stored column was not

Derived over a PARTIAL payday set, the last row falls to `start + cadence - 1` instead of
`lead(start_date) - 1`, so
**the same period reports a different end depending on which window asked** -- a disagreement a
stored column cannot produce. Row **P14** records the mechanism it first named as REFUTED; row
**P26** carries the half that survives on the other column. C2-a shipped the structural answer: a
calendar is built ONLY from a complete set, and a slice is a `PeriodWindow`.

## 6. Alternatives considered and rejected

**Refuse a gapped batch at the write door.** The first option weighed, and the one finding F-10 was
written as. It is a SIXTH fence around the same functional dependency, and C3 would delete it. It
also makes two live features unusable: `regenerate_pay_periods`' "Corrected first payday" field
could only ever accept the single day after the retained coverage ends, so it could correct a
cadence and never a payday; and P3's blockage gets worse rather than better.

**Bridge a hole with a filler period.** Insert `[latest_end + 1, new_start - 1]` as its own period.
It fabricates a paycheck that never happened: the templates generate a full set of recurring bills
into it, the paycheck calculator writes it an income row, and `growth_engine.period_return_rate`
prorates a return into it (`:288`). A model that invents money events to preserve a stored column is
worse than the hole.

**Rename the table to `budget.paydays`.** Correct about what the row holds, and rejected on cost
with no correctness gain: four inbound foreign keys would move,
`transactions.pay_period_id -> paydays.id` reads worse than what it replaces, and a pay period
genuinely IS identified one-to-one by the payday that opens it. The name `start_date` is kept for
the same reason.

**Delete the out-of-schedule answer and derive the ledger's paycheck from `entry_date`** (C2's fork
1). Rejected 2026-08-10 on `shekel-prod-db`, because its premise is false:
**14 days carry TWO paychecks for one date** and **35 of 327 entries** are dated outside their own
paycheck. The measurement of record is `filing_period`'s docstring and row **P18**, which also shows
the column holds TWO relationships -- a COPY on 174 of 174 transaction-sourced entries, a derivation
only for assertions. That is `C7`'s subject, not this step's.

**Keep `period_index` stored and derive only `end_date`.** Half the normalization, keeping the half
that needs the advisory lock and the uniqueness constraint. *The first draft rejected it on a claim
adversarial review REFUTED -- that the index is never a persisted reference or a wire key; it was
both.* It SURVIVES on better grounds: the index's only stable referent is its position in payday
order, so storing it stores the functional dependency **P1** describes. Its one IDENTITY use, the
truncate form, was wrong before this arc and C3-a re-keyed it onto `id` regardless.
