> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# The loan balance arc: as-built record (Phases A-F, 2026-07-16 .. 2026-07-25)

**Read-only history. Nothing in this file governs work.** The live document is `../README.md`,
which since 2026-07-26 is the CASH plan of record. This file is the as-built record of the LOAN
half of the balance arc, extracted verbatim from that document when the loan work completed so
the live plan could shrink to the work that remains.

**What it records, and what it is good for.** Phases A-E and F2/F3 are COMPLETE and SHIPPED TO
PRODUCTION (PR #64, merge `88c79857`, 2026-07-25): the loan side answers "what do I owe at T"
from ONE total fold over source events, with no partial function, no past/future splice, and no
name-keyed fence. Read this to find out how a shipped loan behaviour was decided, which commit
shipped it, and what its verification was. Do NOT read it as instructions: every step here has
shipped, and the design questions it settled are settled.

**Three standing warnings.**

* **Every dollar figure and line number was true on its write date only.** The arc's own Section 8
  lesson: "documents rot in days here."
* **Nothing here is a citation you may act on.** Re-verify against the code, as the live
  document's Section 7.6 requires of itself.
* **The still-UNRESOLVED loan questions are NOT here.** They stayed in the live document's
  findings ledger when this was extracted, because unfinished work belongs where work is
  planned: B-16, B-17, FU-1/F1, FU-3, N-4, N-5, N-14, N-18, N-19, N-23, N-24, N-25 (the class),
  N-29, N-33, N-35, N-36, and E2 (the super-package option). Three of them were re-verified
  against the code at extraction and their rows corrected; see the live ledger.

**The loan baseline, which is still a LIVE regression gate.** Mortgage (account 3)
**$177,277.97**, Van Loan (account 8) **$15,663.59**. Every CASH commit still re-derives both
before and after, because a cash change that moves a loan figure is wrong -- plan steps X-c2b2
and X-c2b3 both verified "both loans UNMOVED" against exactly these numbers. The gate lives on in
the live document's Section 2; it is repeated here so this record is self-contained.

---

## 1. The running state record, as it stood at 2026-07-26

Verbatim, the state paragraph the plan of record accumulated across the whole arc -- every step
ticked with its hash, every correction in the order it was found. It covers the CASH steps too
(X-a .. X-c2b3, which had shipped by then); the live document carries the cash half forward in
its own, shorter form.

**State as of 2026-07-26:** design verified and locked; ALL rulings answered (D1-D5,
R-A..R-E, Section 4); **Phase D is COMPLETE** (D2 ruled, D2a + D3 shipped -- the name fences are
structurally unnecessary and deleted to the E1-mortal residue) and **the E1 arc is CLOSED**
(E1a-E1e shipped; W9906 no longer exists, and the only balance gates left are W9909's fail-closed
classification and W9910's name-independent package privacy). **F3's two ship gates are CLOSED on
real production data (2026-07-25, see F3)** -- the E1a lineage sweep found zero rows of every class
it names, and the C2 history-window live-render reproduces the receipt with today UNMOVED on both
loans; the whole deploy sequence was REHEARSED on a fresh prod clone (migrations + both backfills
behind E1a's assert) and passes, idempotently, moving no user-visible figure. **C2b** (the N-34
re-key: the split's rate and escrow key on the DUE date) SHIPPED `c2d43332` ahead of the ship on
the developer's ruling 2026-07-25, so prod's genesis ledger is rewritten ONCE. **F3 SHIPPED
2026-07-25** (PR #64, merge `88c79857`): the whole loan arc is in production, CI green, image
published, and `dev` is resynced to `main`. **Phase X, the cash side, is IN FLIGHT** -- the last
unbuilt half of the restructure, and the one carrying the LIVE defects (see the measured prod
evidence in Phase X).  **X-a SHIPPED `929b3a72`** (the cash walk leaf, additive).  **Both rulings
X-b was blocked on are ANSWERED (developer, 2026-07-25, both as recommended -- R-I and R-J in
Section 4), and tracing them corrected the plan's own citation and found a LIVE defect.**  N-38's
door was NOT `resolve_grid_account` (step A1 gated all four of its steps; the dashboard, pulse and
cash-detail surfaces are gated too) but `resolve_analytics_account` -- the calendar, ownership-gated
only -- where the cash-flow view answered `$531.94` for a Van Loan owing `$15,663.59` and
`$178,103.41` for a Mortgage owing `$177,277.97`: finding B-3 itself, live, on the one cash-flow
surface ruling D4's enumeration missed.  Because that is a defect TODAY rather than one X-b would
introduce, it shipped AHEAD of the fold as **X-a1** (`47dd4bbb`).  **X-b SHIPPED `2aedc21c`** --
the fold, still ADDITIVE and unwired (only its oracle calls it), implementing N-37's answer
(back-project from the first assertion over the records) along with R-G's clamp.  Its real-data
parallel run over 840 days x 8 non-loan accounts leaves **ZERO unclassified days**, and the tail
difference against the shipping producers equals EXACTLY the settled money each account has
invisible today -- `-$2,000.00` on the Money Market, which is the defect the developer hit on
2026-07-25 (a transfer marked Paid a week after that account was last anchored, reducing no balance
on any screen: finding cash D1, live).  Two findings recorded, not ruled: **N-39** (the planned
reservation's valuation date, latent -- zero affected rows in either database) and **N-40**
(`live_amount_overrides` reads the wall clock, inherited unchanged from all three shipping
producers).  **X-c is TRACED, RULED and DECOMPOSED (2026-07-25, four developer rulings R-K..R-N,
all as recommended).**  The decisive measurement: the app's OWN persisted double-entry ledger says
Checking `$2,824.26` and the Money Market `$3,659.51`, the X-b fold reproduces both to the cent,
and the shipping projection answers `$2,791.78` / `$5,659.51` -- **the screens contradict the app's
own books by `$32.48` and `$2,000.00` today.**  Tracing also found the step's one-liner wrong in
three places: its central grid invariant is UNACHIEVABLE under the fold (**N-41**, ruled R-K -- the
subtotal rows change basis and gain a Reconciliation row), "the investment contributions base reads
the fold" would rewrite `-$6,315.57` of net-worth history (**N-43**, corrected by windowing), and
the net-worth trend's history gate becomes a stale compensator suppressing 8 periods (**N-44**).
Two further findings: the INTEREST branch needed a ruling the plan never named (R-L, accrual
forward of the latest assertion) and **N-42** -- nothing in the app records WHEN money moved, so the
ACTUAL clock is a data-entry click (ruled R-N: cut over first, record real dates at X-f).  N-39 is
CLOSED rather than decided (R-M).  **X-c0 SHIPPED `5b3764a7`** -- the guard, plus the latent
SECOND clock it uncovered (the add form and the grid styling read the server's `date.today()` where
the guard reads the user's `display_today()`, so a UTC-running process would have posted a date its
own guard rejects).  **X-c1 SHIPPED `9b8c9fdd`** -- the period view, ADDITIVE and unwired, and it
made the step's own claim structural rather than asserted: the two readers now share ONE assembly
(one walk, one plan load, one valuation), so "one valued row set grouped two ways" is what the code
DOES instead of what a test checks.  Its identity holds on **360 of 360** (account, period) pairs of
real data with zero breaks, and the run reproduced every figure ruling R-K was decided on
independently -- including the current Checking column moving `-$140.63` -> `$3,153.22` against a
`+$2,364.54` fold delta, while every FUTURE column is unchanged to the cent.  Two plan corrections:
the leaf gains TWO fields (the budget clock also has to SPLIT the row, by TYPE -- pinned on the one
reachable shape where type and sign disagree), and `period_subtotal(s)` / `PeriodSubtotal` join
X-c2's deletion list, because R-K changes what a subtotal counts.  One out-of-scope defect found
and recorded, not fixed: **N-45**.  **X-c2 is TRACED, RULED and DECOMPOSED (2026-07-25, four
developer rulings, all as recommended).**  Tracing it measured the coupling that fixes its shape:
the INTEREST branch cannot lag the cash map by one commit, because the grid renders
`kind_correct - cash` as its "Interest" row and would relabel the Money Market's missing
`$2,000.00` as **`$2,007.01` of interest earned** (**N-49**); the kind-correct PLAIN scalar is
literally the SAME call as the cash-flow scalar today; and the pulse hero, its chart, and the
grid's balance-vs-subtotal identity are each one screen.  So the cutover is ATOMIC, and only the
two uncoupled pieces come out of it -- ruling R-L's CLOCK half ahead (**X-c2a**) and the
investment / appreciation bases behind (**X-c2c**, measured `$0.00` of movement anchor-forward).
Three further findings, all corrections to the step's own text: its site enumeration is short by
three (**N-47** -- the kind-correct scalar's two branches and the account-detail interest chip),
its deletion list is short by two, one of which is dead ALREADY (**N-46**), and the grid runs three
per-period producer passes where one `cash_period_view` suffices, **338.0 ms -> 96.2 ms** measured
(**N-48**).  R-O rules the Reconciliation row's label, placement and visibility; R-L is sharpened
in place (the assertion's own day accrues).  **X-c2a SHIPPED `0c89da2f`** -- the accrual clock,
whose real cost was a FIXTURE finding rather than the rule: the account factory stamps an opening
assertion with the wall clock while `tests/test_services` freezes today to 2026-03-20, so every
factory-built HYSA was asserted months after its own last pay period and accrued nothing anywhere
(19 tests failed on that, not on the change).  Pinning the opening to its anchor period's first day
keeps every hand-computed interest figure valid UNCHANGED, which is itself the proof the rule moves
no existing fixture; on prod data the live Money Market is unmoved to the cent and only the
ARCHIVED Fidelity Savings moves (`$6.77` -> `$1.45` in its first period).  **X-c2b is TRACED, RULED
and DECOMPOSED (2026-07-26, three developer rulings, all as recommended).**  Tracing re-verified all
four couplings that force ruling R-F's "ONE cutover" and reproduced every headline figure
independently on the prod-shape clone -- Checking `$2,791.78` -> `$2,824.26`, its current grid column
-> `$2,683.63`, the Money Market `-$2,000.00` on every column, the eight blank columns gaining real
balances, the IRAs and the Home unmoved -- and then measured that those couplings bind only the
MONEY-MOVING surface, while two thirds of the step is render plumbing and dead-code deletion across
~15 modules, 6 templates and **34 test files**.  So X-c2b ships **b1** (the shape: one per-period
view, baseline byte-identical) -> **b2** (the cutover) -> **b3** (the replaced producers delete).
Two forks ruled: **R-P** (every surface rendering the subtotals renders R-O's row, mobile included,
on R-O's own non-zero rule) and **R-Q** (the `amount_overrides` parameter DELETES from eight
signatures; the seam owns the live map and the route reads it back, so the cells and the balance row
are identical by construction and the render drops a second ~90 ms build).  Six findings recorded:
**N-50** (`BalanceResult` dies with the flag), **N-51** (the parameter, ruled R-Q -- measured ZERO of
60 columns differ between the stored and live bases), **N-52** (`_accruing_grid_view` deletes; the
post-cutover premium equals the cumulative interest to the cent, `$658.26`), **N-53** (the interest
path's own transaction query and `calculate_balances_with_interest` join the deletion list),
**N-54** (the account-detail interest chip moves `$284.34` -> `$217.75`), and **N-55** (two seam
docstrings cite an obligations panel that no longer exists).  N-44's own figure is corrected:
`/savings` draws ZERO history points today and gains **6**, not 8.  **X-c2b1 SHIPPED `9835d2af`** -- the grid's three producer passes are ONE per-period view, 360 of 360
real-data cells byte-identical HEAD-vs-post, and its own review caught a mobile row-flag scoping
defect it had introduced (fixed, control fires).  **X-c2b2 SHIPPED `d3489728` (2026-07-26) -- THE
cutover, and the only step of Phase X where money moves.**  All six sites read the fold and the grid
reads ONE per-period view; the stale-anchor banner and the net-worth trend's cash history gate went
with it.  Checking today `$2,791.78` -> `$2,824.26` (the figure the app's OWN posted ledger already
carried), its eight blank past columns gained real balances, the Money Market moved `-$2,000.00` on
every column, `/savings` went from 0 to 6 history points -- and **both loans and every investment
map are unmoved**.  Ruling R-K's identity holds on 360 of 360 real (account, period) pairs, twice
over.  Its adversarial review caught four defects it had introduced or inherited, all fixed
in-commit, including a regression of its own (the account-detail page folded twice, N-64) and a
SHIPPED guard that was passing on a docstring mention rather than a call site (N-63).  Recorded, not
fixed: **N-58**, the calendar's flow chip and its balance step now sit on two clocks -- the R-K
contradiction on a second surface, which the developer ruled RECORDED for its own step (after X-f
shrinks the date noise causing it).  **`7de04f0c` shipped alongside on the developer's ruling**: a
settled row in a fresh scenario left that scenario's ledger opening-less (`-$70.00` where the
account holds `$930.00`, N-61) because the effect-time self-heal tested staleness but never
existence; the predicate is now a SKIP whose precondition is that the reconcile would write nothing.
N-62 went with it (`resolve_anchor` had no `id` tiebreak, so two same-instant assertions disagreed
with the walk about which is authoritative).  Both latent, not live -- production creates baseline
scenarios only.  **X-c2b3 SHIPPED `82557ca9` (2026-07-26) -- the pure deletion.**  `_daily_series`
whole, `balance_as_of_date` with its prefix walk, `BalanceResult`, the four per-period subtotal names
with their two W9909 rulings, and `load_account_period_transactions` (after which the kernel issues
no QUERY at all) -- **7614 -> 7598 tests, a delta of exactly the 16 the four retired suites held**,
with 4 executable lines added across `app/` and 2,382 deleted, and `sum_projected` byte-identical.
`_detect_stale_anchor` and the `calculate_balances` arity change were **DEFERRED to X-c2c on the
developer's ruling**: collapsing the 2-tuple reaches ~92 test call sites that X-c2c would then
discard with `_calculator` itself.  The step's real yield was three X-c2b2 residue defects it found
and fixed -- **N-67**, the N-63 shape a SECOND time (the `/accounts` detail guard green on 3
docstring mentions with 0 call sites, now matching the CALL and refusing the double-fold shape),
**N-68** (a fixture dating purchases in a future period, which ruling R-M refuses at both write
doors) and **N-69** (two seam scalar tests vacuous on a row-less fixture) -- plus two list
corrections (**N-66**, `account_period_scope_clause`, deleted here) and one recorded finding
(**N-70**), CLOSED on the developer's instruction in its own commit immediately after.  **NEXT: X-c2c, and it is larger than its one-liner reads -- see the step.**
Phases A and B complete (**A1** `f11382a0`, **A2** `c96c62be`,
**A3** `4e46a0a8`, N-9 `44cbd028`, **B0** `d1586254`, **B1** `e227de08`, **BG**
`dba91dc0`, **B2** `8f070386`). **Phase C: C1** (`18fd3a04`, a loan's origination is its
ledger opening), **C2** (`eb5de4ac`, the ONE CLOCK: an event counts from the day it
happened -- the date its posting already carries in `entry_date`; closed the N-10 leak and
N-12, moved the per-period map to period-END keying), **C3a** (`df775017`, `positions()` -- the
total loan producer), **F2** (`3aecceb0`, the dead year-end service deleted), and **C3c**
(`99cc2816`, interest-in-year is the fold-based `balance_at.loan_interest_in_year`) shipped. **C3 is
DECOMPOSED** (developer ruling 2026-07-18): too large for one revertable commit and reaching into
dead year-end code, so it ships **C3a** -> **F2** (pulled AHEAD) -> **C3c** (interest-in-year is a
DEDICATED producer, NOT `positions().cum_interest` -- the tax figure keys on the display-tz paid
year while the balance keys on UTC) -> **C3b**, each a REFACTOR (baseline unmoved; B-9's
overdue-installment paydown preserved until C6). **C3b is itself DECOMPOSED into C3b1-C3b4**
(developer ruling 2026-07-18, mirroring C3a): the scalar cutover is proven by C3a's oracle but the
per-period MAP has no equivalence proof yet, and `positions()` lives ABOVE `net_worth_kernel` (at its
1000-line cap) so the map branch must MOVE INTO the seam. **C3b1** (`f410afa9`, the scalar + the
liability band read `positions()`; the read pass memoizes the loan walk; scalar now FOLDS a broken
loan instead of raising -- closes B-8 at the scalar) and **C3b2** (`28f8fe51`, the additive
`positions_period_map` producer + its every-period oracle vs the shipping map; current-period clamp
proven load-bearing, incl. the N-10 originate-inside-current-period `0.00`) and **C3b3** (`84e386c6`,
the map dispatch reads `positions_period_map`, MOVED into the seam's `_account_balance_map` since the
kernel cannot import the seam; the map now FOLDS a broken loan -- B-8 closed at the map; dev-clone
live-render UNMOVED to the cent, Mortgage $177,277.97 / Van Loan $15,663.59, map == scalar) shipped.
**C3b4** (`5c62c995`, the dead ledger-domain readers deleted -- `loan_ledger_domain` /
`confirmed_loan_ledger_domain` / `LoanLedgerDomain` gone; `_domain.py` RENAMED to `_linked_ledger.py`
since it also held the two KEPT-reader query helpers `_has_opening_posting` / `_visible_nets`) shipped,
closing the C3b arc.
**`confirmed_loan_balance_map` was KEPT** (C3b3 deletion-list correction, developer ruling
2026-07-18: it reads the KEPT posting ledger and is the Step-4 reconciliation oracle's independent
window; its fate is decided at E1) -- **and DELETED at E1e with its scalar twin**, the oracle's
window moving to the test suite. The C2 real-clone history-window live-render (~26 days Mortgage / ~13 days
Van Loan, today UNMOVED) is still outstanding before the F3 prod ship. **C4** (`c98ea07b`, the loan
route reads the seam through ONE `BalanceContext` and drops its private `resolve_loan_seeded`;
`LoanState.current_balance` KEPT for its two in-cluster readers, deletion deferred to C6-adjacent;
B-13 closed, B-13-route control pins the fold vs the money-blind replay) shipped. **C5** (`821dd0eb`,
the equity chart's debt line is the fold: the CONFIRMED and PROJECTED tiers read
`balance_at.positions()`, the pre-tracking ESTIMATED back-projection KEPT per D2, the axis spans
`min(origination, today)..max(payoff, today)` so the today-clamp is retired and the empty-schedule
clip is gone; the chart reconciles with the equity hero AT today via a shared `window_sample_date`;
B-2 and FU-8 closed) shipped. **C6 is DECOMPOSED** (developer ruling 2026-07-18, mirroring C3/C3b)
into **C6a** (the additive `plan()` producer + hand-computed forward oracle), **C6b** (the cutover:
`positions()`'s forward branch folds `plan()`, the schedule-forward primitives delete, the baseline
consciously moves), and **C6c** (the interest chip + de-dup follow the records). Two scope
corrections vs the plan's one-liner: `AmortizationRow.remaining_balance` deletion is DEFERRED out of
C6 (still read by the payoff-scenario chart, the schedule table, the D2 back-projection, and the
write-side payoff sync), and the ESTIMATED synthesis-to-payoff tier is MANDATORY (a records-only fold
would flatline the equity chart beyond the ~2-year record horizon). **C6a** (the additive `plan()`
producer + hand-computed oracle) shipped `31e00413` (full suite 7371, pylint 10.00); its adversarial
review corrected one HIGH (the ESTIMATED tier must exclude early-settled seed slots) and the C6c
de-dup claim. **C6b** (`f445aa77`, the cutover: `positions()` forward folds `plan()`; the
schedule-forward primitives + W9905 delete; `ctx.loan_plan` memo added) shipped -- **B-9 killed.**
On real data the baseline stayed UNMOVED to the cent (both loans current, 0 overdue); the "baseline
moves" only for delinquent loans / genuine live-vs-stored cash, which the delinquent test fixtures
carry. **C6c is DECOMPOSED** (developer ruling 2026-07-19, mirroring C6a->C6b) to isolate the
tax-figure move: **C6c-i** (`2ba0adcf`, the loan-detail paid-YTD chips fold the settled past --
`balance_at.loan_interest_paid_in_year` / `loan_principal_paid_in_year`, TOTAL producers over the
memoized walk; both posting readers `confirmed_loan_interest_in_year` /
`confirmed_loan_principal_in_year` deleted; no figure moves) and **C6c-ii** (`6014389a`,
`loan_interest_in_year`'s projection folds `plan()`; real Mortgage +$0.02 from the live-cash forward
model; the settled-slot merge STAYS re-keyed onto the WALK -- the plan's "airtight because as_of=today"
de-dup claim was false across the display/UTC clock split, an adversarial-review HIGH) shipped --
**C6c CLOSED**. **C7** (`a3f15aed`, the payment-drift warning + one-click "switch to automatic":
the loan detail page warns when a MANUAL recurring payment has fallen short of the contractual
monthly payment, and one click flips it to `derive_from_loan` so its cash tracks the contract
forever; surfaces N-2) shipped. **C8 DECOMPOSED** (2026-07-19) into C8a (fix the forward fold's
standing-extra tail -- N-15) -> C8b (additive `loan_payoff_date` fold-to-zero) -> C8c (cutover:
payoff derived not persisted, B-14 + B-20). **C8a** (`2e5d3a75`, the ESTIMATED tail folds the
standing extra; N-15 closed) and **C8b** (`511ab220`, `loan_payoff_date` fold-to-zero, additive)
and **C8c** (`8ff9a11e`, the fold's forward tail extends past contractual so an underpayment gets a
real payoff, not `None`; N-16 closed) and **C8d** (`2f0130f5`, the cutover: `LoanFigures.payoff_date`
is the fold to zero and `LoanState.payoff_date` is deleted, so the chip, the cockpit, the Horizon, the
equity axis, and the recurrence bound all read ONE derivation; B-14 + B-20 closed; dev-clone
live-verify moved nothing, and closed C8c's outstanding item) shipped. C8d's adversarial review
then forced two more, both root-cause fixes rather than touch-ups: **C8e** (`6e060884`,
`LoanTerms` splits the scenario-INDEPENDENT contract facts out of `LoanFigures`, so the escrow /
rate / payment-amount write surfaces are no longer coupled to a baseline scenario) and **C8f**
(`fe424560`, the target-date calculator folds the plan; `target_date_outlook` and its
`project_forward` walk deleted, so the panel can no longer contradict the payoff chip) -- **the C8
arc is CLOSED.** **C9 DECOMPOSED** (2026-07-19) once the trace found the app MANUFACTURES the
shape R-C guards against -- the loan-payment setup generated 3 of 4 installments
pre-origination ($3,220.92 of phantom cash debits), so the guard alone was measured to 500 its
own route. **C9a** (`2976614b` + data `7c021281`, the recurrence gains a real `start_date` bound
filtered unconditionally in `match_periods`, derived from the loan's first contractual
installment; its review caught a `day_of_month` desync that emptied a loan's whole payment
schedule, and an unbounded second door at `POST /transfers`) and **C9b** (`d5a02ad2`, the R-C
guard at BOTH write doors on the shared installment derivation; the boundary is `<=`, correcting
R-C's "before") shipped -- **the C9 arc is CLOSED, and FU-5 with it.** **Phase D is in flight.** Tracing D1
found that its premise -- a package boundary replacing ~60 name-keyed fence entries -- rested on a
gate that does not exist: pylint's stock `import-private-name` is fail-OPEN for
`from pkg._module import name` (**N-26**), and the seam already carried a cross-package private
import hiding a real runtime cycle that `cyclic-import` structurally could not report
(**N-25**). So Phase D gained **D0** (the arrow points one way) and **D-gate** (the custom
package-privacy checker). **D0a** (`8285fcad`, the plan memo takes its builder; the cycle is gone,
proven in both directions; the two injected memos land on one mechanism) shipped.
**N-25 was archived out of the live ledger here on 2026-08-13**: its INSTANCE closed at that
commit and the developer ruled the CLASS an upstream limitation with no shared root in this
codebase, so nothing was owed. Recorded because the gate's cycle model and the runtime's
differ in BOTH directions -- N-25 is pylint blind to a real cycle, and balance:N-266 (b) is
pylint reporting one the runtime does not have. **D0b was then
CANCELLED and Phase D redesigned** (developer ruling 2026-07-19: "use structure, not fences") --
scoping D0b showed it would ADD four fence entries, which was the design saying the arrow was
backwards: a balance producer moves deeper INTO the seam, never out to a public leaf. Phase D is now
ONE invariant (every balance producer is private to `balance_at`) enforced by ONE gate, with each
step removing the REASON a fence surface exists rather than shrinking it: **D1** (engines in) ->
**D-ctx** (context in; retires D0a's injection) -> **D-fold** (folds in; the walk stops being a
"producer") -> **D-gate** -> **D3** (delete, not shrink). Then **X1** (the cash side).
**D-docs** (`8e9a0517`) and **D-dead** (`cef81202`, the dead net-worth reducer -- and the two
unpinned `abs` sites its deletion exposed, **N-27**) shipped as D1 prerequisites. **D1a** (`a2149145`, the split; its review found the fence hole **N-28**)
shipped; **D1 is DECOMPOSED into D1a/D1b/D1c** (developer ruling 2026-07-20): re-measuring its scope with an AST
scan found the plan's own correction (a) undercounted the out-of-cluster surface 2 -> 4 consumers
and 2 -> 7 names, and that all 7 are ruled NON-producers -- so the step splits the cluster along
that line (producers IN, non-producers OUT) instead of moving it wholesale. **D1b** (`1616acd8`)
shipped, and HALVED: "off the fence" bundled a TIGHTENING (the W9906 call allowlist, correct) with
a LOOSENING (the W9909 completeness registry), and the loosening was MEASURED to re-open N-28's
hole at 10.00/10 -- a module's reachability surface is its PARAMETERS, not its imports, and this
one held four balance producers until the day before. The registry entry STAYS;
`find_period_containing_date` moved to `loan_ledger._visible` on cohesion. Two adversarial reviews
changed the commit (the CRITICAL, then a self-attesting guard and a toothless test); one deferred
fork recorded as **N-29**.
**D1c** (`70cc04c2`) shipped -- the cash leaf (`cash_ledger` package: `_facts` / `_amounts` /
`_flows`), which DELETES the hand-written `_CASH_EVENT_SOURCE_MODULES` scope by making the cash
layer symmetric with `loan_ledger` (one prefix-matched package key). Its trace found the step was
NOT a pure move (N-30's five stranded functions dragged a DUPLICATED reservation formula out of
`balance_resolver`); the mirror was deleted, the as-of window became a keyword-only parameter, and
`entry_checking_impact` went private. Baseline unmoved (1,402 dev-clone figures + a seeded
entries-aware probe, both HEAD-vs-post identical and teeth-checked); adversarial review fixed four
stale-docstring / layering findings pre-commit. **NEXT: D1d** (move the producers in).
**D1c is REDESIGNED and the move renumbered D1d** (developer ruling 2026-07-20, "make the fences
structurally unnecessary"; the C8c precedent for seating a prerequisite ahead of a cutover). Tracing
the step found its "ZERO out-of-cluster consumers" claim FALSE for `balance_calculator`:
`period_flows` -- the module D1a deliberately placed OUTSIDE the seam -- calls its `sum_projected`,
and the call graph drags FIVE explicitly-ruled non-producers out with it (**N-30**). Placing them
re-opened the larger question, and the answer is the shape the LOAN side already proved:
`loan_ledger` is ONE package the fence scopes with ONE key, while the cash layer is THREE flat
modules whose scope is the hand-written, self-attesting `_CASH_EVENT_SOURCE_MODULES`. So the new
**D1c** builds the cash leaf as a package (`cash_ledger`), finishing the layer D1a explicitly built
for X2 and DELETING a fence rather than adding one; **D1d** then moves the producers in. Also
measured: deleting the engine-cluster W9909 rulings at the move re-opens the fail-open hole until
D-gate ships (**N-31**), so they TRAVEL with their modules and die at D3.
**D1d SHIPPED (cash chain `229a7889`, net-worth chain `34bf0446`, 2026-07-20):** all five producers
are now private inside `balance_at` and `_BALANCE_SEAM_MODULES` collapsed to
`{app.services.balance_at}` -- the one package boundary D-gate will enforce. The plan's scope text
was stale ("`_calculator` is N-30" was false post-D1c; only `_kernel` needed the two exports); the
net-worth move promoted a lazy import off a FALSE cycle rationale (**N-32**).
**D-ctx SHIPPED (D-ctx-a move `0dd6395d`, D-ctx-b dissolution `00036224`, 2026-07-20):** the read-pass
context is now `balance_at._context`, and D0a's builder injection is GONE -- the forward plan/payoff
are PUBLIC pass-through caches the seam FILLS, not injected memo methods. Two plan-text claims were
DISPROVED and corrected in the step (the "sibling import, cycle cannot recur" -- a probe showed
sibling mutual top-level imports still trip `R0401`; and "`_LOAN_RESOLVER_PRODUCERS` deletes" -- it
guards a live `current_balance` leak, so it is RE-KEYED, deletion deferred to D3). The context's
W9909 ruling travels to a self-attest-pinned `_SEAM_PRIVATE_CONTEXT_MODULES` (N-31 applied).
**D-fold SHIPPED (`3dc32d14`, 2026-07-20):** the loan BALANCE fold moved into the seam as a private
module (`balance_at/_fold.py`: `sample_cumulative` / `_dated_deltas` / `fold_from_walk` /
`fold_loan_balances`), and `loan_ledger/_fold.py` was renamed `_walk.py` -- the leaf now holds only
the WALK (facts). A PROVEN pure move (all 9 defs AST-body-identical to HEAD). Scope was FOUR names,
not the plan's two: `_dated_deltas` is `fold_from_walk`'s helper, and `sample_cumulative` is
balance-side only (no writer caller), so moving it out is what makes un-fencing the walk safe -- no
public leaf name then turns a walk into a balance. Fence = **Option B** (developer-ratified over
N-31 travel): the folds are plain seam-private like their twin `fold_forward`, OFF the frozen fence
(the D0b lesson; D-gate closes the residual). All leaf fence edits TIGHTEN: `walk_loan_ledger` ->
the leaf's W9909 NON-producer set (a fact), `sample_cumulative` dropped, `_LOAN_LEDGER_READER_PRODUCERS`
shrunk to the two posting readers, allowlist tightened to `{loan_posting_service,
loan_payment_service}`. Baseline UNMOVED on the dev clone (Mortgage $177,277.97, Van Loan
$15,663.59); pylint 10.00, 153 checker tests, full suite 7478; adversarial review clean after one Low
(a stale `_context.loan_walk` docstring).
**D-gate SHIPPED (`0ba7ecc8`, 2026-07-20):** the package-privacy checker
`shekel-private-module-import` (W9910) -- a module outside package P may not import `P._x`, nor any
name from it, in any spelling, TYPE_CHECKING included; name-INDEPENDENT, fail-closed, hard-gated in
all eight `--fail-on` locations (the eighth, `/standards`, was measured to DRIFT in this very step
and is now pinned by the gate-consistency test). Green with ZERO standing exceptions, measured
twice (AST scan, then the shipped checker over all three linted trees); N-26 closed. Two step
discoveries: membership needed a PER-BOUNDARY physical-file arm (`scripts/` is a namespace package
whose entry points lint as top-level modules; its adversarial review caught the statement-wide
version passing a private-subpackage reach), and the residual OUTSIDE the ruled scope is recorded
as **N-33** (13 cross-package private-NAME imports, routes -> `app.utils.account_validation`; fix
is a rename to public).
**D2 RULED (2026-07-24): distinct balance types defend NOTHING -- declined as gold-plating,** as
the step's own text allowed.  Measured basis: the gate stack has NO static type checker (an
annotation is enforced by nothing), a `Decimal` subclass decays to plain `Decimal` on the first
arithmetic unless ~20 operators are overridden (a policy layer bigger than the fences D3 deletes),
the one family-confusion defect that ever shipped (B-3) was closed at the SOURCE by D4 where a
return type could not have prevented it, cross-family arithmetic mixing has ZERO measured
instances, and the attribute-leak class closes by DELETION.  Root cause 3 completes structurally:
**D2a** (`9f592502`) deletes `LoanState.current_balance` -- the last balance-at-T on a public
bundle -- re-routing the seam's last two readers (`_kernel._projection_seed`, the whole forward
tier's seed; `_loan_figures._is_retired`) onto the fold.  Healthy loans proven unmoved (B2 +
dev-clone to the cent); a BROKEN loan's figures converge on the fold the page already shows
($239,761.08 money-blind replay vs $0.00 fold, pinned by a new control); B-12's balance half
closes structurally; production-dead `resolve_account_loan` deleted; ~12 test files re-point
their oracle windows one level down (`_replay_from_anchor` / `confirmed_loan_balance_at` / the
seam scalar) with every hand-computed value unchanged.
**D3 SHIPPED (`6e3a6c79`, 2026-07-24): the name fences DELETED down to the honest residue.**
Gone: `_BALANCE_PRODUCERS` + its allowlist, `_LOAN_RESOLVER_PRODUCERS` + allowlist (incl. N-17's
three dead entries), `_CONTEXT_LOAN_PRODUCERS`, and five of six seam-private W9909 rulings (N-31's
deletion) -- every deleted list was the fail-OPEN rot direction; what survives rots CLOSED.  The
residue, each named with reason and resolving step: W9906 keeps ONE surface (the two posting
readers -> deletes at E1 via the walk-seeding path) and W9909 keeps the completeness registry on
the public ingredient packages.  **D3's adversarial review measured two 10.00/10 holes the
deletion rationale missed, both closed in-commit as new W9909 scopes:** a public METHOD on the
publicly re-exported `BalanceContext` escaped every gate (`_context`'s ruling is the ONE
seam-private survivor -- W9910 cannot see attribute access), and `loan_payment_service` (the one
reader-allowlisted module) accepted a public reader wrapper unclassified.  Plus `loan_resolver`
scoped (B-12 closed fail-closed).  Controls fired RED on all four shapes.  **E1 is DECOMPOSED and
RULED (2026-07-24, four developer rulings, all as recommended -- see Phase E): E1a (the checked
projection + N-13's root fix) -> E1b (escrow into the reconcile) -> E1c (the walk-built view,
additive) -> E1d (the cutover; `confirmed_loan_view` dies) -> E1e (W9906 deletes; the readers go
package-private -- **amended at execution to "the readers are DELETED", see below**).  E1a SHIPPED
`545799fb` (B-5's invariant + N-13 closed; the born-settled create
door closed; the real Mortgage's pre-E1a cross-date residue self-heals); E1b SHIPPED `7cbc0271`
(all seven escrow write routes reconcile through the sync chokepoint; N-3 closed; merge's
"needs no reconcile" ruling reversed to "reconciles as an idempotent no-op"); E1c SHIPPED
`ee570bcf` (the walk-based `confirmed_view` builder reproduces the posting view byte-for-byte --
balance from the fold, rows re-accumulated over the visible subset; a broken loan FOLDS where the
reader returns `None` per B-12; the row construction extracted to a shared
`confirmed_amortization_row` emitter so byte-equality is structural; every-day oracle across nine
shapes + teeth + the broken-loan and N-11 divergence demos).  **E1d is DECOMPOSED (2026-07-25) on
the developer's option-D ruling and both halves SHIPPED (see below); **E1e SHIPPED `62cedd7d`
(2026-07-25), and NOT as its one-liner read.** The step said "the readers go package-private";
tracing it found they had ZERO callers in `app/` or `scripts/` -- E1d-b took the last one -- so
package-private would have kept ~197 lines of production code alive for its own test suite, the
dead-code shape C3b4 / D2a / F2 each deleted. The developer's ruling ("is there a way to make this
structurally unnecessary?"): DELETE them, moving the oracle window to `tests/_test_helpers.py`
(`posted_loan_balance_at` / `posted_loan_balance_map`). That removes the door rather than guarding
it -- with no public single-account balance-at-T producer outside the seam, a call allowlist has
nothing to guard, so W9906 deletes whole. Its replacement is STRONGER, measured: the two spellings a
consumer would write now rate E0611 / E1101, hard-gated by `--fail-on=E`. Dev clone BYTE-IDENTICAL.
**Found and NOT fixed: N-35** (`ledger_report_service` is not W9909-scoped; a public balance-at-T
born there measures 10.00/10). **NEXT: the X phase per Section 5** (F3 shipped `88c79857`). Per-step
detail below: E1d-a `35aae5ef` (the whole-loan read moves
INSIDE the seam as the private `balance_at._resolution`, and the resolution memo comes off
`BalanceContext` -- two hand-written fence surfaces DELETE, provably behaviour-identical) and E1d-b
`e0092d0e` (the cutover: every resolution and all three loan routes seed from the walk-built
`confirmed_view`; `confirmed_loan_view` + `confirmed_loan_history_rows` + four helpers delete;
W9906's allowlist tightens to one module, `loan_payment_service` goes ledger-free WHOLE and the
reconciliation suite's function-granularity fence collapses to a file fence; the E1c oracle retires
and all nine shapes plus the 13 moved row pins are re-anchored HAND-COMPUTED).  Dev clone
BYTE-IDENTICAL across both.  E1d-b also found **N-34** -- the split's rate/escrow still key on the
pay-period start, not the due date, contradicting D5 and C2's shipped claim -- recorded, gated with
a control that flips on the fix, and NOT fixed there (it moves recorded balances); **it is fixed at
C2b.**  With E1e shipped, **Phase E's E1 arc is CLOSED**; E2 stays a recorded option.


---

## 2. The loan problem, and the three root causes (live Section 1 as it stood)


The app answers "what is this account's balance at time T?" in many places, many ways, and the
ways disagree. On real data, today: the grid renders the Mortgage RISING by the full payment
each month while every other page shows it falling; the checking projection silently drops every
transaction you settle after your last balance assertion (so you re-assert the balance ~3 times
a week to force it back -- 44 times in under four months); and a loan configured before its
closing date will take five pages down with a 500 the day it closes, fired by the clock alone.

Underneath every one of those is the same three-part root cause:

1. **The honest balance function is PARTIAL.** `confirmed_loan_balance_at` answers `None` when
   the posting ledger has no opening and raises for future dates. A partial function cannot be
   the single source, so every caller composes it with something else -- a projection, a seed, a
   flag, a fallback -- and every composition is a new producer that can disagree with the others.
   All the arc's invented machinery (`is_originated`, `owed_from`, `projection_seed`, the
   past/future splice, "the two kinds of zero", `LoanLedgerNotOpenedError`, the 95-entry pylint
   fence) exists to manage that partiality.
2. **A derived cache is treated as the source of truth.** The posting ledger is a projection of
   facts you already store (loan params, anchor events, settled payments), but reads treat it as
   the truth, so a sync that did not run becomes data loss the app must lie about or die over.
   Worse, what the sync persists depends on the wall clock at the moment it ran
   (`_sync.py:139` -> the anchor drop in `_walk.py:356-358`).
3. **"A balance" has no type.** It is a bare `Decimal` anyone can compute, copy into a DTO,
   store in a column, or render in Jinja. The fence can only see calls; most leaks are reads
   (`LoanState.current_balance`, `AmortizationRow.remaining_balance`,
   `accounts.current_anchor_balance` -- the last is read in 15 modules and rendered raw in 3
   templates).

The cash side is the same disease one layer worse: the anchor (a user assertion) is the truth,
the projection adds only Projected items and DROPS settled ones, and the daily-series producer
disagrees with the scalar on most days of every period.


---

## 3. The foundation, and the loan fold (live Sections 2 and 3 as they stood)

The event grammar below is what shipped. The live document's Section 3 now states the CASH and
MODELED-ASSET grammar in this slot; the two are deliberately the same shape, and the loan one
came first because loans are the complete-data case.

### The foundation

All commit references for the arc live in this table so nothing else has to be consulted.

| shipped | where | reference |
|---|---|---|
| `balance_at` seam (one read surface, kind-correct dispatch) | prod | PR #45, 2026-06-27 |
| INTEREST-kind grid balance | prod | PR #47, 2026-06-28 |
| Double-entry posting ledger: transfers (Step 2), cash/envelopes (Step 3), loan REAL-split postings (Step 4) | prod | PR #48 2026-06-28; 2026-06-29; PR #51 2026-07-01 |
| Loan read switch (past reads ledger-authoritative) | prod | PR #52, 2026-07-02 |
| Actuals reporting (Step 5) | dev->main | PR #58 |
| `BalanceContext` (one resolution per loan per read pass) + `is_paid_off` off the ledger | dev | `b61aee9c`, `866e30b0`, `84c6e066`, `7b7c909b` (2026-07-13) |
| Fail-loud arc C1/C1b/C2/C2b/C3 (fixtures write through production's path; origination modeled; broken loan raises; context handle fenced) | dev | `2a88456c`, `603aea73`, `def3c8ff`, `9ea61f8a`, `fe77744e` |
| Phase A (A1/A2/A3 + the N-9 Schedule A fix) | dev | `f11382a0`, `c96c62be`, `4e46a0a8`, `44cbd028` |
| The loan fold: `loan_ledger` leaf (B0) + `fold_loan_balances` (B1) | dev | `d1586254`, `e227de08` |

**Verified, twice, independently:** the seam's loan answers are correct. A read-time fold over
source events (anchors + settled payments, the reader's visibility rule) matches the seam on
**every day of both real loans' history -- 212 days, zero mismatches** -- and reproduces the
developer-confirmed baseline to the cent (2026-07-14 review, re-verified from scratch
2026-07-16). The problem is everything around the seam, and the machinery keeping it correct.

**Baseline (as of 2026-07-16):** Mortgage (account 3) **$177,277.97**, Van Loan (account 8)
**$15,663.59**. Re-derive from the seam before and after every arc commit; if a commit moves
either, the commit is wrong. Do not trust prose figures older than their write date -- pin
oracles in tests.

### The loan fold

A loan's balance is a fold over its event stream. The fold is TOTAL: it cannot return `None`
and cannot raise; asked about a date before any event it returns `0.00` as the correct fold of
an empty prefix. That single property deletes the partiality and everything built to manage it.

```text
LoanEvent = (effective_date, seq, kind, status, payload)

kind   = ORIGINATION  balance := original_principal     (loan_params -- immutable)
       | ASSERTION    balance := asserted_balance       (loan_anchor_events -- append-only)
       | PAYMENT      balance -= split(cash).principal  (settled/projected transfer shadows)

status = ACTUAL (settled) | PLANNED (projected; effective = max(due, as_of + 1d) --
         "a plan cannot have already happened") | ESTIMATED (synthesized, no record)

balance_at(loan, T) = fold(events where effective_date <= T, ordered by (effective_date, seq))
```

* **One split function** (`loan_ledger.split_one_payment`, reused verbatim -- it lives in the
  fold's leaf since B0, and the posting writer imports it from there) divides ACTUAL and
  PLANNED cash alike; the cash the grid shows leaving checking is the cash the loan folds.
* **Predictions fill the gaps in the record -- in both directions -- and never where a record
  exists**: contractual back-projection before the first record (ESTIMATED tier, visually
  distinct); payment RECORDS within the materialized horizon (PLANNED); contractual synthesis
  beyond it (ESTIMATED). An installment with NO record behind it never happened and never pays
  the debt down (delinquency reads honestly).
* **Public API, three entries**: `positions(account, ctx, dates) -> {date: LoanPosition}`
  (balance + cum_principal + status -- serves the scalar, the map, series, principal-in-window);
  `plan(account, ctx) -> [PlannedPayment]` (carries NO
  balance; payoff date = `plan[-1].date`, derived, never stored); `events(account, ctx)`.
  **Interest-in-year is NOT on `positions()`** (C3c, developer ruling 2026-07-18): it keys on the
  DISPLAY-tz paid year (the tax clock) while every `positions()` figure keys on the UTC visible date
  (the balance clock), so it is a DEDICATED `balance_at.loan_interest_in_year` -- two clocks, two
  functions.
  `is_originated` / `is_retired` / `is_paid_off` are derived on demand, never copied into DTOs.
* **The posting ledger STAYS** -- as the general ledger (balance sheet, statements,
  attribution), not as the answer to "what do I owe." It becomes a checked projection:
  `sum(postings) == fold(ACTUAL events)`, asserted at WRITE time. A missing posting becomes a
  detectable, repairable cache inconsistency instead of an outage. The write walk loses its
  clock (the rule "posting early changes when the fact is RECORDED, never when it is SHOWN" is
  already written in `_walk.py:243-252` and already implemented on the cash side).
* **Structure replaces the fence** (as-shipped through D3, superseding this bullet's early
  wording): the engine cluster is private inside the seam package (D1d) behind the W9910
  package-privacy gate (D-gate); `LoanState.current_balance` is deleted (D2a);
  `AmortizationRow.remaining_balance` is RULED IN-PURPOSE, not deleted (D2 ruling 2026-07-24: a
  schedule row's declining balance IS the display artifact -- the amortization table's balance
  column, the payoff-scenario curves, and the D2-ruling ESTIMATED tier, which shows the CONTRACT
  on purpose and cannot use a fold-derived figure; no fence entry existed because of it);
  cash-flow and net-worth balances get NO distinct types (D2 ruling: nothing left for a type to
  defend, and no static checker exists to enforce one -- revisitable only as its own
  static-typing arc); W9905 deleted (C6b); W9906 deleted down to the two posting readers (D3) and
  then DELETED WHOLE with them (E1e) -- the structural end state is that no public single-account
  balance-at-T producer exists outside the seam, so a call allowlist has nothing to name.  What
  survives is W9909 (classification, fail-closed) and W9910 (package privacy, name-independent).
* **Cash is the same fold** (assertion events + transaction events), built AFTER the loan proves
  the machinery, because cash is the incomplete-data case: the anchor legitimately survives as a
  periodic reset (a bank-statement assertion). The instant-partition rule it needs (settle
  instant vs assertion instant) already exists in `account_posting_service/_walk.py` -- the
  projection engine is the one holdout that ignores it.

**Why not the minimal alternative** (just fix the write clock and keep the splice): it shares
steps A1-A3/C1 but keeps the partial function, the splice, the flags, and the fence -- the
measured defect generator (42 commits in three weeks, a new defect found in every commit's
review, all gates green throughout). The fold deletes the generator; the minimal fix feeds it.


---

## 4. The loan rulings (D1-D5, R-A, R-C, R-D, R-E)

Ruling **R-B** is NOT here: the cash projection consumes it directly, so it stayed live. Where a
surviving cash ruling cited one of these, the cited rule was restated inline at the citation, so
no live sentence depends on this file.

### Locked (developer rulings, 2026-07-14; re-examined and ratified 2026-07-16)

| # | ruling |
|---|---|
| D1 | An overdue installment with NO payment record pays nothing down. One with a Projected record projects normally, clamped to `max(due, as_of + 1d)`. Accepted simplification: a delinquent balance holds flat (no penalty interest) -- same as today. |
| D2 | Pre-tracking history is contractual back-projection, rendered as an explicit ESTIMATED tier. The visible step where the estimate meets the tracking-start assertion is honest; keep it. |
| D3 | A future payment uses PLANNED cash (the transfer's amount), plus a drift warning vs contractual PITI + `extra_principal` with a one-click "update the transfer" action. A deliberate overpayment never trips it. |
| D4 | The grid refuses amortizing accounts (picker + `?account_id=` + `PATCH /accounts/<id>/true-up` all gated on kind). A loan's balance is not a transaction sum. |
| D5 | ONE clock: origination on `origination_date`, an assertion on `anchor_date`, an ACTUAL payment VISIBLE on its settled date (R-A below). Ties: payments before anchors on the same date; anchors by `created_at`. The split inputs (ordering, rate, escrow) key on the DUE date -- contract time -- so out-of-order or late settlement can never re-split an installment; verified to move nothing on current data (period-start -> due-date windows contain no rate/escrow version change); gate it anyway. |

### Answered (developer ruling, 2026-07-16: all four as recommended)

| # | ruling | consumed by |
|---|---|---|
| **R-A** | An ACTUAL payment's balance event is VISIBLE on its **settled date** (`paid_at` civil date; due date is the fallback when `paid_at` is NULL). The split math stays keyed to the DUE date -- ordering, rate, AND escrow -- so out-of-order or late settlement can never reorder installments or re-split one (concretely: the July payment settled 2026-07-07, one day after the 07-06 escrow change; due-date keying keeps its escrow $616.99, where settled-date keying would move the split by $0.34 and the baseline with it). Rejected: due-date visibility, which double-counts ~$1,911 in net worth for the days between due and settle every month (real case: due 07-01, settled 07-07). The cash ledger already dates cash by `paid_at`, so loan and checking now move together. | C2 |
| **R-B** | The cash projection counts a settled transaction iff `COALESCE(paid_at, period start)` is after the latest anchor's `created_at` -- SHARED with the posting walk's existing rule, never copied. The archived X0 "post-anchor period" rule is dead: it double-counts on 15 measured real-data pairs. **Sharpened at R-F/R-H (2026-07-25): the comparison is INSTANT vs INSTANT, not civil date vs civil date** -- on prod the Checking anchor asserted 12:57:08 UTC and two expenses settled 13:07 the SAME UTC day, so a date-keyed partition would leave them invisible; and the sharing is STRUCTURAL (one walk, R-H), not one rule written twice. | X-a / X-c |
| **R-C** | The transfer write boundary REJECTS a loan payment dated before the loan's origination (root-cause fix; the measured case was $1,200 leaving checking with nothing recording it against the loan). Not modeled as a prepayment; not left documented. **Two corrections at C9 (2026-07-19), both measured:** (a) the boundary is `<=` origination, not "before" -- a payment due exactly ON the origination date sorts ahead of that anchor and is subsumed by its reset, erased identically ($0.00 principal, the whole cash to Refund); (b) the write boundary alone was NOT the root cause. The app's own loan-payment setup GENERATED the shape (3 of 4 payments pre-origination on a mortgage closing next month, $3,220.92 of phantom cash debits), so the guard shipped second, behind a recurrence start bound -- shipping it first was measured to 500 the loan's own create-transfer route. | C9a (generator), C9b (guard) |
| **R-D** | The year-end summary service and its tests are DELETED (dead code carrying B-7/B-10; `/analytics/year-end` already 302s). Rebuild on `positions()` later if ever wanted. ~~`_income_tax` survives -- the live Taxes tab uses it.~~ **Corrected 2026-07-16 (A2):** only TWO functions are live -- `_compute_mortgage_interest` -> `_loan_year_interest` -- reached because `tax_report_service.py:84` imports a PRIVATE name across packages, and their only real coverage sits in the file F2 deletes (`TestMortgageInterestGenesisHybrid`). Both die at **C3**, which deletes their input type; by F2 nothing is stranded, so F2 stays a clean whole-package deletion. If C3 has not landed when F2 runs, F2 must move the two functions to `tax_report_service` (their only caller) rather than leave the private import. | F2 (C3 first) |
| **R-E** (N-11; answered 2026-07-17) | A raw transaction typed onto a loan account (which moves the posted balance but not the fold) is **FORBIDDEN AT THE SOURCE**, not modeled as a third event kind. Every write path that could type one onto an amortizing loan refuses it on the D4/R6 predicate (`classify_account is AMORTIZING` / `has_amortization`): the two transaction-create routes, the recurrence-template form, AND -- found in the guard's own adversarial review, so BROADER than the plan's cited `create.py:78` -- the salary-profile account picker (which copies `template.account_id` into `recurrence_engine`). Rejected: a third event kind, which keeps a cash-basis paydown path the grid refuses to render and contradicts D4. This makes the fold complete BY CONSTRUCTION, so B2's every-day equality needs no N-11 exception. Any pre-guard row is an F1-class data item; the two real loans carry none (B1's 212-day match). | BG (guard), B2 |


---

## 5. Phases A-E, as built

### Phase A -- stop the bleeding, build the net (no model change)

- [x] **A1** `fix(accounts): a loan is not a cash account` -- **SHIPPED `f11382a0`
  (2026-07-16).** As built, two more doors than scoped: the Net Worth Cockpit's click-to-edit
  cell offered the CASH anchor editor on loan cards (the live UI door -- now read-only for
  amortizing kinds), and the full-form edit wrote anchors kind-blind (now refuses a CHANGED
  loan anchor).  Service guard (`AmortizingAccountAnchorError`) + route 422s + all three
  resolver steps + the settings picker.  Closes B-3 and B-15; residue recorded as N-4/N-5
  (the reset's balance-preserving re-anchor and the create factory's origination anchor).
  Verified live on the dev clone: `?account_id=3` resolves to Checking; the $1.00 Mortgage
  true-up is refused; baseline unmoved.
- [x] **A2** `test(loan): pin the forward walk's value, not two producers agreeing` -- **SHIPPED
  `c96c62be` (2026-07-16).** **As scoped, this step could not do its job, and the correction was
  the step.** Adding a paid shape to `test_every_loan_shape` does NOT make the
  `_forward_rows` `is_confirmed` filter's deletion
  visible: `_assert_agrees` compares the scalar to the map, and on the forward tail both sides
  are the SAME call, `_projected_owed_at(_forward_rows(schedule), p.end_date, projection_seed,
  owed_from)` (`net_worth_kernel.py:510` and `:995`) -- `f(x) == f(x)`, exactly the shape
  Section 7.2 forbids. **Measured 2026-07-16: with the filter deleted, all 7,401 tests pass.**
  Second, `balance_from_schedule_at_date` returns the LAST qualifying row's `remaining_balance`
  rather than subtracting principal, so the filter changes an answer ONLY between `ctx.as_of`
  and the first UNCONFIRMED row's due date; every future period end-date the matrix probes
  (04-09, 04-23, 05-07, 05-21) lands past that window, where it is a measured no-op. So: pin
  the VALUE inside the window on a paid-then-trued-up loan (new
  `TestForwardWalkExcludesLedgerBookedRows`; the fixture measures $48,496.25 -- the delta is
  `last_confirmed_row.remaining_balance - projection_seed`, unbounded in the true-up's size).
  Still add the paid shape to the matrix (Section 7.4; its BEGUN half IS a real two-reader
  check) and fix the "every loan shape" overclaim. Also B-21 (assert the value, not
  `is not None`) and an independent hand-computed oracle for the LIVE Taxes number -- its only
  live-path test spends `_compute_mortgage_interest` as its own oracle, so a double-count ships
  green (shown to fire). NOT done: a negative control for `_loan_year_interest`'s
  `not row.is_confirmed` -- it is unreachable by construction (N-6). Building that oracle
  surfaced a LIVE tax defect off the arc's path, fixed in its own commit (N-9, `44cbd028`).
- [x] **A3** `fix(loan): the ledger records what is KNOWN; the readers decide what has
  HAPPENED` -- **SHIPPED `4e46a0a8` (2026-07-16).** The clock is out of the loan write walk
  and G1 is closed. **"The readers already bound by visibility" was FALSE, and correcting it
  was the step.** Two regressions measured from naively posting every anchor: (1) an anchor's
  read bound is `LEAST(entry_date, pay_period.start)` (`_asof.effective_date`) -- a
  period-START rule, so a FUTURE anchor resolves to a PAST date and a loan originating INSIDE
  the current period read its full principal as owed TODAY ($200,000.00 five days early),
  trend contradicting hero; (2) `confirmed_loan_view` stopped returning `None`, and the
  ledger's honest `0.00` for a loan that does not exist yet seeded `_build_forward_inputs`,
  collapsing a 360-row schedule to **ZERO rows** (payoff = origination date, $200,000 flat
  forever). Root cause of both: the map and the view INFERRED "not originated" from the
  ledger's SILENCE -- an inference that held only because the clock forced it. Fix: both ask
  the FACT (`origination_date`), as the scalar `amortizing_balance_at` already did and
  documented. `confirmed_loan_view` now takes `params` (all 4 callers already held it: no
  re-load, and the origination cannot be mismatched to the account).
  Also: `loan_balance_anchor_history` applies the display bound to the walk's OUTPUT;
  `_test_helpers`' future-anchor guard deleted (its reason is gone); `create_baseline`'s body
  moved to a new `baseline_service` (grid.py sat at exactly 1000/1000 lines and the route was
  orchestrating two posting packages; `scenario_resolver` cannot host it -- both packages
  import it, so pylint reports R0401). Every control shown to fire. Residue: **N-10**.
  **N-8 is NOT closed: it was misattributed** (see its row). NOTE: a second write-path clock
  read remains by design -- the settle-time freeze resolves P&I as of today
  (`loan_payment_service.py:762`); D3's drift warning is what surfaces it (C7).

### Phase B -- the fold, as an oracle only

- [x] **B0** `refactor(loan): the walk is a leaf, not the ledger's private` -- **SHIPPED
  `d1586254` (2026-07-17). Not in the original plan; B1 could not be written honestly
  without it.** The fold ALREADY EXISTED as `loan_posting_service/_walk.py` -- a private of
  the GENERAL ledger, which is backwards: E1 makes the postings a checked projection of the
  fold, which is only expressible if the fold is the leaf they derive FROM. It priced itself
  too: B1's recipe below needed FOUR private names out of that package (the R-D smell), and
  both prototypes reached through them. Now `app/services/loan_ledger/` owns the walk
  (`_split`, `_events`, `_fold`); the posting package imports it; the rewritten probe needs
  zero private imports. Pure move (AST-identical modulo renames), 7,410 green. Also killed a
  live duplicate: `_settled_income_shadows` existed TWICE (`loan_loaders` unordered, `_walk`
  sorted), each claiming to be the single derivation -- now one public
  `loan_loaders.settled_income_shadows`.
- [x] **B1** `feat(loan): the loan ledger answers a date -- one fold over one event stream`
  -- **SHIPPED `e227de08` (2026-07-17).** `fold_loan_balances(loan, scenario, dates)`, folded
  from SOURCE events, reading the postings table never; TOTAL (any date, any account, a
  `Decimal`; `0.00` for the empty prefix; no `None`, no raise). Matches the seam on every day
  of both real loans. **Three amendments to this step as written, all forced by the code:**
  (a) **`_plan` deferred to C3** -- B2's oracle can only target the PAST, since the seam's
  future answer is B-9 (overdue installments paying down debt nobody paid) and proving the
  fold reproduces it would be proving a defect; C3 needs the plan because it deletes both
  forward producers. (b) **No `BalanceContext` memo** -- the memo is a production-path
  optimization and B1 is not on it; C3 adds it when a read pass would otherwise re-walk per
  date. (c) **`_split` is NOT a re-export module** -- it OWNS `split_one_payment` (see B0).
  The recipe above also omitted two inputs the working prototypes needed
  (`merge_anchor_and_payment_events`, and the anchor's pay-period resolution), and named the
  visibility rule without noting it is the WRITER's period assignment reproduced from source.
  **Its own review found the trap that mattered**: the fold TOOK a period list, which was not
  an alignment but its only divergence vector -- a window (the shape the grid passes, and the
  shape C3 hands the seam's AMORTIZING branch) moved the balance $150,000.00. The parameter
  is gone; the fold and the writer now share one `owner_pay_periods` query.
- [x] **BG** `fix(loan): a loan's balance is not a transaction sum` -- **SHIPPED
  `dba91dc0` (2026-07-17).** R-E's forbid-at-source guard, pulled ahead of B2 because B2's
  completeness premise (no raw loan transaction can exist) rests on it. Three sources gated
  on the D4/R6 predicate: `_reject_transaction_on_loan` on both transaction-create routes;
  the `_validate_template_form` kind check on the recurrence-template form; and the
  salary-profile picker, which now deposits via the shared
  `account_service.active_accounts_query(amortizing=False)` composer (no inline copy). **The
  template and salary sources are BROADER than the plan's cited `create.py:78`** -- found
  tracing every `TransactionTemplate` build and `generate_for_template` caller; the salary
  source was caught in adversarial review (a loan as the user's first active account made the
  auto-picker post salary income onto it). Read-inert (no read path, no data touched), so the
  baseline cannot move; every guard's control is shown to fire (the salary control fails when
  the `amortizing` filter is flipped). Closes N-11 at the source.
- [x] **B2** `test(loan): the reference fold is the oracle, and it is exhaustive` --
  **SHIPPED `8f070386` (2026-07-17).**
  parallel-run fold vs seam on **EVERY DAY** of every loan's domain, over generated shapes
  (including A2's paid shapes) plus real data. **Sampling is forbidden**: a 14-day sample once
  scored perfect while wrong by $178,103.41 on 22% of days. Every divergence is explained and
  signed off, never silenced. **B2 gates all of Phase C.**
  **What B2 does and does not prove, stated precisely (B1 made this concrete).** The fold and
  the posting readers SHARE the walk -- by design, since Section 3 reuses one split function
  and E1 makes the postings a projection of the fold. So a fold-vs-seam equality is NOT an
  independent proof of the split's VALUE; it proves the posted cache faithfully projects the
  fold on every day, which is exactly what C3's cutover needs and exactly E1's invariant
  checked at read time. The split's value is pinned elsewhere and stays there: the Step-4
  reconciliation oracle's parallel run against the un-seeded resolver, A2's hand-computed live
  Taxes oracle, and B1's hand-computed fold figures. Do not let B2's equality be mistaken for
  the correctness proof; it is the equivalence proof. **Required shapes** (Section 7.4): the
  A2 paid shapes, a tracking-start import, an ARM step, escrow, a payoff overpayment, a
  pre-anchor payment, a late-settled payment whose period does not contain its due date, and
  **N-11's raw-transaction-on-a-loan shape -- now closed by construction (BG)**: B2 both
  demonstrates the divergence is real (a forced raw transaction moves the reader by its amount
  while the fold holds) and asserts the create route refuses the only user path, so the
  every-day equality needs no N-11 exception. Each shape carries a realization assert (the
  ARM rate, the escrow slice, the payoff reaching zero) so a feature no-oping in BOTH
  producers cannot pass green, and a negative control proves the harness fails on a forced $1
  divergence.

### Phase C -- the cutover (order is load-bearing)

- [x] **C1** `fix(loan): a loan's origination is an event, not a footnote` -- **SHIPPED
  `18fd3a04`.** ORIGINATION is ALWAYS the loan's opening (SYNTHESIZED from params, not "a row
  excluded"); a `tracking_start` is an ordinary `is_opening=False` ASSERTION that RESETS the walk
  and now STACKS like a true-up (the deleted `_opening_anchor_fact` supersession was the B-11
  mechanism). The idempotent deploy backfill re-instates reversed openings -- no migration.
  **Today's balance UNMOVED** (verified to the cent on both real loans, held by B2); the only
  movement is a pre-tracking date reading the origination principal FLAT (the plateau) instead of
  $0 -- aged out of the /savings render window, so B-11 is closed at the producer. Closes B-22.
  Display: "Tracking start" badge + Origination/tracking-start rows on the Balance anchors card.
  B2's tracking-start shape pins the plateau ($250k flat, drift -$150k).
- [x] **C2** `fix(loan): one clock -- an event happens on the date it happened` -- **SHIPPED
  `eb5de4ac`.** R-A (settled-date visibility; NULL-`paid_at` falls back to
  period-start).  **"due-date split keying" is CORRECTED OUT of this line (2026-07-25, measured at
  E1d-b): only ORDERING moved to the due date.  The split's RATE and ESCROW still keyed on the
  pay-period START -- see N-34, which carries the measurement; **C2b below is where the claim
  becomes true.** **The one clock IS the posting's `entry_date`:** readers bound by
  `entry_date <= as_of`, the fold reproduces it via one shared `to_utc_civil_date` the writer
  also calls -- fold == reader by construction. Deletes `_asof.py`; moves
  `confirmed_loan_balance_map` to period-END keying; the fold is now calendar-INDEPENDENT
  (dropped `owner_pay_periods` and its no-periods raise -- more total). Closes the N-10 leak at
  source and N-12; the four N-10 guards are NOT all retired here (#1/#2 at C3b,
  `confirmed_loan_view`'s stays for B-1). History repositions in bounded windows, today unchanged;
  signed off via B2 + full suite (7,446 green, pylint 10.00) + adversarial review. Recorded, out
  of scope: **N-13**.
- [x] **C2b** `fix(loan): the split's rate and escrow key on the installment, not the pay period`
  -- **SHIPPED `c2d43332` (2026-07-25).**  Finding N-34's fix, sequenced AHEAD of the F3 prod ship
  (developer ruling 2026-07-25).  Ships out of numeric order, and the number is the point: C2
  recorded "due-date split keying" as shipped when only ORDERING had moved.  (Not to be confused
  with the ARCHIVED fail-loud arc's own "C2b" in the Section 2 table -- different arc, different
  numbering; this is the only C2b in the balance arc's step list.)  **Why now, measured:** on PROD data the only rate/escrow
  versions are dated 2018-12-01 / 2023-02-14 / 2023-12-01 and NONE falls inside any payment's
  period-start-to-due-date window, so the re-key is a provable no-op on real data today -- while
  after the next mid-window escrow or rate edit (a shape the dev clone already carries) the same
  fix would move recorded balances, the posted ledger, and the Schedule-A figure.  Shipping it
  before F3 also means the deploy rewrites prod's genesis ledger ONCE, not twice.  Full suite 7504,
  pylint 10.00 on all three trees, 146 checker tests.
  **The scope is SIX sites, not the two the finding named, and re-tracing that is the step.**  The
  finding cited `_split.split_one_payment` (rate) and `_walk._replay_events` (escrow); the trace
  adds the PLANNED forward tier (`balance_at._plan._planned_from_shadows`, rate AND escrow), the
  live-cash derivation (`loan_payment_service._shadow_live_amount`, feeding BOTH the projected
  display override and the settle-time freeze), the resolver's escrow subtraction
  (`prepare_payments_for_engine`), and the escrow forward-only GUARD.  The live-cash site is not
  optional: the derive-mode cash a payment CARRIES is built from escrow and the split BACKS THAT
  OUT, so re-keying one end alone would silently move the difference into PRINCIPAL -- the
  cash==split invariant (escrow spec Sec. 1) forces them to move together.
  **The guard is the step's other half, and it is a real hole the re-key would otherwise open:**
  the boundary was the latest settled payment's PAY-PERIOD START, so once the split reads the due
  date an escrow version effective between the two would silently re-split a settled payment.
  `latest_settled_payment_period_start` is DELETED for `latest_settled_payment_due_date`, built on
  the SAME `_settled_payment_due_dates` derivation the tracking-start guard reads -- so the guard,
  the walk, and the tax figure now share one statement of a payment's date instead of two.
  **NOT moved, deliberately: `rate_period_engine._replay_from_anchor`'s rate lookup (N-36)** -- it
  consumes REDISTRIBUTED records whose `due_date` may be an invented collision-shifted date, so
  coupling its balance to that date trades one defect for another; its rows and balance are
  discarded whenever a `confirmed_view` is supplied, which is every production read since E1d-b.
  Verification: six sites, six firing controls, each shown to fire by reverting its site (two of
  them added after the adversarial review measured that `_plan` and `prepare_payments_for_engine`
  could be reverted with the whole suite green).  On a fresh PROD clone the full deploy sequence
  passes and every figure is BYTE-IDENTICAL to the pre-fix rehearsal -- balances, every-day
  history, rows, payoff, tax figures, and the posted per-date nets.
- **C3 (DECOMPOSED, 2026-07-18)** `the seam's AMORTIZING dispatch is the fold` -- too large as
  one revertable commit and reached into dead year-end code, so it ships C3a -> F2 -> C3c -> C3b,
  each a REFACTOR (baseline unmoved; B-9 preserved until C6). F2 (Phase F) is pulled AHEAD of C3b
  so the deletions run on a live-only surface.
  - [x] **C3a** `feat(balance): positions() -- one loan producer, fold past and projection future`
    -- **SHIPPED `df775017`.** `balance_at.positions(account, ctx, dates)`: the FOLD
    (`fold_loan_balances`) for a past date on an originated loan, the schedule projection
    (`forward_balance_at_date`, seeded from `generate_debt_schedules`) after -- or ALL dates for a
    loan not yet originated -- split on the shipping scalar's own boundary. **The PAST reads the
    FOLD (source events), not the postings** -- the cutover's heart, B2-proven equal. ADDITIVE and
    unwired (only its oracle calls it); reproduces `amortizing_balance_at` on EVERY day past AND
    future (`test_loan_positions_oracle.py`, 3 tests + teeth), so C3b moves no money by proof. Lives
    in `balance_at`, NOT the `loan_ledger` leaf: the preserve-behaviour forward half needs the
    resolver schedule + seed (W9906-fenced to the seam/kernel, and `net_worth_kernel` is at the
    1000-line cap), so composing fold + resolver is a SEAM job -- it moves to `loan_ledger` at C6
    when fold-native. Reuses `generate_debt_schedules` + `forward_balance_at_date` (DRY), deletes
    nothing.
  - [x] **C3c** `refactor(analytics): interest-in-year is balance_at.loan_interest_in_year` --
    **SHIPPED `99cc2816`.** A DEDICATED seam producer, **NOT `positions().cum_interest`** (developer
    ruling 2026-07-18): the tax figure keys on each payment's DISPLAY-tz civil paid year (the L9
    rule), while the fold/balance keys on the UTC visible date -- a settle 8:05pm EST Dec 31 folds as
    Jan 1 (UTC) yet deducts in the OLD year, so a UTC-keyed `cum_interest` sampled at year-ends
    mis-years it. Two clocks, so interest gets its own function. It folds each settled payment's
    actual interest by its display paid year and adds the schedule's unconfirmed rows for the future.
    Points the live Taxes tab at it; deletes `_compute_mortgage_interest` / `_loan_year_interest` and
    the `is_confirmed` guard (subsumed by reading only unconfirmed rows). **The `settled_due_months`
    de-dup STAYS** -- the plan's "delete both guards" was wrong (corrected 2026-07-18, probe-measured):
    while the future is schedule-row-driven a settled-but-unconfirmed (early-settled) installment is
    in BOTH halves, so dropping the exclusion double-counts it (**+$489.97** measured). It is relocated
    INTO the producer, derived from the SAME fold walk (non-drift); its STRUCTURAL deletion moves to
    C6. Grade on A2's hand-computed live oracle, never the producer as its own oracle (N-7). Closes
    B-6.
  - **C3b (DECOMPOSED, 2026-07-18)** `the seam's AMORTIZING dispatch reads positions()` -- the cutover
    proper, split into FOUR independently-green commits mirroring C3a (developer ruling). Two facts
    forced the split: C3a's oracle proved only the SCALAR equals `positions()` (the per-period MAP has
    NO equivalence proof yet), and `positions()` lives in the seam ABOVE `net_worth_kernel` (at its
    1000-line cap) which cannot import it back, so the map branch must MOVE INTO the seam -- not a
    one-line redirect. **The plan's original single-commit deletion list was the END-of-C3 (post-C6)
    state and is corrected here**: `positions()` (C3a) still consumes `generate_debt_schedules`,
    `DebtSchedule` (`schedule`/`projection_seed`/`owed_from`), `_projection_seed`, and
    `forward_balance_at_date`, and the savings trend history-gate consumes `generate_debt_schedules`
    via `debt_schedule_rows` -- so ALL of those survive to C6, NOT C3b. Of "both forward producers"
    only the MAP's `compute_forward_loan_period_balance_map` dies at C3b; `forward_balance_at_date`
    lives to C6.
    - [x] **C3b1** `refactor(balance): the scalar and the liability band read positions()` --
      **SHIPPED `f410afa9`.** The seam's SCALAR (`balance_at.balance_at` AMORTIZING branch) and
      LIABILITY band (`liability_owed_at_dates`) read `positions()`. A new `BalanceContext.loan_walk`
      memo walks each loan's ledger ONCE per read pass and `fold_from_walk` samples it, so the
      scalar/map/liability folding one loan in a render do not each re-walk it (the redundant-fold DRY
      fix C3a earmarked). `fold_from_walk` is the shared sampling core, and B2's oracle subject
      `fold_loan_balances` DELEGATES to it -- so the every-day oracle grades the exact code production
      runs, not a copy (adversarial-review catch, fixed pre-commit). Deletes `amortizing_balance_at` +
      `loan_owed_at_dates`. The scalar cutover
      is proven by C3a's every-day oracle (RETIRED here, its job done -- ongoing proof is B2 + the
      seam's own tests); the liability forward path is the IDENTICAL `forward_balance_at_date` call, so
      the band cannot move. **Behaviour change (approved 2026-07-18): the SCALAR now FOLDS a broken
      loan (originated, no opening posting) instead of raising `LoanLedgerNotOpenedError`** -- the fold
      reads SOURCE facts, so a cold posting cache is a repairable inconsistency (E1), not a read-time
      outage; the broken-loan test flips from expects-raise to expects-$240,000. The MAP still raises
      until C3b3. Baseline UNMOVED on both real loans (C3a oracle + B2 + full suite 7367). Also fixed
      a PRE-EXISTING dev-only checker failure `test_classification_sets_match_the_real_fenced_modules`:
      C2 (`eb5de4ac`) deleted `_asof.py` but left `effective_date`/`scope_to_linked_ledger` in the
      loan-ledger non-producer set (stale fence entry; uncaught because dev is not CI-gated).
    - [x] **C3b2** `test(balance): the positions-based per-period map is proven equal` -- **SHIPPED
      `28f8fe51`.** `positions_period_map` samples `positions()` -- begun periods at
      `min(period.end, ctx.as_of)`, future periods at `period.end` -- reproducing the splice's
      `period.start <= ctx.as_of` boundary; additive and unwired (only its oracle calls it). The
      current-period clamp is the subtlety B2's scalar proof did not cover: `positions([period.end])`
      would hand the current period to the projection, moving it whenever a payment falls between today
      and period end. **Caller-trace VERIFIED: the per-period map is NEVER read with
      `ctx.as_of != today` in production** (every map caller builds `BalanceContext.build(user_id)` =
      today; the one explicit-`as_of` site is the Taxes tab, which reaches `loan_interest_in_year`, not
      a map), so the clamp reproduces `_build_amortizing_balance_map` exactly -- the historical-`as_of`
      case where the two would diverge is unreachable. The every-period oracle
      (`test_loan_positions_period_map_oracle.py`) parallel-runs vs the shipping `balance_map` over four
      shapes (trued-up + payments, tracking-start plateau, payoff, not-yet-originated) plus a forced-$1
      teeth test, and proves the clamp load-bearing on TWO shapes -- including a loan originating INSIDE
      the current period reading `0.00` not its opening (the N-10 shape, added beyond the plan).
      Adversarial review clean (equivalence correct; the no-posting-after-today invariant the clamp
      rests on confirmed ENFORCED -- server-set `paid_at`, `anchor_date <= today`, the `owed_from`
      gate). Deletes nothing.
    - [x] **C3b3** `refactor(balance): the per-period map reads positions()` -- **SHIPPED
      `84e386c6`.** The map dispatch MOVED into the seam's `_account_balance_map` (the kernel cannot
      import `positions()`), pointed at C3b2's `positions_period_map`. Deletes
      `_build_amortizing_balance_map`, the kernel's AMORTIZING branch + its now-dead `debt_schedule`
      param, `splice_confirmed_and_projected_loan_balances`, `compute_forward_loan_period_balance_map`,
      `_loan_ledger_not_opened`, `LoanLedgerNotOpenedError`, the two-zeros doctrine. **`confirmed_loan_balance_map`
      is KEPT, correcting the plan's deletion list** (developer ruling 2026-07-18): it reads the KEPT
      posting ledger and is the Step-4 reconciliation oracle's independent window, so deleting it would
      gut that oracle; its fate is decided at E1. All four cutover hazards handled: (1) the
      `account.id in inputs.debt_schedules` gate degrades an unconfigured Mortgage to cash, not
      `positions()`'s fail-loud; (2) the map FOLDS a broken loan (E1 decision, mirrors C3b1 -- B-8
      closed at the map); (3) the `_inputs -> _positions` import cycle broken by importing
      `require_scenario` from `resolution_context` in `_positions`; (4) the `current_anchor_period_id is
      None -> None` guard preserved in the moved branch. **Broader than scoped** (traced): the C3b2
      oracle RETIRED (tautology after the cutover, mirroring C3b1 retiring C3a's); `TestScalarAndMapAgree`'s
      docstring corrected (both producers read `positions()` now -- a sampling-consistency check);
      `baseline_service` + the G1 `test_grid` narrative updated (the fold retired the read-outage; G1's
      now-vacuous `balance_at` assertion moved to the posting reader since it folds regardless); the
      W9905/W9906 checker sets + tests repointed off the deleted forward map onto `forward_balance_at_date`;
      a seam-level future-value pin restored the after-payment coverage the retired savings dispatcher
      unit tests carried. Baseline UNMOVED (dev-clone live-render + B2 + full suite 7360, pylint 10.00,
      adversarial review clean).
    - [x] **C3b4** `refactor(balance): delete the dead ledger-domain readers` -- **SHIPPED
      `5c62c995`.** Deleted the seam `loan_ledger_domain`, the reader `confirmed_loan_ledger_domain`,
      `LoanLedgerDomain`, and the private `_confirmed_loan_ledger_start` (0 production callers since F2
      deleted the year-end summary -- whole-repo grep confirmed), plus the fence entry, both packages'
      exports, and their tests. The shared `_is_originated` STAYS (still
      `loan_figures`/`is_retired`/`is_paid_off`). **The plan's "delete `_domain.py`" was too broad, and
      correcting it was the step:** that module ALSO held two load-bearing PRIVATE query helpers the
      KEPT readers build on -- `_has_opening_posting` (the configured-loan sentinel
      `confirmed_loan_balance_at`/`_map` and `_display` guard on) and `_visible_nets` (the grouped
      per-date load `confirmed_loan_balance_map` prefix-sums). So `_domain.py` was RENAMED (`git mv`) to
      `_linked_ledger.py`, stripped to those two helpers (kept VERBATIM) with a rewritten module
      docstring; `_reader.py`'s import repointed, `_display.py` untouched (it takes the helper via
      `_reader`). The deleted seam function's now-orphaned `_require_scenario` import went with it.
      Baseline cannot move (no read path touched); full suite 7357 (= 7360 - 3 deleted domain tests),
      pylint 10.00, the fence classification-completeness guard green, adversarial review clean (its one
      catch -- a gate-invisible orphaned test import, `tests/` being outside the pylint gate -- fixed
      pre-commit). Closes the C3b arc.
- [x] **C4** `fix(loan): the loan page reads the seam like everyone else` -- **SHIPPED
  `c98ea07b`.** The loan ROUTE rendered its balance from the money-blind anchor replay for a broken
  loan (B-13); it reads the seam now. **Scope corrected 2026-07-18 (developer ruling), on two counts
  the one-liner got wrong:**
  (1) **`LoanState.current_balance` was NOT deleted here** -- beyond the route's reads, the field is
  still consumed by TWO in-cluster readers: `net_worth_kernel._projection_seed` (the seed for
  `positions()`'s FORWARD projection -- `positions()` is its only reader) and
  `balance_at._loan_figures._is_retired`. Both equal the fold for an intact loan today, so deleting
  the field means making the seam's forward SEED fold-native, which belongs where `positions()` goes
  fold-native (C6-adjacent). C4 KEPT the field and read the ROUTE off the seam (mirrors C3b3's
  KEEP-correcting-the-deletion-list); the field dies in its own later commit.
  (2) **The migration was the WHOLE loan route package, not "7 reads"** (developer ruling: full, not
  surgical): reading only the balance off a new `BalanceContext` while the route still resolved a
  private `LoanState` for the payment/rate/schedule would resolve each loan TWICE per request -- the
  redundant derivation the arc exists to kill. So the route DROPPED `resolve_loan_seeded` entirely and
  reads through ONE `BalanceContext`: balance from `balance_at.balance_at`, payment/rate/payoff/arm from
  `loan_figures`, schedule from the composer it already runs (`build_baseline_scenarios`'s
  `history_rows + committed_forward` == the dropped `LoanState.schedule`, same `compute_payoff_scenarios`
  call, reviewer-verified). Touches `dashboard`/`calculators`/`schedule`/`escrow_rates`/`payment_transfer`/`_helpers`.
  **Broader than scoped in three places** (found building it + adversarial review): the standalone
  SCHEDULE route is a TABLE, not a balance surface, so it does NOT read the seam -- it composes ONCE via
  a new shared `load_baseline_scenarios` helper and reads its rate off a new cheap
  `loan_resolver.current_rate_baseline` accessor (proven `== resolve_loan(...).current_rate`), not a full
  resolve (else it derived its schedule twice); the refinance paid-off gate now reads the seam balance
  once and drops the redundant `not state.schedule` half (an empty committed schedule implies a zero
  balance; its only divergent case -- a past-term balloon still owing -- is better served by a comparison
  than blocked); the stale `_secured_debt.py` docstring is corrected (the seam FOLDS a broken loan since
  C3b1, no longer raises). A no-baseline user cannot reach a configured loan (baseline created at
  registration), so the seam's `require_scenario` fail-loud is unreachable here -- matched to
  `debt_strategy`, deliberately not guarded. Baseline UNMOVED on real data by proof (intact loans: fold
  == the replay, by B2; `test_cross_page_balance_equality` green); a new route test pins the fix -- a
  broken loan's page renders the fold `$231,200.00`, never the money-blind replay `$239,761.08`. Full
  suite 7359, pylint 10.00, adversarial review clean (its Medium -- the schedule double-compose -- and
  4 Lows all fixed pre-commit).
- [x] **C5** `fix(accounts): the equity chart's debt line is the fold` -- **SHIPPED
  `821dd0eb`.** The chart derived each month's debt from the resolver's CONTRACTUAL schedule rows
  (`remaining_balance`), which advance one installment whether or not the borrower paid it; it
  disagreed with the equity hero (the fold) on eight of thirteen shapes by up to $299,701.35 (B-2).
  It now folds: `SecuredLoanSeries` drops its `back_projection`/`schedule` row lists for a tiered
  `month_balances` map, and the seam samples `balance_at.positions()` once per calendar month -- the
  fold of actual cash at or before today, the same forward projection after. **"The debt line is
  the fold" is the CONFIRMED and PROJECTED tiers; the pre-tracking ESTIMATED contractual
  back-projection STAYS (ruling D2)** -- the fold holds a flat plateau there, not the declining
  curve the loan amortized unseen. Sampling uses the per-period map's begun/future rule, extracted
  here to a SHARED `_positions.window_sample_date` so the map and the chart cannot drift on the
  boundary C3b2 proved load-bearing: a begun month reads `min(month end, as_of)`, so the CURRENT
  month reads today's fold and the chart reconciles with the hero AT today -- closing the M1 gap
  (today's month was `projected` and one payment low; now `confirmed` and equal). The producer
  loses `_loan_month_tiers` (schedule-row derivation) and `_dense_month_balances` (gap-fill,
  unnecessary once the fold samples every month), and `_build_axis` spans `min(origination, today)
  .. max(payoff, today)`, retiring the "defensive" `today_index` clamp (the mechanism that clamped
  a not-yet-originated mortgage's principal onto today, $299,701.35). The empty-schedule clip is
  gone: an empty schedule draws NO back-projection, so FU-8's phantom contractual walk cannot
  recur. **Two sub-points decided this session, mirroring C3b/C4's deletion-list corrections:** a
  RETIRED loan is still DROPPED, not charted with its history (developer ruling: C5 stays a pure
  B-2 fix; retired-history is a later feature); and a mid-life import's ORIGINATION month reads the
  fold's recorded opening principal tagged `confirmed`, NOT `estimated` (developer-ratified: the
  opening is a hard fact, and it renders inside the dotted segment regardless; pinned by a test).
  Dev-clone live-render UNMOVED: the real Mortgage reconciles to the baseline $177,277.97 at today
  (== hero == `positions`), axis Dec 2018 (origination) .. Dec 2048 (payoff), tiers confirmed
  opening $202,000 -> estimated back-projection -> confirmed tracking (Apr 2026) -> projected,
  contiguous, no gaps. Full suite 7359, pylint 10.00, adversarial review clean (no
  Critical/High/Medium; two Low fixed pre-commit -- the shared-helper DRY extraction and the pinned
  origination-month tier).
- **C6 (DECOMPOSED, 2026-07-18)** `feat(loan): a plan is payment RECORDS, not schedule rows` (D1) --
  the forward projection stops walking the resolver's contractual `AmortizationRow` list (which
  amortizes one installment per month whether or not a payment was ever recorded -- B-9 / FU-7) and
  folds over payment RECORDS instead. **Three developer rulings (2026-07-18, recommendations
  ratified):** (1) the forward model is a UNIFIED `plan()` fold -- `plan(account, ctx)` returns ONE
  effective-date-ordered record list, PLANNED (the projected transfer shadows, at their LIVE D3 cash)
  then ESTIMATED (contractual synthesis for every future installment slot no record covers, to
  payoff), and `positions()`'s forward branch folds the confirmed-present seed forward over it
  (`balance_at(loan, T) = fold(events <= T)`, Section 3); NOT a past/future splice, NOT a
  keep-the-schedule record-gate. (2) the loan-detail `interest_paid_ytd` chip stays "paid YTD" but is
  sourced from the FOLD's settled splits (fold and posting reader agree by construction), not
  repointed to the full-year `loan_interest_in_year`. (3) ship DECOMPOSED, additive-first (mirrors
  C3a -> C3b). **Two scope corrections found tracing the code** (the recurring "the deletion list was
  the end-state" pattern): (a) **`AmortizationRow.remaining_balance` does NOT die at C6** -- beyond the
  forward balance it is read by the loan-detail payoff-scenario chart (`_helpers.py:360`), the
  schedule display TABLE (`_schedule.html:71`), the D2 back-projection (`_secured_debt.py:178`, KEPT),
  and the write-side payoff sync that bounds shadow generation (`loan_recurrence_sync.py:67`); its
  deletion belongs with C8's payoff derivation plus a later schedule-table migration. Only
  `_forward_rows` and `balance_from_schedule_at_date` (positions()-only) die at C6. (b) the ESTIMATED
  tier is MANDATORY: projected shadows exist only within the materialized pay-period window (~2y,
  capped at payoff), but the equity chart samples `positions()` monthly to PAYOFF (30y) -- a
  records-only fold would FLATLINE the debt line beyond ~2y, regressing C5. Constraint (not a fork):
  PLANNED events depend on `as_of` (the `max(due, as_of + 1d)` clamp), so they live in the READER
  (`positions()`), never in the clock-free `walk_loan_ledger` fact-walk. B-9's baseline move is narrow
  -- an installment due at or before `as_of` that has not settled stops reducing today's balance
  (the past is ACTUAL-only); a still-planned overdue one clamps its record forward to `as_of + 1d`;
  normal future amortization and beyond-horizon synthesis are unchanged. **Two sourcing rulings
  (2026-07-18, recommendations ratified):** (i) the PLANNED tier folds each projected shadow's LIVE
  D3 cash (`live_loan_transfer_amounts` = P&I + current escrow + `extra_principal`), NOT its stored
  `effective_amount` that the current forward amortizes -- so the loan balance and the checking side
  move together. This makes C6b a TWO-reason baseline move (B-9's overdue-gate AND the stored->live
  cash correction, each reconciled and signed off separately), and NARROWS C7 to the drift WARNING +
  one-click sync (the loan already gets the planned payment). (ii) the ESTIMATED tier sources each
  future no-record installment's (date, contractual P&I, rate) from the existing
  `loan_resolution.contractual_schedule_from_origination` (already shared with the D2 back-projection)
  and re-folds the balance -- never reading its `remaining_balance` -- so it inherits the engine's
  exact first-payment-date / term convention (no divergence) rather than re-implementing installment
  stepping.
  - [x] **C6a** `feat(loan): plan() -- the unified PLANNED + ESTIMATED payment record stream` --
    **SHIPPED `31e00413`.** Additive and unwired (baseline unmoved), graded on a HAND-COMPUTED
    forward oracle, NOT an equivalence-to-current oracle (that would prove B-9). (2026-07-18; full
    suite 7371, pylint 10.00, code-reviewer clean after one HIGH fix.) As built: the ONE split
    arithmetic extracted (`split_payment_cash` / `PaymentCashSplit`, `_split.py`) and the fold's
    date-sampling core extracted (`sample_cumulative`, `_fold.py`) so ACTUAL / PLANNED / ESTIMATED and
    the past + forward folds all share one implementation (both behaviour-preserving for the B2-proven
    path); `projected_income_shadows` loader (`loan_loaders.py`, the settled set's complement);
    `balance_at/_plan.py` = `loan_plan` (live-cash PLANNED shadows + contractual-from-origination
    ESTIMATED) + `fold_forward` (seed-then-plan fold). Oracles: `test_loan_plan_forward_oracle.py` (8
    hand-computed fold cases) + `test_loan_plan_assembly.py` (4 cases: all-ESTIMATED future-only + B-9,
    PLANNED de-dup, early-settled no-double-count). **Adversarial-review HIGH fixed pre-commit:** the
    ESTIMATED tier double-counted an early- / on-day-settled installment (in the seed by settled date,
    due at or after `as_of`, not a projected record) -- `_estimated_from_contract` now also excludes
    the `confirmed_shadows_through` seed slots (see the C6c correction).
  - [x] **C6b** `refactor(balance): positions() forward folds plan(); retire the schedule-forward
    primitives` -- **SHIPPED `f445aa77`.** `positions()`'s forward branch folds the loan's payment PLAN
    (`fold_forward(seed, owed_from, ctx.loan_plan(account), forward_dates)`); `forward_balance_at_date` /
    `_forward_rows` / `_projected_owed_at` / `balance_from_schedule_at_date` + the `ZERO_MONEY` constant
    deleted. **B-9 killed.** Three things beyond the one-liner:
    (1) **the memo** (developer ruling: memoize now) -- `BalanceContext.loan_plan(account)`, a per-pass memo
    mirroring `loan_walk` so the scalar, map, liability band, and equity chart share ONE plan build per loan;
    a documented lazy import breaks the seam<->context cycle (the plan is a SEAM composition, unlike the leaf
    `loan_walk`); classified a W9906 NON-producer (records, not a balance). The 2x-per-pass
    `contractual_schedule_from_origination` redundancy between the memo and the equity back-projection is
    DEFERRED (**N-14**, developer ruling: pure-CPU, property-page only, needs a rate_changes-equivalence check).
    (2) **W9905 RETIRED WHOLE** (developer ruling: it guarded ONLY the two deleted functions) -- checker + its
    tests deleted, unregistered, stripped from all six `--fail-on` locations; `_BALANCE_PRODUCERS` drops the
    two names. This PULLS D3's W9905 retirement AHEAD; D3 is now "shrink W9906" only. (3) **the "baseline
    consciously moves" framing is CORRECTED by the dev-clone render:** on REAL data the baseline is UNMOVED to
    the cent (Mortgage $177,277.97, Van Loan $15,663.59 at today; both loans current, 0 overdue-clamped),
    because today reads the fold of the past (untouched) and a healthy loan's forward plan fold reproduces the
    contractual paydown to the cent. B-9's overdue-gate and the live-cash correction move numbers ONLY on a
    delinquent loan (unpaid overdue installments) or a genuine live-vs-stored-cash case -- neither present on
    the real loans; the "move" manifests on the delinquent test fixtures instead. Seam tests reworked off
    "seam == schedule walk" tautologies + B-9-encoding onto the fixed behavior
    (`TestForwardWalkExcludesLedgerBookedRows` -> `TestForwardFoldSeedsFromTheConfirmedPresent`; the Horizon
    amortize-to-zero fixture now originates today, since a no-payment past-origination loan is correctly
    delinquent under the fold and never reaches zero). Full suite 7371, pylint 10.00, `tools/pylint/tests`
    149; adversarial `code-reviewer` caught a CI-blocking gate-consistency miss (the canonical `--fail-on`
    list still named the retired checker) + 2 doc/docstring cleanups + 1 Low, all fixed pre-commit. L1
    deferred: `fold_forward` is protected by the private `_plan` module but NOT name-fenced like the walk
    path's `fold_from_walk` (a D3 fence-pass candidate; developer ruling: keep it off the frozen fence).
  - **C6c (DECOMPOSED, 2026-07-19)** `the interest follows the records` -- decomposed into two
    independently-green commits (developer ruling), mirroring C3a->C3b and C6a->C6b, to ISOLATE the
    tax-figure move: **C6c-i** folds the chips (no figure moves), **C6c-ii** rewires the Taxes
    producer's projection onto `plan()` (may move the Schedule A figure on delinquent / drifted loans).
    Developer scope rulings (2026-07-19): rewire the Taxes producer onto `plan()` (not chip-only), and
    fold BOTH chips (delete both posting readers), not interest alone.
    - [x] **C6c-i** `refactor(loan): the paid-YTD chips fold the settled past` -- **SHIPPED
      `2ba0adcf`.** The loan-detail `interest_paid_ytd` / `principal_paid_ytd` chips read new
      settled-only fold producers `balance_at.loan_interest_paid_in_year` /
      `loan_principal_paid_in_year` (each sums a settled split's interest / principal by the display-tz
      paid year, via the read pass's memoized `ctx.loan_walk` the balance hero already folds -- one
      walk for the whole page). The posting readers `confirmed_loan_interest_in_year` /
      `confirmed_loan_principal_in_year` (zero other production callers) + their dead helper
      `_attribute_net_by_shadow_to_year` are DELETED; the W9906 checker's stale classification entries
      go with them. The producers are TOTAL (never `None`), so a cold posting cache folds the real
      figure where the reader hid the chip -- a B-8-class improvement, no regression (the detail page
      renders only for a configured loan). `loan_interest_in_year`'s settled half shares the new
      `_settled_sum_in_year` (behavior-preserving); its projected half is untouched here. **No figure
      moves** (fold == posting reader by B2 / E1; the chips are settled / past-only). New
      `test_loan_paid_in_year.py` (11 hand-computed cases) + the four test files that used the deleted
      readers reworked (settled cross-check re-pinned hand-computed, N-7). Full suite 7367, pylint
      10.00, 149 checker tests, code-reviewer clean (one Low doc fix -- a stale
      `_principal_net_by_shadow` docstring -- applied).
    - [x] **C6c-ii** `refactor(analytics): interest-in-year's projection folds the plan` -- **SHIPPED
      `6014389a`.** `loan_interest_in_year`'s PROJECTED half now folds the loan's forward `plan()` (a new
      `balance_at._plan.plan_interest_in_year` + a `_split_plan` extraction shared with `fold_forward`,
      seeded from the SAME `projection_seed` `positions()` folds), so the tax figure's FUTURE and the
      balance's future come from ONE forward model (B-6 unified the past; this the future).
      **Year-attribution basis = the EFFECTIVE (expected-paid) date** (developer ruling 2026-07-19): an
      overdue-but-still-projected payment's interest deducts in the year it is expected to clear, not the
      closed year it was contractually due. On the real Mortgage the 2026 Schedule A figure moves
      **+$0.02** ($9,140.62 -> $9,140.64), entirely the plan's live-cash forward model (the $0.34/mo
      escrow drift the C6b balance already adopted); a DELINQUENT loan's figure now DROPS (B-9 for the
      deduction -- unpaid overdue installments no longer project phantom interest).
      **The plan's "de-dup relocates onto the plan, airtight because `as_of = date.today()` UTC bounds
      every settled payment into `confirmed_shadows_through`" was WRONG, and correcting it was the step
      (adversarial-review HIGH):** the tax `as_of` is a DISPLAY date (`analytics` passes
      `to_display_date(now)`) while `confirmed_shadows_through` keys on the UTC `payment_visible_on`, so a
      payment settled evening-Eastern (its `paid_at` rolls into the next UTC day) is in the settled half
      (display paid year) yet OUTSIDE `confirmed_shadows_through(as_of)` -- the plan re-synthesizes its
      installment and DOUBLE-COUNTS the deduction (measured $495.01 on the regression fixture). So the
      settled-slot merge STAYS, re-keyed onto the WALK (the SAME set the settled half sums, clock-blind)
      via a restored `_due_slot` + `plan_interest_in_year(exclude_slots=...)`; the plan's own
      `confirmed_shadows_through` de-dup stays for the BALANCE (its seed excludes the same payments it
      re-adds, so it nets -- the interest half diverges only because its settled sum is on the DISPLAY
      clock). New `plan_interest_in_year` hand-computed oracles (effective-year, the overdue clamp, empty
      plan) + the reworked `test_loan_interest_in_year` (schedule oracle -> plan-based, both merge tests
      reworked) + a new evening-rollover regression test (verified to FAIL without the walk-merge:
      $5,721.16 vs correct $5,226.15) + reworked C17-2 (the plan reproduces the contractual schedule for
      a CURRENT loan). Full suite 7372, pylint 10.00, adversarial code-review clean (the HIGH fixed
      pre-commit, re-reviewed clean). **C6c CLOSED.**
- [x] **C7** `feat(loan): the payment you plan is the payment the loan gets` (D3) -- **SHIPPED
  `a3f15aed`.** The payment-drift warning + one-click switch (live: the real Mortgage transfer
  $1,910.95 vs contract $1,911.29 since the 2026-07-06 escrow change; the Van Loan silent, its
  $531.94 == contract). **NARROWED (developer ruling 2026-07-18):** C6b's PLANNED tier already folds
  the transfer's cash (the STORED `effective_amount` for a manual payment, the LIVE derive cash for a
  derive one), so the loan balance already reflects the planned payment; C7 is the WARNING and the
  one-click action only, not the cash adoption. **Two developer rulings (2026-07-19) resolved
  "update the transfer":** (1) the one click **SWITCHES TO AUTO-TRACK** (flips `derive_from_loan`,
  resets `default_amount` to the contract) -- the root-cause fix that never re-drifts (no shadow
  regeneration; the read-time live override applies, exactly as a fresh derive transfer relies on),
  NOT a one-time amount bump that would re-drift on the next escrow change; (2) the warning is
  **UNDERPAYMENT-ONLY** (a deliberate overpayment never trips it). The drift is inherently
  manual-mode-only: a DERIVE payment recomputes its cash to the contract every read and cannot drift,
  so it is excluded; `extra_principal` is added live on top of BOTH the stored base and the contract
  so it cancels, making the comparison base-vs-P&I+escrow (the D3 "vs contractual PITI +
  `extra_principal`" reading -- a short BASE warns even when a standing extra pushes total cash over
  contract, the C6a/M1 firing-control shape). One shared `_total_payment_from_seam` is the single
  P&I+escrow assembly for the loan card / create default / track switch, so the drift SHOWN and the
  amount WRITTEN cannot diverge (adversarial-review M2). Surfaces **N-2** (the settle-time freeze's
  clock read is what the drift warning makes visible). Full suite 7383, pylint 10.00, adversarial
  `code-reviewer` clean (no Critical/High; its 2 Medium + 2 Low all fixed pre-commit: the
  extra-cancellation firing control, the shared-leaf DRY, the sync-comment accuracy, the
  base-vs-total wording).
- **C8 (DECOMPOSED, 2026-07-19)** `the payoff date is derived, never persisted from a schedule` --
  kills B-14 (recurrence sync persisting a blind-walk payoff) and B-20. The trace found the payoff
  computed in FIVE producers, all off the resolver's committed schedule walk, with THREE
  inconsistent empty-schedule fallbacks (`origination_date` at `dashboard.py:222` and `_state.py:355`;
  `as_of` at `_payoff.py:482`; `None` at the target-date outlook); the ONE persisted copy is
  `RecurrenceRule.end_date` (synced from `state.schedule` by `loan_recurrence_sync`, read by
  `recurrence_engine.py:474` to bound shadow generation). The detail-page "Projected payoff" chip
  renders `summary.payoff_date` (`_build_planned_summary`), NOT `ctx.payoff_date`. **Section 3's
  "payoff date = `plan[-1].date`" is INACCURATE and correcting it is the step:** `loan_plan`'s
  ESTIMATED tail runs to the CONTRACTUAL payoff, so `plan[-1].date` overstates payoff for any
  extra-payer and mis-reports a paid-off loan. The correct derivation is FOLD-TO-ZERO -- the date
  `positions()` shows the balance reaching zero -- so the payoff, the balance chip, and the equity
  chart cannot disagree. Two rulings (2026-07-19, recommendations ratified): (1) **fix the fold's
  forward model FIRST, then derive** (N-15 below) rather than derive a payoff known-wrong for
  extra-payers; (2) B-20's paid-off state shows a **"Paid off" badge on `is_retired`** (the
  true-up-payoff predicate; a degenerate `$0`-principal loan reading "Paid off" on its own detail
  page is harmless, unlike the equity chart), no historical date. **A THIRD ruling (2026-07-19),
  forced by C8b's review (finding N-16):** for an UNDERPAYING loan the fold left a residue at the
  contractual payoff and `loan_payoff_date` returned `None` -- likely the real Mortgage, whose
  `$0.34/mo` drift compounds to ~$43 at Dec 2048, flipping a 34-cent drift to "no payoff." The fix
  chosen (over accept-`None` or a tolerance-snap band-aid) is to **EXTEND the forward model past the
  contractual date until the balance clears (capped)**, so a drifted loan gets a correct slightly-later
  date and `None` means genuine non-payoff. Ships **C8a -> C8b -> C8c (extend the tail) -> C8d (the
  cutover)**, additive-/fix-first (mirrors C3a/C6a); the cutover was renumbered C8d to seat the
  extend-tail fix ahead of it.
  - [x] **C8a** `fix(loan): the forward fold keeps the standing extra past the record horizon` --
    **SHIPPED `2e5d3a75`.** N-15: `loan_plan`'s ESTIMATED tail (`_estimated_from_contract`) applies the
    loan's standing `extra_principal` (threaded off the memoized `ResolvedLoan`, not re-read), so the
    fold matches the resolver's full-term committed trajectory. The PLANNED tier already folds the extra
    (live D3 cash), so this is the tail-only correction; escrow stays stripped (`split_payment_cash`
    subtracts it, the ESTIMATED escrow is `0.00`), and `covered_slots` excludes PLANNED slots so the
    extra lands exactly once. **The DRY threading was the refactor:** `resolve_loan_bundle` loads the
    standing extra ONCE and threads it into the resolve AND onto `ResolvedLoan.extra_principal`;
    `resolve_loan_seeded` shed its now-unused `account_id` (both callers updated). **The plan's oracle
    description was corrected as built:** not a hand-computed short loan, but
    `test_standing_extra_folds_past_the_shadow_horizon` -- a CURRENT-PERIOD loan (clean past, so the
    fold and the committed schedule agree on the timeline rather than diverging on unpaid history via
    B-9) with NO projected shadows, so its ENTIRE forward is the ESTIMATED tier. It parallel-runs the
    fold (`balance_at`) vs the resolver's `committed_forward` (an INDEPENDENT producer:
    `project_forward` vs `split_payment_cash`) on every month, plus a post-horizon teeth vs the
    extra-free contractual (a THIRD reference) -- verified to FAIL without the fix. Real data UNMOVED
    (neither loan carries a standing extra). Full suite 7384, pylint 10.00, adversarial `code-reviewer`
    clean (no Critical/High/Medium; all five hazards -- double-count, split, DRY, oracle, back-projection
    leak -- verified; its 2 Low docstring-staleness nits fixed).
  - [x] **C8b** `feat(balance): the payoff date is a fold to zero` -- **SHIPPED `511ab220`.** Additive
    `balance_at.loan_payoff_date(account, ctx) -> date | None`: folds the plan forward from the
    confirmed-present seed (the SAME seed + memoized plan `positions()` uses) and returns the **DUE**
    date the balance first reaches `<= 0` (**corrected from the plan's "effective date"** -- DUE matches
    the resolver's `committed_forward[-1].payment_date`, the value shown today; they differ only for an
    overdue-but-projected clearing installment). `None` for an already-retired seed (`<= 0`), negative
    amortization, or an underpayment that pays DOWN but leaves a residue within the contractual horizon
    (the caller disambiguates the paid-off state via `is_retired`). **Baseline UNMOVED for a healthy or
    overpaying loan** (== the resolver committed payoff, proven by the seam oracle); it DELIBERATELY
    moves for an UNDERPAYMENT -- `None` here vs the resolver's `is_last_month`-forced contractual date
    (a phantom final payment), an adversarial-review Medium: the plan's blanket "baseline unmoved" was
    scoped to healthy/overpaying and the residue case pinned. Unwired (only the oracle reads it);
    `_PlanSplit` gains an inert `balance_after`; the fold-to-zero reuses the ONE `_split_plan`. Oracle
    `test_loan_payoff_date_oracle.py` (11: hand-computed pure fold + seam vs the resolver). Full suite
    7395, pylint 10.00, code-reviewer clean (no Critical/High; its Medium + 2 Low all fixed). **The
    underpayment `None` -> indefinite-recurrence implication is C8c's to wire.**
  - [x] **C8c** `fix(balance): the forward fold pays past the contractual date until the loan clears` --
    **SHIPPED `8ff9a11e`.** N-16: `_estimated_from_contract` stopped synthesizing at the contractual
    payoff, so an underpaying loan (even a cent-scale drift) left a residue and `loan_payoff_date`
    returned `None` where the real loan pays off a month or so later. It now extends past the contractual
    last row with up to `_PAYOFF_EXTENSION_MONTHS` (60) more monthly installments at the level P&I (+
    standing extra), via a shared `_synthesize` helper that covers both the contractual rows and the
    extension, so the fold-to-zero clears a drifted loan at its true slightly-later date; `None` now
    means genuine non-payoff (negative amortization or a drift past the cap). **HEALTHY / overpaying
    loans are UNMOVED by construction:** `plan_payoff_date` returns the FIRST zero-crossing (the
    contractual date), so the later installments never move the payoff, and `positions()` past the payoff
    folds to no-ops on the zero balance. **Adversarial-review Medium corrected the balance-safe scope:**
    the one WIRED surface that samples past the contractual payoff is the savings net-worth HORIZON band
    (`_horizon.py:139`, one year past the longest contractual payoff), where a drifted loan's far-future
    point moves CORRECTIVELY (the Mortgage's Dec 2049 point ~$43 -> `$0.00`, phantom debt removed) -- the
    scalar / per-period map / equity-chart axis all sample at or before the contractual payoff and are
    untouched. **OUTSTANDING: live-verify that Mortgage horizon-tail move on the dev clone before F3**
    (with the C2 history-window render). Oracle `TestPayoffTailExtension` (underpayment clears exactly
    two extension installments past contractual -- teeth verified `None` with the extension disabled;
    severe drift -> `None`; healthy not resurrected past payoff) + the C6a assembly test asserts the
    contractual prefix + the 60-installment extension + its level-payment cash. Full suite 7398, pylint
    10.00, code-reviewer clean (no Critical/High; the Medium scope-correction folded in, its 2 Low
    test-rigor gaps closed).
  - [x] **C8d** `fix(loan): the payoff date is derived, never persisted from a schedule` --
    **SHIPPED `2f0130f5`.** The cutover: `LoanFigures.payoff_date` reads
    `balance_at.loan_payoff_date`, and since it is the ONE funnel every payoff consumer already went
    through, the detail chip, the /savings cockpit, the Horizon's debt-free date + payoff milestones,
    the debt-summary metric, and the refinance fallback all follow from that single edit. The
    equity-chart axis and `loan_recurrence_sync` are repointed explicitly. `LoanState.payoff_date` is
    DELETED at the source, taking all three inconsistent fallbacks with it (`origination_date` at
    `_state.py` and, with `_build_planned_summary`, at `dashboard.py`; `_payoff.py`'s `as_of` and the
    target-date outlook's `None` stay -- they belong to the WHAT-IF calculator, whose committed-vs-
    accelerated comparison is a different question and out of this step's scope). B-14 + B-20 closed.
    **Three developer rulings (2026-07-19), all as recommended.** (1) The axis takes THREE branches,
    not `max(payoff or today, today)`: a real payoff -> `max(payoff, as_of)`; RETIRED -> `as_of` (its
    debt line is history, and it is dropped from the chart anyway -- this reproduces the pre-C8d value
    exactly); `None` and NOT retired -> the PLAN's last modelled installment, because a loan that never
    clears still owes every month ahead, and ending its span at today would draw no forward debt beside
    a market value that keeps appreciating (future equity overstated by the whole balance -- the B-2
    shape). (2) A RETIRED loan's recurrence bound is `ctx.as_of`: ONE rule, no new producer, and it
    retires the `origination_date` sentinel. (3) The payoff is MEMOIZED per read pass.
    **Four things beyond the one-liner.** (a) **The memo INJECTS its deriver rather than importing it**
    -- `BalanceContext.loan_payoff(account, derive)`. A lazy import (the shape `loan_plan` uses) made
    pylint report `cyclic-import` for real: `_positions` imports the context at runtime AND reaches it
    again through `net_worth_kernel`, so the back-edge closed three cycles. Injection fixes the
    inversion at the root -- the lower layer owns the memo SLOTS, the seam owns what fills them --
    rather than suppressing the message; migrating `loan_plan` to the same shape is a Phase-D
    candidate. (b) **`_build_planned_summary` retired whole**: the detail page read only
    `monthly_payment` (passed IN) and `payoff_date` off its 7-field `AmortizationSummary` -- never
    `total_interest` -- so it was building a dead bundle around one fallback. (c) **A `None`-crash
    found by tracing, not by a test**: `_refinance_results.html:62` called `.strftime` on
    `current_payoff`, which falls back to `ctx.payoff_date` when the contractual forward slice is
    empty; guarded. (d) **The create-transfer route now syncs the bound TWICE**, and both calls are
    load-bearing: the payoff is a fold over the forward PLAN, so the pre-generation call (which must
    stay -- it bounds generation) cannot see the payments it is about to generate, and on a loan with
    overdue installments it lands months late and stays there until some unrelated mutation corrects
    it. Measured on the route's own fixture: `2056-07-01` written, `2056-03-01` correct. **Fence:**
    `loan_recurrence_sync` came OFF the W9906 resolver allowlist (it no longer resolves); the other
    three entries turn out to be equally dead -- recorded as **N-17**, left to Phase D.
    **Dev-clone live-verify: NOTHING MOVES.** Balances at the baseline to the cent (Mortgage
    `$177,277.97`, Van Loan `$15,663.59`); both payoffs unchanged (`2048-12-01` / `2029-02-22`); the
    equity axis unchanged; and the pre-C8d derivation reproduced side by side agrees with the new one
    on both loans. The one difference found is the finding itself: the Mortgage's STORED recurrence
    `end_date` is `2048-11-01` against a computed `2048-12-01` -- B-14's drifted copy, stale against
    BOTH derivations, self-healing at the next mutation. This also closed C8c's outstanding item (see
    N-16: the predicted `~$43` Mortgage residue does not occur). Full suite 7410, pylint 10.00, 150
    checker tests; every new control shown to fire by reverting the code under it.
  - [x] **C8e** `refactor(balance): a loan's contract terms are not scenario-scoped` --
    **SHIPPED `6e060884`.** C8d's own adversarial review found that giving `LoanFigures` its
    first scenario-scoped field (the derived payoff) silently coupled THREE non-balance WRITE
    surfaces to a baseline scenario they never needed: the escrow editor (add / delete / version /
    rename / merge), the rate-history OOB swap, and the recurring-payment amount all read only
    `monthly_payment` / `current_rate`, never reach `balance_at`, and so began 500ing for a user
    with no baseline. That state is real, not hypothetical -- `baseline_service` exists to repair
    it and documents "a loan configured while the baseline was gone" (finding G1). **The guard was
    the symptom; the bundle was the defect.** `LoanFigures` mixed two cohesion classes, and nothing
    exposed it while every field happened to be scenario-independent. Split along the dependency
    that actually exists: a new `LoanTerms` (payment, rate, `is_originated`, `is_arm` -- params +
    rate history only, no scenario) that `LoanFigures` **COMPOSES** rather than re-declares, the
    same anti-stale-copy ruling `_LoanAccountResult` already carries. `balance_at.loan_terms` is
    the narrow entry; `_loan_figures_now` became `_loan_terms_now`; `/debt-strategy` takes terms
    too (it never wanted the payoff). Fail-loud stays exactly where a scenario is genuinely
    required, and nothing degrades into a guessed answer. Live-verified on the dev clone: terms
    resolve with the baseline DELETED, and `figures.terms == loan_terms(...)` on both real loans.
  - [x] **C8f** `refactor(loan): the target-date calculator folds the plan` -- **SHIPPED
    `fe424560`.** C8d's review finding H1, and the reason C8d could not ship alone: the
    target-date panel rendered `outlook.committed_payoff_date` as "Current Plan Pays Off" -- the
    CHIP's question, from the schedule walk C8d had just retired -- so on a loan behind its
    schedule the two disagreed, unlabelled, 200px apart. **Deleting the duplicated date was NOT
    enough, and tracing that is what set the scope:** the panel's verdict (`required_extra == 0`
    -> "your current payment plan already pays this loan off by the target date") came from a
    binary search over `project_forward`, so it still contradicted the chip for any target between
    the contractual and the folded payoff. Measured on the regression fixture: contractual
    2056-01-01, fold 2057-12-01, target 2057-06-01 -> the old search said "already paid off". So
    the whole outlook moved onto the fold: `_split_plan` takes a what-if `extra_monthly`,
    `plan_required_extra` binary-searches it (monotone -- every added cent is pure principal), and
    `balance_at.loan_required_extra` is the seam entry. **`TargetDateOutlook` +
    `target_date_outlook` + their `project_forward` walk are DELETED** (-103 lines), retiring the
    fourth inconsistent fallback the C8 trace named. The panel now prints no payoff date at all --
    one question, one producer, one place on the page -- and the FOLD owns every verdict there,
    including "not achievable", rather than falling through to the contractual answer. The retired
    tests' surviving invariants moved WITH the producer (the searched extra reaches the target and
    a cent less misses; a standing overpayment lowers the top-up -- F-27's acceptance); the one
    that did not survive is pinned in a note where the class stood. Real data: `extra @ payoff` is
    `0.00` on both loans (the panel and the chip agree by construction), and a year early costs
    `$25.71/mo` on the Mortgage, `$290.66/mo` on the Van Loan.
- **C9 (DECOMPOSED, 2026-07-19)** `a loan cannot receive a payment before it originates` --
  R-C ruled 2026-07-16 as a write-boundary reject. **The trace found the one-liner was half
  the step, and measuring it decided the decomposition:** the app MANUFACTURES the shape
  itself, so a guard alone would have 500ed its own flow. `create_payment_transfer` built its
  `RecurrenceRule` with no start bound and generated across every materialized pay period, so
  a mortgage closing 2026-04-15 got 3 of 4 payments dated PRE-ORIGINATION -- $3,220.92 of
  phantom cash debits on a 10-period fixture (production materializes ~52), each an FU-5
  erasure the moment it settled. Shipping the R-C guard first was measured to raise
  `ValidationError` straight out of the loan's create-transfer route. So it ships **C9a**
  (bound generation, so the app stops producing the shape) -> **C9b** (the guard, which
  nothing legitimate then trips) -- the fix-the-model-then-cut-over shape of C6a->C6b and
  C8a->C8d. Developer rulings (2026-07-19): the bound is a new nullable `start_date` column
  (not `start_period_id`, which regeneration discards); the guard covers the UPDATE path too;
  the tests are made real rather than deleted; and C9 cleans up the rows the defect created.
  - [x] **C9a** `fix(loan): a recurring payment does not start before the loan does` --
    **SHIPPED `2976614b`** (+ the data half `7c021281`). A recurrence rule had a real END
    bound (`end_date`, filtered unconditionally in `match_periods`) but no real START bound:
    `start_period_id` only seeds `effective_from` when the caller passes none, and
    `regenerate_for_template` and the unarchive path both pass their own, so it is silently
    discarded there. The fix is the SYMMETRIC partner: `recurrence_rules.start_date`, filtered
    in `match_periods` unconditionally so no caller can bypass it, written by
    `loan_recurrence_sync` (renamed `sync_recurring_payment_end_date` ->
    `sync_recurring_payment_bounds`, ONE entry for both ends so no chokepoint can move one and
    leave the other stale). The start half runs AHEAD of the scenario guard -- C8e's lesson,
    since it needs only params. `rate_period_engine.first_installment_date` is the derivation
    and it is the ENGINE's convention, not a calendar guess: one month after origination on
    `payment_day`, so 2026-04-15 + day 20 bills 2026-05-20, NOT 2026-04-20 (which
    `monthly_due_date`, a different question, would give); pinned against
    `contractual_schedule_from_origination(...)[0].payment_date` over six shapes.
    **Adversarial review found two defects, both measured and fixed in-commit:** (1)
    `day_of_month` is ALSO derived from `payment_day` and nothing re-pointed it, so with only
    `start_date` moving (day 1 -> 20) the bound advanced past the day the rule still matched
    and regeneration produced **ZERO** installments -- the loan's whole recurring payment
    vanished; `_sync_loan_cadence` now moves both together, leaving a day-less
    (every-paycheck) rule alone. (2) `POST /transfers` builds its rule from the FORM and never
    synced, so a loan payment set up there reproduced the defect exactly (3 pre-origination
    installments, `start_date` NULL) -- `bind_rule_to_loan` bounds the new rule at
    materialization, taking the rule DIRECTLY because the account-keyed lookup returns the
    FIRST active template and would leave a second one unbounded. Also replaced the hand-rolled
    `FakeRule` stub (the B-17 anti-pattern) with a real unsaved `RecurrenceRule`: it drifted the
    moment a column was added, killing 17 tests on `AttributeError` rather than on behaviour.
    Migration `a1c7e2f4b930` is additive; the purge `b2d8f3a6c541` deletes only what a routine
    regeneration would (the engine's own `partition_regeneration_rows` predicate) and LEAVES a
    settled pre-origination payment with a loud warning -- its cash really moved and it carries
    postings, so it is an F1-class item for a human. Verified on a scratch DB built to CARRY the
    shape (the real one has none): 2 purged, 4 correctly kept, shadows by CASCADE, and
    `downgrade` restored all six from the jsonb snapshot. Baseline UNMOVED.
  - [x] **C9b** `fix(transfers): a loan cannot receive a payment before it originates` --
    **SHIPPED `d5a02ad2`.** The guard, at `create_transfer` AND `update_transfer`.
    **The boundary is `<=`, and correcting R-C's "before" is part of the step:** a payment due
    exactly ON the origination date sorts ahead of that anchor and is subsumed by its reset,
    erased identically. Swept on a 2026-03-01 origination: due 01-15 / 02-28 / 03-01 all book
    $0.00 principal and $1,200.00 to Refund; 03-02 books $366.67 principal. The installment is
    the SHARED `loan_loaders.installment_for` (extracted from `loan_payment_due_date`, which
    delegates to it), so the guard refuses exactly what the fold erases -- including a transfer
    with NO `due_date`, keyed on its pay-period start, which is the ad-hoc shape FU-5 was found
    in. The UPDATE door is guarded because the transfers PATCH route forwards `due_date` /
    `pay_period_id`; the check runs before any field is applied and fires only when one of
    those two moves, so a legacy pre-origination row stays editable otherwise. A payment
    between origination and the first contractual installment stays ALLOWED (an early extra
    payment folds correctly) -- C9b is about EXISTENCE, C9a about the contract.
    **Adversarial review fixes:** the guard read an un-ownership-checked `PayPeriod` ahead of
    `_get_owned_period` (a cross-user id answered 400-with-a-date where the rule requires an
    indistinguishable 404); `create_payment_transfer` bounded via the account-keyed lookup, so
    a SECOND recurring payment left the new rule unbounded, and had no `ValidationError`
    handler; and the atomicity test had no teeth -- its `rollback()` erased the evidence and it
    passed with the guard moved after the amount mutation. All fixed, all controls shown to
    fire. Recorded not fixed: **N-23** (carry-forward batch blast radius) and **N-24**
    (`period_population` / unarchive have no handler). The FU-5 test is REWORKED, not deleted:
    its old construction is now a forbidden write, so it builds the same state a way production
    still can -- an installment dated after origination, settled EARLY -- and its negative
    control still fires. Full suite 7468, baseline unmoved.

### Phase D -- structure replaces policy

- **D0 (added 2026-07-19)** `the seam's dependency arrow points one way` -- not in the plan as
  written; the trace for D1 found it, and D1 cannot be honest without it. D1's whole claim is that
  a PACKAGE BOUNDARY can replace ~60 name-keyed fence entries, which holds only if something
  enforces the boundary. Two measurements decided the shape. (1) **pylint's stock
  `import-private-name` is fail-OPEN for the exact form D1 needs**: `from pkg._engine import
  build_balance_map` rates 10.00/10 while only `from pkg import _engine` is flagged (**N-26**), so
  the gate must be a custom checker. (2) **The seam already had a cross-package private import that
  a real runtime cycle hid behind** (**N-25**). D0 clears it so the gate can ship fail-CLOSED with
  no type-checking exemption -- an exemption being the very shape N-25 is made of.
  - [x] **D0a** `refactor(balance): the read pass's plan memo takes its builder` -- **SHIPPED
    `8285fcad`.** `BalanceContext.loan_plan` INJECTS the builder (C8d's `loan_payoff` shape,
    developer-ratified there) instead of importing it lazily mid-method; the runtime cycle
    `balance_at._plan -> resolution_context -> balance_at._plan` is gone, proven in both directions
    by neutralising the masking edge (see N-25). Two copies of one four-line memo collapse onto a
    generic `BalanceContext._memoized`, whose `key not in slots` membership test is load-bearing and
    now SHARED -- a truthiness check would re-derive an empty plan and a `None` payoff on every read
    forever, which the refactor made a two-memo failure instead of one. The builder is funnelled
    through ONE `balance_at._plan.memoized_plan` rather than named at each of the five seam readers,
    since the memo keys on the derivation handed to it. Honest about the trade in the docstring:
    injection buys the DAG at the cost of an argument, which Section 8 argues against, and the
    lesson's remedy ("load it, do not take it") is unavailable because loading it IS the cycle.
    Three controls shown to fire. Adversarial review fixed four pre-commit: a provably false
    `Raises:` contract (a raised build is never memoized, so the guard fires on EVERY call -- the
    property that makes it trustworthy), an out-of-scope rewrite of the frozen-fence ruling, the
    untested "an empty result memoizes" property, and a bare `Callable` that described neither call
    site. Baseline UNMOVED on the dev clone (Mortgage $177,277.97, Van Loan $15,663.59; payoffs
    2048-12-01 / 2029-02-22); full suite 7470, pylint 10.00.
  - ~~**D0b** relocate `_plan.py`'s fold half to the `loan_ledger` leaf~~ -- **CANCELLED
    2026-07-19, and cancelling it is what produced the design below.** Scoped as "move the fold
    DOWN to the public leaf", it was measured to cost **four NEW fence entries** (`fold_forward` as
    a producer, plus non-producer rulings for `plan_payoff_date` / `plan_required_extra` /
    `plan_interest_in_year`), because `loan_ledger` is W9909-scoped whole. Those four entries were
    the design telling us the ARROW WAS BACKWARDS: a balance producer moved into a PUBLIC package
    needs a fence precisely because it is now reachable. **A balance producer should move deeper
    INTO the seam, never out of it.** The `PlannedPayment` problem D0b existed to solve dissolves on
    its own once the context lives inside the seam (**D-ctx**). Also corrected here: D0b's claim
    that the move closed the `fold_forward` gap "structurally" was **wrong** -- `loan_ledger` is
    public, so the move would have forced CLASSIFICATION (fail-closed, a real gain) but left the
    CALL restriction name-keyed.

**The from-scratch design (developer ruling 2026-07-19: "use structure, not fences").** One
invariant, one gate, no lists:

> A balance-at-T can only be produced by code inside `app/services/balance_at/`, and every module
> in that package is private.

`D-gate` alone enforces it: *no module may import another package's private module, or a name from
it.* Name-INDEPENDENT and fail-closed, so it cannot rot the way a name-keyed deny list does -- and
it needs no completeness checker, because W9909 exists ONLY to compensate for a deny list failing
open.

**Why today's fence needs a "wider allowlist", and what that was telling us.** It classifies
`walk_loan_ledger` as a balance producer on the grounds that "the walk IS a balance-at-T
computation". It is not: it returns FACTS, and it takes `fold_from_walk` to turn them into money.
Both ends are fenced because the fold is one call away -- which forced an allowlist wide enough to
admit the write side, which needs the walk 20+ times. Measured caller split (2026-07-19):

| name | real call sites | what it is |
|---|---|---|
| `walk_loan_ledger` | `resolution_context` + 3 `loan_posting_service` modules | **facts**, both sides need it |
| `fold_from_walk` | `balance_at/_positions.py`, nothing else | **balance** |
| `fold_loan_balances` | **zero in production** (B2's oracle tests only) | **balance** |

So move the FOLDS into the seam and the WALK needs no fence at all: a consumer holding a walk can no
longer reach a balance from it. Structure does what the allowlist was straining to do.

**What Python's structure can and cannot do, stated so no step over-claims.** A package boundary can
stop a consumer IMPORTING or CALLING a producer, and can stop it NAMING a balance-carrying type.
It cannot stop an attribute read on an object the consumer legitimately holds. So the residual rule
is a DATA rule -- *a public seam output carries no balance unless that is its purpose* -- which the
design already follows (`loan_figures` deliberately carries none; `debt_schedule_rows` hands out
rows, not the bundle). `ResolvedLoan` is the one violator left, via `LoanState.current_balance`
(kept at C4 for two in-cluster readers); deleting it is what finally retires
`_CONTEXT_LOAN_PRODUCERS`, since a bundle with no balance on it needs no fence.

**Ordering, forced by a measured import (not a preference).** When this ordering was set,
`net_worth_kernel.py:52` was the ONLY module below the seam with a real `BalanceContext` import (the
loan leaf and `loan_resolution.py` merely mentioned it in docstrings), so the context could not move
into the seam until the engines had. **D1 preceded D-ctx** (both shipped -- the engines are now
`balance_at._kernel` etc., the context `balance_at._context`, and the loan leaf's `_fold.py` -> `_walk.py`
no longer mentions `BalanceContext` at all since D-fold), and D-gate lands LAST so it ships green with
zero standing exceptions rather than training the exemption habit it exists to prevent.

- [x] **D-docs** `fix(pylint): a private function's docstring is gated like a public one` --
  **SHIPPED `8e9a0517`.** A D1 prerequisite, found tracing it: the two FALSE
  `dict[int, net_worth_kernel.DebtSchedule]` hints in `savings_dashboard_service/_net_worth.py`
  (the caller passes ROW LISTS) were the only reason that module imported the kernel at all, so D1
  could not cleanly make the kernel private until they were corrected. Root cause was a gate hole:
  pylint's `no-docstring-rgx` defaults to `^_`, exempting every private function from needing a
  docstring AND -- docparams keying on the same option -- from having one CHECKED, which is most of
  this codebase. Now `^__repr__$` (measured: 60 findings without the exemption, 58 of them model
  `__repr__`; 7 with it, all fixed). Two of the 7 were a config defect, not a doc defect:
  `ignored-argument-names` matched `^kwargs$` by NAME, so it exempted two ORDINARY parameters that
  are genuinely read -- both renamed `updates`. Its own adversarial review caught FOUR false claims
  in the new docstrings, the very class the commit exists to kill; all corrected, and the
  `.pylintrc` comment now states the gate's real boundary (a docstring with no `Args:` section still
  passes -- closing that is 213 findings and its own arc).
- [x] **D-dead** `refactor(net-worth): delete the dead net-worth reducer` -- **SHIPPED
  `cef81202`.** A D1 prerequisite: `net_worth_kernel.sum_net_worth_at_period` had ZERO production
  callers, so D1 would have moved dead code INTO the seam. The live per-period reduction is
  `_net_worth._sum_composition_at_period` (banded, strictly richer; `compute_net_worth_series`
  derives assets/liabilities/net from it), so deletion leaves the rule ONE home, not zero. **The
  deletion exposed a real hole and closing it was the larger half:** the 5 deleted tests were the
  repo's only negative-sign liability assertions, and the reduction's `abs(bal)` has TWO sites
  (hero + per-period band). Every live liability fixture stores a POSITIVE balance, so both
  `abs` calls could be deleted with the whole suite green (measured: 7466 passed with the band's
  removed) -- a Credit Card's negative balance would then read as an ASSET, with the hero and the
  trend contradicting each other on one page. One test per site, each control shown to fire. Also
  corrected the F2 year-end residue and the seam's stale "DELEGATES the per-kind dispatch to
  `build_account_balance_map`" claim (wrong for AMORTIZING since C3b3), plus removed the untracked
  `year_end_summary_service/` namespace-package residue.
- **D1 (DECOMPOSED, 2026-07-20)** engine cluster private inside the seam package -- **the plan's
  scope correction (a) was itself WRONG, and re-measuring it forced the decomposition.**
  It claimed the whole out-of-cluster surface in `app/` was one `debt_schedule_rows` call, two
  false `DebtSchedule` hints, and a `TYPE_CHECKING` `AnchorPoint` import. An AST scan (regex
  import-greps miss `from app.services import (\n  balance_resolver,\n)` -- see the Section 8
  lesson) measures **4 consumer modules reaching 7 names**, and **18** test files with a real
  import, not 14:

  | consumer | reaches |
  |---|---|
  | `routes/accounts/detail.py` | `resolve_anchor`, `interest_by_period_for_account`, `AnchorPoint` |
  | `routes/grid.py` | `live_amount_overrides`, `period_subtotals`, `PeriodSubtotal` |
  | `services/investment_dashboard_service.py` | `resolve_anchor` |
  | `savings_dashboard_service/_orchestrator.py` | `debt_schedule_rows` |

  **Every one of the 7 is an explicitly-ruled NON-producer; not one consumer reaches a balance
  producer.** So the cluster's entire out-of-cluster surface IS its non-producer set -- which is
  the D0b signal repeating. Moving the five modules in wholesale would force `balance_at/__init__`
  to re-export 7 non-balance names (a stored anchor FACT, per-period net SUMS, live override
  amounts, interest EARNED, amortization ROWS), growing the public seam with things that are
  definitionally not balance-at-T. **The dual of D0b's lesson: a producer moves deeper INTO the
  seam; a NON-producer moves OUT of it** -- exactly the reasoning correction (b) already applies to
  `account_projection`.

  **The code DECIDES most of the split, so it is not a judgment call.** The line is *does this
  compose a balance producer?* Two must be INSIDE: `interest_by_period_for_account` (runs
  `_account_interest_projection` -> `calculate_balances_with_interest`, a real money walk, and its
  own docstring says the two halves share one walk so they "cannot drift onto two copies") and
  `debt_schedule_rows` (composes `generate_debt_schedules`). Five must be OUTSIDE: the
  `balance_resolver` call graph shows `resolve_anchor` / `AnchorPoint` / `load_balance_transactions`
  / `live_amount_overrides` / `period_subtotal(s)` / `PeriodSubtotal` touch NO producer, and the
  producers depend on THEM -- one-way, no cycle.

  Developer ruling 2026-07-20: split along the ruling line, place the non-producers by cohesion,
  ship D1c as two commits. `balance_resolver.py` is at EXACTLY 1000 lines (pylint's default
  `max-module-lines`) because it holds three concerns; the three split at ~250 / ~190 / ~460.
  Phase X is the reason the grouping matters: **X2 is "a cash account is an event stream"**, and the
  anchor fact + transaction loader + live amounts ARE that stream -- so D1a builds the layer X2
  needs instead of a leftover module X2 must re-split.
  - [x] **D1a** `refactor(balance): split balance_resolver into events, flows, producers` --
    **SHIPPED `a2149145`.** `cash_events` (the FACTS), `period_flows` (what MOVED, not what is
    HELD), and `balance_resolver` (the PRODUCERS: `balances_for` / `balance_as_of_date` /
    `BalanceResult`), all still OUTSIDE the seam. **Pure move, proven not asserted:** an AST
    comparison vs the pre-split file reports 14/14 definitions identical, none dropped or added, so
    the baseline cannot move. The one-way arrow is verified at RUNTIME in a fresh interpreter, not
    by grep -- including that `live_amount_overrides`' function-level `income_service` /
    `loan_payment_service` import closes no cycle from its new home. 5 app/ consumers + ~10 test
    files rewired via AST scan; `test_balance_resolver_anchor.py` renamed to `test_cash_events.py`.
    **The plan's "come OFF the fence entirely (like `account_projection`)" was WRONG, and the
    adversarial review proving it is the step's most important part.** Moving these names out of a
    W9909-scoped module took the fail-CLOSED completeness check with them: a new public
    balance-at-T in `period_flows`, folded from `resolve_anchor` + `period_subtotals` +
    `round_money` -- not one fenced NAME among them -- rated 10.00/10, **and so did a route
    rendering it.** A real balance on a screen outside the seam with every gate silent: the third
    instance of the miss the checker's own header calls "a design defect in the FENCE, not a lapse
    in diligence". The correction is that the two lists answer DIFFERENT questions -- W9906's
    allowlist asks "may this module CALL a producer?" (no, and it correctly fires if they try),
    W9909's scope asks "must a new public function here be CLASSIFIED?" (yes). Both modules are now
    W9909-scoped via a new `_CASH_EVENT_SOURCE_MODULES` and stay OFF the W9906 allowlist; probe
    re-run, W9909 fires. Three docstrings had asserted the removal was precedented by
    `account_projection`, which is on BOTH lists -- corrected, along with 7 stale cross-references
    including `log_events.py`'s `EVT_ANCHOR_CACHE_RECONCILED` description, which named an emitter
    that no longer exists. Full suite 7467, pylint 10.00, 150 checker tests. Deferred: the
    `period_subtotal` tests stay in `test_balance_resolver.py` (they share its fixtures; relocating
    means promoting two helpers into the conftest-imported `tests/_test_helpers`, its own commit).
  - [x] **D1b** `refactor(balance): a kind classifier is not a chronology module` --
    **SHIPPED `1616acd8`.** Correction (b)'s premise VERIFIED (`account_projection` imports only
    `app.enums`, defines no producer, calls none; 18 real importers, so moving it inside would make
    18 modules import a private name) -- **but the step as written deleted FOUR fence entries, and
    only ONE of the two removals is safe. Measuring that is the step.** The entries answer
    different questions (N-28): coming OFF the **W9906 call allowlist** is a pure TIGHTENING (it
    never used the exemption), while coming off the **W9909 completeness registry** is a LOOSENING,
    and "it defines no producer" would justify it with a fact about the TREE. **Measured both
    ways:** with the registry entry dropped, a public `balance_on(account, target)` folding
    `account.transactions` -- **needing no new import at all**, since the module's reachability
    surface is its PARAMETERS (`classify_account` takes a live `Account`) -- rated **10.00/10**,
    and so did a route rendering it. N-28's shape, a fourth time. The history settles it: this
    module DEFINED the loan forward-projection producers (`forward_balance_at_date` /
    `balance_from_schedule_at_date` / `compute_forward_loan_period_balance_map` /
    `splice_confirmed_and_projected_loan_balances`) through all of Phase C and shed the last one at
    `f445aa77`, **one day before D1b**. So the W9909 entry STAYS (now two names, on a new
    `_KIND_CLASSIFIER_MODULES` scope constant, since `_ENGINE_CLUSTER_MODULES` no longer covers
    it); only the allowlist membership and the W9906 message's stale cluster list go. **Also in
    the step, and the reason the ruling is honest:** `find_period_containing_date` MOVED to
    `loan_ledger/_visible.py`. Its only two callers were the balance seam (`_kind_correct.py:278`)
    and that chronology leaf, which had been importing a kind CLASSIFIER to reach its own primitive
    -- a COHESION correction (where does the rule belong?), NOT Section 8's private-import smell;
    that import was public and ordinary, and calling it Section 8 would have condemned the new
    arrangement too (see N-29).
    The ruling travelled WITH the name into a package the registry scopes WHOLE (N-28's rule),
    verified live: W9909 fired at the destination the instant the name landed. Pure move (function
    body AST-identical; the signature gained the type hints the bare `list` lacked); `loan_ledger`
    loses its `account_projection` dependency. Baseline UNMOVED, proven by running pre- and
    post-change code side by side on the dev clone: **all seven** non-plain accounts identical to
    the cent (Mortgage $177,277.97, Van Loan $15,663.59, and the INTEREST / INVESTMENT /
    APPRECIATING kinds that actually route through the moved function). Full suite 7471 (7467 + 4
    new locator tests -- it had NO direct coverage, and `resolve_anchor_pay_period` files every
    anchor correction's NOT NULL `pay_period_id` from its answer, the path whose
    miss-and-fall-through-to-`periods[0]` case `owner_pay_periods` measures at $150,000.00), 151
    checker tests, pylint 10.00. Three docstring corrections found on the way: `_visible.py`'s "Pure: no query" (false --
    `owner_pay_periods` queries), `loan_ledger.__init__`'s dependency list (omitted
    `app.extensions` / `app.utils`), and `_horizon.py`'s cross-reference. **Adversarial review
    caught the loosening as a CRITICAL** after it had been built, measured, and live-verified
    green -- Section 7.5 in one finding; its three dependent false docstring claims and a false
    "the one module with no fence entry" superlative (B-12 names `loan_resolver` and others) went
    with it. **A SECOND review of the correction found two more, both measured and both fixed:**
    (H1) the restored registry entry was SELF-ATTESTING -- most of the W9909 scope is DERIVED from
    the W9906 allowlist, but this one now sits on a hand-written constant nine lines from the entry
    it covers, so deleting both (the "D1b redux" a later agent would write) passed **150 green**
    and re-opened the probe at 10.00/10. Fixed by a BEHAVIOURAL pin that names its three
    hand-scoped modules LITERALLY (`test_flags_unclassified_export_in_every_hand_scoped_module`);
    binding it to the constants would reproduce the hole one level down. It closes the same gap for
    D1a's `cash_events` / `period_flows`, which had it too. (H2) the new fallback test had NO
    TEETH: the fixture listed the LATEST period first, where "first met" and "max by index"
    coincide, so mutating the comparison to `if fallback is None` left all four tests green --
    while production (both callers `ORDER BY period_index` ASCENDING) would have returned the
    EARLIEST period. Fixture reordered; mutant verified dead. Also corrected: a **misquote of
    Section 8** in three places (it names a PRIVATE-import smell; this was an ordinary public
    import, and the paraphrase would have condemned the new arrangement too -- the move stands on
    COHESION), an overstated W9909 claim (it sees public functions and public methods, not a
    dunder-computed attribute or a module-level alias -- both rate 10.00/10, structural and
    pre-existing), "none can yield a figure" about an ORM row, and the rationale written five times
    now written once. **Deferred, developer ruling wanted: N-29** (the seam's non-loan branch now
    reaches into a loan-named package for a generic calendar primitive).
  - [x] **D1c** `refactor(balance): build the cash-ledger leaf as a package` -- **SHIPPED
    `70cc04c2`.** The prerequisite the move's own trace found, and the one step in Phase D that
    DELETES a fence surface rather than adding one. **Premise is N-30:** `balance_calculator` is not
    a whole-module move, because `period_flows` calls its `sum_projected` and the call graph
    (`sum_projected` -> `income_amount` / `_expense_amount` -> `_entry_aware_amount` ->
    `entry_checking_impact`) takes FIVE explicitly-ruled NON-producers out with it. Placing them by
    cohesion (the D1 ruling line, developer ruling 2026-07-20) gives the cash side the shape the LOAN
    side already proved: `app/services/cash_ledger/` -- `_facts` (`resolve_anchor` /
    `load_balance_transactions`), `_amounts` (`live_amount_overrides` plus the per-row valuation
    rules: the cash analog of `loan_ledger/_split`, what ONE row is worth to checking),
    `_flows` (`sum_projected` + `period_subtotal(s)` / `PeriodSubtotal`: what a SET of rows sums to),
    and a public `__init__`. `cash_events.py` and `period_flows.py` fold in; D1a's reason for keeping
    the flows OUT of the balance seam is untouched, since the package is not the seam. What remains
    in `balance_calculator` is EXACTLY the producer pair (`calculate_balances` /
    `calculate_balances_with_interest`), so D1d's split is decided by the code, not by judgment.
    **The fence gain is the point:** the hand-written two-module `_CASH_EVENT_SOURCE_MODULES` --
    which D1b's H1 review found SELF-ATTESTING (deleting the constant and its entry together passed
    150 green while re-opening the probe at 10.00/10) -- collapses into ONE `_CASH_LEDGER_MODULES`
    package key that prefix-matches, exactly as `app.services.loan_ledger` is scoped today, so
    creating a module inside the package can no longer escape the scope (Section 8's fail-closed
    lesson, closed STRUCTURALLY instead of by a literal-string test; pinned by
    `test_package_scope_covers_a_submodule_that_does_not_exist_yet` + its boundary-teeth sibling). It
    also finishes the layer D1a explicitly built for X2 ("the anchor fact + transaction loader + live
    amounts ARE that stream") rather than leaving X1/X2 a scattered layer to re-split.
    **NOT a pure move, and ratifying that is part of the step (adversarial-review Medium 4 + rule
    8):** the relocation is AST-identical per definition (10/10 relocated defs proven, an
    `_Unqualify` normaliser handling the one now-sibling call), BUT `balance_resolver` carried a
    private `_entry_aware_amount_dated` / `_sum_period_as_of` that DUPLICATED the reservation formula
    and the projected-sum loop, and pylint's cross-file `duplicate-code` (a hard gate here) fired the
    moment both copies called the same relocated `income_amount`. Keeping a bare module qualifier
    would have silenced it -- a band-aid rule 1 forbids -- so the RIGHT fix was to delete the mirror:
    the as-of window is now an optional (keyword-only) parameter of the ONE `_entry_aware_amount` /
    `sum_projected`, and `entry_checking_impact` went PRIVATE (`_entry_checking_impact`) since its
    only external caller was that deleted mirror -- structure retiring a fence ruling, Phase D's
    point in miniature. This is a real design decision on a step first scoped "pure move"; the
    unification is behaviour-preserving (differential harness: 252 input combinations HEAD-vs-new,
    0 mismatches; the guard reorder `not entries` before `is_projected` is load-bearing, since
    `is_projected` reads `status_id` and must not run on a non-ORM fake). **Baseline cannot move,
    proven not asserted:** 1,402 dev-clone figures identical HEAD-vs-post, plus a seeded
    projected-expense-with-entries probe (rolled back) that reaches `_entry_checking_impact` the flat
    dump could not -- identical, and teeth-checked (a $1 injection moves 134 lines). Full suite 7477,
    pylint 10.00, 153 checker tests; adversarial `code-reviewer` clean after its High 1/2 (stale
    docstrings naming the deleted mirror), Medium 3 (a producer reaching the leaf through another
    producer's re-export), Medium 5 (positional `as_of` confusable with `amount_overrides` -> made
    keyword-only), and its Lows were all fixed pre-commit.
  - [x] **D1d** move the producers in as private -- **SHIPPED as two commits: the CASH chain
    `229a7889` and the NET-WORTH chain `34bf0446`.** All five balance producers now live inside
    `balance_at` as private submodules (`_cash_engine` / `_calculator` / `_daily_series` /
    `_kernel` / `_investment`), so `_BALANCE_SEAM_MODULES` COLLAPSED to `{app.services.balance_at}` --
    the fence is now ONE package boundary, the shape D-gate will enforce. **The plan's scope text was
    STALE and re-tracing it was the step:** "`_calculator` is N-30" is FALSE after D1c (which moved
    `sum_projected` into `cash_ledger`), so BOTH `_calculator` and `_cash_engine` had ZERO
    out-of-cluster consumers; only `_kernel` needed exports -- the two the plan named
    (`debt_schedule_rows`, `interest_by_period_for_account`), re-exported from `balance_at.__init__`
    as NON-producers the account-detail route and the savings orchestrator read off the seam.
    **Commit 1 (cash chain)** is a PROVEN pure move (`_calculator` git-identical 100%; def-identity +
    dev-clone baseline unmoved + normalized content-identity), fence re-keyed onto a new hand-scoped
    `_SEAM_PRIVATE_ENGINE_MODULES` (the W9909 rulings TRAVEL, N-31; die at D3), the H1 literal-naming
    self-attest test extended. **Commit 2 (net-worth chain)** is a pure move for `_investment` and a
    pure move PLUS one root-cause fix for `_kernel`: its two lazy in-function `net_worth_investment`
    imports carried a FALSE "breaks the cycle" rationale (the sub-chain imports nothing back, its own
    docstring said so), so once siblings they PROMOTED to a top-level `from . import _investment` and
    the dead disables deleted -- behavior-preserving, cycle verified absent (**N-32**). The
    `net_worth_kernel` entry came off `_CONTEXT_LOAN_MODULES` (prefix-covered) and
    `_ENGINE_CLUSTER_MODULES` is now empty. Both commits: pylint 10.00, checker tests 151, full suite
    7477, all four import orders clean (Python 3.14 defers annotations, so the eager-annotation hazard
    I first chased does not exist); dev-clone loans UNMOVED (Mortgage $177,277.97, Van Loan
    $15,663.59) and investment / property / interest all compute; 8 (cash) + 6 (net-worth) fence
    probes fire; adversarial `code-reviewer` clean on each (no Critical/High/Medium; 4 Low doc-drift
    fixed across the two).
- **D-ctx (DECOMPOSED, 2026-07-20)** `the read pass's context belongs to the seam that defines it`
  -- move `resolution_context` into the seam and retire D0a's injection. **The plan text carried two
  claims the trace DISPROVED, and correcting them was the step** (developer-ratified 2026-07-20).
  (1) "once inside, a plain sibling import is honest and the cycle cannot recur" is FALSE: a 2-module
  probe proved a top-level mutual import between siblings still trips `R0401` (9.00/10) -- moving into
  one package does not dissolve a module-level cycle, and `_plan`/`_positions` import `require_scenario`
  from the context at RUNTIME, so a context method that called the builder back would reintroduce
  N-25's exact cycle. (2) "`_LOAN_RESOLVER_PRODUCERS` deletes" is PREMATURE: it guards the same
  `LoanState.current_balance` leak (still live, two in-cluster readers `_kernel._projection_seed` /
  `_loan_figures._is_retired`) the DATA-rule note above says survives until that field dies -- deleting
  the fence now is the N-28/N-31 loosening trap, so it is RE-KEYED, not deleted (deletion stays D3's).
  So the step ships as two commits: the pure MOVE, then the structural DISSOLUTION (public pass-through
  caches, NOT the plan's "sibling import"). The last cross-package private import (`PlannedPayment`)
  dissolves as a cross-PACKAGE concern (it becomes an intra-seam sibling), which is what cancelled D0b.
  - [x] **D-ctx-a** `refactor(balance): the read pass's context moves into the seam` -- **SHIPPED
    `0dd6395d`.** `git mv resolution_context.py` -> `balance_at/_context.py`; `BalanceContext` /
    `require_scenario` re-exported from the seam; 53 importers rewired (11 seam siblings to a sibling
    import, 42 consumers/tests to the seam re-export). PROVEN PURE: all 9 definitions
    AST-body-identical, the only content change the `PlannedPayment` import going cross-package ->
    sibling. Fence re-key: `_LOAN_RESOLVER_MODULES` and the W9909 ruling KEY move to `balance_at._context`;
    the redundant `_LOAN_LEDGER_READER_MODULES` / `_CONTEXT_LOAN_MODULES` entries drop (the `balance_at`
    prefix covers them). **N-31 applied, and needed MORE here than for the engines**: the move newly
    puts the context under the W9906 allowlist (as `resolution_context` it was NOT in
    `_BALANCE_SEAM_MODULES`, so it could not call a balance producer), so W9909 becomes its ONLY
    completeness gate -- its ruling travels to a dedicated, self-attest-pinned
    `_SEAM_PRIVATE_CONTEXT_MODULES` (named literally in the H1 test), not just a re-key. pylint 10.00,
    153 checker tests, full suite 7477; adversarial review clean (2 Low fixed: import-group
    consistency in `_inputs`/`_positions`, a stale `BalanceContext.loan` doc ref in `_kernel`).
  - [x] **D-ctx-b** `refactor(balance): the forward memos are filled by the seam, not injected` --
    **SHIPPED `00036224`.** The dissolution: `BalanceContext.plans` / `payoffs` become PUBLIC
    pass-through caches (keyed by `account.id`) the seam FILLS through `_plan.memoized_plan` and a new
    symmetric `_positions.memoized_payoff`, sharing one private `_memoize_once`. `ctx.loan_plan` /
    `ctx.loan_payoff` / `ctx._memoized` and the `PlanBuilder` / `PayoffDeriver` aliases DELETE -- the
    context receives NO builder now. **This is where the plan's "sibling import" was wrong and the fix
    is the shape**: the memo LOGIC moves UP into the builder modules (which already import the context),
    the caches are inert state the seam fills, and protected-access (W0212) forces the fields public --
    an asymmetry that ENCODES the DAG (the resolver/walk memos derive from below-context leaves and
    stay PRIVATE methods; the plan/payoff derive from above-context builders and are PUBLIC caches).
    The `PlannedPayment` edge is now an intra-seam sibling `TYPE_CHECKING` import typing the `plans`
    field. Behaviour-preserving (derivations byte-unchanged; build-once, membership-not-truthiness, and
    fail-loud-on-every-call all preserved); dev-clone baseline UNMOVED (Mortgage `$177,277.97` /
    payoff 2048-12-01, Van Loan `$15,663.59` / payoff 2029-02-22, both caches confirmed filling). The
    two builder-in-key defense tests DELETED (that failure mode is structurally gone -- no builder to
    pass); the memo tests reworked onto the public caches with teeth intact; two tests ADDED for the
    fail-loud-every-call property the docstrings asserted but nothing pinned before. pylint 10.00, 153
    checker tests, full suite 7478; adversarial review clean (Low #2 -- untested fail-loud -- closed by
    the two added tests; Low #1 -- public caches -- weighed and kept per the reviewer). **The memo
    ACCESSORS did NOT all "stay methods" as the plan said**: `loan_plan`/`loan_payoff` became the
    module funnels, while `resolved_loan`/`loan_walk` stay fenced methods (tied to
    `LoanState.current_balance`'s deletion, which retires `_CONTEXT_LOAN_PRODUCERS`).
- [x] **D-fold** `a fold is a balance; a walk is a fact` -- **SHIPPED `3dc32d14` (2026-07-20).** The
  loan BALANCE fold moved OUT of the `loan_ledger` leaf INTO the seam as a private module
  (`balance_at/_fold.py`), and `loan_ledger/_fold.py` was renamed `_walk.py` -- the leaf keeps the
  walk, the events, the split and the loaders, the FACTS both sides need. **The plan's "move two
  names" was incomplete, and re-tracing it was the step:** the move is FOUR names -- `fold_from_walk`
  and `fold_loan_balances` (the plan's two), plus `_dated_deltas` (`fold_from_walk`'s private helper)
  and `sample_cumulative` (used ONLY by the two folds, never the writer/leaf side, so the leaf's own
  "everything BOTH sides need" rule ejects it). Moving `sample_cumulative` out is what makes
  un-fencing the walk SAFE: once it leaves, no public leaf name turns a `LoanLedgerWalk` into a
  balance-at-T. A PROVEN pure move (all nine moved/kept definitions AST-body-identical to pre-split
  HEAD). **Fence = Option B (developer-ratified, over N-31 travel):** the moved folds become plain
  seam-private names like their twin `fold_forward` (ruled off the frozen fence at C6b/L1), NOT a
  traveled name-fence -- a balance producer moving DEEPER into the seam sheds its fence rather than
  gaining one (the D0b lesson), and D-gate closes the residual for every seam-private at once. All the
  leaf-side fence edits are TIGHTENING/reclassification: `walk_loan_ledger` moved from
  `_LOAN_LEDGER_READER_PRODUCERS` into the `loan_ledger` W9909 NON-producer set (a fact, no longer a
  balance-at-T); `sample_cumulative` dropped from the leaf's non-producers (it left);
  `_LOAN_LEDGER_READER_PRODUCERS` shrank to the two posting readers; the allowlist tightened to
  `{loan_posting_service, loan_payment_service}` (measured: `confirmed_loan_balance_at`'s only app
  caller outside its own package is `loan_payment_service`). B2's oracle keeps calling
  `fold_loan_balances` through the seam. Dev-clone baseline UNMOVED to the cent (Mortgage $177,277.97
  / payoff 2048-12-01, Van Loan $15,663.59 / payoff 2029-02-22); pylint 10.00, 153 checker tests, full
  suite 7478; adversarial review clean after one Low (a stale `_context.loan_walk` docstring still
  calling the walk a balance-at-T, realigned). The accepted residual -- like `_plan.py` today,
  `_fold.py` is an unscoped seam module whose folds are ungated-BY-NAME until D-gate -- is Option B's
  known posture, closed structurally at D-gate. N-29 (the calendar primitive's home) was not taken up
  here; still deferred.
- [x] **D-gate** `a package's private modules are private` -- **SHIPPED `0ba7ecc8` (2026-07-20).**
  The custom checker `shekel-private-module-import` (W9910, `shekel_checkers/package_privacy.py`),
  hard-gated in all eight `--fail-on` locations plus the gate-consistency test's canonical list.
  The rule as ruled: a module outside package `P` may not import `P._x`, nor any name from it --
  all three spellings (`from P._x import name`, N-26's fail-open form, re-measured on pylint 4.0.5
  before building; `from P import _x`, astroid-resolved so a private NAME defined in a public
  module stays out of the ruled scope, the module-vs-name split failing CLOSED when the base is
  unresolvable; `import P._x` aliased or not), relative imports resolved against the importer,
  TYPE_CHECKING NOT exempt (a dedicated test pins the N-25 shape). Name-INDEPENDENT and
  fail-closed: no allowlist, no name list, nothing to rot. Shipped green with ZERO standing
  exceptions per the design, measured twice (an AST scan, then the shipped checker over `app/` +
  `scripts/` + `shekel_checkers/`). **The step's one correction, found by the gate's own first
  run: "inside package P" needed a PHYSICAL membership arm, granted PER BOUNDARY.** `scripts/` is
  a PEP 420 namespace package, so pylint names its entry points TOP-LEVEL (`rotate_sessions`, not
  `scripts.rotate_sessions`) and the dotted-name rule alone flagged the four `scripts._script_lib`
  sibling imports -- files sitting IN the package directory, the library's documented consumers.
  Membership is now dotted-name OR importing-file-inside-the-owner's-directory (astroid-resolved,
  real-paths-only: astroid's `"<?>"` string-build placeholder can suppress nothing, pinned by a
  direct discriminating test after it silently passed eight flag tests mid-development). The
  step's adversarial review then demonstrated the statement-wide version of that arm passed a
  private-SUBPACKAGE reach a regular-package twin flags (a sibling is a member of `scripts`, never
  of `scripts._libpkg`), plus two fail-closed branches with no discriminating observer -- all
  fixed pre-commit, every new guard mutation-verified to fire. Controls: all four import
  spellings fired on a probe route (the N-31 probe shape now reds the build); W0212 measured to
  cover the `pkg._x` attribute-access residual. Scope stated honestly: the gate binds the LINTED
  trees; `tests/` are outside pylint scope by ratified decision #1 and do import seam privates --
  a known, deliberate boundary to keep in mind at D3. Out-of-ruled-scope residual measured and
  recorded as N-33. pylint 10.00 on all three trees, 184 checker tests (31 new), full suite 7478.
- [x] **D2** distinct types: cash-flow balance vs net-worth balance -- **RULED "NOTHING"
  (developer ruling 2026-07-24), the answer the step's own text allowed.**  Measured basis: no
  static checker anywhere in the gate stack (an annotation enforces nothing); the only
  self-enforcing option (`Decimal` subclass wrappers) decays to plain `Decimal` on the FIRST
  arithmetic unless ~20 operators are overridden -- a policy layer bigger than the fences D3
  deletes -- and defends a class with ZERO measured instances; the call-choice class (B-3) was
  closed at the SOURCE (D4); the attribute-leak class closes by deletion (D2a).  Two Section-3
  amendments ratified in the same ruling: NO types (mypy adoption revisitable only as its own
  arc), and `AmortizationRow.remaining_balance` ruled IN-PURPOSE display detail rather than
  deleted (its readers are the schedule table, the payoff curves, and the ESTIMATED tier that
  must show the CONTRACT; no fence entry existed because of it).
- [x] **D2a** `refactor(loan): the resolver bundle carries no balance` -- **SHIPPED `9f592502`
  (2026-07-24).**  The enabling structural step the D2 ruling resolves root-cause 3 through:
  `LoanState.current_balance` -- the last balance-at-T on a public bundle, and for a broken loan
  the money-blind anchor replay -- is DELETED, and the seam's last two readers re-route onto the
  fold over the pass's memoized walk: `_kernel._projection_seed` (the seed of the WHOLE forward
  tier: payoff, required-extra, forward balances, interest projection) and
  `_loan_figures._is_retired` (+ `_is_paid_off` on it).  Healthy loans proven unmoved (B2's
  every-day oracle; dev-clone baseline to the cent, payoffs 2048-12-01 / 2029-02-22); a BROKEN
  loan's figures converge on the fold its page already shows -- the seam's last internal fork
  closed ($239,761.08 replay vs $0.00 fold, pinned by the new
  `test_broken_loan_figures_follow_the_fold_not_the_replay` control with hand-verified
  arithmetic).  Also deleted: `resolve_account_loan` (production-dead; its documented write/sync
  callers shed their calls at C4/C8a) and its fence entry.  ~12 test files re-point oracle
  windows one level down with every hand-computed value UNCHANGED: replay pins read
  `_replay_from_anchor` directly (still live as the composer's unseeded schedule seed), ledger
  windows read `confirmed_loan_balance_at`, loan-detail windows read the seam scalar the page
  renders since C4.  The Step-4 reconciliation oracle keeps its independence (its replay window
  never reads the ledger).  B-12's balance half closes structurally.  Adversarial review: no
  Critical/High; 2 Medium + 4 Low doc residue, all fixed pre-commit.
- [x] **D3** DELETE W9906 and W9909 down to what genuinely cannot be private -- **SHIPPED
  `6e3a6c79` (2026-07-24).**  W9905 already RETIRED at C6b (`f445aa77`).  As ruled (2026-07-24,
  "full deletion + B-12 scope + record the E1 path"): `_BALANCE_PRODUCERS` (12 names) + its
  one-package allowlist, `_LOAN_RESOLVER_PRODUCERS` + its five-module allowlist (three were
  N-17's dead exemptions), `_CONTEXT_LOAN_PRODUCERS` (`resolved_loan` / `loan_walk` -- the bundle
  carries no balance since D2a, the walk is the leaf's public FACTS), and five of six
  seam-private W9909 rulings (N-31's deletion) are GONE; ~21 checker tests of dead surfaces
  removed, behavior pins re-targeted onto kept surfaces, none dropped without a successor.  **The
  honest residue,** each named with reason and resolving step: W9906 keeps the two posting
  readers (write-cluster-owned; funneled to `loan_posting_service` + `confirmed_loan_view`;
  DELETES at E1 -- seed the resolver's confirmed slice from the WALK, the view dies, the readers
  go package-private behind W9910) and W9909 keeps the fail-closed classification registry on the
  public ingredient packages, which now rots CLOSED only (a stale entry names a dead function;
  the deleted allowlists were the direction that rotted OPEN, N-17).  **The step's adversarial
  review measured two 10.00/10 holes the deletion rationale missed -- both closed in-commit:**
  (H1) a public METHOD on the publicly re-exported `BalanceContext` reaches every route with no
  `__init__` edit and W9910 blind to attribute access, so `_context`'s W9909 ruling is the ONE
  seam-private survivor (probe `ctx.balance_now` re-fenced); (H2) `loan_payment_service` -- the
  one reader-allowlisted module -- accepted a public wrapper returning
  `confirmed_loan_balance_at` unclassified (pre-existing, now scoped with 7 rulings;
  `confirmed_loan_view` documented as the E1-mortal view composer).  Plus the ruled tightening:
  `loan_resolver` package scoped (7 rulings) -- **B-12 closed** fail-closed.  Controls fired RED
  on all four probe shapes; pylint 10.00/10 on all three trees, 163 checker tests, full suite
  7479.  Net -528 lines.

### Phase E -- the ledger becomes a checked projection

- **E1 (DECOMPOSED, 2026-07-24; four developer rulings, all as recommended)** the ledger becomes a
  CHECKED projection, and the last W9906 surface deletes.  **The trace corrected the step's first
  clause:** postings are ALREADY generated from the event stream (Step 4 / B0 --
  `sync_loan_postings` walks the source facts once and reconciles both posting halves to
  walk-derived targets), so the write half is the CHECK, not the generation.  **The DB-level
  option is measured OUT:** the "existing deferred-trigger pattern" is the per-entry balanced
  trigger (`posting_infrastructure.py`), but fold equality needs rate periods, effective-dated
  escrow, and the split engine -- Python -- so a plpgsql twin would be the two-implementations
  drift rule 1 forbids; the invariant lives at the app-tier sync chokepoint, which every
  posting-relevant door already funnels through (transfer settle/edit/delete, params edits,
  true-ups, rate changes, resets, backfill -- enumerated).  **Rulings (2026-07-24):** (1) the
  walk-based view derives IN THE SEAM from the read pass's memoized walk -- leaf enrichment (a
  payment split carries the ``due_date`` the walk already derives for ordering; the walk carries
  its resolved rate periods), a seam-private builder reusing the fold's sampling,
  `BalanceContext.resolved_loan` threading the view into `resolve_loan_bundle`, and a public seam
  entry for the three loan-route call sites -- never a re-loading builder outside the seam;
  (2) the assert is PER-VISIBLE-DATE and the reconcile becomes DATE-AWARE (N-13's root fix: a
  correction or cash leg whose `entry_date` no longer matches the walk's visible date is stale --
  reversed and re-posted at the true date; a settled `paid_at` edit triggers the loan sync); the
  visible-date delta derivation becomes ONE shared leaf function so the writer, the fold, and the
  assert cannot drift; (3) the assert is FULL equality over the linked ledger, ship-gated on a
  data sweep proving zero walk-invisible legacy rows (the N-11 class: a pre-BG raw transaction on
  a loan, a pre-R6 transfer out of one; mirror of Step-4's mandatory pre-anchor cleanup) -- any
  found row is an F1-class human decision, never a silent exclusion; (4) N-2's write-side clause
  is ACCEPTED-BY-DESIGN (the freeze writes SOURCE cash the walk then splits, so it cannot desync
  the checked projection) -- its row is closed.
  - [x] **E1a** `fix(loan): the posted ledger is a checked projection` -- **SHIPPED `545799fb`
    (2026-07-24).**  The per-visible-date assert in `sync_loan_postings` (posted per-date nets vs
    the walk's dated deltas, negated into posting space; PostingError on mismatch; forced-$1
    control fires) closes B-5's invariant; `dated_deltas` moved seam -> leaf as the ONE clock
    statement both sides consume.  **Four things beyond the one-liner, each found BY the assert or
    its review.**  (1) N-13's root fix re-keyed ALL THREE posting reconciles (transfer,
    transaction, loan-payment correction) per `(pay_period_id, entry_date)` -- a settled `paid_at`
    edit re-dates in ONE pass, both directions, mutation-verified; the shared emission loop and
    the linked-ledger query core each collapsed to ONE function (both duplications measured
    R0801).  (2) The lineage-transfer heal: the sync probes the linked ledger's transfer-source
    per-date nets (one grouped query steady-state) and re-syncs only stale transfers -- candidates
    from the LEDGER, not the walk (adversarial-review H2: a reverted / soft-deleted payment's
    pre-E1a residue sits outside the walk), each at its CURRENT settled sense.  The dev-clone
    sweep found the class LIVE on the real Mortgage (a net-zero ±$2,410.95 pair straddling
    2026-06-18 / 07-02, the old latest-date reversal rule's residue): it heals in the first sync,
    assert green, baseline unmoved at $177,277.97 (rolled-back probe).  Hard-deleted residue
    (`transfer_id` SET NULL) is the assert-surfaced F1 class; **the PROD-data sweep is an F3 ship
    gate.**  (3) The assert exposed a latent door: `create_transfer` never posted a transfer BORN
    settled and stamped no `paid_at` (ten fixtures leaned on it; production always creates
    Projected) -- the create chokepoint now applies the update path's two settle rules
    (`TransferSpec.paid_at` explicit or now(); reconcile + loan sync; `paid_at` on an unsettled
    create refused).  (4) One test RE-PINNED, for developer confirmation: the schedule route's
    "overpayment not shown as Extra" pin predated the read switch and stayed green only through
    the unposted born-settled hole; it now pins the ratified contract (a POSTED overpayment's
    actual $500.00 extra renders).  Full suite 7484, pylint 10.00 all three trees, 163 checker
    tests; adversarial review's two High fixed at the root, re-reviewed clean, all Lows applied.
  - [x] **E1b** `fix(loan): an escrow write reconciles the ledger like every other loan write` --
    **SHIPPED `7cbc0271`.**  All seven escrow write routes end on a shared `_commit_escrow_change`
    tail that runs `sync_loan_postings_all_scenarios` before committing, so escrow is no longer the
    one loan-write door that leaves the postings unre-derived; the E1a assert now covers escrow
    writes.  N-3 closed.  The forward-boundary guard STAYS as the settle-frozen-cash protection
    (spec Sec. 4.2), unchanged; under it the sync is always a no-op, so E1b is defense-in-depth (a
    guard regression self-heals the postings) + assert coverage + one exception-free invariant.
    **Developer chose ALL SEVEN** (the uniform invariant) though the trace proved only FIVE can
    attempt to move a split (spec Sec 4.2's guarded ops): rename is name-only and merge is
    planner-verified escrow-per-date-preserving, so their sync is a proven no-op -- **merge's prior
    "needs no reconcile" ruling is REVERSED to "reconciles as an idempotent no-op"** (route +
    `plan_escrow_line_merge` + `_escrow_unchanged_by_merge` docstrings; `test_merge_preserves_settled_payment_split`
    now VALIDATES E1b -- the merge reconciles yet the split stays byte-identical).  No eighth escrow
    write door exists (whole-app grep of every `EscrowLine`/`EscrowComponentVersion` write).  Tests:
    a parametrized forge-heal firing control across ALL SEVEN routes (each heals a stale-dated cash
    entry through its POST; verified to FAIL with the sync disabled) + a baseline-unmoved no-op
    control; shared `linked_net_by_date` test helper extracted (DRY, keeping the service test's
    independence-from-production).  Full suite 7492, pylint 10.00 all trees; adversarial review clean
    (no Critical/High/Medium; 3 informational Lows, one applied).
  - [x] **E1c** `feat(loan): the confirmed view folds from the walk` -- **SHIPPED `ee570bcf`
    (2026-07-24).**  The seam builder `balance_at.confirmed_view(ctx, account)` reproduces
    `confirmed_loan_view` byte-for-byte: the balance is `fold_from_walk` (B2-proven == the posting
    reader) and the history rows re-accumulate the reader's `_replay_history_events` contract-order
    running balance over ONLY the payments/anchors VISIBLE by `as_of` -- sourced from the walk's
    splits/corrections, never the postings.  Additive and unwired (only the oracle reads it); E1d
    threads it into `resolve_loan_bundle` and deletes the reader.  **The three developer rulings
    realized:** the row `remaining_balance` is the visible-subset re-accumulation (NOT the full-walk
    balance-after, which double-counts a future-settled-but-earlier-due payment -- Q1); a BROKEN loan
    (originated, no opening posting) FOLDS where the partial reader returns `None` (B-12, the
    C3b1/C3b3 repairable-cache decision -- Q2), DEMONSTRATED not folded into the equality; and
    `LoanPaymentSplit` carries its `due_date` + resolved `RatePeriod` so a row's displayed rate is
    provably the rate its interest accrued at (Q3), the split staying pure and the due date derived
    ONCE (the merge now returns the governing date it already computes for ordering).  **One scope
    addition, the duplicate-code root fix:** the byte-equal row construction was genuinely duplicated
    with the reader, so it extracted to ONE shared emitter `rate_period_engine.confirmed_amortization_row`
    (a `ConfirmedRowInputs` bundle) both the reader and the walk view now call -- byte-equality is
    STRUCTURAL, not two copies that match.  Oracle (`test_confirmed_view_oracle.py`): every-day
    whole-view equality across nine shapes (the B2 set + biweekly collision, late settle,
    underpayment) + a firing teeth control (a bumped row principal makes the harness raise) + the
    broken-loan and N-11 divergence demos.  Full suite 7504, pylint 10.00 all trees, 163 checker
    tests; adversarial review found no Critical/High/Medium.
  - **E1d is DECOMPOSED into E1d-a / E1d-b** (2026-07-25, on the developer's option-D ruling): the
    cutover needed the whole-loan read to sit INSIDE the seam first, and that move is provable in
    isolation while the cutover is not.
    - [x] **E1d-a** `refactor(balance): the whole-loan read moves inside the seam` -- **SHIPPED
      `35aae5ef` (2026-07-25).**  `app/services/loan_resolution.py` -> `balance_at/_resolution.py`
      (private, W9910-protected, re-exported NOWHERE), and `BalanceContext.resolved_loan` ->
      `balance_at._resolution.resolved_loan(account, ctx)` filling a public `loans` pass-through
      cache through the one `_memoize_once` primitive.  Two hand-written surfaces DELETE rather
      than shrink or travel: the `app.services.loan_resolution` W9909 scope (its only production
      caller was the seam, and E1d makes its confirmed seed a balance-at-T, so Phase D's invariant
      puts the composer on the producer's side of the boundary) and `_context`'s `resolved_loan`
      ruling -- the last public METHOD on the publicly re-exported context, the H1 surface W9910
      structurally cannot see.  Every function body unchanged character-for-character; dev clone
      BYTE-IDENTICAL.  Its adversarial review caught the first cut re-exporting `resolved_loan` /
      `ResolvedLoan` publicly, which would have moved the read from two classified surfaces to zero
      behind an ungated door -- the exact fail-open shape W9909 exists to close; the door is gone
      and the test call sites reach the private module like the six sibling files already did.
    - [x] **E1d-b** `refactor(loan): the resolver's confirmed slice seeds from the walk` --
      **SHIPPED `e0092d0e` (2026-07-25).**  The cutover: `resolve_loan_bundle(account, ctx)` seeds
      every resolution with `balance_at.confirmed_view` (`resolve_loan_seeded` folds in) and the
      three loan-route call sites read the same seam entry; `confirmed_loan_view`,
      `confirmed_loan_history_rows`, and its four now-dead helpers DELETE.  `confirmed_view` also
      stopped loading `LoanParams` -- it reads the origination off the walk's own `is_opening`
      correction, so the date the rows are numbered from and the walk they fold cannot be
      mismatched.  **Three hand-maintained exemptions die with the reader:** W9906's allowlist
      tightens to `{loan_posting_service}` (the readers now have NO `app/` caller at all),
      `loan_payment_service` becomes ledger-free WHOLE so the reconciliation suite's
      function-granularity M-1 fence collapses to a file fence, and `_LEDGER_READ_SWITCH_FUNCTIONS`
      goes with it.  `_LOAN_PAYMENT_SEAM_MODULES` deliberately STAYS (privilege gone, ingredient
      surface remains -- the D1b no-bundled-loosening lesson).  Two RULED semantic changes, both
      pinned: a loan with no OPENING posting FOLDS (B-12 / E1c's Q2; clearing the ledger proven to
      move nothing) and a what-if scenario answers from its anchors.  The E1c oracle retires with
      its counterparty; all nine shapes AND the 13 row-economics pins moved off the deleted reader
      are re-anchored HAND-COMPUTED in `test_confirmed_view.py` (the E1c carried requirement,
      discharged), plus two assertions restored that the deletion would have dropped (on-schedule
      rows byte-checked against the contractual replay; a withheld view resolving identically to
      the un-seeded resolver).  Full suite 7502, pylint 10.00 all three trees, 162 checker tests;
      dev clone BYTE-IDENTICAL across both commits.  **Found and NOT fixed: N-34** (the split's
      rate / escrow still key on the pay-period start, not the due date -- D5's ruling and C2's
      claim; measured $500.00 on one payment, reaching five surfaces, invisible to E1a's assert).
      Gated with a control that flips on the fix; fixing it moves recorded balances, so it is its
      own step.
  - [x] **E1e** `refactor(pylint): W9906 deletes with its last subject` -- **SHIPPED `62cedd7d`
    (2026-07-25).  The step's one-liner said "the readers go package-private"; the trace found that
    would keep ~197 lines of production code alive for its own test suite, and the developer ruled
    for the structural answer instead: DELETE them.**  `confirmed_loan_balance_at` /
    `confirmed_loan_balance_map` had ZERO callers in `app/` and `scripts/` -- E1d-b took the last
    one, the seam had folded a loan's past from source events (C3b1 / C3b3), and the balance sheet
    reads the postings through `ledger_report_service`.  Their only remaining job was to be the
    counterparty an oracle grades the fold and the resolver against, and the reconciliation suite's
    own rule already says where that belongs (`_independent_loan_linked_net` reads through a
    DIFFERENT join shape "so the two cannot share a lookup bug").  So the window moved to
    `tests/_test_helpers.py` (`posted_loan_balance_at` / `posted_loan_balance_map` -- same postings,
    same `entry_date <= as_of` bound, same OPENING sentinel, same sign and rounding) and the
    production pair was deleted.  **The `__init__` re-export door that motivated a guard is then
    unaskable**: it was MEASURED at 10.00/10 (restore one re-export line and a route renders a
    posting-cache balance-at-T with every gate green), and deleting the producer removes the thing
    to re-export.  W9906 deletes whole -- message, allowlist constants, both visitors, its unit
    tests, all eight `--fail-on` locations, the gate-consistency canonical list -- and every W9909
    producer set is now the empty frozenset, the invariant stated as data.  **The replacement is
    stronger, not weaker:** the two spellings a consumer would write rate E0611 / E1101 (hard-gated
    by `--fail-on=E`), where the private-module and attribute reaches were already W9910 / W0212.
    `confirmed_loan_payment_history` STAYS a posting read (the attribution purpose, Section 3).
    Two RULED semantic changes, each stated where it binds: the window answers a FUTURE date
    (carrying the confirmed sum flat) where the reader raised -- that raise was a domain guard for
    callers that no longer exist -- and the per-period form IS the scalar per period, so the old
    map-vs-scalar agreement test became `f(x) == f(x)` and now pins hand-computed values.  The fold
    oracle's lost `end <= today` bound is restored as an explicit assertion, on the oracle's side
    where the anti-sampling floor lives.  Dev clone BYTE-IDENTICAL; full suite 7501; 146 checker
    tests; all three trees zero pylint messages; controls fire (a $1.00 drift reddens 38 tests, the
    disabled OPENING sentinel 8, the new domain assertion raises).  **Adversarial review changed the
    commit three times:** a per-period test that had silently become a tautology, a new
    `line-too-long` the rounded 10.00 score hid, and an overreaching "no public balance producer
    outside the seam at all" claim -- corrected to "single-account", with the statement tier's real
    gap recorded as **N-35**.
- [ ] **E2 (recorded option, developer ruling 2026-07-24; sequenced AFTER E1 at the earliest)**
  the super-package boundary: move the read seam, the write cluster, and the shared leaves
  (`loan_ledger` / `cash_ledger`) under ONE package whose shared internals are private to it, so
  the W9909 classification registry -- the last name-keyed surface -- dissolves structurally too.
  Large reorganization with its own arrow risks (the D0b class); W9910's per-boundary membership
  would need extension.  Recorded so the option cannot be forgotten, NOT committed to: the
  registry's residue is small, fail-closed, and self-attest-pinned, so the reorg must earn its
  churn on its own merits when E1's shape is known.


---

## 6. Phase F -- closeout, as built

**F1 is NOT here.** It is an unresolved operator question -- the Van Loan's `$905.33` true-up
step against the servicer's statement -- so it moved to the live findings ledger rather than into
history.

- [x] **F2** `refactor(analytics): delete the dead year-end summary service` -- **SHIPPED
  `3aecceb0`.** The whole `year_end_summary_service` package + its two test files deleted (the route
  302s; `compute_year_end_summary` had no live caller). R-D's two still-live functions
  (`_compute_mortgage_interest` -> `_loan_year_interest`) RELOCATED to their only caller
  `tax_report_service` (C3c has not landed, so they move rather than die), B-19's false
  `DebtSchedule` hints fixed to the real row-list type, their unique hybrid coverage moved to
  `test_tax_mortgage_interest.py`. **Broader than R-D scoped** (the full suite caught it): four LIVE
  cross-consistency tests reached into the package internals -- repointed to the live producers, or
  the deleted year-end surface dropped from the equality checks. The obsolete pre-fence
  `calculate_balances` git-grep guard went with it (the W9906 fence supersedes it). ~12 stale
  docstring PROVENANCE mentions remain (deferred doc-sweep; not broken code). Net -6.7k lines.
- [x] **F3** prod ship: dev -> main PR for the whole arc per the standard pipeline. -- **SHIPPED
  2026-07-25** (PR #64 `balance architecture: Phases A-E complete`, merge `88c79857`).  CI green
  (`lint-and-test`, 26m), the signed image published by `docker-publish.yml`, and `dev` resynced to
  `main` (0/0 divergence).  The two gates below were closed BEFORE the merge, on a fresh prod clone.
  **Both ship gates are CLOSED on real production data (2026-07-25), and the deploy was
  REHEARSED rather than reasoned about.**  Method: a read-only `pg_dump` of prod restored into a
  scratch database on the dev Postgres instance, then the exact deploy sequence
  (`flask db upgrade` -> `backfill_loan_payment_postings_after_migration` ->
  `backfill_all_account_anchor_postings_after_migration`, the two hooks `entrypoint.sh` runs on
  every container start), with E1a's checked-projection assert live.  Results:
  * **The E1a lineage / N-11 sweep (gate 1): ZERO rows of every class it names.**  No raw
    transaction typed onto a loan, no transfer OUT of one, no linked-ledger entry with a NULL
    `transfer_id` (the hard-deleted residue the heal cannot re-sync), and zero C9a purge candidates
    (the purge migration prints `purged: 0` and warns about nothing).  Every settled payment has
    exactly ONE transfer-source cash entry, dated at its `paid_at` civil date (or its period start
    where `paid_at` is NULL) -- none of the cross-date residue the dev clone carried.
  * **The deploy PASSES, and it is idempotent.**  The assert did not fire; a second run wrote
    nothing (221 entries, same max id).  It DOES rewrite the Mortgage's genesis ledger, exactly as
    C1 designs: `2018-12-01` goes from a posted-and-reversed net `0.00` to the real
    `-202,000.00` opening, and the `2026-03-31` tracking-start stops being the opening and becomes
    a `+23,624.57` reset correction (same total, honest composition).  **No user-visible figure
    moves** -- balances, every-day history, rows, payoff dates, tax figures and paid-YTD chips are
    identical before and after.  The Van Loan's ledger does not change at all.
  * **The C2 history-window live-render (gate 2) reproduces the receipt.**  Rebuilding each loan's
    daily history under the pre-C2 visibility rule and diffing against the shipped one clock, on
    every day of both domains: **today is UNMOVED** (Mortgage `$177,277.97`, Van Loan `$15,205.63`,
    fold == pre-C2 == seam), and history repositions in bounded windows -- Mortgage 32 days in 3
    windows (max 14d), Van Loan 22 days in 3 windows (max 9d).  Every window is one event's date
    shift, and the archived receipt's one worry is GONE: its first Mortgage window was a
    `$178,375.43` FALSE ZERO, and with C1 shipped that window now reads the origination principal
    held flat (delta `$23,896.59` = `202,000.00 - 178,103.41`), the plateau C1 promised.  This is
    what made C1-before-C2 a correctness gate rather than a preference, confirmed on real data.
  * Also verified: the dev clone has DRIFTED from prod (it carries an escrow line merge/rename
    effective 2026-07-06, a `paid_at` edit re-periodizing one Mortgage payment, and is missing the
    Van Loan's July payment).  Its live-verifies remain valid as before/after REGRESSION checks --
    which is how the arc used them -- but they are not prod-shape checks, which is why these two
    gates had to run on a fresh clone.  The escrow change ruling R-A cites exists only there.


---

## 7. Closed findings register

Every finding the arc CLOSED, with the commit that closed it. IDs keep their names so older
references resolve. The rows that are still OPEN are in the live document's Section 6, not here.

| id | finding (one line) | worst measured | status | closed by |
|---|---|---|---|---|
| B-1 | Future-origination loan posts no OPENING; seam 500s five pages when the date arrives | outage | **closed (`4e46a0a8`)** -- reproduced (configure 2026-03-20 / close 04-15 / read 05-07, no re-sync: hero AND map both raised), control fires | A3 |
| G1 | `grid.create_baseline` resynced account anchors but NOT loans, so a loan configured while its owner had no baseline (its opening posts per SCENARIO, and there was none) stayed opening-less through the one recovery path -- every loan surface 500s, with no way back short of re-saving the loan's params | outage | **closed (`4e46a0a8`)** -- reproduced (delete the baseline, POST /create-baseline, read the loan: raised); control fires | A3 |
| B-2 | Property equity chart derives debt from schedule rows; wrong on 8/13 shapes | $299,701.35 | **closed (`821dd0eb`)** -- the debt line reads `balance_at.positions()` (the fold) for the confirmed + projected tiers; the axis clamp and empty-schedule clip are gone; dev-clone live-render reconciles to the hero at today ($177,277.97) | C5 |
| B-3 | Grid renders a loan's balance RISING (cash producer on an amortizing account) | +$1,910.95/mo, unbounded | **closed (`f11382a0`)** | A1 |
| B-4 | `_forward_rows` `is_confirmed` filter had zero discriminating tests -- **measured: the filter could be deleted and all 7,401 tests passed** | $4,449.72 archived; $48,496.25 on A2's fixture; unbounded (= `last_confirmed.remaining_balance - projection_seed`) | **closed (`c96c62be`)** -- value pinned inside the only window where the filter decides anything, control shown to fire | A2 |
| B-5 | Balance sheet renders a negative liability, HTTP 200 | -$7,643.80 | **mechanism closed (`4e46a0a8`)** -- the clock-dropped opening that let a payment split against a ZERO balance is gone; reproduced on the ordinary settle path (payment settled early, due AFTER a future origination: interest $0.00 / principal $0.00 / **excess $1,073.64**, whole payment booked as a Refund Receivable and the Schedule-A interest erased -> $833.33 / $240.31 / $0.00). The linked ledger netted to exactly $0.00 -- the loan reading as owing NOTHING while the borrower's cash sat in a Refund. **Invariant closed (`545799fb`)** -- `sum(postings) == fold(ACTUAL events)` asserted per visible date after every loan sync; a divergent ledger rolls back at the write that caused it (control fires on a forced $1 drift) | A3 (mechanism), E1a (invariant) |
| B-6 | Taxes tab prints interest for a loan the seam refuses to value | $4,156.61 | **closed (`99cc2816`)** -- the Taxes tab reads the fold-based `balance_at.loan_interest_in_year`, which answers from source events for the settled past (no schedule fallback for a loan the posting cache cannot value), so the interest figure and the balance come from the ONE total producer; the no-opening fallback is gone, demonstrated by `test_cleared_ledger_still_answers_from_the_fold` | C3c |
| B-7, B-10 | Year-end omits a true-up payoff; spends a fabricated `jan1=0` | $255,300.26 | dead code; deletion ruled (R-D) | F2 |
| B-8 | Fail-loud misses future valuations (returns before the ledger read) | unbounded | **closed (`f410afa9` scalar; `84e386c6` map)** -- both producers now fold a broken loan from SOURCE facts for past AND future, so no fail-loud path remains at either to be inconsistent about | C3b1 (scalar); C3b3 (map) |
| B-9 / FU-7 | Projection pays down overdue installments nobody paid | -$15,755.38/period | **closed (`f445aa77`)** -- the forward branch folds `loan_plan` (payment RECORDS + future contractual synthesis), which never synthesizes a strictly-past installment, so an overdue installment with no settled record holds the balance flat; verified on the dev clone (both real loans current, 0 overdue-clamped) and in the reworked seam tests + the C6a fold oracle | C6b |
| B-11 / FU-4 | Period before the ledger's opening renders the loan debt-free | $17,134.85 | **closed (`18fd3a04`)** -- origination is the opening now, so a pre-tracking date reads the origination principal held flat (the plateau), never debt-free; verified $0 -> $202,000/$32,402.45 on the real loans; aged-out of the /savings trend window meanwhile, closed at the producer; B2's tracking-start shape pins the plateau | C1 |
| B-12 | Unfenced producer tier below the fence; `loan_resolver` package wholly unfenced | -- | **closed (`9f592502` structural + `6e3a6c79` scope)** -- D2a deleted the tier's only balance carrier (`LoanState.current_balance`), so `resolve_loan`'s bundle can no longer hand out a balance-at-T; D3 then scoped the whole package for W9909 (package key, 7 rulings), so a NEW public balance producer born there errors at its definition (control fired) | D2a (structural); D3 (fail-closed scope) |
| B-13 | Loan detail route answers a broken loan from the money-blind replay | $199,600.80 | **closed (`c98ea07b`)** -- the route reads `balance_at.balance_at` (the fold) now, not `LoanState.current_balance`; the route test renders a broken loan's page at the fold `$231,200.00`, never the replay `$239,761.08` (control fires) | C4 |
| B-14 | `loan_recurrence_sync` persists a payoff date off the blind walk | -- | **closed (`2f0130f5`)** -- the bound is DERIVED from the seam (`loan_figures.payoff_date` -> `recurrence_end_date`), never walked off `state.schedule`; the retired / never-pays-off split is `is_retired`, not an empty schedule. Dev-clone live-verify found the shape the finding named: the real Mortgage's STORED `end_date` is `2048-11-01` while both the pre- and post-C8d derivations say `2048-12-01` -- a persisted copy drifted from the computed one, self-healing at the next payoff-affecting mutation | C8d |
| B-15 | Kind-blind true-up writes a cash anchor onto a LOAN (had fired: both real loans carry rows) | -- | **closed (`f11382a0`)**; residue N-4/N-5 | A1 |
| B-18 | Cash scalar fabricates pre-anchor balances from today's anchor | **$2,932.41 returned for 2026-06-03 on prod data, 2026-07-25** | **closed (`d3489728`)** -- the fold replays every assertion, so a pre-anchor date reads the balance in force then rather than today's | X-c2b2 |
| B-19 | False `DebtSchedule` type hints in `_income_tax` | -- | **closed (`3aecceb0`)** -- hints fixed to the real `dict[int, list]` / `list` row type on the relocation to `tax_report_service` | F2 |
| B-20 | True-up-paid-off loan shows origination as payoff date, no badge | -- | **closed (`2f0130f5`)** -- the origination fallback is gone at the source (`LoanState.payoff_date` deleted), and a retired loan's `payoff_date is None` + `is_retired` badges "Paid off" on the detail chip; route test asserts the origination month is ABSENT from the page (control fires) | C8d |
| B-21 | `TestBrokenLoanFailsLoud` cash fallback asserts `is not None`, not the value | -- | **closed (`c96c62be`)** -- pinned at the $150,000.00 anchor | A2 |
| B-22 | Dead `insert_origination_event` fixture helper | -- | **closed (`18fd3a04`)** -- helper + its no-op seeds deleted; test loans now match production (origination synthesized, no stored row) | C1 |
| N-12 (B1) | **The two ledger readers disagree about when an anchor becomes visible.** `confirmed_loan_balance_at` bounds an anchor by `LEAST(entry_date, period.start)` (`_asof.effective_date`); `confirmed_loan_history_rows` bounds its non-payment events by raw `entry_date` (`_reader.py:_classify_linked_nets`). They therefore diverge for any `as_of` in `[period.start, entry_date)` -- two readers of ONE ledger, contradicting each other about one loan. Measured on the real Mortgage: on 2026-03-26..03-30 the scalar says **$178,103.41** and the history rows' last `remaining_balance` says **-$272.02** -- a NEGATIVE liability, the B-5 shape. Contained today, not by design but by two unrelated gates: a user true-up is schema-bound to `anchor_date <= today` (`routes/loan/params.py:244`), and the future-origination case is stopped by N-10's four `origination_date` predicates -- so no surface passes an `as_of` inside the window. One clock retires both bounds | $178,375.43 (the divergence; the rendered figure is a negative liability) | **closed (`eb5de4ac`)** -- the scalar now bounds anchors by `entry_date` (their own civil date), the same rule the history reader already used, so the two agree | C2 (`remaining_balance` itself dies at C6) |
| FU-5 | Settled payment into an unoriginated loan vanishes | $1,200 test case; **$3,220.92 phantom cash** on the generator found at C9 | **closed (`2976614b` + `7c021281` generator; `d5a02ad2` guard)** -- the fold erases such a payment ($0.00 principal, the whole cash to a Refund Receivable) while the cash side still debits, so it is now REFUSED at both transfer write doors on the SHARED installment derivation, and the recurrence can no longer generate one (`start_date` bound). The shape's real source was not the hand-made transfer the finding names but the app's own loan-payment setup, which generated 3 of 4 payments pre-origination on a mortgage closing next month; existing rows are purged, settled ones reported | C9a (generator + data); C9b (guard) |
| FU-8 | Empty schedule admits the whole contractual walk as back-projection | $197,049.32 class | **closed (`821dd0eb`)** -- an empty schedule now draws NO back-projection (`_back_projection_by_month` returns `{}`); the loan's real balance comes from the fold, which answers $0.00 after payoff | C5 |
| cash D1 | Settled post-anchor transactions counted by NO producer | $9,431.72/17 days archived; **re-measured on prod 2026-07-25: $2,108.15 invisible at that instant, and $53,880.81 gross across 130 rows in 45 assertion gaps historically** | **closed (`d3489728`)** -- the fold counts a settled row from the day its money moved; on the prod-shape clone Checking's scalar moved `+$32.48` onto the figure its own posted ledger already carried, and the Money Market `-$2,000.00` | X-c2b2 |
| cash D2 | Scalar is period-flat; contradicts the daily series | $999.48 on 07-16 archived; **re-measured 2026-07-25: $15.96 on Checking that day, $246.36 at the worst day of the current period** | **closed (`d3489728`)** -- the scalar, the period map and the daily series are ONE running total sampled at three grains, so they cannot differ | X-c2b2 |
| cash D3 | Pre-anchor: scalar fabricates, map omits; every re-anchor rewrites the whole past | **re-measured 2026-07-25: the scalar returns $2,932.41 for 2026-06-03 while the map omits all 8 pre-anchor periods** | **closed (`d3489728`)** -- every assertion is replayed, so a past date reads the balance in force THEN and every requested period is present; Checking's eight blank columns gained real balances | X-c2b2 |
| N-1 (07-16) | Archived X0 rule would double-count early-settled transactions | 15 real pairs | plan defect, corrected | R-B / X-c |
| N-2 (07-16) | Settle-time freeze reads the clock (`loan_payment_service.py:762`) | -- | **closed (developer ruling 2026-07-24, E1 decomposition)** -- C7's drift warning surfaces a stale frozen amount (`a3f15aed`); the write-side clock read itself is ACCEPTED-BY-DESIGN: the freeze writes SOURCE cash (the shadow's `actual_amount`) the walk then splits, so it cannot desync the checked projection; the split keys on the DUE date (R-A), and the capture rule IS the ratified D3 cash semantics ("the cash you would pay if you settled now").  No code change | C7 (surfacing); E1 ruling (write-side) |
| N-3 (07-16) | Escrow writes never trigger a posting sync (guard-only protection) | -- | **closed (`7cbc0271`)** -- all seven escrow write routes reconcile through `sync_loan_postings_all_scenarios` before committing, so the E1a checked-projection assert now covers escrow writes; the forward-only guard stays as the settle-frozen-cash protection (not the sole one); a stale posting self-heals through any escrow write (forge-heal firing control, verified to fire) | E1b |
| N-6 (A2) | `_loan_year_interest`'s `not row.is_confirmed` guard fires on NO live path today: it would only matter when the ledger answers for interest but not for the schedule, and both gate on the same `_has_opening_posting` (`_reader.py:310` vs `:152`), so `_payoff.py:285` has already swapped the replay's redistributed rows for raw-due-dated ledger rows that `settled_due_months` alone excludes. **That unreachability is a cross-module coincidence, not a structural guarantee** -- `confirmed_loan_view` ALSO returns `None` for `as_of > today` (`loan_payment_service.py:529`) where the interest reader has no `as_of` at all, so a caller passing a year-end `as_of` makes this guard the only thing between the Taxes tab and a double-count. Kept, untested: the state is unreachable, so a control would have to hand-build the rows (the anti-pattern B-17 names) | -- | **closed (`99cc2816`)** -- `_loan_year_interest` deleted whole at C3c; the fold-based producer's `not row.is_confirmed` is the always-reachable projected-rows filter (source it only from unconfirmed rows), not a dead guard, so nothing unreachable is kept | C3c |
| N-9 (A2) | **Schedule A counted a CAR LOAN's interest as home-mortgage interest.** The pre-fix `_load_debt_accounts` selected on `has_amortization` alone -- set on AUTO_LOAN, STUDENT_LOAN, PERSONAL_LOAN and HELOC as well as MORTGAGE -- so every amortizing account fed `_compute_mortgage_interest`. Personal interest is not deductible at all; student-loan interest is above-the-line, never Schedule A. Root cause is its own docstring: "Mirrors `_load_common_data`'s `debt_accounts` selection" -- one predicate answering two questions (Section 8's lesson, live) | **$5,221.16** measured on the suite's split-loan fixture; inflates itemize-vs-standard, so it can advise itemizing when the standard deduction wins | **closed (`44cbd028`)** -- `_load_debt_accounts` -> `_load_mortgage_accounts`, selected by account_type ID; HELOC deliberately excluded (use-of-proceeds unknown, documented like property tax); negative control shown to fire | own commit, found building A2's oracle |
| N-7 (A2) | The live Taxes number's only test used `_compute_mortgage_interest` as its own oracle -- a double-count inside it moved both sides and shipped green (demonstrated) | interest deduction, unbounded | **closed (`c96c62be`)** -- hand-computed live-path oracle ($500.00, paid-date basis) | A2; C3 must grade its rebuild on it |
| N-8 (A2) | ~~The loan write walk stamps postings with the WALL CLOCK, visible in test logs as `Posted anchor correction (source 6 as of 2026-07-16)`~~ **WITHDRAWN 2026-07-16 (A3): misattributed, on two counts.** (a) **Source 6 is `account_opening`** (`PostingSourceEnum` order: transfer 1, transaction 2, loan_payment 3, loan_opening 4, loan_trueup 5, account_opening 6) -- that log line is the ACCOUNT anchor path, not the loan walk. (b) That path reads **no clock at all** (`grep date.today() app/services/account_posting_service/` is empty); its `entry_date` is the assertion's own instant. The 2026-07-16 under a frozen-to-2026-03-20 suite is fixture ORDERING: `seed_user` creates Checking before `freeze_today` applies. The LOAN's anchor corrections stamp the **anchor's own date** (observed: `source 4 as of 2026-04-15` for an origination dated 2026-04-15), never the clock -- the walk's clock read was in the FILTER (which anchors were admitted), never in the stamp, and the filter is what A3 deleted. No defect here | -- | **withdrawn, no code change** | -- |
| N-11 (B1) | **A raw settled transaction typed onto a loan account moves the POSTED balance but not the fold.** Its cash leg books onto the loan's linked ledger and `confirmed_loan_balance_at` sums every linked posting with no kind filter (`_reader.py:167-176`); the reader's own classifier names the case ("a raw settled transaction typed onto the loan account", `_reader.py:623-633`). The fold cannot see it: its payment set is transfer-linked shadows only (`settled_income_shadows`). **This is the one shape where the ledger is RIGHT and the fold is incomplete**, which inverts the "postings are a stale cache" framing: someone acting on it would "repair" a genuine event away. Ruled R-E (forbid at the source). **Reachability proved BROADER than first recorded:** beyond the create routes (`create.py:78` accepted any owned `account_id`), a recurrence TEMPLATE targeting a loan (the engine copies `template.account_id`) and the SALARY-PROFILE auto-picker (found in adversarial review) each generate raw transactions onto the loan -- all three now refuse an amortizing account. B2 demonstrates the divergence is real ($300 forced) and asserts the sources refuse it. | **$300.00** measured on a probe; unbounded (any typed amount) | **closed (`dba91dc0`)** -- all three sources gated; control shown to fire | BG |
| N-10 (A3) | An anchor's read bound is `LEAST(entry_date, pay_period.start)` (`_asof.effective_date`), a period-START rule, so a FUTURE-dated origination is visible from its containing period's START. Measured: origination 2026-03-25 read on 2026-03-20 -> **$200,000.00** from `confirmed_loan_balance_at`, and the same from `confirmed_loan_balance_map` for the current period. No surface renders it: **FOUR** consumers each ask `origination_date` first -- `amortizing_balance_at`, `_build_amortizing_balance_map`, `confirmed_loan_view`, and `balance_at.loan_ledger_domain` (the 4th found by A3's adversarial review; before its guard, `confirmed_loan_ledger_domain` flipped `None` -> a real `opening_balance=$200,000.00` for an unclosed mortgage, and the year-end clamp's not-borrowed guard was left load-bearing on statement ORDER). Four predicates standing where one honest rule belongs ("a safety that is a predicate is not a safety", Section 8). Pinned in the suite (`test_seed_is_none_before_the_loan_originates` asserts the $200,000.00 leak, so C2 has a test to flip). The honest bound is the anchor's own civil date (D5/R-A), which moves history and is therefore gated on C1 (probe-proven: one-clock without the origination event reads $0 for 6 days x $178k at the Mortgage's tracking boundary) | $200,000.00 contained | **leak closed (`eb5de4ac`)** -- the reader bounds the opening by its `entry_date` (the origination), so a future origination is not yet visible and reads the honest `0.00`; the pin flipped. The four guards become redundant: #1 `amortizing_balance_at` deleted at C3b1, #2 `_build_amortizing_balance_map` deleted at C3b3, `confirmed_loan_view`'s STAYS (B-1, clock-independent), `loan_ledger_domain`'s guard SITE deleted at C3b4 (the reader is gone); the shared `_is_originated` fn STAYS (`loan_figures`/`is_retired`/`is_paid_off`) | C2 (leak); C3b1/C3b3 (guards #1/#2); C3b4 (guard #4 site) |
| N-13 (C2) | **Editing a settled payment's `paid_at` does not re-date its postings**, so since C2's settled-date clock the balance's visibility date does not follow an edited settle date. `paid_at` is not in `transfer_service._POSTING_RELEVANT_FIELDS` (it changes no leg), and the loan reconcile is leg-delta based, so a `paid_at`-only edit resyncs the account anchors but leaves the loan correction at its original `entry_date`. Harmless pre-C2 (visibility was period-based); now latent -- and it is the reason a per-visible-date write assert would self-500 (the fold moves, the posting stays, no self-heal), which is what forced the E1a ruling: fix the reconcile, then assert at full strength | -- | **closed (`545799fb`)** -- all three posting reconciles keyed per `(pay_period_id, entry_date)`; a settled `paid_at` edit triggers them and converges in ONE pass in both directions (mutation-verified); the pre-E1a cross-date residue class self-heals via the lineage probe | E1a |
| N-16 (C8b) | **The forward fold's ESTIMATED tail stops at the CONTRACTUAL payoff, so an underpaying loan reports no payoff.** `_estimated_from_contract` synthesizes installments only to the contractual last row, so a loan paying below contract leaves a residue there and `plan_payoff_date` / `loan_payoff_date` return `None` -- even a cent-scale drift. Likely the real Mortgage: its `$0.34/mo` drift (C7) compounds to ~$43 at the Dec 2048 payoff, so a naive cutover would flip its loan card from a date to "no payoff within term." The resolver's forced-contractual date (via `is_last_month`) is also wrong (a phantom final payment). Developer ruled (2026-07-19) EXTEND the tail past contractual until the balance clears (capped), over accept-`None` or a tolerance-snap band-aid | ~$43 residue -> `None` (real Mortgage, illustrative; to verify on the dev clone) | **closed (`8ff9a11e`)** -- `_estimated_from_contract` extends 60 months past the contractual payoff at the level P&I, so a drifted loan clears at its true slightly-later date; `None` now means genuine non-payoff. Healthy loans unmoved (first-crossing wins). **The real Mortgage does NOT drift past contract after all** -- dev-clone live-verify at C8d (the outstanding item, now DONE): its derived payoff is `2048-12-01`, the contractual date, and its balance reads `$0.00` at the 2048 / 2049 / 2050 year-ends, so the predicted `~$43` residue and the horizon-band move never occur. The `~$43` was flagged illustrative when written; the drift is confined to the 24 materialized shadows (24 x `$0.34`) and the contractual schedule's residue-absorbing final row clears it. The extension stays load-bearing for a genuinely underpaying loan (its own oracle) | C8c |
| N-15 (C8) | **The forward fold drops a standing `extra_principal` past the ~24-month record horizon.** `loan_plan`'s PLANNED tier folds the extra (live D3 cash) only for the materialized pay-period window (52 biweekly periods, `config.py:199`); its ESTIMATED tail (`_plan.py:240`, seeded extra-free at `loan_resolution.py:295`) reverts to contractual P&I with no extra, while the resolver's `committed_forward` applies it to EVERY forward month for the full term (`_payoff.py:459`, `_projection.py:675`) -- two contradictory forward models, uncovered (the sole standing-extra test asserts resolver surfaces only, never the fold). Illustrative: a $300k/30yr/6% loan with a $400/mo extra pays off ~20.5yr (resolver + loan card today) but the fold/`positions()` would show ~29.5yr, and the equity debt line would sit years high | ~9yr payoff (illustrative); real data $0 (no standing extra) | **closed (`2e5d3a75`)** -- the ESTIMATED tail folds the standing extra now, matching the resolver's full-term committed schedule on every month (proven by `test_standing_extra_folds_past_the_shadow_horizon`, an independent-producer parallel run with a post-horizon teeth) | C8a |
| N-20 (C8f) | **The required-extra search returned amounts that did not do what they said, twice over -- found by a randomized sweep, invisible to hand-written cases.** (1) The answer was rounded to the NEAREST cent; the threshold rarely lands on a whole cent, so rounding DOWN left a fraction of a cent owing, and at the payoff boundary that pushes the payoff a whole INSTALLMENT past the target -- **99 of 300 generated loans returned an extra that missed**. (2) The bisection ASSUMED the loan's own balance was a valid upper bound ('pay the whole balance as extra and the first installment clears it'), which is false whenever the period interest exceeds the payment cash: `split_payment_cash` takes interest out FIRST, so on $100,000 at 25% against $500 payments an extra of the full balance still leaves $1,583.33 owing, and the search then bisected against a bound that never satisfied its own predicate and returned garbage. Fixed: the bound is DOUBLED until it genuinely reaches the target (capped, else `None`), and the answer is the exactly-minimal CENT (try `ceil(low)`, else `ceil(high)`) so it can neither miss nor overcharge. Both controls shown to fire; the sweep is now a permanent seeded test | 99/300 wrong answers (measured) | **closed (`fe424560`)** | C8f |
| N-21 (C8f) | **A target date in the PAST was answered with a six-figure extra instead of "not achievable".** The reachability guard and the search's own predicate both keyed on the clearing installment's DUE date, but ruling D1 clamps an overdue-but-still-projected payment's EFFECTIVE date to `as_of + 1d` while its due date stays in the past. One such record -- i.e. exactly the delinquent loan this arc exists for -- made a past target look reachable, the fold "cleared" the loan on a past due date, and the panel rendered `Add on Top of Your Current Plan: $248,854.17/mo` for a date on which no money can be paid. A REGRESSION: the retired `required_extra_for_projection` guarded it explicitly (`target_months <= 0 -> None`). Root fix: every comparison in the search keys on the EFFECTIVE date (when the cash actually moves); the payoff DATE the seam reports stays the DUE date per C8b. Note the schema still has no minimum on `target_date` | `$248,854.17/mo` rendered for a past date | **closed (`fe424560`)** -- the randomized sweep now generates D1-clamped records and catches it independently of its unit test | C8f |
| N-22 (C8d) | **A loan that never pays off was DROPPED from the debt-free date, so /savings claimed debt-free while the loan still owed.** C8d made `payoff_date` legitimately `None`; both debt-free producers filtered those out and took `max()` over the rest. Measured: a $900,000 never-clearing loan beside a healthy $12,000 one reported `projected_debt_free_date` 2028-03-01 -- the small loan's payoff -- on a page whose own loan chip says "No payoff at current payment", and `_resolve_horizon_domain` planted a "Debt-free" milestone there. With EVERY loan in that state it returned `is_loan_free=True` and gave a borrower the loan-free horizon window. Impossible pre-C8d (`LoanState.payoff_date` was non-nullable). Fixed: an active non-retired loan with no payoff POISONS the aggregate -- no date, `is_loan_free=False` -- and the cockpit SAYS "No debt-free date at current payments" rather than omitting the line, matching the treatment its loan page already gives the same condition | debt-free 2028-03-01 claimed against $912,000.00 owed | **closed (`2f0130f5`)** | C8d |
| N-17 (C8d) | **Three modules sit on the W9906 resolver allowlist that no longer resolve a loan.** `_LOAN_RESOLVER_MODULES` exempts `loan_payment_service`, `_transfer_loan_posting`, and `app.routes.loan` from the `resolve_account_loan` / `resolve_loan_seeded` / `resolve_loan_bundle` fence, but a whole-repo grep finds NO call to any of the three in any of them (only docstring mentions) -- they shed their direct resolves at C4 and C8a. After C8d removed the fourth (`loan_recurrence_sync`, whose exemption this step actually killed), the read-pass memo is **the only remaining caller in `app/`** -- the arc's "one resolution site" goal, reached without anyone noticing (D-ctx-a re-keyed that entry `resolution_context` -> `balance_at._context` when the memo moved into the seam, but the three DEAD entries are untouched -- their removal is still D3's). A stale ALLOWLIST entry is fail-OPEN for that module (it permits a bypass nobody currently makes), not a wrong number, so it is a guard gap rather than a live defect. Removing the other three belongs to the Phase-D fence pass, not to C8d's scope | -- | **closed (`6e3a6c79`)** -- D3 deleted the whole resolver call surface (its reason died with `LoanState.current_balance` at D2a), so the three dead exemptions went with it; the finding's fail-OPEN rot class is extinct, since every surviving name list is a fail-CLOSED classification | D3 |
| N-26 (D0a) | **pylint's stock `import-private-name` (C2701) does not flag `from pkg._module import public_name`** -- only `from pkg import _module`. Measured on a 2-file probe: the first form rates 10.00/10, the second is flagged. The unflagged form is the natural one and the one D1 depends on being caught, so the "engine cluster private inside the seam package" step CANNOT rest on the stock extension; it needs the custom package-privacy checker (D-gate). Recorded because the alternative -- assuming the stock gate covers it -- would have shipped D1's ~60 fence-entry deletion against a fence with a hole in it | a fence that permits the bypass it exists to stop | **closed (`0ba7ecc8`)** -- the unflagged form re-measured on pylint 4.0.5, then covered by W9910's segment rule (a dedicated N-26-form test pins it) | D-gate |
| N-28 (D1a) | **Relocating a name out of a W9909-scoped module silently un-scopes it, and the two fence lists were being treated as one.** D1a moved `resolve_anchor` / `load_balance_transactions` / `live_amount_overrides` / `period_subtotal(s)` out of `balance_resolver` into two new modules, and left both new modules off BOTH fence lists on the reasoning that they call no producer. The first half is right (W9906's allowlist), the second is not (W9909's completeness scope), and conflating them removed the fail-closed default from the two files likeliest to grow the next cash producer -- they hold every ingredient of a balance-at-T, and X2 builds the cash fold on top of them. Measured in the step's own adversarial review: a public `running_balance_map` in `period_flows` folded from `resolve_anchor` + `period_subtotals` + `round_money` touches no fenced NAME, so it rated **10.00/10**, and a route consuming it rated 10.00/10 too. The generalisation is the finding: **W9909's scope must follow a relocated public name, or the move itself is the hole** -- a deny list keyed on module identity fails open the moment a module is created | a balance-at-T on a screen outside the seam, every gate green | **closed (`a2149145`)** -- `_CASH_EVENT_SOURCE_MODULES` scopes both new modules for W9909 while keeping them off the W9906 allowlist; probe re-run fires | D1a |
| N-27 (D1) | **The net-worth reducer with no callers, and the `abs` nothing pinned.** `net_worth_kernel.sum_net_worth_at_period` had ZERO production callers (app/, scripts/, templates, no dynamic reach) while four docstrings named it as the home of the asset-plus / liability-minus rule -- including the balance seam's own front door. The live reduction had silently moved to `_net_worth._sum_composition_at_period` (banded) with `compute_net_worth_series` deriving from it, and to `compute_net_worth_today` for the hero; the dead copy's only tests graded it against hand-built dicts (the B-17 anti-pattern), so it read as covered. **The deletion exposed the real defect:** those tests were the repo's ONLY negative-sign liability assertions, and the surviving rule has TWO `abs(bal)` sites. Every live liability fixture stores a POSITIVE balance, where `abs` is a no-op -- measured: the per-period band's `abs` could be deleted and all **7466** tests passed. A Credit Card's balance is stored NEGATIVE, so a regression would add a debt to the ASSET side and put the hero and the trend in contradiction on one page | `abs` deletable with a green suite; a $1,000 swing on a $500 card, hero vs trend disagreeing | **closed (`cef81202`)** -- reducer + its 5 tests deleted, W9909 ruling with them (the reverse-staleness guard fired); one new real-path test per `abs` site, each control shown to fire | D-dead |
| N-30 (D1c) | **The cash balance layer is three flat modules plus five functions stranded in a PRODUCER, and its fence scope is a hand-written list.** D1c as written moved `balance_calculator` whole on a claim of "ZERO out-of-cluster consumers"; an AST scan measures one -- `period_flows.py:138` calls `sum_projected`, and `period_flows` is the module D1a deliberately placed OUTSIDE the seam. The call graph takes five explicitly-ruled NON-producers with it (`sum_projected`, `income_amount`, `_expense_amount`, `_entry_aware_amount`, `entry_checking_impact`), so the producer module has been holding the per-row checking-VALUATION rules all along -- including `entry_checking_impact`, the entries-aware reservation formula behind the grid-vs-savings divergence, reached by a sibling (`balance_resolver._entry_aware_amount_dated`) that documents itself as "mirroring" it. The generalisation is the finding: the LOAN side's facts / split / chronology live in ONE package the fence scopes with ONE key, while the CASH side's equivalents are scattered across `cash_events` / `period_flows` / `balance_calculator` and scoped by the hand-written `_CASH_EVENT_SOURCE_MODULES`. The asymmetry IS the hole, and structuring the cash side like the loan side deletes the hand-written scope | structure; the formula it strands is the $160 vs $114.29 (F-002 Pair C / E-25) class | **closed (`70cc04c2`)** -- the cash layer is now the `cash_ledger` package (`_facts` / `_amounts` / `_flows`); the mirror `_entry_aware_amount_dated` / `_sum_period_as_of` DELETED (the window is a parameter of the one rule), `entry_checking_impact` gone private, and `_CASH_EVENT_SOURCE_MODULES` collapsed to the one prefix-matched `_CASH_LEDGER_MODULES` key | D1c |
| N-31 (D1c) | **Relocating a module INTO the seam silently un-scopes it for W9909 -- N-28's rule, reached from the opposite direction.** `balance_at` is deliberately NOT on the W9909 registry ("its public functions ARE the seam entries every consumer is supposed to call"), so the moment an engine module becomes `balance_at/_kernel.py` and its ruling deletes, a new public producer born there is unclassified AND unfenced (its name is not in `_BALANCE_PRODUCERS`). Measured on this tree, both halves: a public `probe_balance_map_at` added to `balance_at/_positions.py` rated **10.00/10**, and an `app/routes/probe_consumer.py` importing it privately and rendering it rated **10.00/10** under the full CI `--fail-on` set (exit 0). Stock `import-private-name` does not cover the import form either (N-26). So the engine-cluster rulings must TRAVEL with their modules at D1d and delete at D3, once D-gate has made the boundary structural -- the same "only the TIGHTENING is safe" split D1b reached from the other side (the W9906 allowlist collapse is the tightening; the W9909 deletion is the loosening) | a balance producer inside the seam, and a route rendering it, with every gate green | **closed (`6e3a6c79`)** -- travel done at D1d (`229a7889` / `34bf0446`); D3 deleted the five engine rulings now that D-gate makes the boundary structural.  ONE traveled ruling deliberately SURVIVES: `_context`'s, because `BalanceContext` is publicly re-exported and a public METHOD on it is reachable with W9910 blind (measured at D3's review, probe re-fenced) -- the finding's rule, applied once more at the boundary structure cannot reach | D1d (travel); D3 (deletion; `_context` kept) |
| N-32 (D1d) | **A lazy in-function import survived for months on a FALSE cycle rationale.** `net_worth_kernel.build_account_balance_map` imported `net_worth_investment`'s two growth builders lazily inside its INVESTMENT / APPRECIATING branches, each `# pylint: disable=import-outside-toplevel` claiming it "breaks the `net_worth_kernel -> net_worth_investment` cycle." There was no cycle: `net_worth_investment` imports nothing back (its OWN module docstring said "imports NOTHING back... carries no cycle"), so the two docstrings contradicted each other and the disable guarded nothing. Pre-D1d it was inert (both modules outside the seam); the move made it a `disable-rationale` honesty problem -- a lazy import with no honest reason to be lazy. The stock `import-private-name` checker that might have flagged the cross-package reach does not exist in this pylint version (N-26 territory), so nothing caught it. Fixed at D1d commit 2: once siblings in the package, promoted to a top-level `from . import _investment` and the dead disables deleted; behavior-preserving (same functions, byte-identical args), cycle verified absent (pylint `cyclic-import` clean, all import orders clean, dev-clone investment / property balances unmoved) | a dead disable + two contradictory docstrings, gate-green | **closed (`34bf0446`)** | D1d |
| N-34 (E1d-b) | **The split's RATE and ESCROW still key on the payment's PAY-PERIOD START, not its DUE date -- ruling D5/R-A says the due date, and C2's line claimed it shipped.** ORDERING did move to the due date (`loan_ledger.merge_anchor_and_payment_events`) and VISIBILITY to the settled date, but `_split.split_one_payment:233` resolves the rate period on `shadow.pay_period.start_date` and `_walk._replay_events:160` resolves escrow the same way.  A pay period starts up to ~2 weeks BEFORE the installment it pays, so a rate or escrow version effective inside that window governs the wrong side of the boundary.  **Measured** (E1d-b probe, SPLIT_LOAN): a 12% rate effective 2026-01-25 -- strictly between period 1's 2026-01-16 start and its 2026-02-01 due date -- does NOT govern that payment; it splits at 6% (interest $500.00, principal $500.00, balance $99,500.00) where due-date keying gives 12% (interest $1,000.00, principal $0.00, balance held at $100,000.00).  The split is the single source for FIVE surfaces, so the error propagates to all of them: the owed balance, the posted ledger's `loan_interest` leg and derived principal (`_payments.py:213-226`) and hence the payment-history table, the Schedule-A tax interest (`balance_at._loan_interest:270`), and the paid-YTD chips.  **E1a's checked-projection assert CANNOT catch it** -- both sides derive from the same walk, so it stays self-consistent while wrong. D5 measured it as moving nothing on the real loans (their period-start-to-due-date windows contain no version change) and said "gate it anyway"; that gate existed as a pinned-defect control, and C2b flipped it into the fix's pin (`test_confirmed_view.py::TestSplitInputsKeyOnTheDueDate`).  **Its scope was WIDER than this row: six sites, not two** -- the PLANNED forward tier and the live-cash derivation key the same way, and the cash==split invariant (the cash CARRIES the escrow the split BACKS OUT) forces them to move together; the escrow forward-only guard's boundary moved with them, because a period-start boundary becomes too permissive the moment the split reads the due date | $500.00 interest on ONE payment (measured); unbounded in the rate delta | **closed (`c2d43332`)** -- and on PROD data the re-key moves NOTHING (no rate/escrow version falls inside any payment's window; the deploy rehearsal is byte-identical with and without it), which is why it was ruled ahead of the F3 ship rather than after | C2b |
| N-37 (X-a) | **The fold's answer BEFORE an account's first assertion is undesigned, and the shape is live on production.** `walk_cash_ledger` absorbs a settled row attributed before the OPENING assertion into that assertion's correction, so the running total is exactly right at the opening and every date after. But `dated_deltas` emits such a row at its OWN visible day, so a prefix taken BEFORE the opening is those rows summed from a zero seed -- a balance the account never had. It is faithful to the POSTED ledger, which holds the same partial sum there (so re-keying them onto the opening would break the walk-vs-ledger equality X-a establishes), which is why the leaf records the fact and does not decide it. **Measured on prod 2026-07-25:** Fidelity Savings carries 1 such row and the Money Market 4; the prefix reads `$500.00` on both. The row first framed this as the loan side's two options -- answer `0.00` (the empty prefix, as `fold_loan_balances` does before any event) or hold the opening balance flat backward (the C1 plateau, ruling D2's shape) -- and **both were rejected at the ruling in favour of a third the framing missed**: back-projecting the assertion over the records, which contradicts neither (see R-I). Pinned by `TestPreOpeningSources` so the ruling has a control to flip, exactly as N-34 was gated before C2b | $500.00 on two real accounts | **RULED 2026-07-25 (R-I): back-project from the first assertion over the records, flat before the earliest one** -- the loan side's `0.00` does NOT transfer, because a cash account's first anchor row is a TRACKING start (on real data a `cfb15e782f86` BACKFILL row created days to weeks after the account row), not an origination.  Worked figures and the rejected options are in R-I.  `TestPreOpeningSources` is the control that flips when X-b implements it | X-b |
| N-38 (X-a) | **A loan account walked as cash yields a cash-basis balance that ignores interest.** `walk_cash_ledger` deliberately refuses no account kind (the refusal in `account_posting_service.walk_account_ledger` is a WRITE concern -- which correction family a loan's anchors book into), and a resolver could point a cash-flow surface at an amortizing loan, so `settled_cash_facts(loan_account_id, ...)` returns the loan payments' INCOME shadows and the walk sums them at face value.  **This row's original citation of `resolve_grid_account` was WRONG and the correction is the finding**: that resolver has gated all four steps since A1, and the open door was `resolve_analytics_account` -- the calendar, ownership-gated only -- which made this a LIVE defect rather than one X-b would introduce. That is the cash-basis paydown path ruling R-E forbids as a third event kind. **Not a regression:** today's `cash_balance_map` is equally kind-blind by design (the grid's balance row must reconcile with the transaction rows it renders, whatever the account), so X-a introduces nothing new -- but the X-b fold inherits the shape and X-c is where it becomes a rendered number. Needs an explicit ruling (refuse in the fold, dispatch on kind, or ratify the cash-flow view as deliberately kind-blind) rather than a rediscovery at the cutover | **$15,131.65 on the Van Loan** ($531.94 rendered vs $15,663.59 owed; the Mortgage $825.44 high), measured live on the dev clone 2026-07-25 -- and the finding's own cited door was WRONG: `resolve_grid_account` has been gated since A1, the open one was `resolve_analytics_account` (the calendar) | **closed (`47dd4bbb`)** -- ruling R-J, refused at the SOURCE; control fires | X-a1 |
| N-39 (X-b) | **A planned envelope's entries-aware RESERVATION has no ruled valuation date, and the three shipping producers already disagree about it.** The fold values each planned row through the shared `sum_projected`, whose optional `as_of` bounds ENTRY inclusion inside the expense reduction -- so an entry dated AFTER the reader's now either does or does not reduce the reservation, and the fold must pick one for a row it lands as a single step. X-b passes `as_of`, on ruling R-G's own principle one level down (a purchase that has not happened cannot have cleared the bank); the alternative is `None`, counting every loaded entry whatever its date. **The fork is live in the shipping code and is itself a divergence:** `balance_as_of_date` (the calendar scalar) windows entries, while `calculate_balances` (the grid, the period map) and `_net_by_attribution_day` (the daily ramp) do not -- two answers for one row, which is the cash D2 family. **Measured 2026-07-25: ZERO entries on projected rows are dated after today in EITHER database**, so nothing moves today and this is latent, not live. Pinned by `TestThePlannedTier.test_an_entry_dated_after_the_reader_now_does_not_clear_early`, marked PINNED-NOT-ENDORSED, which flips if the ruling goes the other way (the same control shape N-37 carried before R-I). Recorded rather than buried because X-c is where it becomes a rendered number | $0.00 today (no such row exists); worked on the live Groceries envelope #2280 the exposure is **`-$89.45`** as a debit or **`+$60.55`** as a CC entry | **RULED 2026-07-25 (R-M): the fork is CLOSED, not decided** -- a future `entry_date` is refused at the write door, after which the `as_of` window drops nothing and DELETES, so the two shipping surfaces stop disagreeing because there is nothing left to disagree about | X-c0 (guard), X-c2 (deletion) |
| N-41 (X-c trace) | **The grid's stated invariant is UNACHIEVABLE under the fold, and its subtotal rows already read `$0.00` for every past period.** `balances[p] - balances[p-1] == subtotals[p].net` holds today only because both sides count exclusively UNPAID rows (`_flows.py:128` filters through `is_projected`) off an anchor-seeded walk. The fold counts money that MOVED -- settled cash, the R-G clamp, and the assertion corrections -- none of which the subtotal can see, so the plan's "re-prove the invariant" is not a verification task but a design gap. Measured on the real Checking account: it breaks on **8 of 59 period pairs**, worst **`$2,505.17`** in the CURRENT column (fold delta `+$2,364.54` against a `-$140.63` subtotal); decomposed, the remainder is `19 of 130` settled rows visible outside their own pay period (nets to `$0.00` across history, `+/-$2,007.46` within a period) plus the assertion corrections. Separately and already live: p2..p8 each show `$0.00` income and `$0.00` expenses while up to `$1,717.92` moved | `$2,505.17` in one column; `$0.00` subtotals against `$36,323.99` of gross real movement | **RULED 2026-07-25 (R-K)**: subtotals count every attributed row, one Reconciliation row carries the rest, identity holds by construction.  **The producer is BUILT (X-c1)** and every figure above re-measured independently on the prod-shape clone: 19 of 130 rows out of column, worst timing swing `-$2,007.46`, `$0.00` across history, the eight past columns `$0.00` under the shipping subtotal, the current column `-$140.63` against a `+$2,364.54` fold delta.  The new identity holds on 360 of 360 (account, period) pairs.  It becomes a rendered number at X-c2 | X-c1 (producer) / X-c2 (cutover) |
| N-44 (X-c trace) | **The net-worth trend's history gate becomes a stale compensator the moment the fold lands.** `_net_worth._honest_history_start_index` gates the trend's "actual" segment at each cash account's anchor period, because `_CASH_GATING_KINDS` accounts have no balance before it -- a compensator for exactly the map omission (cash D3) X-c closes. Measured consequence TODAY: Checking and the Money Market are both anchored in the CURRENT period (id 9, index 8), so the gate equals the current index and `/savings` shows **zero** periods of net-worth history. Once every past period carries a real assertion-replayed balance the cash arm of the gate is not just unnecessary but wrong -- it suppresses every period of true history. **Re-measured 2026-07-26 (X-c2b trace), and the "~8 periods" figure is corrected in both directions:** the gate suppresses ALL history today (`honest_start` 8 == the current index, so `/savings` draws ZERO history points), and removing the cash arm restores **6**, not 8 -- the loan arm then gates at index 1 (the Van Loan's schedule start) and `_TREND_HISTORY_PERIODS = 6` caps the tail. The same run proves the gate LOAD-BEARING until the fold lands: at period indexes 1-7 both Checking and the Money Market have no balance at all under today's producers, so deleting the arm one commit early would draw six history points understated by ~`$6,460` | 0 history points drawn on `/savings`; 6 restored | **closed (`d3489728`)** -- the cash ARM is deleted and `/savings` draws the 6 restored points; cash still PARTICIPATES at a no-constraint index, because dropping it from the loop entirely put a LOAN-FREE user into the no-history default and took away the very history this closes (N-59) | X-c2b2 |
| N-47 (X-c2 trace) | **X-c2's one-liner enumerates the moved sites short by three.** It names "all three cash entries plus the kernel's PLAIN branch, the INTEREST branch and the grid footer". Traced, the cutover surface also contains: (a) `_kind_correct.balance_at`'s **PLAIN** branch (`:252-255`) and (b) its **degraded-AMORTIZING** branch (`:266-269`), both of which call `_cash_engine.balance_as_of_date` directly -- these are the KIND-CORRECT scalar, not "the kernel's PLAIN branch" (which is `_kernel.base_account_balance_map`, a map), and for a PLAIN account the kind-correct scalar and the cash-flow scalar are literally the same call, so /savings and the dashboard would disagree if one moved without the other; and (c) `_kernel.interest_by_period_for_account`, which shares `_account_interest_projection` with the INTEREST balance branch, so ruling R-L moves the account-detail "Interest, next 12 mo" chip too -- a rendered figure the step never named | -- | corrected in the decomposition (X-c2a takes (c), X-c2b takes (a) and (b)) | X-c2a / X-c2b |
| N-48 (X-c2 trace) | **The grid runs THREE per-period producer passes where one suffices, and the fold would make it four.** Measured on the prod-shape clone (Checking, 60 periods, 5 runs each): `grid_balance_view` 90.7 ms + `period_subtotals(visible 6)` 74.2 ms + `period_subtotals(plan 13)` 75.8 ms + the route's own `live_amount_overrides` build 97.3 ms = **338.0 ms** of producer work per render. Each pass rebuilds the live override map, which is ~90 ms of the cost on its own (it recomputes the salary paycheck and the loan-derive cash). A naive cutover keeps that shape and ADDS the fold's own internal override build. Pointing the grid at ONE `cash_period_view(all_periods)` -- which X-c1 built to return the balance, the subtotals and the remainder from one assembly -- measures **96.2 ms**, and it makes ruling R-K's identity a property of the render rather than of a test. The route's own map build stays (the cell templates read `txn.live_estimated_amount`), so the render is ~193 ms: the two maps are provably identical, since both seams filter their candidates through `is_projected` over the same account/scenario row set | 338.0 ms -> 96.2 ms of producer work per grid render | recorded; the one-pass read is X-c2b's shape | X-c2b |
| N-49 (X-c2 trace) | **The INTEREST branch cannot lag the cash map by one commit: the grid would relabel the missing money as earnings.** `_grid._accruing_grid_view` renders the per-period delta of `premium = kind_correct_balance - cash_balance` as the read-only "Interest" row. Fold the cash map while `_account_interest_projection` still seeds off the `current_anchor_balance` cache and the premium absorbs the whole cutover delta: measured on the real Money Market, kind-correct `$5,666.52` against a folded `$3,659.51` gives an increment of **`$2,007.01`** in the current column -- finding cash D1's `$2,000.00` of invisible settled money rendered as interest earned, on the highest-stakes screen. This is the measured reason the cutover is ATOMIC across the cash map and the INTEREST seed, and the reason ruling R-L's clock half (which has no fold dependency) is the only piece that can ship ahead of it | **`$2,007.01`** rendered as interest for one commit | closed by construction (the decomposition; X-c2b moves both together) | X-c2b |
| N-50 (X-c2b trace) | **`BalanceResult` dies WITH the stale-anchor flag, and the deletion list names only the flag.** X-c2b's text deletes `_calculator._detect_stale_anchor`, the `stale_anchor_warning` flag, its banner and its OOB swap -- but the flag is a FIELD of `_cash_engine.BalanceResult`, which is also the return type of `balances_for` (the producer X-c2b explicitly KEEPS for the two investment / appreciation bases). With the flag gone the dataclass carries one field, and both surviving callers (`_investment.py:98` and `:445`) already read `.balances`, so the survivor returns a bare `OrderedDict` and the dataclass deletes. Three more consumers move with it: `_cash_flow.cash_balance_map`'s return annotation, `accounts/detail.py:279-280`, and `dashboard_pulse_service.py:149-151` | -- | **closed (`82557ca9`)**, with its consumer list CORRECTED: only the two `_investment` callers remained by then.  The other three had already moved at X-c2b2 -- `_cash_flow.cash_balance_map` returns the fold's `OrderedDict`, and both routes read the fold direct -- so the survivor's return type was the whole change | X-c2b3 |
| N-51 (X-c2b trace) | **The `amount_overrides` parameter has nothing left to do the moment the fold lands, and it is the last surface where one account can be valued on two income bases.** It exists so a caller can put TWO walks on ONE income basis -- `grid_balance_view` normalizes `None -> {}` for INTEREST precisely because `balances_for` auto-builds a live map from `None` while `calculate_balances_with_interest` does not, an asymmetry `_kind_correct.balance_map:60-79` documents as "load-bearing for callers". The fold has one walk and builds its own map inside `_cash_plan`, so the parameter is dead through eight signatures (`cash_balance_map`, `cash_daily_balance_series`, `grid_balance_view`, `balance_map`, `_account_balance_map`, `build_account_balance_map`, `base_account_balance_map`, `account_balance_map_from_inputs`). **Measured 2026-07-26 on the prod-shape clone: ZERO of 60 columns differ between the stored and live bases on any of the six non-loan accounts** -- Checking carries 51 live overrides whose stored `estimated_amount` already equals the live value, and the INTEREST account carries none -- so the deletion moves nothing today. Ruled R-Q: delete the parameter, the SEAM owns the map, and the grid route reads it back off `GridBalanceView` rather than building a second one | `$0.00` today; the class is the grid-vs-savings income divergence | **closed (`d3489728`)** -- deleted from all seven remaining signatures; the seam owns the map, the route reads it back, and it measures byte-identical to HEAD on all six real accounts | X-c2b1 / X-c2b2 |
| N-52 (X-c2b trace) | **`_accruing_grid_view` deletes whole, and with it a compensator for a state the fold makes unreachable.** It derives the grid's Interest row by SUBTRACTING two independently-computed balance maps (`premium[p] = round_money(kc[p]) - cash[p]`, `increment[p] = premium[p] - premium[p-1]`) and carries an explicit branch for "a cash period the kind-correct map lacks" (`_grid.py:139-152`), reachable only under anchor-cache divergence (finding cash D4). Once BOTH maps derive from ONE fold over ONE period list, the increment IS the accrual map `_layer_interest` already returns -- so the subtraction, the premium baseline and the divergence branch all delete, and the Interest row stops being a residual. **Verified on the prod-shape clone 2026-07-26:** the post-cutover premium peaks at exactly `$658.26` on the Money Market, equal to its cumulative modelled interest to the cent | a residual row standing in for a producer | **closed (`d3489728`)** -- `_accruing_balances` deleted; the accrual is the map `_layer_interest` returns, and the premium peaks at exactly `$658.26` = the cumulative accrual | X-c2b2 |
| N-53 (X-c2b trace) | **Two more names die with the INTEREST seed, and one of them is the interest path's own transaction query.** Once the base comes from the fold's period-end sample, `_calculator.calculate_balances_with_interest` (whose whole body is "base + layer") collapses to its `_layer_interest` half, and `_kernel.load_account_period_transactions` -- whose ONLY caller is `_account_interest_projection`, as its own docstring says -- goes to zero callers. Neither was on X-c2b's deletion list. Note the query's stated reason is already stale: it loads EVERY non-deleted row "because the interest accrual downstream applies its own status logic and needs the full row set", but the accrual (`_layer_interest`) reads no transaction at all and the base filters through `is_projected` regardless | ~90 lines kept alive for their own tests | **closed**: `calculate_balances_with_interest` went early at X-c2b2 (`d3489728`, with the rule it composed -- `duplicate-code` refuses two copies of the walk), and `load_account_period_transactions` at X-c2b3 (`82557ca9`), taking `balance_predicates.account_period_scope_clause` with it (N-66).  The kernel now issues no query at all | X-c2b2 / X-c2b3 |
| N-54 (X-c2b trace) | **The cutover moves the account-detail "Interest, next 12 mo" chip, and X-c2b's one-liner does not name it.** N-47 caught this chip for ruling R-L's CLOCK half (shipped X-c2a); the SEED half moves it again, because `interest_by_period_for_account` shares `_account_interest_projection` with the balance path. **Measured 2026-07-26 on the prod-shape clone:** the chip reads `$284.34` today and `$217.75` after, and the modelled interest over the whole 60-period horizon `$793.56 -> $658.26`. The cause is not a rule change but the base: the Money Market really holds `$2,000.00` less than the screens say (finding cash D1), so less principal earns. The kind-correct current column moves with it, `$5,666.52 -> $3,664.04` | `-$66.59` on the chip; `-$135.30` on the horizon | **closed (`d3489728`)**, and its CHIP figures corrected: `$284.34` -> `$217.75` was measured over a `p8..p33` window, while `_interest_next_year`'s own window (`[current+1, current+26]`, so `p9..p34`) gives **`$292.43` -> `$225.76`** (`-$66.67`).  The horizon figures reproduce EXACTLY (`$793.56` -> `$658.26`), so the cause and the magnitude stand; only the window was off by one period | X-c2b2 |
| N-55 (X-c2b trace) | **Two seam docstrings name a consumer that no longer exists.** `_grid.py`'s module docstring and `_cash_flow.cash_balance_map` both cite "the dashboard obligations panel" as a reader of the cash-flow view. `app/routes/obligations.py` is a redirect-only shell whose own docstring says the card "is dropped: the dashboard's end-balance chart and the grid footer own" it, and `obligations_aggregator` imports no balance producer at all (`TransactionTemplate`, `TransferTemplate`, `amount_to_monthly`, `round_money`). An uncited claim about the code in the modules the cutover rewrites, so it is corrected with them (Section 7.6) | -- | recorded; corrected at X-c2b1 | X-c2b1 |
| N-57 (X-c2b1) | **N-48's ``338.0 ms`` baseline did not reproduce the shipped route's threading, so the headline saving is smaller than it reads.**  The measurement timed ``period_subtotals`` twice at ``74.2`` / ``75.8 ms`` -- each rebuilding the ~90 ms live override map -- but ``grid.index`` builds that map ONCE and threads it into both subtotal calls, which measure ``3.0`` / ``4.4 ms`` when threaded.  Re-measured on the same clone 2026-07-26: the shipped route's producer work is ``219.9 ms`` (route map 84.5 + grid_balance_view 128.0 + subtotals 3.0 + 4.4), and X-c2b1's one-view shape is ``127.6 ms`` -- the whole saving being the duplicate override build, not the subtotal passes.  The direction and the cause N-48 names are right; the arithmetic overstated the incumbent by ~118 ms.  Recorded because X-c2b2's own budget is set against these figures | -- | corrected in place | X-c2b1 |
| N-59 (X-c2b2) | **Deleting the history gate's cash arm takes a LOAN-FREE user's history away -- the opposite of what N-44 intends.**  `_honest_history_start_index` returns the CURRENT period's index when nothing gates ("the trend never fabricates a backward run for an investment-or-property-only set"), so removing cash from the gate loop entirely drops a user with no loans into that default: 0 history points where N-44 exists to restore 6.  The real-data measurement did not show it (both prod loans gate at index 1, so `max()` was unchanged); the SUITE did, on `test_tail_reaches_back_to_cash_anchor`.  Fixed in-commit: cash PARTICIPATES at a no-constraint index instead of being skipped, so it constrains nothing but still marks the set as having a RECORDED past -- which is exactly what the default protects against when it is absent | 6 history points -> 0 for every loan-free user | **closed (`d3489728`)** -- control fires (`test_a_modelled_only_set_still_draws_no_history` is the one that fails if cash is dropped from the loop) | X-c2b2 |
| N-60 (X-c2b2) | **The dashboard hero read the as-of-TODAY balance under a label promising the period's END.**  `_pulse.html` labels `#balance-display` "End of this period" and captions it "projected through <period end>", while `_pulse_hero` and `dashboard_service.compute_balance_section` both read the as-of-today scalar.  That was legitimate only because the scalar was period-FLAT: it summed the whole period's still-projected rows whatever their dates, so "today" and "period end" were the same number.  The fold makes it date-precise (cash D2), so the two diverge by the whole unpaid remainder of the current period -- and the hero would have shown TODAY's balance under a label promising the END, differing from the chart's first point beside it.  Fixed in-commit by making the figure follow the label: the hero reads its period map's current entry (so it IS the chart's first point rather than a second producer that must agree with it) and the revert fragment reads the scalar at `current_period.end_date` | the unpaid remainder of the current period, on the dashboard headline | **closed (`d3489728`)** | X-c2b2 |
| N-61 (X-c2b2 fixture work) | **A settled row in a scenario the account's linked ledger had no entry in leaves that ledger opening-less.**  `self_heal_anchor_corrections` tested only whether a posted correction had gone STALE ("did the change land at or before the latest assertion, moving that anchor's walked `ledger_before`?"), never whether one EXISTS.  A scenario becomes live for an account the moment an entry first lands on its linked ledger there, and `sync_account_anchor_postings_all_scenarios` only visits scenarios that are ALREADY live -- so the emission that made a scenario live was the one that skipped minting its opening.  **Measured in the production shape:** Checking opened 2026-01-02 at `$1,000.00`, a fresh scenario, one `$70.00` expense settled 2026-03-03 -> that scenario's linked ledger summed to `-$70.00` instead of `$930.00`, and the trial balance still CLOSED because the missing opening and its equity twin were both absent.  Latent rather than live: production creates baseline scenarios only (`auth_service` at registration, `baseline_service` at recovery) and a baseline carries its corrections from account-create time, so it cannot fire until a scenario-clone feature ships -- precisely when it would have shipped wrong.  Surfaced because the cutover's fixture work removed the same-UTC-day over-fire the suite had been passing through | an account's whole opening balance missing from a scenario's ledger | **closed (`7de04f0c`)** -- the predicate is now a SKIP whose precondition is that the reconcile would write nothing (both arms must hold); two controls fire, one of which counts the CALL because an idempotent reconcile writes nothing either way | X-c2b2-adj |
| N-62 (X-c2b2 fixture work) | **`resolve_anchor` had no tiebreak, so two same-instant assertions disagreed with the WALK about which is authoritative.**  `cash_ledger._facts.resolve_anchor` ordered `created_at DESC` and took `.first()`, while `merge_anchor_and_cash_events` orders `(created_at, id)`.  Two assertions sharing an instant -- a same-second true-up, or any fixture that stamps both -- and which row the resolver called "latest" was a query-plan artifact (a top-N heapsort over a seq scan returns whichever the heap yields first), so it could flip with an `ANALYZE` or a parallel scan and silently swap which balance is authoritative while the fold replayed the other one last | which of two same-instant assertions is authoritative, decided by the query plan | **closed (`7de04f0c`)** -- `id DESC` added, so the resolver and the walk break ties identically | X-c2b2-adj |
| N-63 (X-c2b2 review) | **A shipped static guard was passing on a docstring MENTION rather than a call site.**  `test_grid_balance_computation_routed_through_resolver` greps `app/routes/grid.py` for the literal `balance_at.cash_balance_map` -- but the route stopped calling that entry at X-c2b1, and the string survived only in a docstring, so the guard went on reporting the wiring intact while what it locked had moved.  It failed at X-c2b2 only because the docstring was rewritten.  Fixed in-commit: it matches `balance_at.grid_balance_view(` WITH the open paren (a call site, not a name) and gains a third arm refusing the kind-correct `balance_map(`; control fires.  The generalisation is the finding: **a static guard that greps for a NAME cannot tell code from prose**, and the codebase has several | a routing guard green while the routing it locks has moved | **closed (`d3489728`)**; the class is a Section 8 lesson | X-c2b2 |
| N-64 (X-c2b2 review) | **The account-detail page folded the same account TWICE per render.**  `_cash_projection` called `balance_at.balance_map` and then `interest_by_period_for_account`, each discarding the half the other wanted.  That was cheap while the accrual's base was one transaction query plus a pure walk; once the base became the cash FOLD each call became a full assemble -- walk + plan load + the ~90 ms `live_amount_overrides` build (finding N-48's cost) -- so one detail render paid it twice.  The helper's own docstring made the DRY argument explicitly ("they cannot drift onto two copies of the same walk"), which two copies of the walk is exactly what the page then did | ~90 ms of duplicate producer work per detail render | **closed (`d3489728`)** -- one seam entry (`interest_projection_for_account`) returns both halves of the ONE walk | X-c2b2 |
| N-66 (X-c2b3) | **The deletion list was short by one, and the name is in `app/utils`.**  `_kernel.load_account_period_transactions` was the ONLY caller anywhere of `app.utils.balance_predicates.account_period_scope_clause` (AST-scanned over `app/` + `scripts/` + `tests/` + `tools/`: one call site, ZERO test references), so deleting the loader orphaned the clause.  Its own docstring named the caller that no longer exists ("the net-worth kernel's interest-balance and settled-net queries", the second of which F2 deleted).  Leaving it is the dead-code-alive-for-nothing shape N-46 named, one tree over from where the list was looking | ~35 lines of dead SQL-clause builder | **closed (`82557ca9`)** -- deleted with its caller; the kernel now issues no query at all | X-c2b3 |
| N-67 (X-c2b3) | **A SECOND shipped static guard was green on prose, one step after N-63 predicted there were more.**  `test_cash_detail_balance_routed_through_seam` asserts `"balance_at.balance_map" in accounts_source` -- and plan step X-c2b2 replaced that call with `interest_projection_for_account` (finding N-64's one-fold fix), leaving the string alive in THREE docstrings.  **Measured on the tree at `0ad4f936`: 3 occurrences, all prose, ZERO call sites, guard GREEN.**  Unlike N-63 it did not even fail late, because nothing rewrote the docstrings.  Fixed: every arm matches the CALL with its open paren, the interest arm names the entry that actually ships, and a FOURTH arm REFUSES `balance_at.balance_map(` -- so the guard now pins N-64's fix (calling the whole-account map beside the interest map folds the account twice per render) instead of merely asserting a name exists.  All four shown to fire, alone, on their own mutation | a routing guard green while the routing it locks had moved | **closed (`82557ca9`)**; the class is the Section 8 lesson N-63 wrote, now with a second measured instance | X-c2b3 |
| N-68 (X-c2b3) | **A grid fixture dated its purchases in a FUTURE period -- a state the app now refuses at both write doors.**  `test_grid_subtotal_reconciles_balance_delta` put a `$300.00` envelope in `periods[5]` with two cleared debits dated that period's START, ~2 weeks ahead of today, and asserted the entries-aware `$50.00` reservation.  The retired `period_subtotal` counted every loaded entry whatever its date, so it passed; the fold values the reservation at the READER'S NOW (`sum_projected`'s entry-date window), so a purchase that has not happened reserves nothing and the same fixture reads `$300.00`.  Both halves are the shipped rule -- ruling R-M / plan step X-c0 REFUSES `entry_date > today` at both write doors, so the fixture built through the ORM a state production cannot reach, and the sibling test that renders the figure through `GET /grid` always dated its entries on the CURRENT period's start.  The N-8 / N-65 family, on the entry clock rather than the assertion clock | a reconciliation test passing on an unreachable state | **closed (`82557ca9`)** -- the rows moved to the current period (a partially-spent envelope in the period you are in), and the target is derived from `get_current_period` rather than an index | X-c2b3 |
| N-69 (X-c2b3) | **Two `balance_at` scalar tests were VACUOUS: on a row-less fixture every producer answers the anchor.**  `test_cash_equals_balance_as_of_date` and `test_loan_without_schedule_degrades_to_cash_producer` each assert that a seam scalar equals a second reader, over an account holding ONE opening assertion and no transactions -- so both sides are the anchor and any producer satisfies them.  Surfaced by this step's own firing control, not by reasoning: replacing the kind-correct PLAIN branch with a bare `resolve_anchor` read left both GREEN.  They had the same hole before the re-point (the old reference, `balance_as_of_date`, also returned the anchor), so this is a pre-existing vacuity the deletion exposed rather than introduced | two seam-routing guards that could not fail | **closed (`82557ca9`)** -- each fixture gains one still-projected row (`$750.00` and `$4,700.00`, neither the bare anchor) and the control now fires both | X-c2b3 |
| N-70 (X-c2b3) | **`balance_predicates.attribution_year` is dead, and has been since plan step F2.**  ZERO production callers in `app/` or `scripts/` (verified against `0ad4f936` as well, so this step did not orphan it); it survives on `tests/test_utils/test_balance_predicates.py` alone -- the dead-code-alive-for-its-own-tests shape C3b4 / D2a / F2 / E1e / N-46 each deleted.  Its docstring names its consumers as "the year-end spending section and the transfers section", and F2 deleted that whole package.  Found while deleting its file-mate `account_period_scope_clause` (N-66); RECORDED rather than deleted because the two were orphaned by different steps -- this one is F2 residue, not this arc's, and X-c2b3 deletes what X-c2b2 replaced | ~29 lines kept alive for its own test | **closed (`9d42b3cc`)** -- recorded first (out of the balance arc's scope, plan Section 9 / CLAUDE.md rule 6), then deleted on the developer's instruction in its own commit, which is the shape rule 6 asks for: report, then act when told.  Re-traced before deleting -- one definition, references only in its own test file, no template or dynamic use, and no hand-rolled twin of the `COALESCE(due_date, period_start).year` rule anywhere (`calendar_service` composes the surviving `attribution_date` instead).  The function was the file's last, and the last user of its `date` import; the test class was its file's last, and the last user of BOTH its `date` and `attribution_year` imports | own commit (F2 residue) |
