> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Archived evidence and rejected alternatives: the recurrence redesign

**Lifted out of `implementation_plan_recurrence_redesign.md` on 2026-08-11**, unchanged. Sections 2 and 6 of that
document as they stood; their headings are kept so an existing citation still resolves.

## 2. Evidence

Live dev-clone census, 50 rules (2026-08-05):

```text
pattern            rules  dom duedom  moy  end start stperiod off!=0  n!=1
Annual                20   20      0   20    0     0       20      0     0
Monthly               14   14      0    0    4     2       13      0     0
Every Period           7    0      0    0    1     0        5      0     0
Once                   4    0      0    0    0     0        4      0     0
Semi-Annual            2    2      0    2    0     0        2      0     0
Quarterly              2    2      0    2    0     0        2      0     0
Monthly First          1    0      0    0    0     0        1      0     0
ORPHANED rules            : 5      (recurrence_rule_id is ON DELETE SET NULL
                                    on both templates -- deleting a template
                                    leaks its rule forever)
```

`due_day_of_month` is used by ZERO rules. `interval_n` / `offset_periods` are at their defaults in
ALL 50.

### D2 -- an edit ignores the chosen "First paycheck"

`start_period_id` is consulted only when `effective_from is None` (`recurrence_engine.py:121`); the
update route always passes `effective_from = data.pop("effective_from", date.today())`
(`templates.py:643`). Probe:

```text
PROBE: chosen start period_index = 8
PROBE: after CREATE  earliest_index/count = (8, 2)
PROBE: after EDIT    earliest_index/count = (4, 6)
```

Four transactions materialised in pay periods the user explicitly excluded. The model docstring
already documents the bypass (`recurrence_rule.py:63-68`) and it is unfixed. 45 of 50 live rules
carry a `start_period_id` (47, not the 45 an earlier draft said -- the census table above sums to
47).

### D3 -- the monthly matcher was neither total nor injective (CLOSED at R4a)

Kept because **D18 is its surviving half**: `compute_due_date` is the last reader of the same
endpoint-month scan, so the defect moved from period selection into the row's DATE rather than
dying. The probe (a monthly day-15 rule finding 6 of 12 occurrences at a 90-day cadence, and
returning one period twice) is in `recurrence_as_built_2026-08-08.md`.

### D4 -- cash date and installment date cannot differ for a loan

Traced on the live Van Loan:

```text
loan_params:      payment_day = 22, origination 2023-02-14
recurrence rule:  Monthly, day_of_month = 22, due_day_of_month = NULL
generated:        period 2026-07-16..07-29  ->  due_date 2026-07-22
```

The developer's actual facts: cash leaves 22 Jul; the lender's installment is due 1 Aug. Three
columns cover two facts, and one does double duty:

| fact | true value | column today |
|---|---|---|
| cash date (money moves, drives period placement) | Jul 22 | `recurrence_rules.day_of_month` = 22 |
| contractual installment (drives the amortization ledger) | Aug 1 | `recurrence_rules.due_day_of_month` = NULL **and** `loan_params.payment_day` = 22 |

**An earlier draft of this section called the generated row's `due_date` the CASH date. It is not**,
and the correction is what forces R-R12: `models/transfer.py:169-177` and
`loan_loaders.loan_payment_due_date:576-644` both state that on a loan payment the stored `due_date`
IS the installment, that the genesis walk orders payments by it, and that its strict
`anchor_date < due_date` post-anchor boundary is applied against it -- so moving it moves the POSTED
balance. Migration `c4e91a7b2d38` already rewrote live rows off the pay-period basis onto it.
Measured on production 2026-08-08: 112 loan shadows, Van Loan every one on the 22nd, Mortgage every
one on the 1st, and **2 carry a `due_date` outside their own pay period** (the first Van row is
dated 2026-04-22 in a period opening 2026-04-23), which `utils/dates.attribution_date:205-214`
clamps back in for the calendar and the daily balance line while the ledger reads it unclamped. So
the app currently believes the Van installment is the 22nd, ten days from the truth.

`rate_period_engine.monthly_due_date:313` reads `payment_day` as "the contractual due date".
`loan_recurrence_sync._sync_loan_cadence:164` force-overwrites
`rule.day_of_month = params.payment_day` and documents the overwrite as intentional.
**So for a loan the cash date is pinned to the installment date and they can never differ.**

Cost, measured: **zero dollars.** Loan interest is monthly-nominal --
`round_money(balance * annual_rate / 12)`, `app/utils/money.py` `accrue_monthly_interest` -- with no
day count anywhere in the amortization path. What is wrong is date labels (payoff date, schedule
rows, payment history), the `first_installment_date` bound, and one anchor-boundary comparison where
a true-up dated inside the gap could flip a payment's inclusion.

### R-R3 -- one `anchor_date` cannot hold "the 31st", and that is a REGRESSION

Ruled 2026-08-05, before R2b was built. Section 3's `anchor_date DATE` was to be the sole source of
a calendar rule's day-of-month. Measured against the CURRENT engine, it loses information.

Today the day is the integer `day_of_month`, clamped per month (`recurrence_engine.py:546`,
`min(day_of_month, last_day)`), so `day_of_month = 31` means "the last day of every month". A DATE
cannot hold 31 when the anchor month is shorter, and the clamped value it holds instead propagates
forward:

```text
monthly day=31, first occurrence April 2026
  today  : Apr 30  May 31  Jun 30  Jul 31  Aug 31  Sep 30  Oct 31  Nov 30
  anchor : Apr 30  May 30  Jun 30  Jul 30  Aug 30  Sep 30  Oct 30  Nov 30
           (anchor_date = 2026-04-30)          4 of 8 WRONG

monthly day=30, first occurrence February 2027       7 of 8 WRONG
annual  Feb 29, anchored in a non-leap year          never fires on Feb 29 again
```

5 of 12 possible start months (Feb, Apr, Jun, Sep, Nov) clamp a day-31 rule's anchor.
**Zero live rules are affected** -- the only day-31 rule is annual in March -- so this is about what
the model permits going forward, and it is silent: the user sees a plausible date, never an error.

Its 2026-08-05 ruling -- keep `anchor_date` and repair the lossy 29-31 case with a 0..1
`recurrence_month_anchors` subtype -- is **SUPERSEDED by R-R13 below**, which was taken once R-R8
supplied the half of the evidence R-R3 did not have.

### R-R13 -- `anchor_date` SPLITS, and the repair table goes with it

Ruled 2026-08-08. R-R3 weighed "split `anchor_date` into phase + `starts_on`" and rejected it for
making two columns hold one value in the common case. That was decided against the CLAMPING evidence
alone. R-R8 then established a second, larger loss the rejection never weighed:
**the column means different things per unit** and is never NULL, so nothing signals the shift --
`_occurrence.py:61-70` and `ResolvedRecurrence`'s own docstring (`_resolution.py:200-206`) both
state that for the PERIOD unit the anchor is a BOUND and not the first occurrence. A column whose
meaning depends on another column is the exact defect section 1 names, re-created in the model meant
to delete it, and R7c is about to freeze it NOT NULL.

**Ruling: `starts_on DATE NOT NULL` (the opening validity bound, ONE meaning for every unit) plus
`nominal_day SMALLINT NULL CHECK (nominal_day BETWEEN 1 AND 31)` on the rule itself.**
`budget.recurrence_month_anchors` is DROPPED rather than given its first writer.
`recurrence_weekday_anchors` STAYS: an nth-weekday anchor is two fields with their own domain, a
real subtype rather than a repair for a lossy encoding.

Three things this deletes, all of them apparatus the encoding required:

- the side table whose whole content is "the day I actually meant", present iff the primary encoding
  lost it;
- the two-place read `subtype.nominal_day if present else anchor_date.day`, implemented twice today
  (`_occurrence.py:393`, `_occurrence.py:489`);
- **`_require_generable`'s third refusal** (`_occurrence.py:489-505`), which exists only to check
  that the two representations of one fact agree. With the day stored once the disagreement is
  unconstructible, which is the difference between a fence and a structure.

**Consequence for R8.** The mutual-exclusion problem R-R3 handed R8 -- "DDL cannot say at most one
of two subtype rows" -- disappears with the second subtype. What R8 must still refuse is
`nominal_day` beside a weekday anchor, which is one table and one row, so a CHECK can express it.

**Consequence for R7c.** Its backfill writes `starts_on` and `nominal_day` instead of `anchor_date`,
and its downgrade round-trip is easier, not harder: `nominal_day` is exactly `day_of_month`.

### R-R6 -- the bounds move from PERIODS to OCCURRENCES

Ruled 2026-08-05, **SHIPPED at R4a**; the eight measured shapes and the four that moved are in
`docs/plans/historical/recurrence_as_built_2026-08-08.md`. The rule: `match_periods` bounded
PERIODS, so a row could be generated whose OWN occurrence lay outside the window the user stated
(defect D5). The engine bounds OCCURRENCES instead. Zero live rules were affected.

**Same ruling, second question, and this half is still LIVE because R7c's backfill must reproduce
it: `Monthly First`.** Its anchor is the 1st of the first month whose OWN first paycheck falls on or
after the effective start -- not the 1st of the effective month, which would place the first row in
a paycheck EARLIER than the one the user chose, because the placement rule is "the first period
starting on or after the occurrence". On the developer's schedule a rule starting at the 2026-07-30
paycheck skips July (whose first paycheck, 07-02, precedes the chosen start) and anchors 2026-08-01.
Today's engine puts that row on 07-30, the LAST July paycheck, which the rule's own name says it
should not. Zero live rules affected: the one `Monthly First` rule starts at period index 0.

### R-R7 -- the orphaned rules stay until the leak itself is closed

Ruled 2026-08-05. Two claims in earlier drafts of this document were measured and are false. R2b's
said the 5 orphaned rules have "neither a start period nor a template": ids 4, 41, 43 and 47 all
carry `start_period_id` (1, 1, 3, 2), so they are as derivable as any other rule and R2b backfills
all 50. R9's said changing the template FKs off `ON DELETE SET NULL` stops rules leaking: that FK is
`transaction_templates.recurrence_rule_id -> recurrence_rules.id`, so it fires when a RULE is
deleted. The leak runs the other way -- `templates.hard_delete_template` (`templates.py:904`)
deletes the template and leaves its rule -- so no `ondelete` on that FK can close it.

**Ruling: deleting the 5 rows moves out of R2b and into the commit that closes the hole** (step
R-F6), so the cleanup and its cause are reviewed together and R2b stays purely additive.

### R-R2 -- a signed day offset was proposed and disproved

A `due_offset_days SMALLINT` was the first proposal, and the Van case disproves it: `+10` gives Jul
22 -> Aug 1 correctly, then Sep 22 -> Oct 2 and Feb 22 -> Mar 4, both wrong by the month's own
length. "Due on the 1st of the following month" is a (day, month-offset) pair, not a day count.
**R-R12 settles it a level down**: the installment is a DATE on the generated row (`due_on`), so
nothing has to encode an offset at all, and `due_day_of_month`'s implicit `+1` inference from
`due_dom < dom` (`recurrence_engine.py:699`) dies with `compute_due_date` at R5.

## 6. Alternatives considered and rejected

**Archived to `docs/plans/historical/recurrence_as_built_2026-08-08.md`** under rule 5 on
2026-08-08, to buy the room ruling R-R13 needed. Three were weighed before R1 and none has been
re-opened: adding two enum members, RFC 5545 RRULE strings as the storage model, and materialising
occurrence dates into their own table.

## 1. Root cause (lifted 2026-08-26)

**The state below is the one this arc DELETED, and it is preserved verbatim as the argument for
why.** It was still in the live document in the PRESENT tense on 2026-08-26, with five citations
into `app/services/recurrence_engine.py` -- a PACKAGE since R7c, so every one of them resolved to
nothing. Measured that day on `origin/dev`: `RecurrencePatternEnum` survives in `app/enums.py`
only inside a comment saying it was deleted; `budget.recurrence_rules` holds thirteen columns and
none of `pattern_id`, `day_of_month` or `month_of_year`; the five `_match_*` helpers survive only
in a docstring saying they went; and neither `recurring_view.py` nor `savings_goal_service.py`
names `Once` at all. Rulings **R-R16** / **R-R18** / **R-R27** and steps R7c-a..R7c-c are what
changed it.

### As it stood

`RecurrencePatternEnum` (`app/enums.py:136`) is a closed set of eight names. Four of them --
Monthly, Quarterly, Semi-Annual, Annual -- are the same idea with a different integer baked into the
*name*: every 1, 3, 6, or 12 months. The integer lives in a column (`interval_n`) only for the
paycheck-space family (`Every N Periods`).

**One cadence family got a knob; the other got hardcoded constants.** That is why "every other
month" and "every two years" have nowhere to live. It is not a missing enum member; adding two would
repeat the exercise at the next gap.

Second-order consequences of the same fusion:

- `budget.recurrence_rules` is a wide sparse table: 8 columns whose validity depends on
  `pattern_id`, with **no constraint tying presence to pattern**. The engine papers over malformed
  rules with `rule.month_of_year or 1` and `rule.day_of_month or 1`
  (`recurrence_engine.py:504,516`), so a broken annual rule silently becomes January instead of
  failing loud.
- `Once` is a row in the recurrence table that means "no recurrence", requiring four separate guards
  to suppress: `recurrence_engine.py:115`, `:257`, `recurring_view.py:236`,
  `savings_goal_service.py:427`, plus `templates.py:931`. Transaction templates already model this
  correctly (`recurrence_rule_id IS NULL`); transfers were forced onto `Once` because their form has
  no null option (`_recurrence_fields.html:49`).
- Generation is a **reverse** mapping ("scan every period, ask if it contains the target day"),
  which needs five near-identical `_match_*` helpers (`recurrence_engine.py:527-628`) and is neither
  total nor injective (see D3).

## 0. The split MEASUREMENT and the R6/X-an contradiction (lifted 2026-08-26)

**Both are finished business.** The file-overlap trace of 2026-08-05 is what decided that this
arc splits, and its conclusion is the one sentence the live section keeps. The
"R6 ships with X-an" contradiction was found on 2026-08-09 and is now GATE-GRADED
(`conventions.md` rule 13, `steps.md`'s blocker column), so it cannot be re-entered and the
account of it is a record rather than a warning. Lifted under rule 5 to bring the live document
back under its cap; the `developer-decision` the section still OWES stayed behind.

### As it stood

**The ORDER is `steps.md`'s and is not restated here.** This section holds the MEASUREMENT the order
rests on and the one question the measurement could not answer.

The recurrence work SPLITS. Measured file overlap against
`docs/audits/balance_architecture/README.md`'s live blocks (2026-08-05):

```text
R1-R4 (engine core)  vs X-an                 : 0 files
R1-R4                vs X-f4 deletion set    : 0 files
R1-R4                vs xx-attempt-1-held-rde: 0 files
R1-R4                vs xd-attempt-1-parked  : 1 file  (_recurrence_common.py)
PAY_PERIODS_PER_YEAR vs X-an / X-f4 / X-d    : 0 files

R5+R6 (dates)        vs X-an                 : 4 files -- ALL FOUR of X-an's surfaces
R5+R6                vs X-f4 deletion set    : 1 file  (cash_ledger/_events.py)
```

**What the measurement says.** The ENGINE CORE touches no file the balance arc's anchor half is
editing, so it constrains nothing there; it delivers every-other-month, every-two-years, weekly,
nth-weekday, count-bounded end, business-day shift and defects D1, D2, D3. The DATE work is a
different story: `R5` and `R6` sit on all four of X-an's surfaces and one file of X-f4's deletion
set, which is why they are a separate half and why the index gates them where it does.

**"R6 ships with X-an" was UNSATISFIABLE, and the contradiction was inside this section** (found
2026-08-09 while building X-an's first leaf). Three statements could not all hold: R6's own
specification derives the installment "over the rule plus `due_on`"; `due_on` is created by R5; and
R5 cannot precede the balance step that deletes from `cash_ledger/_events.py`. The sentence right
after the ordering claim already said so -- "`due_on` is created by R5 and READ by R6, so the order
is forced anyway". `steps.md` recorded only `R6 blocked by balance:X-an`, and nothing reconciled the
two; **that column is now graded, so the same contradiction cannot be re-entered** (`conventions.md`
rule 13).

**What survives is the TRACE, not the ship.** The file overlap is real and unchanged: this arc asks
which date IS the contractual installment while X-an asks which date decides a payment already
HAPPENED, and tracing them apart means tracing the loan half's date semantics twice. X-an-a was
traced with R6's question in view and shipped without it.

**`developer-decision` OWED, and it is the one thing in this section the index cannot settle.** Two
options: re-point R6 behind R5, which is what `steps.md` currently records; or split off the half
that needs no `due_on` -- the single `loan_installment_date` accessor over the rule -- and ship that
beside the remaining X-an leaf.
**The index recording the first option is not the developer choosing it.**

**Consequence for Half A:** it must leave the `due_date` contract byte-identical so the R1 oracle
stays green, so no step before R5 touches the column. The transaction-template form's live "Due Day
of Month" field (`_recurrence_fields.html:104-111`, `routes/templates.py:472,649`) stays exactly as
it is until R5 gives the installment a column of its own.

---

