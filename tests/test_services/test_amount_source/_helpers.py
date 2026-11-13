"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_amount_source.py``, moved verbatim by ``tools/split``.
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import AcctTypeEnum, AmountSourceEnum, TxnTypeEnum
from app.extensions import db
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.services import template_amount_service, transfer_legs
from app.services.amount_ownership import declare_derived
from app.services.cash_ledger import derived_amount_basis, resolve_transaction_amount
from tests._test_helpers import (
    add_escrow_line,
    create_loan_account,
    current_pay_period,
    generate_row_of,
    generate_transfer_of,
    loan_params_for,
    make_cadence_rule,
    make_every_period_rule,
    make_loan_payment_template,
    make_salary_profile,
    repriced_by_the_owner,
    transfer_repriced_by_the_owner,
)
from tests.oracles.recurrence_baseline import EVERY_PERIOD


# The definition's price history, and the row dates that select from it.  TWO
# versions rather than one, deliberately: a single-version series answers the
# same figure on every day, so a test built on one cannot tell a resolver that
# reads DATES from one that ignores them -- nor the series from the
# ``default_amount`` scalar it replaced, which ``set_amount`` kept equal to the
# NEWEST version.  Production has exactly one multi-version template of each
# kind, which is why this discrimination lives here rather than in the oracle.
_OLD_PRICE = Decimal("178.00")


_NEW_PRICE = Decimal("165.30")


_PRICE_ROSE_ON = date(2026, 1, 1)


_PRICE_FELL_ON = date(2026, 3, 1)


_DUE_UNDER_OLD_PRICE = date(2026, 2, 14)


# A TRANSACTION row's date is the ENGINE's (plan step balance:X-cf-3b): an
# every-paycheck definition dates its row on the paycheck's own start
# (``compute_due_date``), so WHICH paycheck a row is generated in is what
# selects its price.  The seeded calendar runs biweekly from 2026-01-02, so
# its first paycheck opens under the old price and its sixth (2026-03-13)
# under the new one; the date tests assert the window each row lands in.  A
# row older than the series needs a paycheck before 2026-01-01, which that
# calendar does not hold -- its one case rebuilds the calendar to open in
# 2025.  A TRANSFER's date is the engine's too since plan step balance:X-ch
# (the twin builder :func:`_generated_transfer`), so the same two paychecks
# select a transfer's price; ``_DUE_UNDER_OLD_PRICE`` is what one case asks
# the DEFINITION's series directly with.
#
# **Under the every-paycheck cadence a row's due date IS its paycheck's
# start**, so a case whose subject is "the due date, not the period start"
# cannot be built on it: the mutation it exists to catch reads the same day.
# Such a case takes a MONTHLY cadence on the 1st and the paycheck that covers
# the 1st but does not start on it (``_PAYCHECK_STRADDLING_THE_PRICE_FALL``,
# 2026-02-27..03-12, whose row the engine dates 2026-03-01).
_PAYCHECK_UNDER_OLD_PRICE = 0


_PAYCHECK_UNDER_NEW_PRICE = 5


_PAYCHECK_STRADDLING_THE_PRICE_FALL = 4


#: The first contractual installment of :func:`_mortgage` (originated
#: 2026-01-01, paid on the 1st), which is the occurrence its MONTHLY payment
#: rule first names; :func:`_loan_payment` generates in the paycheck covering it
#: (:func:`current_pay_period`, the calendar's own placement).
_FIRST_INSTALLMENT = date(2026, 2, 1)


_CALENDAR_OPENING_BEFORE_THE_SERIES = date(2025, 12, 5)


# The figure every OWN fixture stores on the row itself, and every settled one
# records as what moved.  No rule may answer it, so any test that returns it
# has caught a resolver reading a column.
_NOT_AN_ANSWER = "999.99"


def _basis_for(seed_user):
    """The AmountBasis for this owner and scenario -- what a caller builds.

    A basis is pinned to an owner and a scenario since plan step X-au-c2b, not
    to a row set, so there is one per test whatever it resolves.  Its two
    derivations are lazy, so building it here costs nothing until a rule asks.
    """
    return derived_amount_basis(seed_user["user"].id, seed_user["scenario"].id)


def _declare_transfer_derived(xfer):
    """Declare *xfer* priced by its definition, which EMPTIES its amount column.

    The application's own writer (``declare_derived``), flushed: a row states
    either a figure it owns or the relation that prices it, never both
    (``ck_transfers_amount_ownership``).  It had a transaction twin,
    ``_declare_derived``, which declared shadow rows and went with its last
    caller at plan step balance:X-bi-6-4d-2, where no shadow is born (ruling
    **R-BAL167** class 4), with ``_state_own_amount``, its other direction.

    Only ``template`` can price a transfer -- a transfer has no parent transfer
    -- so the relation is not a parameter here.  ``ck_transfers_adhoc_owns_amount``
    additionally refuses the declaration outright on an ad-hoc transfer, so this
    is only ever called on a generated one.
    """
    declare_derived(xfer, AmountSourceEnum.TEMPLATE)
    db.session.flush()
    return xfer


def _resolve(seed_user, txn):
    """Resolve one row against a basis built over exactly that row."""
    return resolve_transaction_amount(txn, _basis_for(seed_user))


def _priced_template(
    seed_user, name="Geico", txn_type=TxnTypeEnum.EXPENSE, *,
    cadence=EVERY_PERIOD, fires_on_day=None,
):
    """A transaction template whose series states two prices, two months apart.

    Every-paycheck by default; a case whose subject is the row's OWN date
    against its period's start states a MONTHLY *cadence* and the day.
    """
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Rent"].id,
        transaction_type_id=ref_cache.txn_type_id(txn_type),
        name=name,
    )
    db.session.add(template)
    db.session.flush()
    template_amount_service.set_amount(
        template, _OLD_PRICE, effective_on=_PRICE_ROSE_ON,
    )
    template_amount_service.set_amount(
        template, _NEW_PRICE, effective_on=_PRICE_FELL_ON,
    )
    # The definition first, then the cadence onto it (plan step R-F6): a
    # definition with rows is one that repeats, and the engine writes them.
    make_cadence_rule(template, cadence, fires_on_day=fires_on_day)
    db.session.flush()
    return template


def _seriesless_template(seed_user, name="Never Stated"):
    """A transaction template created around the write door, so its series is empty.

    It repeats every paycheck all the same: the engine writes rows from a
    cadence, not from a price, which is exactly what leaves this row with a
    definition that states nothing.
    """
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Rent"].id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        name=name,
    )
    db.session.add(template)
    db.session.flush()
    make_every_period_rule(db.session, template)
    return template


def _salary_template(seed_user, txn_type=TxnTypeEnum.INCOME):
    """A template an ACTIVE salary profile drives, and the profile."""
    template = _priced_template(
        seed_user, name="Data Manager", txn_type=txn_type,
    )
    profile = make_salary_profile(seed_user, db.session)
    profile.template = template
    db.session.flush()
    return template, profile


def _template_row(period, template, *, owns=False):
    """The ENGINE's row of *template* in *period* (plan step balance:X-cf-3b).

    DERIVED -- declaring its definition and storing no figure -- which is the
    state the template cutover (plan step X-au-e) puts every non-override row
    in, and it is dated by the engine: on the paycheck's start, for the
    every-paycheck cadence every definition here carries.  Pass ``owns=True``
    for the other half of the model -- a row the OWNER re-priced, taken the
    way the edit door takes it (:func:`repriced_by_the_owner`) -- where the
    stored ``_NOT_AN_ANSWER`` IS the answer.

    It built the row by hand and then linked it until X-cf-3b, which is the
    shape the census in ledger row BAL-480 could not see (date first, link
    second); the one axis its callers set on it, the DUE DATE, is now which
    paycheck they generate in.
    """
    txn = generate_row_of(template, period)
    return repriced_by_the_owner(txn, _NOT_AN_ANSWER) if owns else txn


def _legs_of(xfer):
    """Return *xfer*'s two legs, from-side first, as a screen reads them.

    ``transfer_legs.leg_of`` on each endpoint.  It replaces ``_shadow_of``,
    which returned one of the two shadow ROWS and went with its last caller at
    plan step balance:X-bi-6-4d-2, where no shadow is born (ruling
    **R-BAL167** class 4): a leg is read off its transfer, and
    ``amounts_by_key`` prices it.
    """
    return [
        transfer_legs.leg_of(xfer, xfer.from_account_id),
        transfer_legs.leg_of(xfer, xfer.to_account_id),
    ]


def _generated_transfer(
    seed_user, period, to_account, *,
    series=_OLD_PRICE, later=_NEW_PRICE, stored=Decimal("111.11"),
    owns=False, cadence=EVERY_PERIOD, fires_on_day=None,
):
    """The ENGINE's transfer, in *period*, of a template whose series states TWO prices.

    The twin of :func:`_template_row` (plan step balance:X-ch): DERIVED,
    parent and both legs -- the parent names its definition and each leg
    names the parent, the state plan step X-au-f puts every generated
    transfer in -- so their amount columns are EMPTY and a resolver reading
    any of the three answers ``None`` against an asserted ``Decimal``.  It is
    dated by the engine: on the paycheck's start under the every-paycheck
    *cadence* it carries by default, so WHICH paycheck selects the price
    (``_PAYCHECK_UNDER_OLD_PRICE`` / ``_PAYCHECK_UNDER_NEW_PRICE``), or on
    the day a MONTHLY *cadence* names inside the paycheck, for the one case
    whose subject is the date being the row's OWN and not its period's.  It
    built the transfer through the ad-hoc door, dated it by hand and linked
    it afterwards until X-ch -- date first, link second, the shape ledger
    row BAL-488's census could not see.

    Pass ``owns=True`` for the shape where a HUMAN authored the figure --
    parent and legs all holding ``stored``, a figure no derived rule may
    answer -- taken the way the transfer edit door takes it
    (:func:`transfer_repriced_by_the_owner`), which ruling **R-IO** says
    must keep the figure they typed.

    The price is stated through ``template_amount_service.set_amount``, the
    one write door, at the two dates the module's constants name.
    """
    template = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=to_account.id,
        name="Money Market Contribution",
    )
    db.session.add(template)
    db.session.flush()
    template_amount_service.set_amount(
        template, series, effective_on=_PRICE_ROSE_ON,
    )
    if later is not None:
        template_amount_service.set_amount(
            template, later, effective_on=_PRICE_FELL_ON,
        )
    make_cadence_rule(template, cadence, fires_on_day=fires_on_day)
    xfer = generate_transfer_of(template, period)
    if owns:
        transfer_repriced_by_the_owner(xfer, stored)
    return xfer, template


def _mortgage(seed_user, escrow_annual=Decimal("3600.00")):
    """A $200k / 6% / 360mo mortgage with an escrow line, paid on the 1st.

    P&I = amortize(200000, 0.06, 360) = 1,199.10; escrow = 3,600 / 12 = 300.00.
    """
    loan = create_loan_account(
        seed_user, db.session, name="Live Mortgage",
        principal=Decimal("200000.00"), rate=Decimal("0.06000"),
        term=360, origination_date=date(2026, 1, 1), payment_day=1,
        account_type=AcctTypeEnum.MORTGAGE,
    )
    params = loan_params_for(db.session, loan.id)
    add_escrow_line(
        db.session, loan.id, "Property Tax", escrow_annual,
        effective_date=params.origination_date,
    )
    return loan


def _loan_payment(
    seed_user, *, derive, extra=None, to_account=None,
    series=Decimal("1300.00"), stored=Decimal("1250.00"), owns=False,
):
    """A mortgage payment transfer in one of its two modes.

    The ENGINE's transfer of the loan's own definition
    (:func:`make_loan_payment_template` -- a MONTHLY rule on the loan's
    payment day, bound to its first contractual installment, with the
    ``loan_payment_settings`` row that carries the MODE), generated in the
    paycheck covering ``_FIRST_INSTALLMENT`` and dated on it.  A hand-built
    one sat in whichever paycheck the caller named and carried that date by
    hand (plan step balance:X-ch).

    TWO DISTINCT figures -- the definition states ``$1,300.00`` and, with
    ``owns=True``, the parent transfer's column holds ``$1,250.00`` -- so a
    manual-mode assertion names which of the two it means.  The review that
    built this fixture found an earlier one setting every figure to one
    number, which made the test pass for any implementation.

    **There were THREE until plan step X-au-g-2c-2, and the third is now
    UNREPRESENTABLE rather than merely unused.**  Each shadow held ``$1,200.00``
    so that a manual-mode assertion could prove the rule did not read the
    shadow's own column -- which is exactly what the deleted
    ``_manual_shadow_amount`` did read.  From that step a shadow declared
    ``PARENT_TRANSFER`` from birth and ``ck_transactions_amount_ownership``
    refused it a figure, so the distinction the third number graded was made
    by the schema while shadows were born.  None is born now, and no row can
    name a transfer: ``ck_transactions_names_no_transfer`` refused one until
    plan step balance:X-bi-6-5 dropped the column itself.

    ``owns=True`` takes the transfer for the OWNER at ``stored`` through the
    transfer door (:func:`transfer_repriced_by_the_owner`).  It also took each
    shadow's figure back to ``$1,200.00`` until plan step balance:X-bi-6-4d-2,
    where no shadow is born and the database refuses one (ruling
    **R-BAL258**), so that figure went with its row: the one case that named
    it names the transfer's own ``$1,250.00`` as the rival base instead
    (ruling **R-BAL272**).

    Returns the transfer.  It returned ``(the checking-side shadow, both
    shadows)`` until that step (ruling **R-BAL167** class 4); a case that
    priced a shadow asks the transfer or its legs (:func:`_legs_of`).
    """
    loan = _mortgage(seed_user) if to_account is None else to_account
    template = make_loan_payment_template(
        db.session, seed_user, loan, amount=series,
        derive_from_loan=derive,
        extra_principal="0.00" if extra is None else extra,
    )
    xfer = generate_transfer_of(
        template, current_pay_period(seed_user["user"].id, _FIRST_INSTALLMENT),
    )
    if owns:
        transfer_repriced_by_the_owner(xfer, stored)
    db.session.flush()
    return xfer


def _declare_loan_payment_derived(xfer):
    """Declare a loan payment's transfer derived.

    **It declares the TRANSFER alone since plan step balance:X-bi-6-4d-2**,
    where no shadow is born (ruling **R-BAL167** class 4); it looped the
    transfer's shadows first, and the paragraphs below are the history of
    that loop's scope.

    Separate from :func:`_generated_transfer` for two reasons.  One is ordering:
    a test builds the payment, watches the producer price it off the stored
    column, and only THEN declares it -- the transition the loan cutover (plan
    step X-au-g) performs.

    **It declares BOTH legs since plan step X-au-g-2c-2, and the bound that
    once stopped it is twice gone.**  It read: the loan-side income leg cannot
    be declared at all, because ``loan_payment_service.get_payment_history``
    prices every shadow income row on the loan account through
    ``row_valuation.settled_contribution``, which REFUSES a row whose plan is
    derived.  Plan step **X-au-g-2c-1** routed that reader through
    ``cash_ledger.contributions_by_key``, closing finding **N-266**(a) and making
    the scope a CHOICE; **X-au-g-2c-2** then made it not a choice either, by
    declaring every transfer shadow derived on live rows.  A fixture that
    declared one leg would now be building a pair the application cannot
    produce.

    **This docstring used to call the bound a CYCLE, and it was not one.**  It
    read: the loan resolves through ``load_loan_context`` ->
    ``get_payment_history``, so the rule that prices the row routes back to the
    row.  That path was deleted at plan step X-au-g-1 --
    ``_resolve_loan_basis`` read the loan's terms alone (and plan step
    recurrence:R25 deleted it for the pass's ``LoanCalendars``) -- leaving finding
    N-266(a)'s conclusion standing on ONE UNROUTED READER, which is what
    X-au-g-2c routed.  Three weeks between the diagnosis and its true cause,
    which is why a finding's claim is re-measured before its remedy is built.
    """
    return _declare_transfer_derived(xfer)
