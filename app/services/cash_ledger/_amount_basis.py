"""
Shekel Budget App -- Cash ledger: the DERIVATION TIER a read pass holds.

**Split out of :mod:`._amount_source` at plan step X-au-j**, which took that
module past ``max-module-lines``.  The cut is by tier rather than by size, and
it is the seam the package docstring's table already draws: what a row's amount
IS is a question about the ROW and its rules, and it lives next door; what the
owner's live producers ANSWER is a question about the OWNER and a SCENARIO, and
that is this module.  Nothing here classifies a row, dispatches a rule or
resolves a figure -- :class:`AmountBasis` is data and its three constructors
resolve nothing at all.

**Shaving prose to stay under the cap was the alternative, and this project has
already ruled against it**: *"three lines of headroom is not a design, and the
structural answer is a package with one private leaf per verb"*
(``transaction_service`` package docstring).  A first attempt at this step
trimmed two docstrings to reach 1010 lines before taking that sentence at its
word.

Nothing here imports from :mod:`._amount_source`, so the split introduces no
cycle: this module is strictly below it, and strictly above
:mod:`._loan_pricing`, which it imports outright.

Boundary discipline (``CLAUDE.md`` Architecture / B6-01): plain ids in, a
frozen dataclass out; no Flask import, no writes, and no runtime import of the
PAYCHECK stack (finding **N-267**).  *The same sentence named the loan-resolver
stack until plan step X-au-g-2a moved rule 4's producer into this package: the
loan derivation is a module of the cash ledger now, so there is no reach to
defer and the import is stated at the top like any other.*
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.services.loan_ledger import LoanCalendars

from ._loan_pricing import LoanPricing, loan_pricing

if TYPE_CHECKING:  # pragma: no cover - typing only
    # Named for the annotations alone.  A runtime import would put the paycheck
    # / tax stack on this module's load path, which is the cycle the call
    # sites below defer to avoid (finding N-267).  The LOAN half needed the
    # same deferral until plan step X-au-g-2a; its producer is a module of this
    # package now, so it is imported outright above.
    from app.services.income_service import PaycheckPricing, SalaryPricing

@dataclass(frozen=True)
class AmountBasis:
    """One read pass's live DERIVATIONS, pinned to an owner and a scenario.

    Built by :func:`amount_basis` and consumed by
    :func:`resolve_transaction_amount`.  The two derivations stay APART rather
    than merged, and that is the whole reason this type exists: a merged map
    makes "which rule applies" a question about map membership, which is the
    link-derived discriminator ruling R-FI refuted, and it hides which producer
    answered.  *A ``_amounts.live_amounts`` did merge their ANSWERS for callers
    that wanted one map; it went with the read-time repair at plan step X-au-d,
    and what a caller asks for a row set now is ``amounts_by_key``, which
    dispatches per row rather than merging per producer.*

    **It holds the derivations rather than per-row answers, and that is plan
    step X-au-c2b's restructure.**  It was
    ``{priced_ids, {transaction_id: net}, {transaction_id: cash}}`` -- built
    over ONE row set, because that is the shape both producers returned.  But
    everything expensive behind those maps is scoped by the OWNER, the SCENARIO
    and the LOAN, never by the caller's row set: the paycheck engine runs over
    the owner's whole pay-period set, and a loan's P&I, payment day and escrow
    history are the loan's.  Storing the lookup's output instead of the
    derivation behind it is what made a pass row-set-shaped, and three defects
    followed from that one mistake:

      * a request that loaded two row sets paid every derivation twice --
        findings **N-268** (the dashboard pulse re-pricing rows the cash fold
        had priced) and **N-269** (the transfer settle door re-querying the
        transfer it had just loaded), which are two filings of this one cause;
      * ``live_loan_transfer_amounts`` and ``live_loan_payment_amount`` were two
        implementations of ONE rule, the second's docstring stating that it
        "mirrors" the first's candidate filter -- kept in step by hand;
      * a row outside the set had no answer, so the basis had to carry
        ``priced_ids`` as a membership guard: without it a MISS was
        indistinguishable from a producer's deliberate omission, and an
        adversarial review reproduced the consequence -- a manual loan payment
        resolved outside its own basis answered ``$1,250.00`` against a correct
        ``$1,400.00``, silently dropping a standing ``$150.00`` extra.

    **That guard is DELETED rather than kept, because the failure it caught is
    now unconstructible.**  Nothing is "absent" from a derivation: a manual
    payment's cash is COMPUTED from its own config whenever it is asked, so
    there is no membership question left to answer wrongly.  A guard against a
    state the model cannot reach is a fence, and this arc's business is making
    fences structurally unnecessary rather than adding them.

    Both derivations are LAZY, so a pass that prices no paycheck and no loan
    payment issues no query -- the "fast no-op when there are no candidates"
    property the row-set producers had, kept rather than traded for the sharing.

    Attributes:
        user_id: The owner these derivations are pinned to -- read off the
            pricer the basis was built over since plan step salary:C12, so it
            cannot name a different owner than the paychecks do.
        scenario_id: The scenario they resolve under.
        salary: The scenario-and-pricer salary derivation
            (:class:`app.services.income_service.SalaryPricing`): what each
            active profile pays, per template and period, read from the
            pass's own pricer.
        loans: The pass's loan derivation
            (:class:`._loan_pricing.LoanPricing`): each destination loan's
            rate-period set, contractual payment day and escrow history,
            resolved at most once per loan.  It answered *which transfers are
            loan payments* too until plan step X-au-g-2c-2 deleted the
            read-time repair that needed the question asked scenario-wide; the
            amount model reads the refinement off the parent transfer it was
            handed, per row.
    """

    user_id: int
    scenario_id: int
    salary: "SalaryPricing" = field(compare=False, repr=False)
    loans: LoanPricing = field(compare=False, repr=False)


def amount_basis(
    paychecks: "PaycheckPricing", scenario_id, loan_calendars: LoanCalendars,
) -> AmountBasis:
    """Return the read pass's :class:`AmountBasis` over its pricer and scenario.

    Resolves NOTHING -- both derivations behind it are lazy -- so building one
    is free and a caller may build it before it knows whether any row will need
    it.  What it costs to ask is paid once per pass however many row sets ask,
    which is the point of plan step X-au-c2b's restructure.

    **It takes the pass's PRICER rather than the owner's id, and that is plan
    step salary:C12** (ledger row **P63**).  Built from ``(user_id,
    scenario_id)`` alone, the salary derivation had nothing to read a paycheck
    from and derived a SECOND pricer over a second calendar, so a render that
    priced a salary row and read a payroll feed derived the owner's calendar
    twice and priced the overlapping paydays twice.  The owner is read off the
    pricer now, which is one fewer thing the two can disagree about.  Two
    production callers hand a PASS's pricer: :meth:`app.services.balance_at
    .BalanceContext.amounts`, which hands its own :meth:`~app.services
    .balance_at.BalanceContext.paychecks`, and the carry-forward context,
    which hands the same under the scenario it carries forward within; a
    producer that holds NO pass builds one through :func:`derived_amount_basis`
    below, whose name is what its census greps for.

    Calling the derivations per row is finding **N-228**: the paycheck engine
    runs ``paycheck_calculator.project_salary`` over the owner's whole
    pay-period set, because four of its judgements read that set -- the
    third-paycheck test, the first-paycheck-of-month deduction cadence, the
    FICA wage-base cumulative and a deduction's annual cap (**N-390**).  *The
    biweekly rounding residue was this sentence's reason until plan step
    balance:X-aw deleted the residue; the requirement outlived it.*  One basis
    per read pass is what makes
    the per-row rules cheap; a read pass holds its own through
    :meth:`app.services.balance_at.BalanceContext.amounts`.

    **It took the OWNER's id rather than an ``Account`` from plan step
    X-au-c2's re-keying until salary:C12 took the pricer.**  The only thing it
    ever read off the account was ``account.user_id`` (the salary derivation
    scopes its profile lookup by owner; the loan derivation scopes by scenario
    alone), so requiring the object forced a CROSS-ACCOUNT reader -- the
    calendar, the spending report, a dashboard -- to group its rows by account
    and pay for one basis per group; the owner now arrives on the pricer.

    **The loan derivation takes NEITHER a clock nor a scenario**, and the
    second went at plan step X-au-g-2c-2 with the config map it scoped: a
    loan's terms are not scenario-scoped, so the argument only ever expressed a
    mistake.  Pricing a row against another scenario's basis is refused by
    :func:`._amount_source.resolve_transaction_amount`, from the row's own
    column.  **Neither derivation reads a clock, and plan step X-au-g-2b is
    what made that true of the loan half.**  It was built as
    ``loan_pricing(scenario_id, date.today())`` and resolved every loan-payment
    shadow's P&I against that one date -- finding **N-40**, and the last
    ``date.today()`` call anywhere in this package (a control asserts the
    absence: ``test_amount_source.TestTheAmountModelReadsNoClock``).  Ruling
    **R-IJ** closed it STRUCTURALLY rather than by threading a different date: a
    loan's contractual terms resolve on the installment they govern, so
    :class:`._loan_pricing.LoanPricing` has no date to take.  Plan step
    **X-i2**, which hands each memoized loader the read pass's own ``as_of``,
    therefore no longer has this derivation as a subject -- there is nothing
    left here for a pass-level clock to correct.

    **It takes the pass's LOAN CALENDARS for the same reason it takes the pricer**
    (plan step recurrence:R25, ruling **R-R105**, finding **REC-545**): a loan
    payment's price and the loan's charges read ONE calendar, so the pass that
    walks a loan hands its own memo here rather than letting the pricer load
    the loan's terms a second time.

    Args:
        paychecks: The read pass's
            :class:`~app.services.income_service.PaycheckPricing`.  Its owner
            scopes the salary derivation's profile lookup, and its calendar
            and memo are what a salary row's paycheck is read from.
        scenario_id: The scenario the amounts resolve under.
        loan_calendars: The read pass's
            :class:`~app.services.loan_ledger.LoanCalendars` -- the memo its
            loan walk reads too, or a fresh one for a caller holding no pass.

    Returns:
        The unresolved :class:`AmountBasis` for that owner and scenario.
    """
    # Pylint: ``import-outside-toplevel`` -- ``income_service`` is imported
    # locally to keep the paycheck / tax stack off this module's load path and
    # out of any import cycle, exactly as ``_amounts`` has always done; it is
    # only needed at call time.  The LOAN derivation needed the same deferral
    # until plan step X-au-g-2a moved it into this package, and now does not:
    # :func:`._loan_pricing.loan_pricing` is imported at the top.
    # pylint: disable=import-outside-toplevel
    from app.services import income_service
    return AmountBasis(
        user_id=paychecks.user_id,
        scenario_id=scenario_id,
        salary=income_service.salary_pricing(scenario_id, paychecks),
        loans=loan_pricing(loan_calendars),
    )


def derived_amount_basis(user_id: int, scenario_id: int) -> AmountBasis:
    """Return an :class:`AmountBasis` for a producer that holds NO read pass.

    **The interim that goes when every producer holds its pass, and its name
    is that census.**  Twelve call sites reach here because no
    :class:`~app.services.balance_at.BalanceContext` is in reach -- the two
    settle doors, the row re-render helpers (two), the two template-conflict
    choosers, the credit workflow, the spending report, the profile archive,
    and the companion, reconcile and statement-match scopes through
    :func:`baseline_amount_basis` -- and the pricer it is built over derives
    the owner's calendar on the first paycheck priced
    (:func:`~app.services.income_service.derived_paycheck_pricing`), which is
    exactly what :func:`amount_basis` itself did for every caller until plan
    step salary:C12.  So this costs what that cost and refuses what that
    refused: a non-salary row derives nothing.  Their ledger row is
    **BAL-491**, owned by plan step balance:X-i6; a caller holding a pass
    reads ``ctx.amounts()`` instead, each site moved deletes one call here,
    and this constructor goes with the last.

    Args:
        user_id: The owner whose rows are being priced.
        scenario_id: The scenario the amounts resolve under.

    Returns:
        The unresolved :class:`AmountBasis` for that owner and scenario.
    """
    # Pylint: ``import-outside-toplevel`` -- deferred for the reason
    # :func:`amount_basis` defers the same import (finding N-267).
    # pylint: disable=import-outside-toplevel
    from app.services import income_service
    return amount_basis(
        income_service.derived_paycheck_pricing(user_id), scenario_id,
        LoanCalendars(),
    )


def baseline_amount_basis(user_id: int) -> AmountBasis:
    """Return the BASELINE scenario's :class:`AmountBasis` for *user_id*.

    **One statement of the Phase-1 scenario pin, because three surfaces make
    it** (plan step X-au-j): the reconcile panel, the statement-match review
    pass and the companion card each price rows the app does not filter by
    ``scenario_id``, so each must say WHICH scenario it prices under.  Spelled
    per site -- as it was until an adversarial review counted three -- that is
    three edits when what-if scenarios land, on a pin whose whole failure mode
    is two surfaces pricing under DIFFERENT scenarios, which is exactly what
    :func:`resolve_transaction_amount` refuses a row for.

    Phase 1 is baseline-only: the only two scenario writers
    (``registration_service.register_user``, ``baseline_service``) both write a
    baseline under ``uq_scenarios_one_baseline``, so an account fully isolates
    a row set today.  When that changes, every caller of this and every scope
    it prices take the same operating scenario in ONE edit.

    It RAISES rather than answering ``None`` (ruling **R-BW**): a pass that
    cannot name its scenario has no honest figure to publish, and the
    application's one handler answers
    :class:`~app.exceptions.BaselineMissingError` with the setup-recovery
    response.

    Args:
        user_id: The owner whose rows are being priced.

    Returns:
        The unresolved :class:`AmountBasis` for that owner's baseline.

    Raises:
        BaselineMissingError: When the owner has no baseline scenario.
    """
    # Pylint: ``import-outside-toplevel`` -- the resolver is imported at call
    # time for the reason ``amount_basis`` imports its producers that way, and
    # to keep this module free of a service-layer import at load.
    # pylint: disable=import-outside-toplevel
    from app.services.scenario_resolver import require_baseline_scenario
    return derived_amount_basis(user_id, require_baseline_scenario(user_id).id)
