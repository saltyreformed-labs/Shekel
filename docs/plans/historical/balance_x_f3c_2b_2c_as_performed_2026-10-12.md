> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# balance:X-f3c-2b-2c as performed (2026-10-12)

**The operator act that repaired account 10, its archived twin and Checking through the app's own
doors.** `X-f3c-2b-2c` is the account-10 repair: acts 1-10 of
`docs/audits/balance_architecture/account_10_repair_runbook.md`, which the developer performed on
production through the app's own doors (**R-HJ**) on 2026-10-12. **This file is the act's RECORD,
and the commit that adds it is the commit `X-f3c-2b-2c`'s SHIPPED row cites** -- ruling
**R-SAL40** (`docs/plans/rulings.md`): an operator act's record is a commit. Under ruling
**R-BAL132** it carries no production figure: every balance, amount, count and row id the act
moved or the checks measured stays in the handoff folder named below, and this file points at it
by path only.

## What was performed, and when

* **What:** acts 1-10 of the runbook, in its order, each through the screen the runbook names
  (**R-HJ**, **R-HL**, **R-HM**, **R-BAL3**; act 4b deleted by **R-BAL3**).
* **When:** 2026-10-12. Stamp `T=2026-10-12_195633`; the acts ran 19:5x-20:2x EDT, and the POST
  block ran at 20:24 EDT.
* **From which runbook:** the runbook at commit `f3bbc6c59`. The runbook's code check read
  **SAME CODE** at REV `3c70ed4ae`, the commit then on `main` and on production.
* **Rehearsed first, the same evening:** the runbook's REHEARSAL block, extracted verbatim with
  only its placeholders filled, ran on a copy of production dumped at the stamp. Its rehearsal
  printed `rehearsal complete` and its check `check complete`, and the acts were read from the
  sheet it wrote.

## In which mode

* **Start now, ACT BY ACT:** the developer reported each act against the sheet's own success
  signs, and the session then read out the next act from the sheet.
* **No "Add earlier paychecks" first:** production's pay calendar already reached back far enough
  for the acts, measured read-only before the dump.

## Any stop, and its answer

**None.** No stop rule fired, no save was refused, no Add was duplicated, and no remedy was
performed.

## The POST result

The runbook's POST block, run as written after act 10, dumped production, restored the dump into
a copy and compared that performed copy with the rehearsal's own files. The check run on the
performed copy ended with this line (quoted from the log):

```text
check complete
```

and the block's four comparisons, its last four lines, printed (quoted from the log):

```text
SCORE a10 AS REHEARSED
SCORE a1 AS REHEARSED
EVERY BALANCE AS REHEARSED
CHECK AS REHEARSED
```

That is: both accounts' scores against their banks, every balance the baseline reads, and the
check's whole output on the performed copy were identical to the rehearsed copy's. What production
holds after the act is therefore what the rehearsal measured, and those figures stay in the folder
below.

## Where the evidence is

`~/projects/shekel-handoffs/balance-2026-10-12/f3c2b2c/`, on the developer's machine and outside
the repository (**R-BAL132**): the two block logs, `l3_rehearsal_block.log` and
`l3_post_block.log`, and, each named for the stamp, the sheet the acts were read from and the
snapshot taken before act 1; the scores of both accounts before the act, on the rehearsal copy and
on the performed copy; the baselines and the checks of the rehearsal copy and of the performed
copy; the renders before and after the rehearsal; and the two production dumps either side of the
act.

## The entry

The step's row in `docs/plans/steps.md`, as it stood when the act was performed:

  - **X-f3c-2b-2c** Perform the account-10 repair as a rehearsed OPERATOR runbook (**R-HJ**,
    **R-HL**, **R-HM**, **R-BAL3**): both accounts' books open 2026-03-25 at their banks' own
    closes, 2026-03-26's four bank lines are RECORDED, the twin is zeroed, five transfers move onto
    the bank's days and five dividends are recorded. Act 4b is DELETED. **MOVES MONEY.** Closes
    **BAL-468**, **N-275**; carries **BAL-467**.
