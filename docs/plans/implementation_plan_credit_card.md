# Credit card arc: the argument

**Status: RE-MINTED FROM SCRATCH 2026-09-18 (`credit_card:R-CC14`..`R-CC22`). The design of record
is `docs/design/credit_card_from_scratch.md`; the arc's twelve leaves are its section 5, and its
section 5 also holds the map from the seventeen 2026-07-19 leaves to these.** The 2026-07-19 plan
(its architecture, its phases 0-6 and its consumer inventory) is archived whole to
`historical/credit_card_plan_2026-07-19.md`.

**The 2026-07-19 "Ratified sequencing" was DISCHARGED and is ARCHIVED** to
`historical/credit_card_sequencing_2026-07-19.md`. It stayed here, live-looking, until 2026-08-11,
warned about only by a paragraph in ANOTHER document -- which is the same failure the archive banner
fixes for archived files (`CLAUDE.md`, Automated enforcement): the warning must be on the artifact.
Two of the bare ids it ordered now name LIVE steps in other arcs (`pay_calendar:C8`,
`recurrence:D1`), so it could send a reader at the wrong work by grep alone.

**The 2026-07-19 gate (ruling R-EB) was DISCHARGED on 2026-09-18** by the per-leaf trace
`credit_card:R-CC13` asked for (**R-CC14**, **R-CC15**), and the same day's design loop re-minted
every leaf from scratch (**R-CC16**..**R-CC22**); no leaf waits on `balance:X-f4`, and
**when each leaf may start is the tracker's answer**, never restated here. Every credit-card test
builds rows through the suite's builders (`one_off_row_of`, `generate_row_of`, `payback_row_of`),
never `Transaction(`: `tests/manual/census_hand_built_rows.py` (`X-bi-7c`'s instrument) counts the
latter.

**Each step, finding and ruling of this arc is a card in the tracker,
`saltyreformed-labs/shekel-plan` (`CLAUDE.md`, Development Status), and what "done" means is
`verification.md`.** What stays HERE is the argument: the context, the design and what verifying
this arc adds to that standard. The eight LOCKED rulings of 2026-07-19 are `R-CC1`-`R-CC8`, in the
order that list numbered them, and the four `Grid / companion hard requirements` taken the same day
are `R-CC9`-`R-CC12`.

## Context

Shekel's "Credit" status is a spreadsheet-era placeholder: marking an expense Credit flips it to a
balance-excluded status (contributes $0 to its period forever, never settles, never posts to the
ledger) and auto-creates a phantom "CC Payback" Projected expense in `period_index + 1`
(`app/services/credit_workflow.py`). The developer pays those bills with a real credit card that has
interest, due dates, minimum payments, and flat-rate cash back.

The current model is financially wrong in five ways: the debt exists nowhere (net worth overstated
by the outstanding card balance); spending attribution is destroyed (the source never posts; the
payback posts under a generic category in the wrong period); cash back is manual; the payback lands
in `period_index + 1` while real cash leaves on the statement due date (up to ~6 weeks later); and
interest/grace is unrepresentable.

A seeded "Credit Card" account type existed from the start (`app/ref_seeds.py:59`,
`AcctTypeEnum.CREDIT_CARD`) and classified `PLAIN` with every behavioural boolean false, while the
seam held a non-loan liability flat at one reserved hook in `balance_at/_liability.py`; **CC-1**
(`6f7cb619`) flagged the type `has_revolving_credit` and made that band read the fold forward for
every non-loan liability, so the hook is gone.

## The design

**`docs/design/credit_card_from_scratch.md` is the argument, ruled 2026-09-18**: a card is a FLAG on
its account type read by the one kind-blind seam (3.1); a purchase is a MOVEMENT on the card, read
on the card, and the plan item never moves (3.2, `R-CC15`); a plan item's `account_id` is where its
money is EXPECTED to move through and the grid reads the owner's cash-flow accounts as one set (3.3,
`R-CC16`); the statement is a derived cycle and its closing a LEVEL (3.4); the payment is ONE
recurring transfer per card with a mode, priced by the card from the fold (3.5, `R-CC18` as amended
by `R-CC22`); interest is a row of a card-owned definition priced by `recurrence:R16-d`'s one
producer (3.6, `R-CC19`); the cutover of the cheat converts every frozen pair by a total rule and
the live ones by the developer's own act at a coordinated release (3.10, `R-CC17` as amended by
`R-CC20` and `R-CC21`); the card is one more account on the SimpleFIN feed (3.11). What the design
deletes is its section 4; what it measured on production is its section 7; what the neutral reviews
refuted is its section 8.

## Verification standard

**The standard is `verification.md`**, one copy for every arc; what every commit owes is
`CLAUDE.md`'s Definition of Done. This section states only what is SPECIFIC to this arc.

- **`CC-1`'s band and `CC-7`'s cutover are live-render gates**: on the dev clone, before landing,
  render the grid on the card, the savings cockpit, obligations, the net-worth trend and the balance
  sheet. Every moved number is individually explained and signed off.
- **End-to-end acceptance on the dev clone**, one walk: create card + params -> settle a projected
  bill with the card as tender (a movement on the card) -> statement closes -> the payment row
  prices from the fold -> settle the payment -> grace holds (interest `$0.00`) -> force an
  underpayment -> the notice, and the finance-charge row once `CC-3i` / `CC-8` ship -> rewards
  accrue -> a redemption row -> the next payment shrinks.
