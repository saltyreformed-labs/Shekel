"""
Shekel Budget App -- Balance-at-T seam (Level 1, Option D Build-Order Step 1).

The single public way any screen obtains an account's balance over time.
Six producers historically answered "what is account A's balance at time
T?", and the three recompute-at-read kinds (loan, investment, property)
each bolted on their own rule for periods before an account's first known
data point; every new surface re-invented that boundary and shipped a bug
at least once.  This package owns all four per-kind boundary rules in ONE
place, documented and tested together (the documented-once contract):

**There are TWO answers here, not five, and the five-bullet per-kind narrative
this docstring used to carry was residue of a dispatch ladder that no longer
exists** (finding N-95, deleted at plan step X-g4b).

* **A configured LOAN** is its amortization :func:`positions` (plan C3): the
  FOLD of the loan's recorded events (anchors + settled payments -- the
  complete record of the past, true-ups above all) for a date at or before the
  pass's as-of, and the forward PLAN fold (payment records, then contractual
  synthesis) after.  A date before any event folds to ``0.00`` (the loan does
  not exist yet), and a paid-off loan holds its folded ``0.00`` flat -- NEVER
  the loan's original principal (which made the liability leap down the moment
  the first payment landed).
* **EVERYTHING ELSE** is ONE event replay
  (:mod:`app.services.balance_at._asset_fold`): the cash fold -- assertions,
  settled rows and the still-projected plan -- plus an ACCRUAL tier that exists
  only for an account whose own parameters model a return, and a CONTRIBUTION
  tier only for one whose payroll funds it.  A checking account, an HYSA, a
  brokerage and a Property are therefore not four dispatches; they are one
  producer given different facts, which is what ruling R-AD deleted the ladder
  to say.  Every period is answered: a past one reads the balance in force
  THEN, and a period before the account's FIRST assertion reads the account's
  stored OPENING EQUITY plus whatever its records hold by then (plan step
  X-f3c-2a, ruling R-GX).

The seam does NOT reimplement the growth math.  It loads each account's
modelled-contribution feed (its investment params, its deductions and the
engine gross-biweekly) from the shared loaders and hands it to the replay,
whose rate resolver reads the one params row the account's kind carries.
Centralising a dispatch that two consumers had each grown their own copy of is
the whole point: a third copy is exactly the duplication this work exists to
kill.

**Five shapes, one seam.**

* The KIND-CORRECT entries (:func:`balance_map`, :func:`build_maps`,
  :func:`balance_at`, plus the growth decomposition
  :func:`investment_growth_since_anchor`) answer "what is this account WORTH" --
  a HYSA accrues interest, a loan walks its amortization schedule, an investment
  / property compounds -- the view the NET-WORTH surfaces (the savings cockpit
  and the dashboards) want.  Since plan step X-g2b they dispatch on ONE
  question, not five: a configured loan is its ``positions()``, and every other
  kind is one event replay whose modelled tiers exist only if the account's own
  parameters put them there (ruling R-AD).  See :mod:`._kind_correct`.
  ``investment_seed_map`` was a sixth entry here and is GONE (ruling R-AE): a
  chart's pre-growth SEED existed only because the previous design could not
  express "this account's balance at a DATE", so a caller now reads
  :func:`balance_at` the day before its projection window opens and there is
  nothing to keep in step.
* The CASH-FLOW entries (:func:`cash_balance_map`, :func:`cash_balance_at`,
  :func:`cash_daily_balance_series`) always return the account's pure
  transaction running-balance with NO kind dispatch -- the view the
  single-account cash-flow surfaces (dashboard pulse, calendar, cash detail)
  need, where the balance must reconcile with the on-screen transaction
  rows, and where the account is NOT guaranteed cash
  (``resolve_grid_account`` can point at any kind).  A modelled return may
  reach such a surface only where a ROW explains it, which is ruling R-K's
  ``balance[p] - balance[p-1]
  == net[p] + period_timing[p] + book_vs_bank[p] + contribution[p]
     + accrual[p]`` -- whose
  FOURTH term is ruling R-AH's correction: a modelled asset has TWO modelled
  tiers, and on the real Empower 401(k) the CONTRIBUTION is the larger of them,
  so the three-term form breaks on 53 of 59 real period pairs.  The GRID has
  those rows and left this family at plan step X-g3b; see :mod:`._grid` for
  :func:`grid_balance_view`, which answers every kind its modelled balance.
  See :mod:`._cash_flow` for the entries the pulse, the calendar and the cash
  detail page still read -- and for :func:`records_balance_at`, the fourth,
  which answers the same fold at a date with the day's OWN assertion not yet
  applied (ruling R-EU): what the records produce, as against what the app
  currently reports for a day the user has already declared a balance for.
  :func:`cash_anchor_history` is the FIFTH and the only one that is not a
  balance at a date at all: it is the account's assertion LOG, every recorded
  balance beside what the records held immediately before it (ruling R-EV).
  It is here rather than on the ``cash_ledger`` leaf because its ``ledger``
  column IS a balance-at-T -- the running total just before an assertion reset
  it -- and no screen reaches one except through this door.
  :func:`cash_outstanding_difference` is the SIXTH (plan step X-f3c-3, ruling
  **R-FN**): the owner's latest declared balance beside what the account's
  books produce for that same day -- opening equity plus every posting through
  it, with no assertion applied -- which is ONE figure per account rather than
  the per-assertion plug that telescopes.  It is the post-cutover balance
  function evaluated today, so what it measures is what plan step X-f3c-5's
  flip will leave unexplained.  See :mod:`._outstanding`.
  :func:`cash_last_record_on` is not a balance either: it is the latest
  day on or before NOW the books hold a record (a settled row or a
  true-up), which is where the bank comparison's drawn days end when the
  records run past the last statement line (finding BAL-651, rulings
  plan#2346, plan#2399 and plan#2400).
* The LIABILITY entry (:func:`liability_owed_at_dates`) answers what every
  debt OWES at a list of FORWARD calendar dates in one resolution pass --
  the shape a long-horizon liability band needs, which neither the period-keyed
  maps nor the scalar can serve without re-resolving each loan per date.  Since
  plan step credit_card:CC-1 it is kind-blind: every liability's future is the
  kind-correct multi-date read (``_kind_correct.balance_at_dates``, which the
  scalar is a one-date reading of), so a Credit Card's projected balance reaches
  the horizon instead of holding flat.  See :mod:`._liability`.  The ONE sign
  flip (what an account owes is minus the balance it holds; ruling R-CC29's
  flip) is NOT a seam export: it was one from plan step credit_card:CC-5-5a
  (ruling R-CC47) until CC-5-5b moved it to
  :func:`app.services.liability_sign.owed`, beside the crossing the balance
  doors read, and outside the seam so the seam's own configured-loan arms can
  read it without a circular import.  They do since CC-5-5c: every balance
  this seam reports is HELD, a configured loan's included (ruling R-CC47).
* The LOAN-FIGURES entry (:func:`loan_figures`) answers everything a loan tile
  wants BESIDE its balance -- the payment, the rate, the payoff date, whether it
  is retired -- and deliberately carries NO balance, so a consumer holding it
  cannot render a wrong one.  See :mod:`._loan_figures`.
* The SECURED-DEBT entry (:func:`secured_loan_series`) packs each loan a property
  secures into the rows its equity chart draws a debt line from.  That line is a
  balance-at-T series, and the assembly used to live in the property ROUTE, which
  had to hold a whole ``ResolvedLoan`` to do it -- one attribute read from an
  unfenced loan balance.  See :mod:`._secured_debt`.

Every family routes through this one package, so no screen reaches a producer
directly -- structurally, since plan step D3: every producer is a PRIVATE
submodule here, and the package-privacy gate W9910
(``shekel-private-module-import``) hard-fails any outside import of one in any
spelling.  That is what let the name-keyed producer fence delete: down to one
call surface at D3, and out of existence at plan step E1e, when the last public
balance producer outside this package (the genesis posting readers) was itself
deleted.

Package layout.  The seam outgrew a single module (the 1000-line cap), so each
view lives in its own private submodule and this ``__init__`` re-exports the
public surface -- consumers keep importing exactly as before
(``from app.services import balance_at``; ``balance_at.balance_map(...)``).
Re-exporting a name HERE is what makes it public: this file is the one
reviewed door a new seam entry ships through.

Dependency direction (SOLID).  Consumers (routes, savings, analytics,
dashboards) depend on this seam; the seam depends only on the outer engine
inputs (``account_projection`` / ``projection_inputs`` / ``income_service``),
the ``pay_calendar`` leaf every "which paycheck" answer comes off -- **this
SEAM read ``pay_period_service`` until plan step C2-f2a, whose end state is
that no module here imports it** -- and the ``cash_ledger`` leaf the cash producers fold
over, the LOAN leaves it composes the loan shapes from (``loan_resolver`` /
``loan_ledger`` / ``loan_loaders`` / ``loan_payment_service`` /
``amortization_engine``), and the models -- never a consumer package.  **Plan
step D1d moved ALL of the balance PRODUCERS INSIDE this package as private
submodules**, so the fence is one package boundary.  **Plan steps X-c2b2 and
X-g2b then replaced every one of them with TWO folds** -- ``_cash_fold`` for
cash and ``_asset_fold``, which is that fold plus the modelled tiers, for every
non-loan kind -- and plan step **X-g4b deleted the replaced producers whole**:
``_cash_engine`` (the anchor-forward roll-up), ``_calculator`` (the pure
carry-forward walk it delegated to), ``_investment`` (the growth / appreciation
three-source merge) and ``_interest`` (the modelled accrual layered over a
finished base map, whose one surviving predicate folded into
``_asset_fold._modelled_return``).  Deleted alongside them at X-c2b3: the
per-day producer ``_daily_series`` whole (the calendar's running-balance line is
the fold sampled at every day of its range), the date-precise scalar
``balance_as_of_date`` with its prefix walk, and the ``BalanceResult``
wrapper -- whose ``stale_anchor_warning`` field the fold makes unrepresentable,
a settled row after the last assertion now MOVING the balance rather than
warning that it might not have (findings cash D1 / D2, N-50).
**Plan step D-ctx then moved the read
pass's resolution CONTEXT in too** (``_context`` = ``BalanceContext``, and
``require_scenario`` beside it until plan step recurrence:R7d-f-2 moved that
guard and ``_memoize_once`` into ``_memoize`` -- ruling **R-R75**, a split of
the module plan ledger row **BAL-483** recorded at the 1,000-line cap, on a
different cut than the one the row named; ``require_scenario`` is re-exported
below as before and ``_memoize_once`` stays package-private, imported by the
five sibling modules that fill a per-account cache): it sits one rung above
the internal DAG's FLOOR, importing ``_memoize`` and the outer loan leaves it
memoizes, and depended on by every producer that folds a loan.  **Plan step E1d-a
then moved the db-facing WHOLE-LOAN read in** (``_resolution`` =
``resolved_loan`` / ``ResolvedLoan`` / ``contractual_schedule_from_origination``,
formerly the public module ``app.services.loan_resolution``): its only production
caller was this seam, and step E1d makes its confirmed seed a FOLD, so the
composer that consumes a balance-at-T now sits on the same side of the boundary
as the producer of one.  It is re-exported NOWHERE -- W9910 alone protects it,
in every import spelling -- which is what lets its hand-written W9909
completeness scope DELETE rather than shrink or travel.  ``_kernel``'s
``interest_by_period_for_account`` is re-exported below as a non-balance seam
entry with no ``app/`` reader (finding N-96; the account-detail route reads its
sibling ``interest_projection_for_account``); ``debt_schedule_rows``, the
savings orchestrator's, went with its last caller at plan step
recurrence:R16-c-2 (ruling R-R112).  ``property_equity_chart`` and
``home_equity_service`` import
FROM here, not the other way round.  Inside the package the direction is
``_grid -> {_asset_fold, _cash_fold, _cash_periods, _inputs}``,
``{_cash_flow, _kind_correct, _cash_periods} -> _cash_fold -> _fold``,
``{_inputs, _positions, _loan_interest} -> _kernel -> _asset_fold ->
{_asset_contributions, _cash_fold}``, ``_kind_correct -> {_asset_fold,
_inputs}``,
``_liability -> {_kind_correct, _inputs}``, ``_secured_debt -> {_loan_figures, _positions,
_inputs}``, ``_loan_figures -> _positions -> {_loan_stream, _plan_fold}`` (the
figures' payoff is the fold to zero, plan step C8d), ``{_positions,
_loan_interest} -> _loan_stream -> _plan`` -- the ONE timeline, the recorded
walk with the forward model's payments replayed behind it (plan step
recurrence:R16-c-1) -- and ``_plan_fold -> _fold`` (the two forward readings
that are not a balance at a date; it held the forward FOLD from plan step
R16-a, when ``_plan`` passed the line ceiling, until R16-c-1 merged that fold
into the timeline) -- and ``_plan -> {_plan_definitions,
_plan_records}``, ``_plan_definitions -> {_plan_records, _resolution,
_context}``, the second split of ``_plan`` at plan step R16-b-2 (the definition WALK that sums
every recurring transfer into a loan, and the two RECORD types both halves
share) -- a DAG with ``_fold`` at the producer floor, so no module imports a
sibling that imports it back.  Every loan producer also
imports ``_resolution`` for the read
pass's ONE whole-loan read; ``_resolution`` imports only ``_context`` among its
siblings, plus ``_confirmed_view`` for the confirmed seed it threads into every
resolution (plan step E1d-b); ``_confirmed_view`` imports ``_context`` and
``_fold``, so that sub-chain is a DAG too.  ``_resolution`` also holds the
loan-payment IDENTITY and ruling R-R56's arm since R16-b-2 (``_plan`` reads
them and ``_loan_figures`` sits above ``_plan``).  ``_memoize`` sits at the
floor, with ``_assertions``, ``_asset_contributions``, ``_fold`` and
``_plan_records`` -- the five modules that import no sibling at runtime (an
AST census of 2026-09-12; the sentence read "three" and named ``_context``
among them until recurrence:R7d-f-2, wrong on both counts since ``_assertions``
and ``_plan_records`` joined); ``_context`` imports ``_memoize``,
``_definition_books`` and ``_recurrence_memos`` (its mixin, ruling R-BAL146,
which imports ``_definition_books``) and nothing else of the package.
``_plan``'s ``LoanForwardPlan``, ``_resolution``'s
``ResolvedLoan`` and ``_cash_fold``'s ``AssembledCashFold`` are all type-only
edges typing the caches the seam FILLS, so the arrow from the fourteen modules
above stays one-way and the cycle finding N-25 names stays open.
``_memoize_once`` lives in ``_memoize`` (in ``_context`` until
recurrence:R7d-f-2), and since plan step **X-i4** it is where a read
pass BINDS the account it values: it takes the ``Account`` rather than a bare
id and refuses one the pass does not own, before the membership test, so every
per-account cache on the pass inherits the rule from the only thing that can
create one.

Boundary discipline (``CLAUDE.md``): no Flask symbol, no writes.  All money
is :class:`~decimal.Decimal`; ``float`` only at a serialization boundary.

Liability classification is NOT a balance concern: the balance MAPS here are
balances only, every one HELD (negative when owed; ruling R-CC47), and the
net-worth reduction lives with its consumers in ``savings_dashboard_service``.
Net worth is the plain sum of those balances -- no sign branch -- and what a
liability OWES is :func:`app.services.liability_sign.owed` of its balance,
which ``_net_worth.compute_net_worth_today`` (the hero's liability total) and
``_net_worth._sum_composition_at_period`` (the per-period liability band) read,
and which :func:`liability_owed_at_dates` below applies for
``_horizon._liability_band``.  Until plan step credit_card:CC-5-5c all three
took ``abs`` instead, because a configured loan reported owed and every other
account held, and a card holding a credit read as debt (ledger row CC-354).
All three classify asset-vs-liability through the one
``account_category.is_liability_account`` home.
"""

from ._cash_fold import CashDayFacts, CashDaySeries
from ._cash_flow import (
    CashAnchorHistory,
    CashAnchorRow,
    CashOpeningRow,
    cash_anchor_history,
    cash_balance_at,
    cash_balance_map,
    cash_daily_balance_series,
    cash_daily_facts_series,
    cash_last_record_on,
    records_balance_at,
)
from ._confirmed_view import confirmed_view
from ._context import BalanceContext
from ._definition_books import (
    definition_books,
    definition_money_accounts,
    money_account_columns,
    resolved_with_books,
    row_books_opened_on,
)
from ._memoize import require_scenario
from ._grid import (
    GridBalanceView,
    GridColumn,
    GridRowFlags,
    empty_grid_view,
    grid_balance_view,
)
from ._inputs import (
    ZERO,
    _contribution_inputs_for_account,
)
from ._kernel import (
    interest_by_period_for_account,
    interest_projection_for_account,
)
from ._kind_correct import (
    balance_at,
    balance_map,
    build_maps,
    investment_growth_since_anchor,
)
from ._liability import liability_owed_at_dates
from ._resolution import is_standing_loan_payment
from ._loan_figures import (
    LoanFigures,
    LoanTerms,
    loan_closing_date,
    loan_figures,
    loan_terms,
)
from ._loan_interest import (
    loan_interest_in_year,
    loan_interest_paid_in_year,
    loan_principal_paid_in_year,
)
from ._outstanding import (
    BooksSpan,
    CashOutstandingDifference,
    cash_outstanding_difference,
)
from ._plan_fold import first_installment_at_most, installments_payoff
from ._positions import (
    loan_installments,
    loan_payoff_date,
    loan_required_extra,
    loan_what_if_owed_at_dates,
    positions,
    positions_period_map,
)
from ._secured_debt import (
    TIER_CONFIRMED,
    TIER_ESTIMATED,
    TIER_PROJECTED,
    SecuredLoanSeries,
    secured_loan_series,
)

# The seam's public surface, re-exported so every consumer's existing
# ``balance_at.<entry>`` attribute access keeps working unchanged after the
# module -> package split.  The underscore-prefixed names are
# internal-but-tested: ``tests/test_services/test_income_service.py`` reaches
# ``balance_at._contribution_inputs_for_account`` to pin the raise-aware gross at the
# seam's own loading point.  It is the ONLY private name here with an outside
# reader.
# They are listed here so the re-export is explicit rather than an unused
# import.
#
# **The list named the wrong test file and the wrong count** (plan step
# X-g3b-0, which corrected it while deleting two of the three names).  It said
# ``test_balance_at.py`` reached ``._assemble_inputs`` / ``._AssembledInputs`` /
# ``._account_balance_map`` "to pin the assembly contract directly".  That file
# reached NONE of them; the only reader of any was
# ``test_income_service.py``, and only of ``._assemble_inputs``.  The first two
# names were deleted with the bundle, and ``._account_balance_map`` went with
# them rather than staying exported for a reader that does not exist -- it is
# imported directly by the one module that dispatches through it.
# ``._require_scenario`` was dropped in the same commit and for the same reason:
# no reader outside this package ever reached it.  The seam modules that want it
# import it directly -- four of them under this name from ``._inputs``, and
# ``._context`` / ``._inputs`` / ``._plan`` / ``._positions`` as
# ``require_scenario`` from ``._memoize`` (from ``._context`` until
# recurrence:R7d-f-2 moved it), which is the same guard under the name it is
# defined with.  (Its underlying ``require_scenario`` stays
# public below -- that is the seam's documented read-pass handle, and a
# different name with a real audience.)  ``._accruing_balances`` was a fourth until plan step
# X-c2b2 deleted it: the grid's Interest row IS the accrual map ``_interest``
# returns, not the difference of two independently-computed balance maps
# (finding N-52).
__all__ = [
    "ZERO",
    "BalanceContext",
    "BooksSpan",
    "CashAnchorHistory",
    "CashAnchorRow",
    "CashOpeningRow",
    "CashOutstandingDifference",
    "CashDayFacts",
    "CashDaySeries",
    "GridBalanceView",
    "GridColumn",
    "GridRowFlags",
    "LoanFigures",
    "LoanTerms",
    "first_installment_at_most",
    "installments_payoff",
    "SecuredLoanSeries",
    "TIER_CONFIRMED",
    "TIER_ESTIMATED",
    "TIER_PROJECTED",
    "_contribution_inputs_for_account",
    "balance_at",
    "balance_map",
    "build_maps",
    "cash_anchor_history",
    "cash_balance_at",
    "cash_balance_map",
    "cash_daily_balance_series",
    "cash_daily_facts_series",
    "cash_last_record_on",
    "cash_outstanding_difference",
    "confirmed_view",
    "definition_books",
    "definition_money_accounts",
    "empty_grid_view",
    "grid_balance_view",
    "interest_by_period_for_account",
    "interest_projection_for_account",
    "investment_growth_since_anchor",
    "liability_owed_at_dates",
    "is_standing_loan_payment",
    "loan_closing_date",
    "loan_figures",
    "loan_terms",
    "loan_interest_in_year",
    "loan_interest_paid_in_year",
    "loan_installments",
    "loan_payoff_date",
    "loan_required_extra",
    "loan_what_if_owed_at_dates",
    "loan_principal_paid_in_year",
    "money_account_columns",
    "positions",
    "records_balance_at",
    "resolved_with_books",
    "row_books_opened_on",
    "positions_period_map",
    "require_scenario",
    "secured_loan_series",
]
