> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Fail-loud ledger authority: delete the schedule's answer for the past

> ## SUPERSEDED FOR "WHAT TO DO NEXT" -- read `audit_loan_balance_producers.md` FIRST
>
> **2026-07-14.** C3 shipped, and a four-lens adversarial audit of this whole arc then ran against the
> running code (17 loan shapes on a cloned DB, plus negative controls applied to every guard the arc
> added). It found **15 defects**, worth up to **$299,701.35**, two of them LIVE on real production
> data and one a **guaranteed outage fired by the clock with no user action**.
>
> **The good news, and it is this plan's vindication: the `balance_at` seam is CORRECT.** Across all
> 17 shapes every seam producer agrees with every other. The defects are all in surfaces that never
> joined the seam.
>
> `docs/audits/balance_architecture/audit_loan_balance_producers.md` carries the findings register,
> the root cause, the recommended design (one fold over an event stream), the ordered arc, and **four
> BLOCKING developer rulings**. **C3b and C3c below are ABSORBED into that document's S6/S8 -- do not
> build them as scoped here.** Sections 1-9 of this plan remain the authoritative record of C1 .. C3
> AS BUILT.

**Status: C1 + C1b + C2 + C2b + C3 DONE, all on `dev`, none on `main`. FU-4 NOT fixed (it is B-11 in
the new audit, and it is LIVE). C4-C6 pending. FU-7 quantified (B-9) and still needs its ruling.**
Successor to `implementation_plan_loan_resolution_context.md`, whose adversarial review (2026-07-13)
produced the findings below. Prerequisite reading: `recurring_loan_balance_root_cause.md` (the design
rule this plan finally enforces), `implementation_plan_loan_read_switch.md` (which made the loan's
PAST ledger-authoritative), and `followup_debt_schedule_attribute_fence.md`.

---

## 0. Resuming this arc (read this first)

**Shipped on `dev`, in order:** C1 `2a88456c` -> C1b `603aea73` -> C2 `def3c8ff` -> C2b `9ea61f8a`
-> C3. Nothing is on `main` yet. Suite 7387, `pylint app/` 10.00 with zero findings.

**The four numbers that tell you the arc is behaving.** Re-check these against the dev clone before
and after ANY further commit; if one moves, the commit is wrong, not the number:

| | |
|---|---|
| Mortgage (account 3) balance today | **$177,277.97** |
| Van Loan (account 8) balance today | **$15,663.59** |
| Year-end 2026 principal paid | **+$2,505.02** / **+$4,251.65** |
| Scalar-vs-map divergences, 2 loans x 59 periods | **0** |

### Next up: NOT C3b. See `audit_loan_balance_producers.md` Section 6.

**The 2026-07-14 audit re-ordered everything.** C3b (below) is real, but it is now step **S6** of a
longer arc, and three higher-severity defects sit in front of it -- including a guaranteed outage
(B-1) and two surfaces that are wrong on real production data today (B-3 the grid, B-10/B-11 the
year-end). **Four developer rulings are BLOCKING; nothing should be built until they are answered.**

The C3b analysis below stands as the record of WHY the loan detail route is broken. The three-option
fork it ends with is MOOT: once there is one balance function (audit S1/S8), the loan page simply
calls it, and `LoanState.current_balance` is deleted because nothing is left for it to be.

### C3b -- the finding (still true; the FIX is now audit S6)

**C3 closed the fence but NOT the hole it was written to close.** C3's own charter (Section 3) says:
*"The resolver keeps its fallback; nothing can READ a balance out of it once C3 lands."* **That was
false, and the plan never noticed, because the plan never looked at the loan detail page.**

`app/routes/loan/` does not use `BalanceContext` or the seam at all. It resolves the loan itself
(`_helpers.py:189`, `resolve_loan_seeded`, on its own `date.today()`) and renders
`state.current_balance` -- **7 live reads** across `dashboard.py:259,482,511` and
`calculators.py:198,405,422,432,495`. When the genesis ledger cannot answer, `resolve_loan` falls back
to `_replay_from_anchor`, the walk this codebase's own comments call **"BLIND TO MONEY"** -- the same
fallback C2b spent a commit deleting from the seam.

**Measured on the dev clone** (Mortgage, genesis ledger deleted inside a rolled-back transaction):

| producer | result |
|---|---|
| `balance_at.balance_at()` -- /savings, year-end, property, debt-strategy | **raises `LoanLedgerNotOpenedError`** |
| `state.current_balance` -- what `/accounts/3` RENDERS | **$177,277.97** |

Five surfaces fail loud; the sixth -- the most-used loan surface in the app -- fails silent. The number
happens to be right today only because the user's true-ups re-anchor the replay. That is agreement by
luck: the exact failure signature the whole arc exists to end.

* **C3b** -- point the loan page's 7 balance reads at `balance_at.balance_at`, resolving through ONE
  `BalanceContext` so the page does not resolve the loan twice (the DRY constraint). A broken loan then
  fails loud on EVERY surface. **Developer ruling 2026-07-13: raise everywhere, no repair path.** A
  broken loan is not a legitimate state (params-create opens the ledger in the same transaction; the
  Step-4 migration backfilled every existing loan), so a loud 500 naming the repair
  (`sync_loan_postings_all_scenarios`) beats a plausible wrong number on a mortgage.
* **C3c** -- **delete `LoanState.current_balance`.** Once C3b lands it has exactly TWO readers, both
  in-cluster (`net_worth_kernel._projection_seed`, `balance_at._loan_figures._is_retired`). Deleting it
  means no module -- allowlisted or not -- can read a loan balance off a resolver bundle again: closed
  by CONSTRUCTION, not by policing, which is the standard the rest of this arc holds itself to
  (`LoanFigures`, `debt_schedule_rows`, `SecuredLoanSeries` all carry no balance for exactly this
  reason). `ResolvedLoan` carries the `ConfirmedLedgerView` it was resolved with instead, which also
  kills a redundant second ledger read the seam does today. Cost: 29 test assertions re-pointed.
  **Design detail still to settle:** what a ROWS-ONLY consumer (`debt_schedule_rows`, used by the
  year-end interest section and the trend's honest-history gate) does for a broken loan -- it needs no
  balance, so it must not be made to raise for one.

**The three-option fork C3b ended on is MOOT** (it was: widen the memo allowlist, vs grow
`LoanFigures`, vs collapse the pre-existing double-compose first). Measured 2026-07-14: the loan page
already runs **2 amortization walks, 2 ledger reads and 2 scenario lookups per load, and never touches
`BalanceContext`** -- and the two walks are the SAME composition (`resolve_loan` computes
`compute_payoff_scenarios` and discards `original_forward`, which `build_baseline_scenarios` then
recomputes from identical inputs). Once the audit's S1/S8 give the app ONE balance function, the page
calls it like everyone else and this fork does not arise. Do not re-litigate it.

Then C4, C5, C6. C5 has grown: several of its "correct the false claims" items were already fixed in
C2/C2b/C3; re-verify each before rewriting it.

**Three things you must know before touching this code again:**

1. **`app/services/net_worth_kernel.py` is at EXACTLY its 1000-line ceiling.** The next addition
   needs the loan cluster extracted to its own module (see C2's AS BUILT residue note).
2. **FU-7 is a live defect with a ruling still owed by the developer** (Section 7). The forward
   projection pays down overdue installments that were never paid -- defect 2a pointed forwards. It
   is latent on real data (both loans carry ZERO past-dated unconfirmed rows -- verified) and LIVE in
   the suite. **The obvious fix was tried and MEASURED: it breaks 26 tests and silently drops a
   payment the user has planned.** Do not attempt it without reading FU-7 in full.
3. **A DRY refactor of a PREDICATE can move money.** C3 collapsed two "this loan is done" tests into
   one and drew **$197,049.32** of phantom debt on a paid-off mortgage (see C3's AS BUILT). The
   instinct was right; the predicate was wrong. When two rules look like duplicates, prove they answer
   the SAME question before merging them -- and if they do not, make one BUILD ON the other rather than
   sit beside it.

**The process lesson this arc paid for twice, stated so it is not paid for a third time.** The C2
design was reasoned from reading the code; four throwaway probes against the RUNNING code then found
four defects in it, and an independent `code-reviewer` pass found three more that C2 itself created.
**Probe before you design, and negative-control every new guard** -- one of this arc's controls did
not fire and was a bad control, which is only knowable by running it.

---

**Revision, 2026-07-13 (developer ruling).** A loan the user knows is coming -- a mortgage closing
next month -- must be SUPPORTED, not forbidden. Probing that state found the app reports **$200,000
owed on a mortgage that has not closed**, at every pay period, for four months. The plan's original
"fork the seam on not-yet-originated" answer treated the symptom; the defect is that a loan's
ORIGINATION is a balance event nothing models. **Section 3a** is the measurement and the replacement
design, and it splits the old C2 into C2 (model the origination) and C2b (the fail-loud). The net
effect on the seam is a rule REMOVED, not added: with the origination modelled, the fail-loud raise
needs no exception.

---

## 1. The one-sentence problem

A loan's balance for a past date can still be answered by TWO different producers that disagree, and
the pylint fence that is supposed to make that impossible reports 10.00/10 while a route renders a
loan balance straight out of the resolution context.

---

## 2. What is actually broken (plain language, with the numbers)

### 2a. The phantom-paydown fix was applied to one producer and not its twin

The loan-resolution-context arc found that when the genesis ledger cannot answer for a loan, the
scalar `amortizing_balance_at` fell back to walking the loan's WHOLE amortization schedule -- so
projected rows that were never actually paid still reduced the reported balance. A $240,000 loan
originated 18 months ago and never paid read as though 17 installments had been made. The arc fixed
that scalar (`net_worth_kernel.py:437-443`) by filtering the walk to CONFIRMED rows only.

It did not touch the per-period sibling. `_build_amortizing_balance_map` (`net_worth_kernel.py:873-877`)
still calls `compute_loan_period_balance_map` with the FULL schedule, and
`balance_from_schedule_at_date` (`account_projection.py:197-201`) reads every row regardless of
`is_confirmed`.

Measured (a $240,000 loan, 6%, 360 months, originated 548 days ago, never paid, ledger not opened):

| read path | producer | balance today |
|---|---|---|
| ledger open (what production does) | scalar | $240,000.00 |
| ledger open (what production does) | per-period map | $240,000.00 |
| **no ledger** (what every loan test does) | **scalar** | **$240,000.00** |
| **no ledger** (what every loan test does) | **per-period map** | **$236,544.21** |

A $3,455.79 divergence between two producers the arc's own docstrings call siblings that "split on
the one boundary the loan architecture turns on."

Driven end to end through `compute_dashboard_data`, that is one page contradicting itself:

```
AssertionError: the /savings loan tile and the net-worth trend's own 'today'
point disagree: tile=240000.00 trend=236544.21
```

The year-end summary has the identical split internally: its debt-progress section reads the scalar
(`year_end_summary_service/_net_worth.py:206`), its net-worth section reads the map (`:115`).

**Production money impact today: none.** Verified against the dev clone: both real loans
(Mortgage id=3, Van Loan id=8) carry `loan_opening` postings, so production takes the ledger path
where the two agree. This is latent on real data and LIVE in the test suite.

### 2b. The fence is green and does not hold

The arc fenced the three resolver FUNCTIONS (`resolve_account_loan` / `resolve_loan_seeded` /
`resolve_loan_bundle`) and then introduced `BalanceContext.loan()` and `.loan_state()`
(`resolution_context.py:138,165`) -- public METHODS that hand any caller the whole `ResolvedLoan`,
including `state.current_balance`, which IS a balance-at-today.

The checker cannot see them. `visit_call` matches names and neither `loan` nor `loan_state` is in a
producer set; `visit_functiondef` returns early unless the parent is a Module
(`balance_seam.py:471`), so methods are never classified at all.

`app/routes/accounts/detail.py:803-808` -- the Property detail route, which is NOT in
`_LOAN_RESOLVER_MODULES` -- does exactly this, reads `state.current_balance` at `:664` and `:679`,
and `property_equity_chart.py:214,218` renders the on-screen secured-debt line from
`row.remaining_balance`. Proven silent: a probe consumer added to `app/routes/companion.py` reading
`ctx.loan_state(account).current_balance` rates 10.00/10.

`LoanFigures` was built to close this "by CONSTRUCTION rather than by policing"
(`balance_at/_loan_figures.py:20`). It closed the front door and the same commit opened a back one.

### 2c. W9909's fail-closed check does not cover the surface this arc fenced

`balance_seam.py:213-229` argues that a name-keyed deny list "fails OPEN," that this shipped twice,
and that "two identical misses is a design defect in the FENCE." Then the same commit added
`_LOAN_RESOLVER_PRODUCERS` without adding its defining modules to `_FENCED_MODULE_RULINGS`. A test
at `tools/pylint/tests/test_shekel_checkers.py:1300` pins the ruling set to the engine cluster plus
`loan_posting_service`; `app.services.loan_resolution` and `app.services.resolution_context` are
absent.

Proven: a new public `loan_balance_right_now()` added to `loan_resolution.py` (returning
`resolved.state.current_balance`) rates 10.00/10. `contractual_schedule_from_origination` already
sits there public and unclassified.

### 2d. `debt_schedule_rows`'s stated premise is false

`net_worth_kernel.py:220-223` says handing consumers rows "is what makes the fence real," because
"the rows carry no balance." `AmortizationRow` carries `remaining_balance`
(`amortization_engine/_projection.py:220`). Every row is a balance. Removing `current_balance` from
the bundle was still worth doing; it did not close the hole by construction, and the docstring says
it did.

### 2e. The test fixtures do not model production, which is why none of this was caught

`create_loan_account` (`tests/_test_helpers.py:436-512`), used at 117 call sites across 25 files,
never opens the ledger. The real route does, in the same transaction as the LoanParams insert
(`app/routes/loan/params.py:125`). `insert_trueup_event` writes a `LoanAnchorEvent` and never
reconciles it into postings; the production writer always does
(`anchor_service.py:374-390` calls `sync_all_scenarios_or_duplicate`).

So the entire loan suite runs on the no-ledger fallback -- a path production never takes -- and does
not run on the genesis ledger path, which production always takes. The purpose-built cross-page
oracle should have caught 2a (it genuinely compares the savings tile against the net-worth trend),
but its fixture (`tests/conftest.py:1531-1537`) inserts a true-up dated TODAY, which re-anchors the
schedule to today-forward and leaves no past-dated unconfirmed rows to phantom-pay. It is the one
loan shape in which the defect cannot appear.

**I dry-ran the fix.** Patching `create_loan_account` to sync postings like production:

```
15 failed, 7352 passed
```

All 15 reduce to two causes:

* **Six paid-off / true-up tests + the two cross-page oracles** fail because the fixture writes anchor
  events without reconciling them, so once the ledger is authoritative the true-up is invisible and
  every surface reports the original principal. The oracle failure is the clearest statement of the
  problem in the whole codebase: `savings=240000.00, loan_detail=240000.00, year_end=240000.00,
  net_worth_trend=240000.00, schedule_table=240000.00` -- five surfaces in perfect agreement on a
  number the fixture's own true-up says is $200,000.
* **Five `test_*_unconfigured_loan_returns_none` reader tests plus one posting-count test** fail
  because their premise ("this loan has no ledger") is what the fixture change removes.

---

## 3. The design rule this plan enforces

> **The past belongs to the ledger. The future belongs to the projection. Neither may answer the
> other's question, and there is no third source. A configured loan whose baseline ledger has no
> opening posting is BROKEN, and a broken loan fails loud rather than producing a number.**

And its corollary, which the arc's own review forced into the open (Section 3a):

> **A loan's ORIGINATION is a balance event like any other. The ledger owns it once it has happened;
> the projection owns it until it does. A loan owes NOTHING before it originates.**

The codebase already states half of this in two places and then breaks it in two others:

* `confirmed_loan_balance_at` RAISES on a future date (`loan_posting_service/_reader.py:200`).
* `loan_owed_at_dates` RAISES on a past-or-today date (`net_worth_kernel.py:314-320`), with the
  explicit reason that an overdue unconfirmed payment "would report the balance net of a payment that
  was never made -- silently UNDERSTATING the debt."
* And then `amortizing_balance_at` and `_build_amortizing_balance_map` each fall back to the schedule
  for the PAST when the ledger is silent -- committing precisely the understatement
  `loan_owed_at_dates` refuses to commit, in two different ways.

**Why this is the right fix and not a narrower one.** The narrow fix (make the map filter to confirmed
rows, matching the scalar) makes today's numbers agree and leaves two hand-synchronised boundary rules
in place, which is the band-aid this project has spent six months paying for. Deleting the fallback
removes the boundary rule entirely, and with it:

* the possibility of the two producers ever diverging again (structural, not policed);
* `compute_loan_period_balance_map`'s and its `current_balance` fallback argument -- the exact argument
  the W9905 `shekel-original-principal-as-balance` checker exists to police;
* the reason the test fixtures could get away with not opening the ledger.

**Why fail-loud is safe.** A configured loan without an opening posting is not a legitimate state. The
opening is written in the same transaction as the LoanParams (`app/routes/loan/params.py:125`), the
Step-4 migration backfilled every pre-existing loan, and `pay_period_admin.reset_pay_periods` resyncs.
The project already fails loud on the sibling invariant: a loan account with no linked ledger account
raises `PostingError` rather than degrading, and `confirmed_loan_view`'s own docstring calls that "the
project's fail-loud rule" (`loan_payment_service.py:500-509`). This plan adds the second invariant to
that same list.

**Where the fail-loud guard lives, and where it must NOT.** It goes in the READ seam
(`net_worth_kernel`'s loan producers). It must NOT go in the resolver: `contractual_schedule_from_origination`
(`loan_resolution.py:287-298`) deliberately passes `confirmed_view=None` to build the property chart's
pure contractual back-projection, so the resolver's None path is a legitimate, used API. The resolver
keeps its fallback; nothing can READ a balance out of it once C3 lands.

---

## 3a. The origination event -- the corollary, and why C2 cannot ship without it

**Developer ruling, 2026-07-13: a loan the user knows is coming (a mortgage closing next month) is a
state the app must SUPPORT, not forbid.** The plan's earlier "C2 DESIGN REQUIREMENT" proposed to fork
the seam on `origination_date > today` and answer `$0.00`. That fork is wrong -- not because the
answer is wrong, but because it treats a symptom. Measured, then fixed properly here.

### 3a.1 What is actually broken (measured, 2026-07-13)

A loan's balance moves on exactly three kinds of event: it comes INTO EXISTENCE at origination
(+principal), payments reduce it, true-ups reset it. Every one of those is modelled -- except the
first, for a loan whose origination has not arrived.

* The genesis walk REFUSES to post an anchor dated after its as-of
  (`_walk._merge_anchor_and_payment_events:356-359`), correctly: it has not happened. So a
  not-yet-originated loan has no OPENING posting, and `confirmed_loan_balance_at` returns `None`.
* The forward projection has no concept of a balance INCREASE. Its whole model is
  "seed, less the payments scheduled by *target*" (`account_projection.forward_balance_at_date`).
* So the origination gets smuggled in through a third door: `select_latest_anchor`
  (`loan_resolver/_periods.py:345-348`) takes `max()` over the anchor facts with **no `as_of`
  filter**, so a loan resolved TODAY seeds its balance from an anchor dated in the FUTURE.

Probed against the real code (mortgage $200,000 @ 5%, 360 months, originating 2026-07-01, read on the
suite's frozen 2026-03-20):

| valuation date | app reports | truth |
|---|---|---|
| 2026-02-01 (past) | **$200,000.00** | $0.00 -- the loan does not exist |
| today (2026-03-20) | **$200,000.00** | $0.00 |
| 2026-05-01 (future, pre-origination) | **$200,000.00** | $0.00 |
| 2026-07-15 (originated, before the first payment) | $200,000.00 | $200,000.00 (correct) |
| 2026-08-15 (after the first payment) | $199,759.69 | $199,759.69 (correct) |

Every pay period from January to May reports $200,000 owed on a mortgage that has not closed. Net
worth is understated by $200,000 across that whole stretch. `LoanState.current_balance` is the single
wrong number all of it flows from, and it is read by **six surfaces outside the seam** --
`routes/loan/dashboard.py:259,482,511` (the loan detail page's "current principal"),
`routes/loan/calculators.py:198,405,432,495` (payoff / refinance), `services/home_equity_service.py`,
`services/property_equity_chart.py:496`, and `routes/accounts/detail.py:664,679` -- so this is not a
seam-local defect and a seam-local guard cannot fix it.

This is the exact mirror image of defect 2a. There, unpaid schedule rows paid DOWN a debt nobody had
paid. Here, an unarrived origination CREATES a debt nobody has taken on. Same root cause: a producer
answering a question with an event that has not happened.

### 3a.2 Why the "fork the seam on not-yet-originated" answer is wrong

It is a THIRD boundary rule, hand-synchronised with the other two, and it does not even close the
hole. `if as_of < origination: return 0` fixes the past and the pre-origination future. It leaves the
window between origination and the FIRST PAYMENT reporting whatever the seed says -- and once the
resolver is corrected the seed is `$0.00`, so July 2026 would report the borrower owing nothing on a
mortgage they closed three weeks earlier. Closing THAT needs a fourth rule. This is precisely the
"two hand-synchronised boundary rules" pattern Section 3 exists to delete, and adding a third and a
fourth is not a fix.

### 3a.3 The fix: model the origination, do not special-case it

One fact, stated once, applied by every producer that values a loan:

> **A loan owes nothing before `origination_date`; from that date it owes its OPENING balance, less
> whatever has since been paid.**

The clean part -- and the reason this is a deduplication rather than a new rule -- is that the ledger
ALREADY implements exactly this. Its OPENING posting IS the origination event, and once the date
arrives the walk posts it and every reader gets the step-up for free. The projection simply has to
carry the same fact for as long as the ledger cannot. **The same anchor the ledger will post as the
OPENING is the one the projection seeds from until it does.** One fact, two owners, split on the one
boundary the whole architecture already turns on.

Concretely, three changes, each stating the SAME fact in the one place that question is asked:

1. **The resolver stops reporting a balance for a loan that does not exist yet.**
   `resolve_loan` derives `current_balance` from `_replay_from_anchor`, which seeds from the latest
   anchor BY DATE regardless of `as_of`. Add the domain guard: `as_of < origination_date` ->
   `current_balance = 0.00`. This is the resolver applying the rule the genesis walk already applies
   to the same anchor list -- two copies of one rule become one. It fixes all six out-of-seam
   surfaces at once, and `LoanState.current_balance` finally means what its name says at every
   `as_of`.

   The guard goes on the BALANCE only, NOT on `_replay_from_anchor` itself: the composer
   (`compute_payoff_scenarios`) shares that replay for the schedule's STARTING STATE, and filtering
   the anchor out there would collapse the loan's 360-row forward schedule to nothing. The balance
   and the schedule's seed are two different questions, and this is where they finally separate.

2. **The forward projection gains the origination as an event.** Both forward producers
   (`forward_balance_at_date`, `compute_forward_loan_period_balance_map`) take the loan's
   `owed_from` date and return `0.00` for any target before it, via ONE shared helper so the scalar
   and the map cannot drift (the same discipline that failure 2a is about). For an already-originated
   loan `origination <= ctx.as_of < target` always holds, so the guard never fires and the numbers are
   byte-identical -- this cannot move a real loan.

3. **The projection's seed is named for what it is.** `DebtSchedule.current_balance` is renamed
   `projection_seed` and is:
   * `state.current_balance` for a loan that HAS originated (the ledger-confirmed present), or
   * the loan's OPENING ANCHOR BALANCE for one that has not (what it will owe the day it originates).

   Sourcing the second from the loan's opening anchor fact -- `synthesize_origination_anchor`, the
   very fact the ledger will post -- and never from the raw `params.original_principal` column is
   deliberate: it keeps ONE definition of "the balance this loan opens at", and it keeps W9905
   (`shekel-original-principal-as-balance`) meaningful. W9905 exists because an EXISTING loan's
   balance was reported as its origination amount. A loan that has not originated legitimately owes
   its opening balance, and that is a different statement -- but it is close enough in shape that it
   gets a named source, a comment, and a test rather than a bare column read.

**What this buys.** The fail-loud raise stays UNCONDITIONAL -- there is no `if not yet originated`
branch beside the ledger check. A not-yet-originated loan never reaches it, because it has no
confirmed past at all: the ledger has no domain, and the projection owns its whole timeline. That is
not an exception to the design rule; it is the design rule, applied to a loan whose entire life is
still in the future.

---

## 4. The standard of proof for changing a test

**Developer ruling, 2026-07-13:** *"The tests should test for the current state of the code. You may
change the tests when you can prove the tests are incorrect or do not match the code."*

That is the authority this plan operates under, and it is narrower than it sounds. It is permission to
correct a test that is WRONG. It is not permission to make a red test green. So every test this plan
touches carries its proof, in the commit message and in the test's own docstring:

1. **What it asserts**, quoted.
2. **What code path it actually exercises** -- named, and shown to be a path production does not take
   (that is the defect in almost every case here: the fixture never opened the ledger).
3. **Why the current assertion is wrong**, or why it pins behaviour this plan deliberately deletes.
4. **The recomputed expected value, by hand**, with the arithmetic shown -- for any assertion whose
   NUMBER changes.

**The hard stop stays.** If a financial assertion's number has to move and I cannot prove from the
code and the data that the old number was wrong, I stop and bring it to you with the arithmetic rather
than editing it. A red test I cannot explain is a finding, not a chore.

Against that standard, the 15 dry-run failures fall out as follows.

**F1. Six paid-off / true-up tests: fixture repair, numbers expected to hold.**
`TestPaidOffFlag::test_paid_off_true_when_confirmed_covers_balance`,
`TestDebtSummary::{test_debt_summary_all_paid_off, test_debt_summary_excludes_paid_off}`,
`TestDebtPrincipalProgress::{test_fraction_one_when_all_loans_paid_off, test_fraction_monotonic_one_paid_one_partial}`,
and `TestDTI::test_dti_zero_debt` mark a loan paid off by inserting a $0.00 true-up EVENT that is never
posted. **Proof they do not match the code:** production's true-up writer
(`anchor_service.py:374-390`) writes the event AND calls `sync_all_scenarios_or_duplicate`; the fixture
(`_test_helpers.py::insert_trueup_event`) writes the event only. Under the ledger an unposted true-up
does not exist, so `is_paid_off` correctly reads False. Repairing the FIXTURE (C1) makes the loan
genuinely paid off, and I expect every asserted number to hold unchanged. If one moves, rule (4) above
applies.

**F2. `TestUnpaidScheduleRowsNeverReduceTheDebt` is deleted, not repaired.** **Proof:** it pins the
behaviour of the no-ledger schedule fallback, and its own body asserts
`confirmed_loan_balance_at(...) is None` as its PREMISE. C2b deletes that fallback, so the test asserts
the behaviour of code that no longer exists. Replaced by `TestBrokenLoanFailsLoud`, which asserts the
raise, and by `TestScalarAndMapAgree`, which is the invariant the deleted test was a one-sided proxy
for.

**F3. Five reader tests keep their contract, and state their premise.** The
`test_*_unconfigured_loan_returns_none` cases test the READER's `None` contract, which is unchanged
(the reader still returns `None`; the SEAM is what raises). They currently get an unconfigured loan by
ACCIDENT, because the fixture never opened the ledger. They get `create_loan_account(...,
open_ledger=False)` so the premise is stated. No assertion changes.

**F4. Audit the rest of the loan suite for fallback-only assertions.** The 15 failures are the tests
that BREAK. There may be tests that still PASS while asserting a number that is only correct on the
no-ledger path. C1 includes a sweep of the 117 `create_loan_account` call sites for assertions whose
expected value depends on the money-blind anchor replay rather than the ledger. Anything found is
reported with its proof before it is touched.

---

## 5. The commits

Each commit is independently green (full suite + `pylint app/` 10.00 with the full `--fail-on` set)
and independently revertable.

### C1 -- `test(loan): make the loan fixtures write through production's reconcile path` -- **DONE**

**Test-only. No `app/` change (verified: `git diff app/` empty). This is the prerequisite for
everything else: it is what puts the suite on the production code path.**

**AS BUILT (differs from the plan as written -- three developer rulings, 2026-07-13):**

* `tests/_test_helpers.py::_sync_loan_ledger` (new, private) -- the shared write-through step,
  calling `loan_posting_service.sync_loan_postings_all_scenarios`, flushing and leaving the commit to
  the caller (production's contract). It calls the PLAIN sync, deliberately NOT production's
  `sync_all_scenarios_or_duplicate`: that wrapper translates a user's double-click into idempotent
  success **by rolling back**, which in a fixture would silently discard the test's setup. A
  duplicate anchor written by a fixture is a fixture bug and must fail loud.
* `create_loan_account` -- **always** opens the ledger; **no `open_ledger` flag**. A boolean opt-out
  would leave a casual escape hatch back onto the fallback path this arc exists to delete. The sync
  runs after the `RateHistory` insert, because the genesis walk resolves rate periods and the
  resolver raises on an empty rate feed.
* `insert_trueup_event` / `insert_tracking_start_event` -- reconcile into postings in the same
  transaction, as `anchor_service._append_loan_anchor_and_sync` does for both.
* `clear_loan_ledger(loan_account_id)` (new, public) -- the **breaker**: the exact inverse of the
  sync, deleting the `loan_opening` / `loan_trueup` / `loan_payment` entries on the loan's own ledger
  accounts (raw SQL; the ORM blocks deletes on the append-only ledger), leaving the Step-2/3 CASH
  entries untouched. It is the ONE way to build a ledger-less loan, so the broken state is always
  explicit at the call site -- and C2's `TestBrokenLoanFailsLoud` reuses it rather than inventing a
  second mechanism. (Implementation note: it resolves the entry ids to a Python list FIRST, because
  the predicate reaches each entry THROUGH its postings; deleting the postings first empties the
  predicate and strands the entry headers. That bug was caught by `TestUserScopedResync`, which counts
  entries rather than postings.)
* The five reader tests + `TestUserScopedResync::test_resync_posts_only_the_users_genesis` +
  `TestUnpaidScheduleRowsNeverReduceTheDebt` call `clear_loan_ledger` and state the premise in their
  docstrings (F3). The last of those is still C2's to delete.
* `tests/conftest.py::cross_page_loan_unpaid_ctx` (new) + `TestLoanCrossPageEquality::
  test_unpaid_loan_owes_its_opening_on_every_surface` -- the never-paid shape 2a lives in, asserting
  non-vacuously (the schedule really does carry unpaid past-dated rows) that all five surfaces AND
  both producers report the full $240,000 opening.

**Measured, not predicted.** The plan's dry-run patched only `create_loan_account` and reported 15
failures. Patching all three writers gives **10 failed, 7357 passed**: the six paid-off / true-up
tests (F1) resolve on their own with every asserted number unchanged, exactly as predicted, because
the true-up now reconciles.

**Three failures the plan did NOT predict** (all one root cause; this is the F4 class, found rather
than theorised): `TestBalanceMapLoan::test_pre_first_payment_uses_current_balance`,
`TestMultiLoanIsolation::test_two_loans_keep_distinct_current_balances`, and the cross-page oracle's
`test_all_surfaces_equal` each asserted **$200,000 at a period BEFORE the true-up**, where the ledger
says **$240,000**. Proven against the dev clone that the ledger's answer is production's: the
Mortgage's past periods step down at each recorded event (178,103.41 -> 177,829.83 -> 177,554.69 ->
177,277.97) rather than carrying today's balance flat backward. The three assertions pinned the
no-ledger fallback. Their guard (PR #44 / `aba0242`) was against `compute_loan_period_balance_map`
being **seeded with the original principal** -- a defect inside the very function C2b deletes.

**Developer ruling:** re-point, do not re-value. Each test now asserts the trued-up balance at a
period at/after the anchor (**number unchanged, $200,000**) and additionally pins the ledger's
pre-anchor answer ($240,000). The PR #44 fence moves to where it actually applies -- the loan's
CURRENT balance -- and the tests become a positive statement of "the past belongs to the ledger"
instead of a contradiction of it.

**Verified:** full suite **7368 passed**; `pylint app/` 10.00/10 with the full `--fail-on` set; zero
new lint findings on the touched test files; the dev clone still reads Mortgage **$177,277.97** and
Van Loan **$15,663.59** to the cent (C1 changes no `app/` code, so this holds by construction).

**Deliberately NOT in C1:** `insert_origination_event` writes a `LoanAnchorEvent` with source
`origination` that no reader reads -- `load_loan_anchor_facts` queries only `user_trueup` and
`tracking_start` and synthesises the opening from the immutable params
(`loan_loaders.py:181-204`). Removing it is dead-code cleanup, and it belongs in C5 with the other
documentation corrections, not in the commit that changes what the suite exercises.

### C1 adversarial review -- what it changed

An adversarial review of C1 (before commit) found two real holes in the work and three false claims in
its own docstrings. All are fixed, each with a negative control proving the guard now bites.

1. **`TestMultiLoanIsolation` had stopped testing isolation.** Once the fixtures open the ledger, every
   BEGUN period is answered by `confirmed_loan_balance_map(account.id, ...)` -- a per-account read that
   is correct even if `build_maps` hands loan A the loan B `DebtSchedule`. Only the FORWARD tail
   consumes that bundle. Both of C1's first assertions were on begun periods, so a crossed schedule
   would have passed. A future-period assertion is added; negative control (swapping the two schedules
   in `_kind_correct.build_maps`) makes loan A report **$47,385.23** -- loan B's balance -- and the test
   now fails. Same trap applies to any future ledger-era test: **assert the forward tail, or you are not
   testing the bundle at all.**

2. **A fixture was writing a ledger shape production forbids.**
   `test_pre_trueup_payment_is_split_from_origination` set a true-up dated 2026-04-15 while the
   `test_services` autouse clock is frozen at 2026-03-20. The sync bounds its walk at `date.today()`
   and DROPS a later anchor, so the loan got its opening and no true-up: it LOOKED ledger-backed and was
   not -- the exact divergence class this arc deletes. Production rejects a future `anchor_date`
   outright (`schemas/validation/loans.py::validate_not_future`). `_sync_loan_ledger` now **fails loud**
   when any anchor post-dates the sync's as-of (reading the clock from the sync's own module, since
   `freeze_today` patches `date` per-module). Sweeping the whole suite with that guard armed found
   exactly ONE offender, now re-dated to 2026-03-15 with every asserted number unchanged.

3. Three docstrings asserted things that were not true (a delete-ordering "safety property" that was
   backwards -- the balanced-entry trigger is `AFTER INSERT OR UPDATE`, never DELETE; "before the caller
   commits", when `_append_loan_anchor_and_sync` commits itself; and an over-claim that the helpers reach
   the whole loan suite, when ~20 modules hand-roll loans). Corrected, since this project treats a false
   docstring as a defect.

### C1 adversarial review -- one finding that PREDATES this arc

**The cross-page oracle's "PR #44 boundary lock" can no longer catch PR #44's bug.** Found while
adversarially reviewing C1; not caused by it. The read switch retired the lock silently.

PR #44 / `aba0242` passed `original_principal` where the schedule map's `current_balance` SEED
belongs. That seed is read in exactly one situation --
`account_projection.balance_from_schedule_at_date`, "used when *target* precedes the first scheduled
payment" (`:190-191`). Before the read switch, EVERY period came from the schedule walk, so a
pre-payment period was a live probe of the seed. Now the confirmed ledger owns every BEGUN period,
and the oracle's fixture (true-up dated today) puts the first payment inside the very next period, so
there is no FUTURE pre-first-payment period either. The seed is therefore invisible to every
assertion the oracle makes.

**Proven, not argued:** reintroducing the defect (seeding `compute_forward_loan_period_balance_map`
with `original_principal`) leaves the oracle GREEN. The schedule ROWS carry the balances.

The W9905 `shekel-original-principal-as-balance` checker -- a build failure -- is now the only fence
on that argument. That is a decent fence, and C2 makes it structural by deleting
`compute_loan_period_balance_map` outright, which removes the argument. Recorded so nobody reads the
oracle's old docstring and believes the test is guarding something it is not; the test's docstring now
says so itself.

### C1b -- `fix(balance): the ledger's domain is a fact; stop reading $0 outside it` -- **DONE**

Began as the test-only fixture prerequisite below. Migrating the ~30 hand-rolled loan builders onto
production's path immediately turned the suite red in ways that were NOT fixture artifacts -- they were
two real production bugs, both since confirmed on the dev clone and both fixed here. The suite was
green only because it never ran the code production runs. That is the whole thesis of this arc,
demonstrated.

**BUG 1 -- an old anchor's date was a lie.** A journal entry carries an ``entry_date`` (its true civil
date) and a NOT NULL ``pay_period_id``. When an anchor predates every pay period the user has,
``_anchors._resolve_anchor_pay_period`` is forced to file it under the EARLIEST period -- which can only
ever push it LATER than it happened. The readers bounded by period start and therefore believed it. A
loan originated 2025-01-01 whose owner's periods begin 2026-01-02 read as owing **nothing for the whole
of 2025**.

*Fix:* new `loan_posting_service/_asof.py`. ``effective_date()`` bounds an ANCHOR by
``LEAST(entry_date, period.start)`` and CASH by ``period.start``. The split follows the NATURE of the
fact: an anchor ASSERTS a date and has no budget dimension; cash IS budgeted to a period, and the
"an early-settled payment must not show until its period begins" rule is deliberate and must survive.
Both readers take the key from that one place, so ``map[P] == balance_at(P.start)`` still holds by
construction. Verified against the clone: of its 66 anchor entries the ONLY two the ``LEAST`` moves are
superseded openings that net to exactly $0.00, so **no production number moves.**

**BUG 2 -- $0.00 outside the ledger's domain was being spent as money.** For a mid-life import (opening
is a ``tracking_start`` years after origination) the ledger genuinely has no record before that date,
and ``confirmed_loan_balance_at`` returns ``$0.00`` -- meaning "no record", NOT "no debt". The year-end
debt-progress read its opening balance at Dec-31-of-the-prior-year, subtracted a real year-end balance
from that fabricated zero, and reported the borrower ADDING debt they had been paying down. **Live on
real data:**

| | before | after |
|---|---|---|
| Mortgage | `jan1=0.00 dec31=175,870.41` **`paid=-175,870.41`** | `jan1=178,375.43` **`paid=+2,505.02`** |
| Van Loan | `jan1=0.00 dec31=12,883.20` **`paid=-12,883.20`** | `jan1=17,134.85` **`paid=+4,251.65`** |

*Fix:* new `loan_posting_service/_domain.py`. ``confirmed_loan_ledger_domain()`` returns the ledger's
``(start_date, opening_balance)``; ``_compute_debt_progress`` clamps its window to it and reports
``tracked_from`` so the surface can say "since 2026-03-31" instead of implying a calendar year. The
principle, and the one to hold on to: **a producer with no evidence must say so, and a caller must never
spend a non-answer as money.** ``opening_balance`` is the OPENING-kind posting sum, deliberately not
``balance_at(start_date)`` -- a payment sharing the opening's pay period would otherwise be netted into
the opening balance and hide $272.02 of principal paid inside the window.

**Test repairs, each with its proof (Section 4).** Four tests demanded the RIGHT number and the code was
wrong (Bug 2); they now pass with their original expected values untouched. Three pinned the phantom
paydown as CORRECT -- most starkly
``test_current_period_point_diverges_from_hero_for_amortizing_loan``, which asserted that the /savings
hero and the net-worth trend MUST disagree about the same loan on the same day. That disagreement is the
symptom this arc opened with. It is now
``test_current_period_point_agrees_with_hero_for_amortizing_loan``. One test
(``test_hard_delete_account_with_params``) had a VACUOUS assertion: it checked
``b"permanently deleted" in resp.data``, which the ARCHIVE flash also satisfies ("cannot be permanently
deleted"). It could not have failed either way.

**Verified:** full suite 7370 passed; `pylint app/` 10.00/10 with the full `--fail-on` set; checker unit
tests 146 passed; the dev clone still reads Mortgage **$177,277.97** and Van Loan **$15,663.59** to the
cent.

### 2a is now PROVEN, with a number -- and the map fallback has NO coverage until C2

C1b's adversarial review asked for the map-level twin of `TestUnpaidScheduleRowsNeverReduceTheDebt`
(the scalar's guard). Written, it FAILS. On a $240,000 loan originated 548 days ago, never paid, with
its ledger removed:

```
period 2026-01-19 reports 235771.76, below the $240,000 owed;
unpaid schedule rows are paying the debt down
```

That is section 2a, demonstrated rather than argued. `compute_loan_period_balance_map` walks the FULL
schedule, so ~17 purely PROJECTED installments pay down principal the borrower never paid.

**It cannot be fixed narrowly.** The scalar sibling was fixed (`7b7c909b`) by filtering its walk to
CONFIRMED rows, which is safe because it answers ONE date and RAISES for the future. This map answers
the past AND the future in a single walk, so the same filter would flatten the forward projection to
the last confirmed balance. Fixing it properly means splicing confirmed + forward -- which is precisely
what the ledger path already does. **C2b deletes the branch; that is the fix.** The test is therefore not
committed (a red test, or a band-aid, would both be worse), and the branch carries no coverage until
then.

**Reachability today: none.** After C1b every configured loan opens its ledger at params-create, and
production runs a single baseline scenario, so `confirmed_loan_balance_map` never returns `None` for a
real loan. The defect is latent, not live. C2b must delete `compute_loan_period_balance_map` outright --
not merely stop calling it.  **(Done in C2b `9ea61f8a`: deleted, and W9905 extended to the two forward
producers that inherited its seed argument.)**

### C2 DESIGN REQUIREMENT -- a not-yet-originated loan is not a broken loan

**SUPERSEDED 2026-07-13 by Section 3a. Kept for the record; do not build the fork it proposes.**

Surfaced by C1b's exit gate (below). ``origination_date`` carries NO not-future validator (unlike a
true-up's ``anchor_date``), so a loan configured before it originates -- a mortgage closing next month --
is a legitimate, reachable production state. Its origination anchor post-dates the sync's as-of, so it
posts NO opening, and C2's fail-loud would RAISE on it: `/savings` and the year-end page would 500 for a
user who did nothing wrong.

This section originally proposed to FORK the seam on ``origination_date > today`` and answer $0.00.
The developer ruled (2026-07-13) that a known-upcoming loan must be SUPPORTED, and the adversarial
probe then showed the fork treats a symptom: the real defect is that the ORIGINATION is a balance
event nothing models, the app already reports **$200,000 owed on a mortgage that has not closed** at
every period for four months, and the fork closes neither that nor the origination-to-first-payment
window. See **Section 3a** for the measurement and the replacement design.

`test_year_end_summary_service.py::TestMortgageInterest::test_mortgage_interest_partial_year` is the live
test that forces the question; it is the ONE remaining failure under the C2 simulation.

### C1b EXIT GATE -- the C2 simulation (measured)

Making `_build_amortizing_balance_map` raise instead of falling back, and running the full suite:

| | failures |
|---|---|
| before C1b | **45** across 8 files |
| after the factory migration | 4 |
| after the FU-4 fix + test repairs | **1** |

The last one is the not-yet-originated loan above -- a C2 design requirement, not a fixture defect. C2
therefore lands against a suite that is otherwise already green under its own semantics.

### C2b PREREQUISITE -- the off-factory loan builders (measured, 2026-07-13) -- **DONE in C1b**

**C1 does NOT put the whole suite on the production path, and the plan is wrong to imply it does.**
Nineteen test files construct `LoanParams` directly instead of going through `create_loan_account`,
so C1's helper fix never reaches them and their loans still carry no ledger.

Simulating C2 (making `_build_amortizing_balance_map` raise instead of falling back) breaks **45
tests across 8 files** -- and that is the MAP producer alone; the scalar's raise adds more:

| file | failures |
|---|---|
| `test_services/test_savings_dashboard_service.py` | 17 |
| `test_services/test_year_end_summary_service.py` | 11 |
| `test_routes/test_savings.py` | 8 |
| `test_routes/test_accounts_dashboard.py` | 3 |
| `test_services/test_net_worth_kernel.py` | 2 |
| `test_services/test_balance_at.py` | 2 |
| `test_routes/test_debt_strategy.py` | 1 |
| `test_integration/test_loan_resolver_single_source.py` | 1 |

C2b therefore needs its own fixture step FIRST: route those local builders through the shared factory
(they are a DRY violation regardless -- `test_balance_at.py::_make_mortgage` is a verbatim
re-implementation of `create_loan_account` differing only in account type), or open their ledgers.
Not doing this first means C2 lands as a 45-test red wall with no way to tell a real regression from
a fixture that never modelled production.

### C2 -- `fix(loan): a loan's origination is a balance event; it owes nothing before it` -- **DONE (`def3c8ff`)**

**The corollary commit (Section 3a). Lands BEFORE C3 because C3's fail-loud is only unconditional
once this exists.** Independently green and independently revertable; it moves no number for either
real loan (both originated years ago, so every guard it adds is structurally unreachable for them --
proven by the regression baseline, not asserted).

**AS BUILT -- everything below shipped, PLUS the following, which the plan did not anticipate.**

**An independent `code-reviewer` pass found THREE regressions C2 CREATED, all outside the seam, all
the same class as R3: a consumer reading a truthful `$0.00` balance as "this debt is repaid."**
Zeroing `current_balance` is correct; every one of these was wrong to spend it. R3 was the plan's
only instance, and it was not the only one.

1. **The dashboard debt track reported 66.67% of principal REPAID by a borrower who had repaid
   nothing.** `_compute_principal_paid_fraction` (`savings_dashboard_service/_metrics.py`) is an
   ALL-LOANS-EVER basis: every loan puts its `original_principal` in the denominator, and a loan
   owing `$0.00` therefore counts as fully repaid. An unclosed $200,000 mortgage beside a never-paid
   $100,000 auto loan read `200000/300000`. It also broke the marker's one design invariant --
   MONOTONICITY -- since the fraction would COLLAPSE from 66.67% to 0% on closing day.
2. **The property equity chart DROPPED a mortgage closing in 26 days** and drew ten years of
   debt-free equity. Its `current_balance <= 0` test means "retired", and an unborrowed loan
   satisfies it. **The route (`_secured_loan_series`) and the chart producer each carried their own
   copy of that test** -- two copies of one rule, and BOTH went wrong the moment the seam started
   answering honestly.
3. **The year-end panel reported -$198,049.28 of principal PAID.** C1b's ledger-domain clamp skips a
   loan whose record begins after the year, but an unborrowed loan has NO ledger at all, so
   `domain is None` **fails OPEN** -- the same inverted figure C1b spent a commit deleting, arriving
   through a door the clamp does not cover.

**The fix, and the reason it is one fix and not three: `LoanFigures.is_originated`.** All three
consumers needed a fact the seam KNEW and did not publish -- *"does this loan exist yet?"* A zero
balance means "owes nothing"; it does NOT mean "has no debt ahead of it", and only this flag separates
them. It has exactly ONE definition (`_loan_figures._is_originated`), which `_is_paid_off` also uses,
and a single negative control (make it return `True`) turns all three regression tests red. That is
the test that it is one fact.

Consequent changes the plan did not list:

* `savings_dashboard_service/_types._LoanAccountResult` now **COMPOSES** `LoanFigures` instead of
  re-declaring its fields. It copied them, and the copy went stale the instant the seam grew
  `is_originated` -- a bundle that must be hand-synchronised with the seam it mirrors is the seam's
  fence with a hole in it. (Caught as a real `R0801` duplicate-code finding, not a style nit.)
* `property_equity_chart.SecuredLoanSeries` gains `is_originated` and an `is_retired` property --
  THE single definition of "drop this loan from the chart", so the route and the producer cannot
  answer it two ways again.
* `routes/accounts/detail._secured_loan_series(loan_accounts, balance_ctx)` -- signature changed. It
  now reads `is_originated` FROM THE SEAM rather than re-deriving `origination_date <= today`
  itself. The first cut derived it locally; the negative control then failed to bite on the chart
  test, which is how the second derivation was caught. **One fact, one producer.**
* Four docstrings the review proved FALSE were corrected -- including this commit's own safety
  argument. The origination guard is NOT "structurally unreachable" for an originated loan:
  `compute_forward_loan_period_balance_map` is handed EVERY period, so a begun period that ended
  before the loan was taken out DOES probe `target < owed_from`. What makes it harmless is the
  SPLICE discarding those values, not unreachability. A reason a future reader would trust must be
  the true one.
* A stale `dict[int, DebtSchedule]` type hint on `_compute_debt_progress` (the caller passes ROWS)
  and `_projection_seed`'s missing type hint.

**Verified:** full suite **7383 passed**; `pylint app/` 10.00/10 with the full `--fail-on` set and
ZERO findings of any kind; checker unit tests 148. Both guards proven load-bearing by negative
control, with a clean partition: 2 tests pin the resolver guard, 4 pin the seam guard, 3 pin the
consumers -- none vacuous. Dev clone unmoved (Mortgage $177,277.97, Van Loan $15,663.59; year-end
+$2,505.02 / +$4,251.65).

**Known residue:** `app/services/net_worth_kernel.py` now sits at EXACTLY its 1000-line ceiling. The
next commit that adds to it must extract the loan cluster (`DebtSchedule`, `generate_debt_schedules`,
`_projection_seed`, `loan_owed_at_dates`, `amortizing_balance_at`, `_build_amortizing_balance_map`,
`_loan_ledger_not_opened`) into its own module -- the same module-size move the investment sub-chain
already made. That touches the W9906 `_ENGINE_CLUSTER_MODULES` list.

* `loan_resolver/_state.resolve_loan` -- the domain guard on the derived balance:
  `as_of < loan_params.origination_date` -> `current_balance = Decimal("0.00")`. Docstring states the
  rule and why it is NOT applied inside `_replay_from_anchor` (the composer shares that replay for
  the schedule's starting state; filtering the anchor there would collapse the forward schedule).
* `account_projection` -- the forward producers learn the origination:
  * new private `_projected_owed_at(forward_rows, target, seed, owed_from)` -- `0.00` before
    *owed_from*, else `balance_from_schedule_at_date`. THE one place the rule is expressed.
  * `forward_balance_at_date(schedule, target, seed, owed_from)` and
    `compute_forward_loan_period_balance_map(schedule, periods, seed, owed_from)` both route through
    it. Scalar and map cannot drift -- the structural lesson of 2a, applied pre-emptively.
* `net_worth_kernel.DebtSchedule` -- `current_balance` renamed `projection_seed`, plus a new
  `owed_from: date`. `generate_debt_schedules` fills them from the pass's ONE resolution:
  `projection_seed = state.current_balance` when the loan has originated by `ctx.as_of`, else the
  loan's OPENING ANCHOR balance (`synthesize_origination_anchor` -- the same fact the ledger will
  post as the OPENING, never the raw `params.original_principal` column; see 3a.3).
* `loan_owed_at_dates` -- passes `owed_from` through to `forward_balance_at_date`. The liability band
  then reports `$0` for an upcoming loan's pre-origination samples instead of its full principal.
* **[R3]** `balance_at/_loan_figures._is_paid_off` -- a loan that has not originated is NOT paid off.
  **C2 CREATES this bug and must therefore fix it in the same commit.** `_is_paid_off` is
  `(any confirmed payment) AND (current_balance <= 0)`. The confirmed-payment guard was assumed to
  make an unoriginated loan safe; it does not. A settled transfer INTO a not-yet-originated loan is
  constructible through the ordinary `transfer_service` (measured), giving one confirmed payment.
  Today it reads `False` only because `current_balance` is the wrong `$200,000` C2 is about to zero.
  Zero it, and an unclosed mortgage renders RETIRED: no debt on the Horizon, dropped from the debt
  card's total. Guard on origination.
* Tests -- `TestLoanNotYetOriginated` (new): the full trajectory of an upcoming mortgage, asserted on
  the numbers in the 3a.1 table, across the scalar, the per-period map, `loan_owed_at_dates`, and
  `LoanState.current_balance`. Specifically the FOUR regions, because three of them are wrong today:
  before origination ($0), the origination-to-first-payment window (full opening balance), after the
  first payment (amortizing), and `current_balance` today ($0). Plus `is_paid_off` is False with a
  confirmed payment present (R3's negative control). Plus a negative control on the whole commit: an
  already-originated loan's map and scalar are byte-identical before and after it.

### C2b -- `fix(balance): the ledger owns a loan's past; a broken loan fails loud` -- **DONE (`9ea61f8a`)**

**The correctness commit.**

**AS BUILT -- everything below shipped, with these differences from the plan.**

* **W9905 was STRENGTHENED, not merely pruned.** The plan said "drop the dead name, keep the live
  one." Doing only that would have SHRUNK the fence: the two FORWARD producers
  (`forward_balance_at_date`, `compute_forward_loan_period_balance_map`) inherited
  `compute_loan_period_balance_map`'s seed argument when it was deleted, and therefore inherited its
  hazard. They JOIN the checker. `_LOAN_BALANCE_MAP_FUNCS` is now those two plus
  `balance_from_schedule_at_date`.
* **`_loan_ledger_not_opened(account, ctx)`** -- the raise is built in ONE shared helper, so the
  scalar and the map cannot report the same corruption two different ways, and the REPAIR is named
  in one place.
* **Test disposition, each with its proof (Section 4):**
  * `TestUnpaidScheduleRowsNeverReduceTheDebt` -- **DELETED.** It pinned the no-ledger schedule
    fallback, and its own body asserted `confirmed_loan_balance_at(...) is None` as its PREMISE. It
    asserted the behaviour of code that no longer exists. Superseded by `TestBrokenLoanFailsLoud`.
  * `TestLoanProjectedBalanceDispatcher` (4 cases) -- **RE-POINTED, not deleted.** Every semantic
    they pin (period-end keying, the pre-first-payment seed, the empty schedule, the no-phantom-jump
    guarantee) moved intact to the FORWARD producer, which is what the surfaces actually call. Their
    synthetic rows gained `is_confirmed=False` -- which is what they always WERE (projected rows) --
    and an `owed_from` predating every period. **Not one expected number changed.**
  * `TestAmortizingReadSwitch::test_builder_splices_ledger_past_and_projection_future` -- its
    comparison producer was the deleted function; re-pointed at the forward producer the builder
    actually uses.
* **`TestScalarAndMapAgree` was negative-controlled, and the FIRST control did not bite.** Adding
  confirmed rows back into the forward walk changes nothing, because a forward row always dominates
  a confirmed one for a future target. Two controls that DO bite: re-key the forward map on
  `period.start_date` (fires: `map=235511.70` vs `scalar=235250.34`), and seed the map one cent off
  the scalar (fires: `map=0.01` vs `scalar=0.00`). **A guard whose negative control does not fire is
  not a guard.** Recorded because the first one looked convincing.
* **Verified on REAL data, not just fixtures:** the new invariant holds across **118 real
  (loan x period) checks on the dev clone with ZERO divergences**, and the baseline is unmoved
  (Mortgage $177,277.97, Van Loan $15,663.59; year-end +$2,505.02 / +$4,251.65).
* **Verified:** full suite **7386 passed**; `pylint app/` 10.00/10, zero findings; checker unit tests
  148.

* New `LoanLedgerNotOpenedError(PostingError)` in `app/services/posting_reads.py` (beside
  `PostingError`, which already models exactly this family of invariant violation). Its message names
  the account, the scenario, and the repair (`sync_loan_postings_all_scenarios`).
* **[R2]** `net_worth_kernel.amortizing_balance_at` -- **the debt schedule must be resolved BEFORE
  the ledger is read.** Today the ledger read comes first and its `None` is harmlessly ignored. An
  AMORTIZING account with NO `LoanParams` -- a Mortgage account whose loan terms were never filled in
  -- returns `None` from `confirmed_loan_balance_at` (measured), and a naive "`None` -> raise" would
  turn that two-click user state into a 500 on `/savings`. So: resolve the schedule; `None` -> the
  cash producer; only THEN read the ledger, and raise on its `None`. Delete the confirmed-rows walk
  added by `7b7c909b` (lines 437-443) and its 20-line comment block.
* **[R1]** `net_worth_kernel._build_amortizing_balance_map` -- when `confirmed_loan_balance_map`
  returns `None`, ask the loan ONE question: **has it originated by `ctx.as_of`?**
  * **No** -- then the ledger's answer is genuinely `$0.00` at every BEGUN period, because every begun
    period starts on or before `ctx.as_of`, which is before origination. Say so explicitly (an
    all-zeros confirmed map) and splice as normal.
  * **Yes** -- then the loan is BROKEN. RAISE.

  Delete the `compute_loan_period_balance_map` fallback (lines 873-877).

  **The earlier draft of this plan said "the projection owns the whole timeline; return the forward
  map for every period." That was wrong, and measuring it is what found this.** The forward map is
  period-END keyed; the confirmed map is period-START keyed. For a loan originating INSIDE the current
  pay period (origination 2026-03-25, today 2026-03-20, period 2026-03-13..2026-03-26) the forward map
  reports the full `$200,000` for EVERY begun period -- while the hero reports `$0`. One page
  contradicting itself about one loan on one day: the arc's own opening symptom, recreated by the fix.
  The splice is now used for EVERY loan, with no exception anywhere.

  **Type the zeros.** The kernel now emits two different `$0.00`s and they must never be confused: this
  one means *"the loan does not exist yet"* and is TRUE; the ledger's pre-opening zero means *"I have no
  record"* and is FALSE (it is C1b's Bug 2, and it still lives at FU-4). The fact that tells them apart
  is the origination date C2 introduces. Say this in the code, not just here.
* `account_projection.compute_loan_period_balance_map` -- now dead. DELETE it. Confirm with a
  repo-wide grep that `_build_amortizing_balance_map` was its only caller (verified 2026-07-13: it
  is, in `app/`).
* `tools/pylint/shekel_checkers/` -- W9905 (`shekel-original-principal-as-balance`) currently guards
  `compute_loan_period_balance_map` AND `balance_from_schedule_at_date`. Drop the dead name, keep the
  live one. Update the checker's tests.
* Tests:
  * `TestBrokenLoanFailsLoud` (new, in `test_net_worth_kernel.py`) -- a configured loan with its
    opening posting deleted (via C1's `clear_loan_ledger`) raises `LoanLedgerNotOpenedError` from
    BOTH the scalar and the map. Replaces `TestUnpaidScheduleRowsNeverReduceTheDebt` (F2).
    Plus **[R2]**: an AMORTIZING account with no `LoanParams` does NOT raise -- it routes to cash.
  * **[R4]** `TestScalarAndMapAgree` (new, the structural invariant) -- for a matrix of loan shapes
    (never-paid, mid-life-imported with a tracking start, trued-up, overpaid, short-paid, paid-off,
    **and not-yet-originated**, **and originating inside the current period**), assert for EVERY
    period:

    ```
    balance_map(loan, ctx, periods)[p.id] == balance_at(
        loan, ctx, p.start_date if p.start_date <= ctx.as_of else p.end_date)
    ```

    **The plan previously specified `balance_at(p.end_date)` for every period. That invariant is FALSE
    on a correct loan and would have been RED from the moment it was written** -- measured: a normal
    never-paid loan reports `map[current period] = $50,000.00` against
    `balance_at(current period.end_date) = $39,634.37`. The map is period-START keyed for BEGUN periods
    (it reads the ledger) and period-END keyed for FUTURE ones (it reads the projection), so the probe
    date must follow the same split. Building the guard as written would have meant "fixing" correct
    code to make a wrong test green. (The $39,634.37 itself is a real defect -- see FU-7.)
  * The cross-page oracle gains the `cross_page_loan_unpaid_ctx` fixture from C1.
  * A not-yet-originated loan does NOT raise (the regression guard on 3a's ruling).

### C3 -- `fix(balance): fence the context's loan handle; a route cannot hold a LoanState` -- **DONE**

**Closes 2b and 2c.**

**AS BUILT (differs from the plan as written -- read this before C3b).**

**All three of the plan's fence claims were PROBE-VERIFIED against the running gate before any code
was written, and all three held**: a route reading `ctx.loan_state(account).current_balance`, a new
public `loan_balance_right_now()` in `loan_resolution`, and a new public method on `BalanceContext`
each rated **10.00/10**. After C3 each is a build failure (negative-controlled, all three fire).

* **`BalanceContext.loan_state` was DELETED, not renamed.** It had **zero callers** (verified across
  `app/`, `tests/`, `tools/`, `scripts/`, templates). Its entire purpose was to hand a consumer the
  `LoanState` this fence exists to keep out of consumer hands. Renaming it would have preserved a
  hole nobody was using.
* `BalanceContext.loan` -> `resolved_loan`. The rename is load-bearing, not cosmetic:
  `_called_name_in` matches a call's ATTRIBUTE name (`_common.py:31`), so a distinctive name is what
  makes it catchable, where the generic `loan` would collide with unrelated code.
* **A FOURTH fenced call surface, not an addition to the resolver's**: `_CONTEXT_LOAN_PRODUCERS` with
  its own, deliberately NARROWER allowlist (`balance_at` / `net_worth_kernel` /
  `resolution_context`). `loan_payment_service`, `loan_recurrence_sync`, `_transfer_loan_posting` and
  `app.routes.loan` may resolve a loan directly -- they need the rich primitives -- but none of them
  may reach into a READ PASS's memo. Pinned as genuinely different sets by
  `test_flags_context_loan_handle_from_a_loan_resolver_module`.
* **W9909 now classifies METHODS** (`_is_public_export_surface`). The old `visit_functiondef` returned
  early unless the parent was a Module, so no method was ever classified -- which is precisely what let
  `BalanceContext.loan` become a hole. The walk is up the whole ANCESTOR CHAIN, not a fixed two-level
  test: a two-level test drops a method of a nested class, and a bare `parent is Module` test drops a
  function defined under a module-level `if`/`try`. Neither shape exists in a fenced module today --
  and "it cannot happen today" is the exact reasoning that produced holes 2b and 2c, so both are
  tested.
* **The registry-vs-reality test shared the checker's blind spot.**
  `test_classification_sets_match_the_real_fenced_modules` walked only `tree.body`, so it could not
  see a method either -- a guard that shares the bug it guards against. It now calls the checker's OWN
  `_is_public_export_surface` rather than re-implementing the rule (a second copy that can drift).
* `_FENCED_MODULE_RULINGS` gains `app.services.loan_resolution` and `app.services.resolution_context`
  (`_LOAN_RESOLVER_DEFINING_MODULES`), closing 2c.
* `LoanFigures` gains `is_arm` (so `/debt-strategy` stops holding a `ResolvedLoan` for one boolean).
* The property equity chart's `SecuredLoanSeries` assembly MOVED from `app/routes/accounts/detail.py`
  into the seam as `balance_at.secured_loan_series(property_account, ctx)`. The DTO moved with it
  (`balance_at/_secured_debt.py`); `property_equity_chart` imports it FROM the seam (consumer -> seam,
  the correct direction -- verified acyclic). The DTO now carries `account_id` + rows + `is_retired`
  and **no balance at all**. The route holds no resolver bundle.

**The adversarial review found ONE CRITICAL REGRESSION C3 CREATED, and it is the lesson of this
commit.** The first cut collapsed the chart's `is_retired` (`is_originated AND balance <= 0`) onto the
seam's `is_paid_off` -- which ALSO requires a confirmed payment -- reasoning that two predicates for
"this loan is done" was the duplication the arc exists to delete. **The DRY instinct was right and the
predicate was wrong.** A mortgage paid off by a LUMP SUM recorded as a balance true-up has no payment
rows, so `is_paid_off` is `False`; the chart therefore charted it, and because a zero-balance loan
resolves to an EMPTY schedule, the back-projection clip (FU-8) then admitted its entire 360-row
contractual walk. Measured, on the running code:

| | |
|---|---|
| equity hero | `total_debt $0.00`, `equity $400,000.00` |
| equity chart, SAME PAGE | `debt[today] $197,049.32`, `equity[today] $202,950.68` |

Two producers, one page, **$197,049.32** apart -- the exact failure class this arc exists to end,
reintroduced by the commit that claims to end it.

**The fix keeps ONE definition and makes it the right one.** `LoanFigures.is_retired` (originated +
ledger balance <= 0) is THE rule for "does this loan have a debt line"; `is_paid_off` is now **built on
it** (`is_retired AND has-a-confirmed-payment`) rather than sitting beside it, so the two cannot drift.
The confirmed-payment guard is a BADGING rule -- do not congratulate a degenerate `$0`-anchor loan --
and is documented as such. Both guards are negative-controlled with a clean partition: charting on
`is_paid_off` reds the new lump-sum test; deleting the origination guard from `_is_retired` reds the
chart test AND the earnest-deposit test. Neither is vacuous.

**Four more false docstring claims the review proved** (this project treats a false docstring as a
defect), all corrected: `build_property_equity_chart`'s own contract still said the series carries a
`current_balance` and that "a loan with a zero balance today is paid off and is dropped here" -- the
defect, stated as the contract; the new `Raises:` on `resolve_home_equity` attributed the no-baseline
raise to `loan_figures`, which carries no such guard (it comes from `balance_at.balance_at`, and only
when the Property secures a configured loan); the seam's `__init__` said "Three shapes" over five and
listed a dependency set that no longer matched; and `_secured_debt` claimed to be "the last route-held
`ResolvedLoan` in the app", which `app/routes/loan/` flatly contradicts (that is C3b).

**Verified:** full suite **7387 passed** (7386 + the new regression guard); `pylint app/` 10.00/10 with
the full `--fail-on` set, zero findings; `pylint tools/pylint/shekel_checkers/` 10.00/10; checker unit
tests **158**. Dev clone unmoved to the cent (Mortgage **$177,277.97**, Van Loan **$15,663.59**,
year-end **+$2,505.02** / **+$4,251.65**, 118 scalar-vs-map checks / 0 divergences). Live-rendered
against the clone: the real property page's chart and equity hero both report **$177,277.97**.

---

**The plan as originally written (kept for the record):**

* `resolution_context.py` -- rename `BalanceContext.loan` to `resolved_loan` and `.loan_state` to
  `resolved_loan_state`. The rename is load-bearing, not cosmetic: `_called_name_in` matches the
  ATTRIBUTE name of a call (`_common.py:31`), so a distinctive name is what makes
  `ctx.resolved_loan_state(...)` catchable, where the generic `loan` would collide with unrelated
  code and force a false-positive-ridden fence.
* `balance_seam.py` -- add both names to `_LOAN_RESOLVER_PRODUCERS`. Allowlist
  (`_LOAN_RESOLVER_MODULES`) gains only `app.services.balance_at` and `app.services.net_worth_kernel`
  (the seam and the cluster that legitimately compose the memo). Routes and dashboards get nothing.
* `app/routes/debt_strategy.py:148` -- `ctx.loan(account).params` is used for exactly one thing,
  `params.is_arm`. Add `is_arm: bool` to `LoanFigures` (rich projection detail, not a balance) and
  drop the context call.
* `app/services/home_equity_service.py:126` -- `ctx.loan(loan) is None` is a configured-loan test.
  Replace with `balance_at.loan_figures(loan, ctx) is None`, which is already a seam entry and already
  returns `None` for a non-loan.
* `app/routes/accounts/detail.py` -- the real work. Move `_secured_loan_series` OUT of the route and
  INTO the seam as `balance_at.secured_loan_series(property_account, ctx) -> list[SecuredLoanSeries]`,
  sourcing each loan's `current_balance` from `balance_at.balance_at` rather than
  `LoanState.current_balance`. The chart's debt line IS a balance-at-T series, so it belongs to the
  seam by the seam's own charter. The route then holds no `LoanState` at all and
  `property_equity_chart` stays pure. (This also sets up follow-up FU-2 below.)
* `balance_seam.py` W9909 -- extend the fail-closed check to the surface this arc fenced:
  * Add `app.services.loan_resolution` and `app.services.resolution_context` to
    `_FENCED_MODULE_RULINGS`, with `contractual_schedule_from_origination` classified as a
    non-producer (pure contractual rows from immutable params; it reads no ledger and answers no
    balance-at-T) and a comment saying why.
  * Extend `visit_functiondef` to classify public METHODS of public classes in a fenced module, not
    just module-level functions. The current early return at `:471` is exactly what let
    `BalanceContext.loan` become a hole. Update `_ENGINE_CLUSTER_MODULES` /
    `_LOAN_LEDGER_DEFINING_MODULES` and the set-equality test at
    `tools/pylint/tests/test_shekel_checkers.py:1300`.
* Tests: checker cases for (a) a route calling `ctx.resolved_loan_state(...)` is flagged, (b) the seam
  calling it is not, (c) a new unclassified public function in `loan_resolution` is flagged, (d) a new
  unclassified public METHOD on `BalanceContext` is flagged.

### C4 -- `fix(balance): a read pass cannot be pinned to a future as-of`

* `BalanceContext.__post_init__` raises `ValueError` when `as_of > date.today()`. Rationale, in the
  docstring: `confirmed_loan_view` returns `None` for any as-of after today
  (`loan_payment_service.py:529`), so a future-pinned pass silently resolves every loan from the
  anchor replay -- the producer this cluster's own docstrings call "BLIND TO MONEY." Verified against
  both real dev loans that a +1-day as-of takes that path today; it returns the same number only
  because the user's true-ups have re-anchored the replay, which is agreement by luck.
* The correct way to value a loan in the future is unchanged and stays: `ctx.as_of = today`,
  `balance_at.balance_at(account, ctx, future_date)`.
* Test: `BalanceContext.build(user_id, as_of=tomorrow)` raises; `as_of=yesterday` does not (the Taxes
  tab's display-tz today can legitimately be yesterday in UTC).

### C5 -- `docs(balance): correct the claims the review found false`

No behaviour change. Every item here is a statement in the code that a future reader would rely on and
that is not true.

**Status re-verified 2026-07-13 after C2/C2b shipped.** Those commits corrected the false docstrings
they touched, so two items below are already DONE. **Re-check each remaining one against the code
before rewriting it** -- do not assume this list is current; that is exactly the failure mode it
exists to fix.

* ~~`net_worth_kernel.debt_schedule_rows` -- "the rows carry no balance" is false;
  `AmortizationRow.remaining_balance` exists.~~ **DONE in C2**: it now says the rows carry no SEED
  (true -- `projection_seed` is the loaded field), and names the real fence.
* ~~`balance_at/_loan_figures.py` -- `_is_paid_off(resolved)` has no type annotation.~~ **DONE in
  C2**: `_is_paid_off(resolved: ResolvedLoan, as_of: date)`.
* **OPEN.** `savings_dashboard_service/_projections.py:42-43` -- "the loan tile reads the resolver
  directly ... never the seam" (and the inline comment near `_compute_loan_account`'s `current_bal`).
  Verified still present and still false: `_compute_loan_account` reads `balance_at.loan_figures` and
  `balance_at.balance_at`.
* **OPEN.** `home_equity_service.resolve_home_equity:118` -- "With no baseline scenario each secured
  loan still resolves ... exactly as before" is false since `7b7c909b`; `balance_at.balance_at` calls
  `_require_scenario`, which raises. Verified still present. State the raise.
* **OPEN.** `TestOneResolutionPerLoanPerReadPass` (`test_savings_dashboard_service.py:4577-4579`) -- the spy sits
  on `resolution_context.resolve_loan_bundle`, so it counts resolutions routed THROUGH the context,
  not "every resolution anywhere in the pass regardless of which module asked."
* **OPEN.** `tests/_test_helpers.py::insert_origination_event` (`:260`, called at `:758`; verified
  still present) -- delete it and its call site. Its docstring claims "the resolver raises ValueError
  on an empty anchor-event list," which the read switch retired; `load_loan_anchor_facts` never reads
  an `origination`-source row. Verify by removal, not by assertion.
* `implementation_plan_loan_resolution_context.md` -- append a "Corrections (2026-07-13 review)"
  section pointing here. Do not rewrite its history; record what it got wrong.

### C6 -- `refactor(balance): one clock per read pass` (may be deferred to its own arc)

The arc's "one clock" claim holds for the loan resolver only. The same read pass still reads
`date.today()` independently at `savings_dashboard_service/_metrics.py:232` (the committed expense
floor) and `:416` (escrow monthly as-of), `_goals.py:227`, and `debt_strategy.py:308` (the strategy
start date, against a context built separately at `:138`). Thread `ctx.as_of` into all four. The
midnight-crossing argument `liability_owed_at_dates` makes for its own sample axis
(`balance_at/_liability.py:126-137`) applies verbatim.

This is correctness-adjacent rather than correctness-critical, and it touches escrow. If C1-C5 are
green and you want to ship, C6 can land separately without weakening anything above.

---

## 6. Verification

**Per commit:** targeted tests, then `pylint app/ --fail-on=<the full CLAUDE.md set>`, then the full
suite. Show real terminal output, not summaries.

**The regression baseline that must not move.** Recorded from the dev clone before any change, and
CONFIRMED CORRECT by the developer (2026-07-13):

| loan | account id | current principal balance | status |
|---|---|---|---|
| Mortgage | 3 | **$177,277.97** | correct; history correct |
| Van Loan | 8 | **$15,663.59** | correct; **history is NOT** (see FU-1) |

Both current balances are correctness oracles. After C2, C2b and C3, `/savings`, `/accounts/<id>` (loan
detail), the property detail page, `/debt-strategy`, and the analytics Taxes tab must each still report
**$177,277.97** and **$15,663.59** to the cent. Any movement in either is a defect in this plan, not a
correction, and the commit stops.

**The Van Loan's HISTORY is not an oracle, and this plan must not change it either.** Its past-period
balances are known-wrong (FU-1). C2b does not touch them: with the ledger open, begun periods ALREADY
read the confirmed ledger through
`splice_confirmed_and_projected_loan_balances`, and C2 only deletes the branch taken when the ledger is
absent. So the Van Loan's historical balances should come out of this plan byte-identical -- still
wrong, and fixed separately. If they move, C2b changed something it should not have.

That asymmetry is the point of the fail-loud design and worth stating plainly: **the Van Loan proves a
correct current balance can sit on top of a wrong history.** Its current balance is right only because
the 2026-06-23 true-up asserts it. A design that lets the schedule answer for the past is exactly how a
wrong history hides behind a right present.

**Live verification (not just tests).** Against the dev clone, render each of the five surfaces above
and read the number off the page, per CLAUDE.md rule 9. A green suite on a fixture-driven path is
precisely what failed us here.

**The new structural guards** (these are the deliverable, as much as the fix):

1. `TestScalarAndMapAgree` -- scalar == map at every period, across EIGHT loan shapes, probing each
   period at the date its own producer is keyed by (see C2b [R4] -- the naive period-END form is FALSE
   on a correct loan).
2. `TestBrokenLoanFailsLoud` -- a loan with no opening posting raises from both producers; an
   AMORTIZING account with no `LoanParams` does NOT.
3. `TestLoanNotYetOriginated` -- the four regions of an upcoming loan's trajectory (C2), on every
   producer. Three of the four are wrong today.
4. `cross_page_loan_unpaid_ctx` -- the cross-page oracle now covers a loan with past-dated unpaid rows.
5. The C3 checker cases -- a route holding a `LoanState` is a build failure.

**Every one of guards 1-3 exists because a PROBE found a bug the plan's prose had missed.** The
adversarial review of C2 (2026-07-13) ran four throwaway probes against the real code and every one of
them turned up a defect -- two in the C2 design itself (R1, R3), one in C2b (R2), one in the guard
above (R4). The plan had been reasoned about, not executed. That is the same failure the whole arc is
about: C1's thesis is that the suite was green because it never ran the path production runs. **The
probes are therefore promoted to permanent tests rather than discarded.**

**The C2 negative control.** C2 changes the resolver, which every loan touches. Its safety claim is
that the guard is structurally unreachable for an originated loan (`origination <= ctx.as_of <
target`, always). That is asserted, not assumed: an already-originated loan's scalar, map, and
`current_balance` must be byte-identical across the commit, and the dev clone must still read
Mortgage **$177,277.97** and Van Loan **$15,663.59** to the cent.

---

## 7. Out of scope (follow-ups)

### FU-1 -- The Van Loan's HISTORY is wrong (its current balance is right); investigate separately

**Developer ruling, 2026-07-13: the Van Loan's current principal of $15,663.59 is CORRECT. Its history
is not.** This plan does not attempt a correction, but the review turned up the artifacts, recorded
here so the follow-up does not start from zero.

**The anchor events** (`budget.loan_anchor_events`, dev clone):

| account | source | anchor_date | anchor_balance |
|---|---|---|---|
| Mortgage | origination | 2018-12-01 | 202,000.00 |
| Mortgage | tracking_start | 2026-03-31 | 178,375.43 |
| Mortgage | user_trueup | 2026-05-22 | 177,829.83 |
| Van Loan | origination | 2023-02-14 | 32,402.45 |
| **Van Loan** | **tracking_start** | **2026-04-11** | **17,020.47** |
| **Van Loan** | **tracking_start** | **2026-04-11** | **17,134.85** |
| Van Loan | user_trueup | 2026-05-11 | 16,575.68 |
| **Van Loan** | **user_trueup** | **2026-05-22** | **17,020.47** |
| **Van Loan** | **user_trueup** | **2026-05-22** | **16,123.31** |
| Van Loan | user_trueup | 2026-06-23 | 15,663.59 |

The Mortgage has a clean single-row chain. The Van Loan carries two `tracking_start` rows on one date
($114.38 apart) and two `user_trueup` rows on one date ($897.16 apart). One of the same-day true-ups
($17,020.47) is a verbatim repeat of the earlier tracking-start value, which smells like a
mis-entered assertion rather than a code fault.

**What is NOT the bug (checked, so the follow-up does not chase it):**

* The unique index is not failing. `uq_loan_anchor_events_acct_date_bal_day` keys on
  `(account_id, anchor_date, anchor_balance, created_at::date)` -- it blocks an IDENTICAL same-day
  re-assertion, and two same-day rows with DIFFERENT balances are permitted by design (that is how an
  operator corrects a same-day mistake in an append-only table).
* The duplicates are not being double-counted into the balance. `_opening_anchor_fact`
  (`loan_loaders.py:108-131`) resolves multiple tracking-starts by latest `created_at`, and the ledger
  reconciles to that target with a correcting delta rather than stacking: the two `loan_opening` legs
  on 2026-04-11 are `-17,020.47` and `-114.38`, netting to `-17,134.85`, which is exactly the winning
  tracking-start. That is the correcting-entry pattern working as designed.

**The actual artifact to chase.** The confirmed ledger balance over time:

| date | confirmed balance | note |
|---|---|---|
| 2026-04-11 | 17,134.85 | opening (later tracking-start wins) |
| 2026-04-23 | 16,683.84 | payment: 451.01 principal |
| 2026-05-11 | 16,575.68 | true-up |
| **2026-05-21** | **16,123.31** | **drops 452.37 with NO payment in the window** |
| 2026-05-22 | 16,123.31 | true-up date |
| 2026-06-23 | 15,663.59 | true-up; correct today |

Van Loan payments are dated 04-23, 05-23, 06-23. The balance falls by $452.37 between 2026-05-11 and
2026-05-21 with no payment in that interval. Two candidates worth measuring first: the 2026-05-22
true-up corrections are being attributed to a PAY PERIOD that starts before their entry date (the
reader is period-assigned, `_reader.py:239-244`), or the pair of same-day true-ups is generating
correcting legs that land on the wrong side of the 05-22 boundary. Either way the current balance still
lands right because the 2026-06-23 true-up pins it -- which is precisely how a wrong history hides
behind a right present.

### FU-4 -- A period BEFORE a loan's opening renders $0 owed -- **NOT FIXED. STILL LIVE.**

**C1b fixed ONE consumer (the year-end debt-progress section), not the producer.** The per-period map
still emits the false `$0.00`, and the 2026-07-14 audit found a SECOND consumer spending it, in the
same file C1b edited: `_compute_net_worth` (`year_end_summary_service/_net_worth.py:66-70`), 180 lines
above the one C1b clamped, has no clamp at all. Live on the clone: the year-end panel reports
**"net worth grew $255,300.26 in 2026"**, fabricated from `jan1 = ZERO`, beside a debt-progress
section that correctly says "tracked_from 2026-03-31" because C1b taught THAT one to.

Now tracked as **B-11** (the producer's false zero, $17,134.85 on the Van Loan) and **B-10** (the
second consumer, $255,300.26) in `audit_loan_balance_producers.md`. **C1b built
`balance_at.loan_ledger_domain` and did not wire it into the map**, so the producer that emits the
false zero now has the fact that identifies it, one call away, and still emits it.

**The original note (2026-07-13), kept for the record:**

`confirmed_loan_balance_map` returns `Decimal("0.00")` for any period preceding the loan's opening
posting (`_reader.py:257`, documented as "nothing confirmed yet as of that date"), and
`splice_confirmed_and_projected_loan_balances` hands every BEGUN period to that map. So a begun
period earlier than the opening renders the loan as **debt-free**.

Measured on the dev clone. The Van Loan's opening is its `tracking_start` (2026-04-11); the user's
first-ever pay period starts 2026-03-26:

| period start | rendered owed |
|---|---|
| **2026-03-26** | **$0.00** -- WRONG |
| 2026-04-09 | $17,134.85 |
| 2026-04-23 | $16,683.84 |

Net worth is overstated by **$17,134.85** in that period on the grid, the net-worth trend's first
point, and the 2026 year-end. It is bounded (one period, one loan: the Mortgage's 2026-03-31 opening
lands INSIDE period 1, so it is clean), but it is precisely the sin the design rule forbids --
silently understating the debt -- and it is the same family as FU-1.

**C2's fail-loud does not catch it**: C2 raises when a loan has NO opening. Here the opening exists,
it is merely dated later than the period, so the ledger answers $0 and no guard fires.

The fix needs a design decision, which is why it is not folded into C2:

* **Back-project contractually** from origination for the pre-tracking months. The codebase already
  has the primitive -- `loan_resolution.contractual_schedule_from_origination` exists for exactly
  this (it is what fills a tracking-start loan's pre-tracking months on the property chart, per FU-2),
  and `synthesize_origination_anchor` seeds it. This is the honest answer: the loan DID exist and DID
  owe something.
* **Or omit the loan** from periods before its opening entirely, so no number is asserted where no
  evidence exists.

Measure the error against real data before designing it, and note the overlap with FU-2 -- both are
"what does a loan's history look like before we started tracking it".

### FU-8 -- An empty schedule makes the back-projection clip admit the loan's WHOLE contractual walk

**Found 2026-07-13 by C3's adversarial review, while measuring C-1. PRE-EXISTING (the route helper
C3 replaced carried the identical clip, verbatim); NOT caused by C3, and C3 does not make it worse.
It is currently UNREACHABLE, and that is the entire problem: it is a loaded gun whose safety is a
predicate.**

`balance_at/_secured_debt.secured_loan_series` clips the contractual back-projection to the months
before the resolved schedule begins:

```python
tracking_start = schedule[0].payment_date if schedule else None
back_projection = [r for r in full_contractual
                   if tracking_start is None or r.payment_date < tracking_start]
```

When the schedule is EMPTY there is no `tracking_start`, so the predicate admits **every** row -- the
loan's entire origination-to-payoff contractual walk, presented as its pre-tracking estimate. A
$200,000 / 360-month mortgage yields 360 rows of fabricated debt.

**Reachable only if a loan with an empty schedule is ever CHARTED.** Today it is not: an empty
schedule means a zero balance, which means `is_retired`, which drops the loan. That is exactly how
C3 shipped the C-1 phantom into review -- the moment the drop predicate was (wrongly) narrowed to
`is_paid_off`, the loan was charted, the clip fired, and the chart drew **$197,049.32** of debt
beside an equity hero reporting `$0.00`. The clip did not change; only its guard did.

**MEASURED 2026-07-14 -- the balloon shape was the right thing to worry about, and it is worth
$80,000.** A loan past maturity still owing $80,000 is NOT retired, so the chart keeps it -- and its
schedule IS empty, so the clip admits the whole contractual walk. The chart's axis becomes Feb 2023 -
Jan 2025 (entirely in the past), `today_index` clamps to the last month, and the Today marker shows
**debt $0.00 / equity $500,000** beside a hero reporting **debt $80,000 / equity $420,000**. The hero
is right. **Now tracked as B-2 in `audit_loan_balance_producers.md`, together with two sibling
mechanisms in the same chart (a mis-clamped axis worth $299,701.35, and a debt line that cannot see a
true-up). All three have ONE fix: the chart's <= today debt must come from the LEDGER, and its axis
must span `min(origination, today) .. max(payoff, today)` rather than the row span.**

**Why it was not fixed in C3, and what the fix needs.** The obvious fix ("empty schedule -> empty
back_projection") is NOT obviously right, which is why it was filed rather than guessed at. The
balloon is charted, and for it the contractual walk is arguably the only history estimate available;
emptying the back-projection would leave it charted with NO rows at all, which `_axis_span` may not
survive. Two candidate answers, and the choice is a modelling decision, not a seam one:

* clip the back-projection to the ledger's DOMAIN (`balance_at.loan_ledger_domain`), so the estimated
  tier can never assert a balance for a date the ledger can actually answer -- this also subsumes
  FU-2; or
* make an empty schedule yield an empty back-projection, and give the balloon shape its own rule.

### FU-2 -- The property equity chart draws its confirmed past from schedule rows, not the ledger

`property_equity_chart.py:216-218` tiers the past months of the secured-debt line from
`row.remaining_balance` on `is_confirmed` schedule rows. `forward_balance_at_date`'s own docstring
(`account_projection.py:345-351`) says confirmed rows are "an INCOMPLETE record of the past" because a
true-up is a ledger event with no schedule row, and cites a real $3.94 divergence from exactly this.
The Mortgage has 3 true-up postings and the Van Loan has 10, so the chart's historical debt line is
very likely wrong by the true-up amounts. C3 puts the assembly behind a seam entry, which is where
the fix goes: the confirmed tier should read `confirmed_loan_balance_map`. Measure the error against
real data before designing it.

### FU-3 -- `resolve_loan_seeded` applies TODAY's standing overpayment at any as-of

`loan_standing_extra_for_account(account_id)` (`loan_resolution.py:141`) takes no as-of, so a pass
pinned to a historical date resolves the loan with today's overpayment plan. Harmless while every
caller pins as_of at or near today (C4 now enforces "not future"), but it is a latent wrong answer for
any historical read and should be dated when one is built.

### FU-5 -- A CONFIRMED payment on a loan that has not originated -- **DEMONSTRATED, NOT FIXED**

Found while designing C2 (Section 3a); NOT caused by it, and C2/C2b do not make it worse.

**It is reachable, and C2's own test builds it.** `tests/test_services/test_balance_at.py::
TestLoanNotYetOriginated::test_not_paid_off_even_with_a_confirmed_payment` settles a $1,200 transfer
INTO an unclosed mortgage through the ordinary `transfer_service` -- a down payment, an earnest
deposit -- and it works. So this is no longer theorised: the shape exists, in the suite, on purpose.

**What actually happens to the money.** The $1,200 leaves checking. On the loan side the genesis walk
drops the future-dated origination anchor, so there is no OPENING to correct against; the loan reports
`$0.00` owed (correctly -- it does not exist yet) and NOTHING records the payment against it. When the
loan later originates it will owe its full principal, as though the payment never happened. Net worth
falls by $1,200 and no surface says a word.

**Why C2 did NOT add a guard, stated plainly because an earlier draft of this section said it must.**
The honest options are not obviously one-sided:

* **Fail loud** on a confirmed payment before origination. But a user CAN produce this through the
  normal UI, so it would 500 a page for data they were allowed to enter.
* **Reject at the write boundary** (the transfer service refuses a loan payment dated before the
  loan's origination). This is the root-cause fix, and it is a WRITE-path change -- a different
  concern from the balance seam, and out of C2's scope.
* **Model it** as a prepayment / down payment that reduces the opening principal. That is a feature.

Nothing here is a balance-seam decision, so folding a guess into C2 would have been exactly the kind
of unilateral design call this arc exists to stop. **It needs a developer ruling.** The measurement is
above; start from it rather than re-deriving it.

### FU-7 -- The forward projection PAYS DOWN overdue installments that were never paid (2a, forwards)

**QUANTIFIED 2026-07-14. Now tracked as B-9 in `audit_loan_balance_producers.md`; the ruling (R2) is
still owed.** Measured across generated shapes on a cloned DB:

| shape | map cliff in ONE pay period | year-end says "principal paid" | actually paid |
|---|---|---|---|
| 41 installments overdue | **-$15,755.38** | **$17,906.63** | **$0.00** |
| 30 overdue | -$6,426.95 | $7,594.73 | $0.00 |
| mid-life import | -$2,383.04 | $5,418.14 | $0.00 |
| recurring, 3 overdue | -$1,205.41 | $2,746.39 | $0.00 |
| delinquent | -$906.31 | $2,746.39 | $1,199.10 |

It lands on the /savings net-worth trend, the Horizon band, and year-end debt progress
simultaneously. **Reachable** (a user who misses payments produces it), not merely latent.

**Its own arc. Found 2026-07-13 by the C2 adversarial review; NOT caused by this arc, and C2/C2b do
not make it worse. Latent on the two REAL loans (both carry ZERO past-dated unconfirmed rows --
verified against the clone), LIVE in the test suite -- the identical posture defect 2a had.**

**The defect, measured.** An auto loan: $50,000 borrowed 2025-01-25, 6%, 60 months, due the 25th, no
payments ever recorded. Read on 2026-03-20:

| | |
|---|---|
| ledger / hero / net-worth trend, current period | **$50,000.00** |
| net-worth trend, NEXT period (`balance_at(2026-03-26)`) | **$39,634.37** |

A **$10,365.63** debt reduction in six days, with no payment recorded and no cash moved. The schedule
carries 14 installments dated 2025-02-25 through 2026-03-25, all past, none paid; the forward walk
counts every installment due on or before the target, so the moment you ask about any date past today,
all 14 fire at once.

**The diagnosis.** The PAST is cash-basis (the ledger counts only what settled). The FUTURE is
due-basis (the projection counts every installment that came due, paid or not). The seam between them
is `as_of`. That cliff IS the seam, made visible. It is defect 2a -- unpaid rows paying down a debt --
pointed forward instead of backward, and `loan_owed_at_dates`' own docstring already calls it what it
is ("silently UNDERSTATING the debt") while refusing to commit it in only one direction.

**The naive fix is WRONG, and this is the finding that matters.** "Start the projection after `as_of`"
was tried and measured: **26 failures.** It does not merely stop paying debt you did not pay -- it
silently DROPS a payment you have planned and will make. Your mortgage is due Jul 1; today is Jul 13;
you have not clicked "mark paid" yet, and a Projected payment record is already sitting there. Under
that fix `project_forward` never visits July's month, so July's override is never applied
(`_projection.py:674`): the balance holds flat, the payoff date jumps a month later, the loan grows a
balloon at maturity -- and it all snaps back the moment you record the payment. **A payoff date that
flickers every month between the due date and the mark-paid.**

**The distinction the fix must turn on** (which the two-option framing missed entirely):

| shape | payment RECORD? | meaning |
|---|---|---|
| never-paid loan (every fixture; a delinquent loan) | none | never planned, never happened |
| your mortgage, mid-period | Projected | planned, just not recorded yet |

Today's behaviour treats BOTH as paid. The naive fix treats BOTH as never happening. Neither is right.

**Starting proposal for the arc (measure before promising):** an installment BACKED by a payment record
(confirmed or projected) is projected; one with NO record behind it never happened and never pays the
debt down. That kills the cliff (the never-paid loan has no records) without touching the mid-period
case (yours has one). It touches the loan detail page, the payoff date, and the schedule table, so it
is a modelling decision with UI consequences -- not a balance-seam change, and not something to bolt
onto C2.

### FU-6 -- The payoff / refinance calculators on a not-yet-originated loan

`routes/loan/calculators.py:405` computes `refi_principal = state.current_balance + closing_costs`.
After C2, `current_balance` is correctly `$0.00` for a loan that has not originated, so "refinance"
would quote the closing costs alone. `:495` already guards the payoff calculator
(`state.current_balance <= 0` -> no scenarios), which after C2 correctly reports nothing to pay off.

Neither is a wrong NUMBER -- they are degenerate answers to a question that makes no sense for a loan
that does not exist. The right fix is a UI-tier "this loan has not originated yet" state on the loan
detail page, which is a design question for the loan-detail surface, not a balance-seam one. Recorded
so it is a decision rather than an oversight.

---

## 8. Rollback

C1 is test-only. C3-C6 are fence and documentation. **C2 and C2b are the only commits that change
what a production surface computes.**

* **C2b**'s change is "raise instead of returning a number." Reverting it restores the fallback and
  the divergence; nothing else depends on it having landed.
* **C2**'s change is confined, by construction, to a loan whose `origination_date` is in the future.
  Neither real loan is one, so reverting it moves nothing on production data -- it only restores the
  $200,000 phantom for an upcoming loan. C2b depends on C2 having landed (without it, C2b's
  unconditional raise would 500 the `/savings` page for a user with an upcoming mortgage), so revert
  C2b first.

The Mortgage's $177,277.97 is the single check that tells you whether the arc is behaving.
