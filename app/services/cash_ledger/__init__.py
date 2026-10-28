"""
Shekel Budget App -- The cash LEDGER leaf: the facts a cash balance folds from.

The cash-side counterpart of :mod:`app.services.loan_ledger`, and deliberately
the same SHAPE.  A loan's balance is a fold over its event stream; so is a cash
account's (plan step X-b, "a cash account is an event stream").  Both folds need
the same things underneath them, and this package owns the cash copies:

==============================  ========================  ======================
question                        here                      loan analog
==============================  ========================  ======================
what is stored?                 :mod:`._facts`            ``loan_loaders``
what happened, when?            :mod:`._events`           ``loan_ledger._events``
where do books open?            :mod:`._books`            (no loan analog)
what IS the amount?             :mod:`._amount_source`    (see below)
what would a row be worth?      :mod:`._definition_cash`  (see below)
what does a pass derive live?   :mod:`._amount_basis`     (no loan analog)
what does a loan resolve to?    :mod:`._loan_pricing`     (no loan analog)
what does an installment cost?  :mod:`._loan_installment` ``loan_resolver``
what was it worth?              :mod:`._amounts`          ``loan_ledger._replay``
did the bank show it?           :mod:`._clearing`         (no loan analog)
in what order?                  :mod:`._walk`             ``loan_ledger._walk``
what do they sum to?            :mod:`._flows`            (a peer reduction)
==============================  ========================  ======================

**The clearing row has no loan analog because a loan has no bank statement to
clear against**: its balance is an amortization the app derives, and its
assertions are the user correcting that derivation.  A cash account's balance is
what a third party says it is, so *which statement showed this line* is a fact
the app must be told (ruling **R-FL**) rather than one it can compute.

**The two AMOUNT rows are two questions, not one split in half** (plan
step X-au-b, ruling **R-FI**).  :mod:`._amount_source` answers *where does this
row's amount come from* -- the figure the amount COLUMN holds or would hold,
by a total dispatch over the five sources a row's amount can have.
:mod:`._amounts` answers *what is this row worth to checking*, which composes
that amount with an entered actual, an excluded status and an envelope's
purchases.  The arrow runs one way: the second consumes the first.  The loan
side has no analog for :mod:`._amount_source` because a loan payment's amount
has exactly one source; a cash row's has five, and four of them are derived.

**The DEFINITION row answers for a row that does not exist yet** (plan step
R16-b-2, ruling **R-R67**).  Rules 3 and 4 are functions of four columns a
generated transfer carries -- its definition, its due date, its paycheck's
payday and its destination -- and :mod:`._definition_cash` states both arms
over those VALUES; :mod:`._amount_source` delegates a written row to them,
and the balance seam's ESTIMATED loan tier prices an occurrence no row answers
yet through the same two, so a row and its estimate cannot part.

**The LOAN PRICING pair is in this package, and plan step X-au-g-2a is what
put it there.**  Amount rule 4 -- a loan payment's transfer is worth what the
loan says that installment costs -- is a rule about a ROW'S AMOUNT, which is
this package's question; its producer lived in ``loan_payment_service`` until
that step, so :mod:`._amount_source` had to reach UP a tier to price a row and
the loan stack could never name this package back.
:mod:`app.services.row_valuation` exists because of that reach.  The cycle it
created was REAL and pylint could not see it -- a ``TYPE_CHECKING`` import
masked the runtime one -- which is measured, with the arms and their dates, in
:mod:`._loan_installment` and stated nowhere else.  The producer moved DOWN
rather than the readers routing around it, so the arrow runs one way again --
this package
names the loan TERM primitives (``loan_loaders``, ``loan_resolver``,
``escrow_calculator``, ``recurring_transfer_query``), none of which names it --
and the loan READING tier may import this package, which plan step X-au-g-2c-1
SPENT: ``loan_payment_service.get_payment_history`` prices its feed through
:func:`contributions_by_id` rather than through
``row_valuation.settled_contribution``.  ``balance_at._plan._planned_from_shadows``
-- the SECOND unrouted reader, which a census at that step found where the
finding had named one -- went through in the same commit, so no reader of an
unsettled loan-payment row is left outside the model.

**IT BROUGHT A ``budget.transfers`` QUERY WITH IT, AND PLAN STEP X-au-g-2c-2
TOOK IT BACK OUT.**  ``_loan_pricing._load_live_payment_configs`` was the ONE
statement against ``budget.transfers`` in these thirteen modules -- the
scenario's transfers INNER-joined through their template to
``loan_payment_settings``, to discover which of them were loan payments.  That
question is asked per ROW off the parent it was handed now (ruling **R-FK**'s
live refinement), because the read-time repair the map fired for is gone.
What would have been wrong is its arriving silently, which is exactly how
``loan_payment_service``'s own "queries ONLY budget.transactions" sentence
came to be false at the top of its file for months.

**THE PLAN HALF READS ``budget.transfers`` BY DESIGN SINCE PLAN STEP X-bi-6a**
(ruling **R-BAL13**, developer ruling **R-BAL38**).  A still-projected
transfer's two legs are not rows in ``budget.transactions``; they are the
parent's projection onto its two accounts, and :func:`planned_cash_rows`
derives them from the parent through
:mod:`app.services.transfer_legs` and prices each by
:func:`resolve_transfer_amount` -- the same figure amount rule 5 answered for
the shadow row a leg replaces, with the shadow taken out of the walk.
The split: the PLAN half of a transfer derives from ``budget.transfers``, and
since plan step ``X-bi-4a`` a SETTLED side is its DATED covering movement
(ruling **R-BAL80**) -- which hung off the side's shadow row in
``budget.transactions`` until plan step ``balance:X-bi-6-4d-2`` and hangs off
the transfer by its side link since.  The movement says where and when the
money moved; how much is the figure the transfer stores once (rulings
**R-BAL365**, **R-BAL366**: :attr:`~app.models.transfer.Transfer.settled_figure`,
read by ``_cash_leg._moved_figure``; the transfer paragraph at the end of
``CLAUDE.md``'s Architecture), so the fold reads no PLANNED amount for a side
that has settled.
**What the two halves could and could not double count, measured rather than
claimed.**  A PROJECTED shadow could not be counted beside its leg: the plan
excluded it by the same ``transfer_id`` test that identified it (the test still
stands in :func:`planned_cash_rows` and excludes nothing now, since no shadow
can be stored).  A SETTLED shadow under a
still-Projected parent -- a STATUS drift, which Transfer Invariants 3 and 4
forbade and no door wrote (0 of 350 shadow rows on the 2026-09-15 snapshot) --
was counted by both halves while the record half keyed settled-ness on the
shadow's status and the plan half on the parent's: ``$250.00`` checking ->
savings with the checking shadow settled around the service folded checking
at ``-$500.00``, and the reverse drift (parent settled, shadows Projected)
folded ``$0.00`` on both sides.  Before X-bi-6a either drift counted ONCE,
because only the shadow was read.  Plan step ``X-bi-4a`` let a side's DATED
movement, not a status, decide which half counts its leg (ruling
**R-BAL79**), and both drifts were pinned in
``tests/test_services/test_transfer_legs/test_the_plan_half.py``; plan step
``balance:X-bi-6-5``'s third leaf gave the plan half back to the status
alone, the status-band rule having made the two one answer.  **Drift A
is structural now**: plan step ``balance:X-bi-6-4d-2`` made a transfer one
row (ruling **R-BAL258**), so no shadow's status can part from its
transfer's.  What pins that is the database's refusal of a shadow row
(``tests/test_models/test_a_transfer_is_one_row/test_the_upgrade.py``'s
``TestTheUpgrade::test_the_database_refuses_a_twin``), and drift A's cases
went with the shadows (ruling **R-BAL313**).  Drift B without its shadows is
a settled transfer whose sides hold no record, the zero close the
status-band rule admits; it still folds ``$0.00`` on both sides and is still
pinned there, on each
(``TestAStatusDriftIsCountedOnce``'s
``test_a_projected_shadow_under_a_settled_parent_is_counted_by_neither``).

**The books row has no loan analog because a loan has no books to open.**  Its
origination is ``LoanParams.original_principal``, synthesized rather than
recorded (``loan_loaders.synthesize_origination_anchor``), so there is no
stored day for a movement to fall before.

Nothing here answers "what is the balance at T".  That is the
:mod:`app.services.balance_at` seam's question, and the arrow runs ONE way: the
producers import this package, and this package imports none of them.  The WALK
is facts (what happened, in the order it happened); the FOLD that samples those
facts at a date is the seam's, exactly as plan step D-fold split the loan pair.

**The walk (plan step X-a, ruling R-H).**  :mod:`._events` and :mod:`._walk`
build the account's ONE event stream -- every balance ASSERTION, every SETTLED
row, and every PURCHASE whose bank posting day the owner recorded (ruling
**R-FM**, plan step X-f3b) -- and :mod:`._clearing` answers which assertion
CLEARED each source (ruling **R-FL**), falling to the first assertion dated on
or after it where no statement is recorded as having shown it (ruling R-DH).
**What an assertion then DOES to a running total is not here** and has not been
since plan step X-f3c-1: that is a policy an account's KIND decides (ruling
**R-FO**), this package is kind-blind (ruling **R-J**), and the replay lives in
``balance_at._assertions`` beside the total it is a policy about.  The seam's
read fold TAKES these facts, and since plan step X-c2b2 every cash figure on
every screen is that fold sampled at a date.  The second consumer is still to come: at step
X-d the posting WRITER reads this walk too, replacing the postings-sourced
:func:`app.services.account_posting_service.walk_account_ledger`, which is what
makes a stale posting a detectable cache inconsistency rather than a second
opinion.  Still-Projected rows are NOT in the walk: their effective date depends
on the reader's as-of (ruling R-G), so they are the seam fold's tier.

**Why a package (plan step D1c).**  These names were spread across three flat
modules -- ``cash_events``, ``period_flows``, and five functions stranded inside
``balance_calculator``, a PRODUCER module (finding N-30).  The scattering had a
cost beyond tidiness: the fence could only cover them with a hand-written module
list (``_CASH_EVENT_SOURCE_MODULES``), which D1b's review found SELF-ATTESTING
-- deleting the constant and the registry entry together passed green while
re-opening the hole.  A package is scoped by ONE registry key that prefix-
matches, exactly as ``app.services.loan_ledger`` is, so a module created inside
it inherits the scope instead of escaping it.  That is the Section 8 lesson --
"a fail-CLOSED gate is scoped by module identity, so creating a module is how
you escape it" -- closed structurally rather than by a literal-string test.

Fence status (post-D3).  This package calls no balance producer -- since plan
step D3 the producers are private ``balance_at`` submodules, so any attempt to
reach one fails structurally at the import (W9910).  It IS scoped for the
W9909 completeness check, so a new public function in ANY submodule must be
classified as a producer or a non-producer rather than defaulting to
unguarded.  D1a's
adversarial review proved that half load-bearing: a cash balance-at-T folded
from these names touches no fenced NAME, so without the scope it -- and a route
rendering it -- both rated 10.00/10.

Services-boundary discipline (``CLAUDE.md`` Architecture / B6-01).  Plain data
in, frozen dataclasses out; no Flask symbol, no writes.  All money is
:class:`~decimal.Decimal`.
"""

# The producer-free half of the valuation lives a tier DOWN
# (:mod:`app.services.row_valuation`), because the loan stack needs it and can
# never name this package.  Re-exported here so a consumer asking what a row's
# money DID names the same module it asks what the row's amount IS -- the same
# reason ``_amounts`` re-exports ``settled_contribution``.
from app.services.row_valuation import (
    leg_settled_amounts_by_key,
    settled_amounts_by_id,
)
# Re-exported so a caller ABOVE the amount model asks the MODEL for the
# model's own eager load and never has to know the relationship graph (plan
# step X-au-g-2c-2).  It is DEFINED a tier down, in ``app.utils``, because
# ``loan_loaders`` -- which this package imports for its loan term primitives --
# is the loader that most needs it and cannot import this package back.
from app.utils.amount_relationships import (
    pricing_load_options,
    settlement_load_options,
    transfer_pricing_load_options,
    valuation_load_options,
)
# The loan-pricing leaf FIRST, because it is the bottom of this package's
# pricing line: ``_loan_installment`` -> ``_loan_pricing`` -> ``_amount_basis``
# -> ``_amount_source``, and the block below reads in tier order.
from ._loan_pricing import (
    LoanPricing,
    loan_pricing,
)
from ._amount_basis import (
    AmountBasis,
    amount_basis,
    baseline_amount_basis,
    derived_amount_basis,
)
from ._amount_rule import (
    AmountRule,
    amount_rule,
    is_loan_payment_definition,
    transfer_amount_rule,
)
from ._definition_cash import DefinitionRow, definition_cash
from ._amount_source import (
    amounts_by_id,
    leg_amounts_by_key,
    resolve_transaction_amount,
    resolve_transfer_amount,
)
from ._amounts import (
    ReconciledThrough,
    contributed_amount,
    contribution_of,
    contributions_by_id,
    leg_contribution_of,
    leg_contributions_by_key,
    planned_leg_contribution,
    settled_contribution,
)
from ._cash_leg import (
    cash_leg_of,
    credit_entry_sum,
    elsewhere_purchase_sum,
    movement_cash_leg,
    movement_figure_for,
    off_statement_sum,
    posted_purchase_sum,
)
from ._clearing import (
    ClearableLine,
    StatementCoverage,
    statement_coverage,
)
from ._books import (
    books_hold,
    earliest_assertion_day,
    earliest_matched_line_day,
    earliest_recorded_movement_day,
    reject_books_open_after_an_assertion,
    reject_books_open_on_or_after_matched_lines,
    reject_books_open_on_or_after_movements,
    reject_line_before_books_open,
    reject_movement_before_books_open,
)
from ._events import (
    CashAnchorFact,
    CashOpeningFact,
    CashSourceFact,
    InFlightMovement,
    account_opening_fact,
    cash_anchor_facts,
    coverage_for,
    governing_account_opening,
    in_flight_movements,
    settled_cash_facts,
)
from ._facts import (
    AnchorPoint,
    governing_anchor,
    governing_anchor_on,
    planned_cash_rows,
    reconciled_through,
    resolve_anchor,
)
from ._flows import sum_projected
from ._walk import (
    CashLedgerWalk,
    dated_deltas,
    walk_cash_ledger,
)

__all__ = [
    "AmountBasis",
    "AmountRule",
    "DefinitionRow",
    "AnchorPoint",
    "LoanPricing",
    "governing_account_opening",
    "in_flight_movements",
    "governing_anchor",
    "governing_anchor_on",
    "CashAnchorFact",
    "CashLedgerWalk",
    "CashOpeningFact",
    "CashSourceFact",
    "InFlightMovement",
    "ClearableLine",
    "ReconciledThrough",
    "StatementCoverage",
    "amount_basis",
    "baseline_amount_basis",
    "derived_amount_basis",
    "books_hold",
    "amount_rule",
    "definition_cash",
    "is_loan_payment_definition",
    "transfer_amount_rule",
    "amounts_by_id",
    "leg_amounts_by_key",
    "account_opening_fact",
    "cash_anchor_facts",
    "coverage_for",
    "contributed_amount",
    "contribution_of",
    "contributions_by_id",
    "leg_contribution_of",
    "leg_contributions_by_key",
    "credit_entry_sum",
    "dated_deltas",
    "earliest_assertion_day",
    "earliest_matched_line_day",
    "earliest_recorded_movement_day",
    "elsewhere_purchase_sum",
    "loan_pricing",
    "cash_leg_of",
    "movement_cash_leg",
    "movement_figure_for",
    "off_statement_sum",
    "settled_contribution",
    "planned_cash_rows",
    "planned_leg_contribution",
    "posted_purchase_sum",
    "reconciled_through",
    "resolve_anchor",
    "resolve_transaction_amount",
    "resolve_transfer_amount",
    "reject_books_open_after_an_assertion",
    "reject_books_open_on_or_after_matched_lines",
    "reject_books_open_on_or_after_movements",
    "pricing_load_options",
    "settlement_load_options",
    "transfer_pricing_load_options",
    "valuation_load_options",
    "reject_line_before_books_open",
    "reject_movement_before_books_open",
    "leg_settled_amounts_by_key",
    "settled_amounts_by_id",
    "settled_cash_facts",
    "statement_coverage",
    "sum_projected",
    "walk_cash_ledger",
]
