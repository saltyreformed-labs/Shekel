> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# X-gj-3b WITHDRAWN: a standing rule does not name a row set (2026-09-02)

**Ruling `bank_import:R-JJ`, developer 2026-09-02.** `X-gj-3b` was ranked #1 and is removed from
the index. **R-HT(b) had TWO clauses and only one is repealed**: *a rule answers a signature
with the row set it pays* goes, and *where the residue lands -- onto a named row, re-pricing it*
STANDS, because `X-gj-3a` shipped it, **R-IU** amended it, and `_variance.py` and `_accept.py`
cite it for behaviour that is live today. **R-HT(a)** stands, shipped at `X-gj-2a` / `X-gj-2b`. This file
holds what rule 4 will not fit in the ruling row: the measurements, the rejected options, and the
question the withdrawal leaves open.

## The census

On a clone of the developer's staged data (378 lines, one account, 2026-09-02), **3 of 221**
recorded acts name more than one app row. 218 name exactly one app-side member -- 153 a purchase
ENTRY and 65 a transaction ROW -- and every act names exactly one bank line, so no
many-lines-to-one-row population exists either.

Each of the three is removed by a root-cause step in another arc:

| act | bank line | app rows | removed by |
|---|---|---|---|
| Amazon order split | `$85.28` | 2 budget rows | nothing, and nothing should -- 1 of that merchant's 26 lines, a different row set every time, which no standing rule can express |
| payroll | `$2,611.90` | `Data Manager $2,572.36` + `Phone Allowance $39.54` | `recurrence:R18`, which gives a paycheck earnings LINES |
| Capital One | `$466.47` | 4 `CC Payback` rows | `credit_card:CC3b` (**N-337**) -- and a group could never balance here anyway: 14 unmatched payment lines worth `$10,842.41` against 22 payback rows totalling `$6,286.46` |

## Why the payroll collapse is measured rather than assumed

The Phone Allowance rides INSIDE the deposit: 2026-08-13 pays `$2,611.90` against
`Data Manager $2,572.36 + Phone Allowance $39.54`. So once it is an earnings line the deposit names
ONE row, whatever cadence `R18` settles for it. The collapse does not rest on the cadence claim.

## The defect is ELICITATION, not the concept

A rule storing the LARGER template set and resolving it against the period's contributing rows is
right on **7 of 7** -- periods 3, 5 and 7 hold no Phone row, and period 1's is `Cancelled` and
excluded by `balance_contributing_clause`. What fails is getting that set from one line's card:
4 of the 7 in-period deposits hold `{Data Manager, Health Insurance Allowance}` and 3 hold those
plus `{Phone Allowance}`, so which set is stored depends on which line the owner pressed the
control on. Stored from a 2-member period and met by a 3-member one, it proposes re-pricing the
salary row `+$39.60` / `+$39.59` / `+$39.59` -- `$118.78` -- and leaves three Phone Allowance rows
worth `$118.62` unpaid.

**It could not do that silently, and an earlier draft of this argument said it could.**
`HandTotals.needs_consent` is true for ANY non-zero difference, the pane names both figures, and
`_reject_unaccepted_difference` refuses an unaccepted one. So it is a wrong SUGGESTION the owner
must approve, not a silent write -- which is still **R-IB**'s cause: a rule is ONE fact and the
control renders once per LINE.

## Rejected

* **R-IB's once-per-merchant surface.** Fixes the elicitation and keeps the stored set, the
  migration and the money door, for a population three other steps are removing.
* **A set-mismatch refusal.** A guard bolted to a rule whose population is being deleted.

## The cost of not building it, stated plainly

The owner keeps deciding per deposit, and the last two reconciliations already disagree: 2026-07-02
matched `Data Manager` ALONE with the whole `$2,524.62` re-priced onto it and that period's Phone
row `Cancelled`; 2026-08-13 matched `Data Manager + Phone Allowance`. A standing rule would have
made those two agree -- on whichever answer was stored first, which is why it is not the remedy.

## Left open for R18

The Phone Allowance's cadence is NOT settled. Its row in the period starting 2026-07-02 -- July's
first payday -- was `Cancelled`, and the bank cannot say whether it was paid, because `$39.54` sits
inside the noise `balance:N-391` calls unattributed. That is a pay-stub question and `R18`'s own
ruling owns it. Opened **D59** and **N-472**.
