"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_services/test_income_service.py``, moved verbatim by ``tools/split``.
"""

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.ref import FilingStatus, RaiseType, Status, TransactionType
from app.models.salary_profile import SalaryProfile
from app.models.salary_raise import SalaryRaise
from app.models.transaction_template import TransactionTemplate
from app.services.pay_calendar import calendar_for
from app.services import income_service
from tests._test_helpers import (
    generate_row_of,
    make_every_period_rule,
    one_off_row_of,
    repriced_by_the_owner,
    start_test_pay_list,
)


# Hand-computed expected values (derived in the package's docstring, ``__init__.py``).
_RAISE_APPLIED_GROSS = Decimal("4120.00")  # the 4,000.00 entry x 1.03


_NO_RAISE_GROSS = Decimal("4000.00")  # the entry: $104,000 a year / 26


#: Inside seed_periods period 6, 03-27, the first paycheck whose pay period
#: (03-08..03-21) opens after the March 1 raise: period 5, 03-13, pays
#: 02-22..03-07 and so half of it (ruling plan#3089, task plan#3100).
_AS_OF_AFTER_RAISE = date(2026, 3, 29)


_AS_OF_BEFORE_RAISE = date(2026, 1, 5)  # inside seed_periods period 0


def _create_profile(
    user_id: int, scenario_id: int, *,
    pay: str = "4000.00",  # $104,000.00 a year / 26
) -> SalaryProfile:
    """Create an active SalaryProfile for the user.

    Helper isolates the FilingStatus lookup + required-column boilerplate
    so each test reads as fixture composition rather than ORM ceremony.
    """
    filing = db.session.query(FilingStatus).first()
    profile = SalaryProfile(
        period_end_days_before_payday=6,
        user_id=user_id,
        scenario_id=scenario_id,
        filing_status_id=filing.id,
        name="Test Salary",
        state_code="NC",
        is_active=True,
    )
    db.session.add(profile)
    start_test_pay_list(profile, Decimal(pay))
    db.session.flush()
    return profile


def _add_one_time_raise(
    profile: SalaryProfile, *, percentage: str = "0.0300",
    effective_month: int = 3, effective_year: int = 2026,
) -> SalaryRaise:
    """Attach a one-time percentage raise to the profile."""
    merit = db.session.query(RaiseType).filter_by(name="merit").one()
    salary_raise = SalaryRaise(
        salary_profile_id=profile.id,
        raise_type_id=merit.id,
        effective_date=date(effective_year, effective_month, 1),
        percentage=Decimal(percentage),
        is_recurring=False,
    )
    db.session.add(salary_raise)
    db.session.flush()
    return salary_raise


def _make_salary_template(seed_user, profile, *, name="Paycheck", account=None):
    """Create an Income template and link ``profile`` to it.

    The producer treats a transaction as salary-linked iff its
    ``template_id`` maps to an active SalaryProfile for the scenario, so
    the test must set ``profile.template_id`` to the created template.

    **It carries a cadence and states NO price**, and the second is not a
    choice this fixture gets to make.  The cadence is what lets the engine
    write the definition's rows (:func:`generate_row_of`, plan step
    balance:X-cf).  A salary-linked definition's amount is DERIVED rather
    than stated (``template_amount_service.owns_its_amount`` is False once
    the profile names it), so ``set_amount`` on it would set the scalar and
    open no series, and amount rule 3 refuses such a row at that arm before
    any series is read (``_stated_amount``) -- which is what keeps a
    dispatch that fell through to rule 3 from answering the scalar's
    ``$4,000.00``, the profile's own no-raise net.  Measured under review
    2026-09-11: ``_rule_within_definition`` mutated to answer TEMPLATE fails
    here with that refusal, not with an empty-series one.

    Args:
        seed_user: The seeded owner bundle.
        profile: The :class:`~app.models.salary_profile.SalaryProfile` that
            prices this definition's rows; its ``template_id`` is set here.
        name: The definition's name.
        account: The account the paycheck lands on; the seed user's checking
            account when omitted.  The engine puts a row on its definition's
            account, so a case wanting the paycheck on an HYSA says so here.
    """
    income_type = (
        db.session.query(TransactionType).filter_by(name="Income").one()
    )
    category = next(iter(seed_user["categories"].values()))
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=(seed_user["account"] if account is None else account).id,
        category_id=category.id,
        transaction_type_id=income_type.id,
        name=name,
    )
    db.session.add(template)
    db.session.flush()
    # The definition first, then the cadence onto it (plan step R-F6).
    make_every_period_rule(db.session, template)
    profile.template_id = template.id
    db.session.flush()
    return template


def _make_txn(
    seed_user, period, *, template=None, type_name="Income",
    status_name="Projected", owned_amount=None,
):
    """Create a single Transaction in ``period`` for the producer tests.

    Two arms, decided by whether a definition is named (plan step
    balance:X-cf):

    * **A row of a DEFINITION** (*template* given) is the ENGINE's row of it
      in *period* (:func:`generate_row_of`): derived, dated, answering an
      occurrence, Projected.  *type_name* is not read -- the row's type is
      its definition's.  *status_name* other than Projected is then laid on
      bare, and *owned_amount* makes it the OWNER's re-priced row through the
      re-price door's two acts (``state_own_amount`` and ``is_override``).
    * **A ONE-OFF** (no *template*) is placed through the producer
      (:func:`one_off_row_of`, plan step balance:X-bi-7c): a rule-less
      definition of its own priced at *owned_amount*, which it must state,
      and the row derived from it -- the shape the grid writes for a one-off.

    ``derived`` was a switch here until X-cf-3: with the engine writing the
    row there is no other shape a non-overridden salary row can have, so the
    flag named the only state and went.

    Args:
        seed_user: The seeded owner bundle.
        period: The pay period the row is funded in.
        template: The definition whose row is wanted, or ``None``.
        type_name: The one-off's transaction type.
        status_name: The status to give the row.
        owned_amount: The figure, as a string.  On a definition's row it is
            what a human re-priced it to (the row then OWNS it); on a one-off
            it is what its own definition is priced at, and is required.

    Returns:
        The flushed :class:`~app.models.transaction.Transaction`.

    Raises:
        ValueError: A one-off with no *owned_amount*: its definition is
            priced at something, and this helper will not guess what.
    """
    status = db.session.query(Status).filter_by(name=status_name).one()
    if template is not None:
        txn = generate_row_of(template, period)
        if owned_amount is not None:
            repriced_by_the_owner(txn, owned_amount)
        txn.status_id = status.id
        db.session.flush()
        return txn
    if owned_amount is None:
        raise ValueError("a one-off's definition is priced; pass owned_amount")
    txn_type = (
        db.session.query(TransactionType).filter_by(name=type_name).one()
    )
    category = next(iter(seed_user["categories"].values()))
    txn = one_off_row_of(
        period,
        name="producer-test txn",
        amount=Decimal(owned_amount),
        user_id=period.user_id,
        account_id=seed_user["account"].id,
        scenario_id=seed_user["scenario"].id,
        transaction_type_id=txn_type.id,
        category_id=category.id,
    )
    txn.status_id = status.id
    db.session.flush()
    return txn


def _net_map(user_id, scenario_id, rows):
    """What the salary derivation answers for each of *rows*, where it answers.

    Plan step X-au-c2b split the producer into an owner-scoped DERIVATION
    (:func:`income_service.salary_pricing`) and a per-row lookup
    (:func:`income_service.salary_net_for`), so the map these tests grade is
    something a caller builds rather than something the producer returns.

    **It was ``_live_net_map`` and asked the READ-TIME REPAIR until plan step
    X-au-d**, which deleted that repair: a salary row is DECLARED derived and
    stores no figure, so there is nothing for a repair to supersede and
    ``salary_net_for`` is the one producer.  The difference is not cosmetic --
    the repair filtered to Projected non-overridden rows and the PRICING rule
    filters on neither (finding **N-262**), which is what
    ``test_the_salary_net.TestSalaryNetFor`` now grades.
    """
    pricing = income_service.salary_pricing(
        scenario_id, income_service.derived_paycheck_pricing(user_id),
    )
    answers = {}
    for txn in rows:
        net = income_service.salary_net_for(txn, pricing)
        if net is not None:
            answers[txn.id] = net
    return answers


def _derived(user_id):
    """The owner's saved schedule AS THE PAYCHECK ENGINE takes it.

    That engine moved onto :class:`~app.services.pay_calendar.DerivedPeriod`
    at pay-calendar plan step C2-f2d-3, and ``income_service`` derives this
    same window internally -- so the oracles that read it are handed the shape the
    producer under test uses, while the ORM rows beside them stay for the
    fixtures that WRITE a ``pay_period_id``.
    """
    return calendar_for(user_id).saved()
