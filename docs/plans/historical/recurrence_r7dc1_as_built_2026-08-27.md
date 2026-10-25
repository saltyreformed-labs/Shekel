> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# recurrence:R7d-c-1, as built: the ROUTE opens the generate pass (2026-08-27)

**What this is.** The design record for `recurrence:R7d-c-1` and for the ruling it carries out,
**`R-R38`** -- written so that ruling's ARGUMENT has a home outside the registry row that states its
RULE. `balance:R-HD` sends an over-cap row's overflow to the as-built record of the step that owns
the ruling; R-R38 is one of two over-cap rows whose owner is both named in the row and shipped, and
this is that record. The row keeps the rule.

**It is a DESIGN record, not a change record.** What the step changed is the two commits, and they
are the authority for that: `61d81c7f` (the split) and `0a396b9a` (the door the caller census
found), shipped in PR #141 and merged to `dev` at `107add57`. The first half had already landed on
the branch as `f34320b4`, with the ruling filed at `f7b0ce16`. Read the code; this file holds why.

## R-R38's argument

**The ruling.** WHO opens the read pass a generate pass runs in: **the ROUTE does, and the DOOR
SPLITS so that it can.**

**What it is answering.** `GenerationSchedule` carries a
:class:`~app.services.balance_at.BalanceContext` and DERIVES its calendar, so the schedule a rule is
resolved against and the pass its bound is resolved in cannot be two values that disagree. Each of
the eight construction sites was already holding a `calendar_for` beside a `get_baseline_scenario`,
which is exactly the pair a pass pins. But three doors -- `extend_pay_periods`,
`regenerate_pay_periods`, `reset_pay_periods` -- performed a write and then a READ-DEPENDENT write
in ONE call, so no caller could get between them to open that pass. The first build answered this
by having `period_population` open its own, **and the developer refused that as a band-aid**: a
module opening its own pass is what a caller with no seam has to do, and the seam was the thing
missing.

**What a pass opened too early answers.** It holds the pre-write CALENDAR -- which
`GenerationSchedule.__post_init__` refuses for `for_period_ids`, because the new ids are not in it,
and silently ACCEPTS for `for_pass`, whose window IS the calendar -- and, from `R7d-c-2`, the
pre-write LOAN, which nothing catches. Measured 2026-08-27 on a production clone: a pass held across
the deletion of the Van Loan's five already-due transfers went on answering a derived payoff of
`2029-02-22` where a fresh pass said `2029-04-22`, with nothing raised. The stale date came back as a
figure. *(That measurement is also carried in `app/routes/_period_population.py`'s module docstring,
so a row-trim that removes it from `rulings.md` loses nothing.)*

**What the split buys.** The route calls the door, then `populate_new_periods`, so "the pass is
resolved after the periods exist and before the rows do" is the ORDER OF TWO CALLS rather than a
paragraph a future writer has to find.

**What the ruling does NOT claim, stated because a first draft claimed it.** The ruling's own text
read "and nothing under `app/services/` builds a pass". That is FALSE and was corrected before the
merge: five modules under `app/services/` call `BalanceContext.build` -- `calendar_service`,
`investment_dashboard_service/_context` and `/_orchestrator`, `loan_recurrence_sync` and
`tax_report_service` -- and `pay_calendar:C11` carves the fourth of those out by name. What R7d-c-1
settles is C11's fork **for the GENERATE path**: `period_population` takes a pass and builds none, so
C11's own sentence, "close the last FIVE service doors", is true again rather than six. The general
predicate is C11's end state and remains owed.

**Safe for all three doors.** The pre-split order was measured equal on 82 journal entries. It is
also order-independent by construction: every read either posting re-sync makes of
`budget.transactions` or `budget.transfers` is keyed on a set of ids taken from the POSTED ledger --
the linked ledger's nonzero per-row nets on the account side, the stale lineage transfers and stale
payment shadows on the loan side, and the loan walk's own `settled_income_shadows` -- and a freshly
generated row is `Projected` and posts nothing.

## The reset reorder, in both directions

The repopulation used to run between the rebuild and the two posting re-syncs; it runs after both
now, because the door returns before it.

**Can a re-sync see what the repopulation writes?** No, for the reason above. **A first draft argued
that by ROLL-CALL** -- "the account-anchor half reads no transaction or transfer at all, and the loan
half reads them only in two places" -- and an adversarial review MEASURED the first clause false
(`walk_account_ledger` -> `_source_net_days` -> `_transaction_source_days` queries
`budget.transactions`; a SQL capture prints it) and found two more loan-side readers. The conclusion
survived; the enumeration was what was wrong. **An argument by roll-call is wrong the moment someone
adds a reader**, which is why the property is stated in its place at every site that carried it.

**Can the repopulation see what a re-sync writes?** It can, and that direction is the STRONGER
argument for the new order, which the first draft did not make. The reset's wipe CASCADE-deletes the
loan's genesis journal entries, so the OLD order generated against an EMPTIED loan ledger and the new
one generates against the re-posted ledger. Today that is invisible: generation's reads off the
schedule are FOUR of `schedule.calendar` and TWO of `schedule.write_period_ids`, which is the whole
set, and none of them reaches a loan. **From `R7d-c-2` the pass folds
the loan to bound a payment**, and then generating before the re-sync would fold a ledger the wipe
had emptied. The new order is the one that survives that step.

Graded rather than argued: `test_the_repopulation_cannot_invalidate_either_resync` resets an owner
holding a loan AND both kinds of active template, repopulates, then re-runs both re-syncs and asserts
every genesis entry is unchanged. Its negative control was shown firing -- append one anchor event
and the fingerprint moves.

## What the split COSTS, and what pays for it

A door no longer guarantees the populate. A future caller can record periods and not fill them, and
nothing in the type system prevents it -- the shipped shape returns `list[PayPeriod]` and a caller
may ignore it. What the owner would see is paydays with no rent, no paycheck and no recurring
transfer.

Before this step **no route test graded the population at all**: the extend route test asserted the
PERIOD COUNT. So `TestEveryDoorThatCreatesAPeriodPopulatesIt` grades the ROWS through the real HTTP
request, one case per door -- extend, regenerate, reset, generate, and the rolling top-up through
`GET /grid` and `GET /dashboard`. Shown firing two ways: with `populate_new_periods` no-op'd all six
fail, and with only `/grid`'s call site mutated exactly one fails. One of the six seeds BOTH engines,
because the producer runs two loops and a caller that ran only the transaction one would otherwise
leave every case green.

Each door also gained its MIRROR -- a case asserting the door leaves the periods EMPTY -- over the
same fixture as its positive sibling, so neither can pass vacuously.

## Money

`$0.00`, through all three doors that reach a generate pass, on identically fresh clones of the
developer's database, HEAD against branch: 308 rows from a 20-period extend, 39 whole-schedule
generates, 13 carry-forward plans. Byte-identical from line 2, `md5 84205e8f...`.

The harness is `tests/manual/verify_generation_pass.py`, registered in `../verification.md`. It
dispatches on whether the tree HAS `app/routes/_period_population`, so ONE file runs both
compositions -- a harness a step's own change makes uncompilable on the HEAD side is not a harness.
Its negative control fires: no-op'ing the populate takes the extend door from 272 transactions and 36
transfers to 0 and 0, and prints 394 diff lines.

## D58, found by the census the split required

Splitting the doors meant enumerating every caller, which is how `POST /pay-periods/generate` turned
out to append pay periods and populate none. It reads as a first-time-setup door and is not:
`record_paydays`' forward-only rule refuses only a payday landing INSIDE an existing paycheck, so any
date after the owner's last is accepted, and `base.html` links it from the main nav on every screen.
Measured through the real HTTP door on a seeded owner holding one every-period `$1,200.00` expense:
3 pay periods appended, 0 template rows in them.

PRE-EXISTING, and R-R38 is what surfaced it -- the ruling makes "who populates what this door
created" a route-level question, and this route did not answer it. The developer ruled on 2026-08-27
that it rides the same PR; it closed with `0a396b9a`. `auth_service.register_user` is now the only
write path in `app/` that creates pay periods without populating, and it is correct there: no
template exists at registration, and the baseline scenario is created after that call, so a
repopulation would return 0 on `ctx.scenario is None`.

## What the two adversarial reviews corrected

Beyond the roll-call above, and recorded because each was a claim this session had already written
down:

- **"this module is that boundary for every write path that creates a pay period"** -- false; two did
  not reach it. It is a census now, naming both and their dispositions.
- **"the ROUTE is the only layer that calls `BalanceContext.build`"**, in
  `generation_schedule.py` -- the same over-claim as the ruling's, left standing in the code after
  being corrected in the ruling. Scoped to a GENERATE pass.
- The module docstring of `period_population.py` still said "the orchestrator the extend and
  regenerate operations run", which the step made false in both halves.
- A negative case in the reset suite carried an inert `transfer_id=None` filter copied from its
  positive sibling, narrowing what it could see; it now grades both engines.
- `test_one_read_pass_serves_the_whole_repopulation` still had its name's property, but the property
  had become a fact of the SIGNATURE; its docstring now states what it actually measures.
- Eleven near-identical "build the pass, then populate" spellings in `tests/` became one helper.

## What a later step must still obey

- **The ordering is the order of two calls at the route.** A door that creates pay periods records
  and returns; the pass is opened after it and before the generation. A new door owes a case in
  `TestEveryDoorThatCreatesAPeriodPopulatesIt`.
- **`for_pass` cannot refuse a stale pass** and is not expected to; `test_a_stale_pass_through_for_pass_is_NOT_refused`
  pins what actually happens, so a later step that teaches it to refuse will say so by failing.
- **`pay_calendar:C11` still owes the general layer predicate**, and `loan_recurrence_sync` is its
  own stated carve-out.
