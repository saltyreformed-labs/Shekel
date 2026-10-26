> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# `balance:X-bl-2b` as built: a payment's three dates are ONE value (2026-09-09)

**What shipped** (`0e93ec9f`). `amortization_engine._dates.PaymentDates` is the
one home for a loan payment's funding period, installment and cash day.
`PaymentRecord(dates, amount)` and `loan_ledger.PaymentInstallment(income_shadow,
dates)` both COMPOSE it; `rate_period_engine.ConfirmedPayment` is DELETED;
`loan_resolver._periods._replay_from_anchor` takes the five values it reads,
keyword-only; `amortization_engine.slotted_dates` is the one application of the
biweekly-collision rule. Ruling **R-BAL9**, which SUPERSEDED **R-BAL7**'s
deferral of the composition. Closed **N-432**; opened **BAL-472**, **BAL-473**,
**BAL-474**.

## The ruling, and why the shipped design is bigger than the question asked

The developer was offered three ways to move the suite's six consumers off the
pricing tier -- six thin assemblies, one shared helper, or the compose refactor
-- and REFUSED the premise of all three: *"I want root cause solutions only. I
want the best from scratch design."*

Composing alone does not fix the defect. `_replay_from_anchor` would still be
typed on the priced `PaymentRecord`, so a caller holding no amounts still could
not feed it. The root cause is two separable facts, and both had to move:

1. **Three types held one payment's three dates**, and two of them NAMED the
   funding basis differently (`PaymentRecord.payment_date`,
   `PaymentInstallment.period_start`). One value, three spellings.
2. **The replay's input was typed as the priced record**, not as the dates it
   actually reads.

## What `ConfirmedPayment`'s deletion cost, stated rather than waved off

Its `settled_on: date` made "an unsettled payment reaching the replay"
UNREPRESENTABLE. That state is representable now, and one `and` conjunct stands
between it and a replayed row.

The trade was taken on a MEASUREMENT: `is_confirmed_payment_eligible` is
`anchor_date < due_date and has_settled_by(settled_on, as_of)`, and
`has_settled_by(None, as_of)` is `False` by that predicate's own documented
contract -- so the caller-side `settled_on is not None` filter the type existed
to justify was pure redundancy, and the type carried no `__post_init__`, so
nothing ENFORCED was lost. The residual risk is covered from both sides: the
predicate's `None` arm is unit-pinned (`test_dates.py`), and the replay
behaviour by `TestReplayTakesTheWholeFeed`.

## The closure figures, and how they were established

Two INDEPENDENTLY WRITTEN walkers over the same metric -- the oracle's own
`_import_closure` and a second implementation written from scratch -- run
against a worktree at `2625963a` and against the shipped tree:

| roots | `2625963a` | shipped |
|---|---|---|
| `loan_loaders` + `loan_payment_service` + `loan_resolver` (pricing) | **101** | 102 |
| the reference's own tier (amount-free) | 45 | **46** |
| `amortization_engine` | 5 | 6 |
| `rate_period_engine` | 6 | 7 |
| `loan_payment_service._engine_prep` | 15 | 16 |

The reference goes **101 -> 46**; like-for-like on one tree it is 102 -> 46, so
**56 modules** are removed. The `+1` everywhere is `_dates` itself. Both of
`X-bl-2b`'s neutral reviewers re-derived every figure independently and
confirmed all ten. The metric is stated in
`app/services/loan_ledger/_installments.py`.

## What was rejected on the way

* **A public app door returning the replay.** `loan_resolver` and `loan_ledger`
  are fenced packages whose public producer sets deliberately hold no
  balance-at-T reader, and a `ScheduleReplay` carries `balance_as_of`.
* **A test-only producer in `app/`** -- ruling **P54**, cited at
  `tests/_test_helpers.py`: a production API with only test callers is the
  speculative shape `CLAUDE.md` rule 13 forbids.
* **A suite-side restatement of the replay assembly**, which would be two
  spellings of one derivation (rule 14).

## The controls this step added, and their mutation grades

* `TestReplayTakesTheWholeFeed` (`test_rate_period_engine.py`) -- a mixed feed
  answers what the settled half alone answers, plus a non-vacuity arm giving the
  projected payment a settle day and asserting the balance moves. **Graded**:
  breaking the eligibility call fires both arms and NO other test in that file,
  so nothing previously graded this at that tier.
* `TestBothFeedsReachOneSlotAssignment` (`test_loan_installments.py`) -- the
  priced feed and the amount-free feed resolve a collision identically. **This
  was ungraded before**: `slotted_dates`' whole reason for existing had no test.
  **Graded**: removing the slotting from the priced feed fires it and only it.

## Two stale claims corrected, and one re-introduced then corrected again

* The oracle's comment quoted *"of the reference's 91-module import closure, 21
  are inside W9908's allowlist and every one of those is `app.models.*`"*. The
  real count is **zero**: the allowlist holds the bare name `app.models` and the
  check tests literal membership, so `app.models.account` is not a member. 21
  was the count of `app.models.*` modules, a different set. The `app.models`
  exclusion is INERT today and kept because the allowlist is read at run time.
* `LoanInputs`'s docstring cited a `dataclasses.replace` caller deriving a
  confirmed-only view. No such caller exists in `app/` or `tests/`.
* A first draft of the new `dates must be a PaymentDates` guard claimed it
  catches a stale positional call. It cannot -- the old shape had four fields
  and the new has two, so a stale call dies on ARITY first. That exact
  correction had already been made once on `test_bool_settled_on_raises_type_error`
  and this step briefly deleted it. The adversarial review caught the
  re-introduction; both are restored.

## What the reviews caught that the census did not

The rule-7 caller census covered `_replay_from_anchor`'s CALLERS and missed the
readers of the two restructured records' FIELDS. Three files were caught by the
first full suite (RED: 4 failed) and one -- `tests/manual/verify_reader_baseline.py`
-- only by the neutral code review. That last one matters most: `_guard` catches
`Exception` and records `{"RAISED": ...}` where a figure belongs, so a probe the
balance arc diffs before and after a cutover would have gone silently dead.
**The lesson is the census's AXIS**: a moved FIELD needs a census of readers,
not of callers.

## Verification

`13,217 passed` (full suite, `./scripts/test.sh`, 5:27). `pylint app/` 10.00/10
exit 0 with all nine custom checkers as `--fail-on`; `scripts/`, `tests/manual/`
(E/F), cross-tree `duplicate-code` and `shekel-decimal-from-float` over `tests/`
all exit 0.
