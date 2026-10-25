> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Two shipped steps, 2026-08-30: `balance:X-f3c-2b-1` and `balance:X-f3c-2d`

Archived 2026-08-30 to free lines in `../README.md`, which stood at exactly 980 of its 1,000-line
cap against a 20-line headroom floor with `X-f3c-2d` owing a specification. `conventions.md` rule 5:
shrink the record of what is DONE, never the specification of what remains. This is the same remedy
`four_shipped_steps_2026-08-30.md`, `five_shipped_steps_2026-08-26.md` and
`x_f3c_2c_as_built_2026-08-30.md` were cut for, and the one the developer chose on 2026-08-29 when
asked.

**Why this step and not another.** Three shipped entries in that README still ran to several lines
each. The other two are load-bearing where they stand and were left alone:

* `X-be-3` -- `four_shipped_steps_2026-08-30.md` states two obligations as *"restated on the live
  `X-be-3` entry"*, so condensing it would drop what an archive already points AT.
* `X-au-c3` -- `x_au_c_as_built_2026-08-26.md` says in as many words that it is **NOT** there and
  stayed live because `X-au-d` and `X-au-e` name it in their `blocked by` cells. Archiving a step a
  live blocker cell names is a mistake `five_shipped_steps_2026-08-26.md` already had to undo once,
  for `X-f1`.

`X-f3c-2b-1` was the only one of the three with no archive record at all, which made it the
omission rather than the sacrifice.

**What stays LIVE in `../README.md` and must not be archived with the rest**: `account 10 now opens
2026-04-05`. `X-f3c-2b-2` is still open and restates FROM that date, so it is an input to unshipped
work rather than a record of finished work.

## The entry, reproduced verbatim

Reproduced rather than re-narrated: this step was built by another session, and a summary of
somebody else's work written by a hand that did not do it is how a record acquires claims nobody
measured.

> * [x] **X-f3c-2b-1** `2cf2ac0a` -- an opening equity is the CLOSING balance for its own day
>   (**R-HG**), so no movement may be dated on or before `opened_on`: refused at
>   `settle_day.record_settle_day` and at `reconcile_service.record_settled_days`, and made
>   UNSTORABLE both ways by three deferrable constraint triggers. 12 rows over five accounts
>   legalised; **account 10 now opens 2026-04-05**, which is what `X-f3c-2b-2` restates FROM.
>   Two balance sheets moved. Closes **N-378**.

## What a later step must obey

* The three deferrable constraint triggers over `budget.transactions`,
  `budget.transaction_entries` and `budget.account_openings` are the UNSTORABLE half of **R-HG**.
  `tests/_test_helpers.append_only_guard_lifted` reaches PAST them to grade the controls beneath;
  they are never deleted. Recorded again in `x_f3c_2c_as_built_2026-08-30.md`, which named the same
  obligation from the other side.
* `account 10 opens 2026-04-05` is the date `X-f3c-2b-2`'s door restates from. It stays on the live
  entry in `../README.md` for exactly as long as `X-f3c-2b-2` is unshipped.

# `balance:X-f3c-2d` as built -- the refusal's three arms

Ruling **balance:R-IC**. Code at `249f66a7`; migration `b8e3d5a06c94`.

**Why it exists at all.** `X-f3c-2c` shipped that morning claiming the three tables were append-only
against "the app, the suite, psql, a migration -- refused identically". A neutral agent, briefed to
break the single sentence its author would most regret being wrong, broke it twice. Both were then
reproduced by hand on a clone, each behind a control that fired first.

* **`TRUNCATE` never reaches a row trigger.** With every account still standing, `TRUNCATE
  budget.account_openings` took the table to zero and was refused by nothing. `system.audit_log` is
  written by a row trigger too, so the log was byte-identical across the statement: the ONE spelling
  that destroyed history both unrefused and UNRECORDED.
* **Delete-and-recreate defeated the predicate** with two ordinary statements in one transaction.
  `DELETE FROM budget.accounts WHERE id=20` then `INSERT` of the same id committed clean, leaving
  the account standing with its assertions destroyed.

**The root cause was one justification covering two arms.** The module docstring read *"This rule is
about the STATEMENT: an UPDATE is refused whatever else the transaction does, so there is nothing to
defer."* That sentence only ever reasons about UPDATE. The DELETE arm inherited the mechanism
unargued, and its question -- is the account gone -- is about the transaction's END state.

## What a later step must obey

* **The three arms are not interchangeable and must not be "simplified" back into one.** A combined
  `BEFORE UPDATE OR DELETE` trigger refuses every shape the suite asserts, just at the wrong moment,
  so every refusal case would still pass. What it cannot be is `DEFERRABLE`, which is why
  `test_the_delete_arm_is_deferred_and_the_others_are_not` grades `pg_trigger.tgdeferrable` rather
  than grading another refusal.
* **`append_only_guard_lifted` must disable the arms BEFORE the delete it means to permit.** A
  transaction that has already deleted from one of these tables holds pending trigger events, and
  PostgreSQL then refuses `ALTER TABLE` on it for the rest of that transaction.
* **These tables need no archive of their own, and the reason is a measured property that could
  stop being true.** All three are in `audit_infrastructure.AUDITED_TABLES` and the audit trigger
  writes `to_jsonb(OLD)` on DELETE -- every column of every destroyed row. Once TRUNCATE is refused,
  every remaining path conserves the row in full first. A table dropped from `AUDITED_TABLES` would
  leave the guard refusing exactly what it refuses today while "history is never destroyed without a
  record" quietly became false, which is why
  `test_a_disposal_conserves_every_column_in_the_audit_log` grades the conservation.
* **`ON DELETE RESTRICT` plus archive-instead-of-delete was AUTHORISED and is void.** It fails on a
  fact in the code neither party had checked: `account_service` writes an opening row AND an
  origination assertion for every account ever created, so "no hard-delete once history exists"
  removes `hard_delete_account` for every account rather than hardening anything.

## What was NOT done, and why

* **No test for the two-level `auth.users` -> `budget.accounts` cascade**, though a refutation pass
  named it a coverage gap. `app/` contains no user-deletion path at all, so it grades a route
  nobody can reach.
* **No ledger rows for either defect.** Both opened and closed inside this step, and the ledger is
  the register of what is OPEN.
