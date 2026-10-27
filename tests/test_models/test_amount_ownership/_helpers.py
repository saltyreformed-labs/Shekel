"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_models/test_amount_ownership.py``, moved verbatim by ``tools/split``.
"""

from decimal import Decimal

from sqlalchemy import insert, text

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.extensions import db
from app.models.ref import TransactionType
from app.models.transaction_template import TransactionTemplate
from app.models.amount_ownership import AmountOwnership
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from tests._test_helpers import (
    bare_expense_template,
    generate_row_of,
    generate_transfer_of,
    settle_day_columns,
)
from app.models.transfer_template import TransferTemplate


def _make_transaction(seed_user, seed_periods, **overrides):
    """Return a Projected row, with *overrides* applied.

    Two arms, decided by whether *overrides* names a definition:

    * **A row of a DEFINITION** (``template_id`` set) is the ENGINE's row of
      that template in the named paycheck (:func:`generate_row_of`, plan step
      balance:X-cf), flushed, with every OTHER override then laid onto it
      bare.  The row arrives derived, dated and answering an occurrence; a
      case that means the OWNER's re-priced row states ``amount_ownership``
      and ``is_override`` and gets exactly the two acts the re-price door
      performs.  ``pay_period_id`` picks the paycheck the engine writes into
      and is not restated afterwards.
    * **A BARE row** (no ``template_id``, or ``None``) is constructed by hand
      and returned UNFLUSHED, naming a rule-less definition of its OWN
      (below).

    Args:
        seed_user: The ``seed_user`` fixture payload.
        seed_periods: The ``seed_periods`` fixture list.  A ``pay_period_id``
            override must name one of them.
        **overrides: Column values to set or replace.  The amount-ownership
            pair is one of them -- ``amount_ownership`` -- and since plan step
            X-au-k it is the ONLY way an ORM caller can state it; the two
            unpaired shapes ``test_the_constraints.py`` grades are written by
            :func:`_insert_transaction_row` instead.

    Returns:
        The :class:`~app.models.transaction.Transaction`: flushed on the
        definition arm, unflushed on the bare one.

    **BARE on purpose; its pricing link is a rule-less definition of the
    owner's** (plan step ``balance:X-bi-7d-1``).  The subject here is a
    CONSTRAINT of ``budget.transactions``, and a control that reached the row
    through a door would grade the door -- so the row is still constructed by
    hand.  What it stopped being is LINK-LESS: the family's cutover
    (``X-bi-7d-2``) re-cuts ``ck_transactions_one_pricing_link`` to ``= 1``,
    so every row staged here names its own definition
    (:func:`~tests._test_helpers.bare_expense_template`) and carries its
    paycheck's start as the day it is due and the occurrence it answers --
    the shape ``one_off.place_row_of`` writes -- unless the case states
    otherwise.  The link is never the subject.
    """
    template_id = overrides.pop("template_id", None)
    if template_id is not None:
        return _row_of_definition(seed_periods, template_id, overrides)
    expense_type = (
        db.session.query(TransactionType).filter_by(name="Expense").one()
    )
    definition = bare_expense_template(
        db.session, seed_user, name="Ownership control definition",
    )
    period = _period_named(seed_periods, overrides)
    fields = {
        "user_id": seed_periods[0].user_id,
        "template_id": definition.id,
        "pay_period_id": period.id,
        "scenario_id": seed_user["scenario"].id,
        "account_id": seed_user["account"].id,
        "status_id": ref_cache.status_id(StatusEnum.PROJECTED),
        "name": "Ownership control",
        "category_id": seed_user["categories"]["Rent"].id,
        "transaction_type_id": expense_type.id,
        "amount_ownership": AmountOwnership.own(Decimal("300.00")),
        "due_date": period.start_date,
        "occurs_on": period.start_date,
    }
    fields.update(overrides)
    fields.update(_settle_day_pair(overrides))
    return Transaction(**fields)


def _period_named(seed_periods, overrides):
    """Return the paycheck *overrides* files the row in, else the first.

    The ONE lookup both arms of :func:`_make_transaction` make: the
    definition arm hands the paycheck to the engine, the bare arm takes its
    START as the row's day, and each reads it off the ``pay_period_id`` a
    case states before the row is built.  Leaves *overrides* untouched, so
    the bare arm can still lay the id onto its row.

    Args:
        seed_periods: The ``seed_periods`` fixture list.
        overrides: The caller's column values, which may name
            ``pay_period_id``.

    Returns:
        The :class:`~app.models.pay_period.PayPeriod` the row is filed in.
    """
    period_id = overrides.get("pay_period_id", seed_periods[0].id)
    return next(p for p in seed_periods if p.id == period_id)


def _row_of_definition(seed_periods, template_id, overrides):
    """The definition arm of :func:`_make_transaction`: the engine's row.

    Args:
        seed_periods: The ``seed_periods`` fixture list.
        template_id: The definition whose row is wanted.
        overrides: The caller's remaining column values, laid onto the
            generated row bare after ``pay_period_id`` has chosen the paycheck.

    Returns:
        The flushed :class:`~app.models.transaction.Transaction`.
    """
    period = _period_named(seed_periods, overrides)
    overrides.pop("pay_period_id", None)
    txn = generate_row_of(db.session.get(TransactionTemplate, template_id), period)
    for column, value in {**overrides, **_settle_day_pair(overrides)}.items():
        setattr(txn, column, value)
    return txn


def _settle_day_pair(overrides):
    """Complete the settle DAY with its basis unless the caller stated one.

    (Plan step **X-az**.)  These builders write bare columns on purpose -- a
    control routed through a door would grade the door -- but a row is only
    bare on the axis its test is ABOUT: a day with no basis violates
    ``ck_*_settle_day_basis_pairing`` before it can reach the constraint the
    test is grading, so the pair is completed here and a test that means to
    break it says ``settled_day_basis_id`` outright.

    Args:
        overrides: The caller's column values.

    Returns:
        The two settle-day columns to lay on, or nothing when the caller
        stated the basis.
    """
    if "settled_day_basis_id" in overrides:
        return {}
    return settle_day_columns(overrides.get("settled_on"))


def _insert_transaction_row(seed_user, seed_periods, *, figure, source_id,
                            **overrides):
    """INSERT a transaction row through Core, bypassing the ORM entirely.

    **This is how the unpaired shapes are written since plan step X-au-k, and
    the change makes the control stronger rather than weaker.**  Before it,
    ``estimated_amount`` and ``amount_source_id`` were two mapped columns and a
    test could hand the ORM either half; now they are one mapped attribute over
    :class:`~app.models.amount_ownership.AmountOwnership`, which refuses a
    figure beside a relation, so an ORM path cannot reach the state the CHECK
    is supposed to refuse.  Routing the control through the ORM would therefore
    have graded the new TYPE -- and the constraint's own job is the writer that
    is NOT this application: a migration, a ``psql`` session, a trigger.  Core
    is that writer.

    **It names a rule-less definition of the owner's and is dated on the
    paycheck's start**, for the reason :func:`_make_transaction`'s bare arm
    does (plan step ``balance:X-bi-7d-1``): the family's cutover makes a
    link-less row unstorable, and a row that violated TWO constraints would
    match one only by the order PostgreSQL reports them in -- which is not
    the control.  A constructor census could not see this writer (it is no
    ``Transaction(`` call), which is why it is stated here.

    Args:
        seed_user: The ``seed_user`` fixture payload.
        seed_periods: The ``seed_periods`` fixture list.
        figure: What to write to ``estimated_amount`` (may be ``None``).
        source_id: What to write to ``amount_source_id`` (may be ``None``).
        **overrides: Any other column values to set or replace.

    Returns:
        The Core ``insert()`` result.

    Raises:
        sqlalchemy.exc.IntegrityError: When the row breaks a constraint, which
            is what every caller here is asserting.
    """
    expense_type = (
        db.session.query(TransactionType).filter_by(name="Expense").one()
    )
    definition = bare_expense_template(
        db.session, seed_user, name="Ownership control definition",
    )
    values = {
        "user_id": seed_periods[0].user_id,
        "template_id": definition.id,
        "pay_period_id": seed_periods[0].id,
        "scenario_id": seed_user["scenario"].id,
        "account_id": seed_user["account"].id,
        "status_id": ref_cache.status_id(StatusEnum.PROJECTED),
        "name": "Ownership control",
        "category_id": seed_user["categories"]["Rent"].id,
        "transaction_type_id": expense_type.id,
        "estimated_amount": figure,
        "amount_source_id": source_id,
        "due_date": seed_periods[0].start_date,
        "occurs_on": seed_periods[0].start_date,
    }
    # Overrides FIRST: ``settle_day_columns`` reads ``settled_on`` to decide
    # the basis beside it, so applying them the other way round would pair a
    # caller's settle day with no basis and kill the row on
    # ``ck_transactions_settle_day_basis_pairing`` rather than on the
    # constraint under test -- a control that fires for the wrong reason.
    values.update(overrides)
    values.update(settle_day_columns(values.get("settled_on")))
    return db.session.execute(insert(Transaction).values(**values))


def _insert_transfer_row(data, *, figure, source_id, **overrides):
    """INSERT a transfer row through Core.  The twin of the function above.

    Args:
        data: The ``seed_full_user_data`` fixture payload.
        figure: What to write to ``amount`` (may be ``None``).
        source_id: What to write to ``amount_source_id`` (may be ``None``).
        **overrides: Any other column values to set or replace.

    Returns:
        The Core ``insert()`` result.

    Raises:
        sqlalchemy.exc.IntegrityError: When the row breaks a constraint.
    """
    values = {
        "user_id": data["user"].id,
        "from_account_id": data["account"].id,
        "to_account_id": data["savings_account"].id,
        "transfer_template_id": data["transfer_template"].id,
        "pay_period_id": data["periods"][0].id,
        "scenario_id": data["scenario"].id,
        "status_id": ref_cache.status_id(StatusEnum.PROJECTED),
        "name": "Ownership control",
        "amount": figure,
        "amount_source_id": source_id,
        # DATED, because the row names a definition and
        # ``ck_transfers_template_row_needs_due_date`` (plan step X-bv-2)
        # refuses a linked row without a date: a probe that violated TWO
        # constraints and matched one would be green only by the order
        # PostgreSQL reports them in, which is not the control.  Legal in
        # every respect but the pairing under test.
        "due_date": data["periods"][0].start_date,
    }
    values.update(overrides)
    return db.session.execute(insert(Transfer).values(**values))


def _make_transfer(data, **overrides):
    """Return a Projected transfer, with *overrides* applied.

    Two arms, decided by whether the transfer names a definition -- and it
    does by DEFAULT, the fixture's transfer template, because
    ``ck_transfers_adhoc_owns_amount`` refuses a declaration on a transfer no
    definition prices, so a test that declares a source has to be talking
    about a generated transfer to be talking about a legal row at all:

    * **A transfer of a DEFINITION** (``transfer_template_id`` left at its
      default or set) is the ENGINE's transfer of that template in the named
      paycheck (:func:`generate_transfer_of`, plan step balance:X-ch): flushed,
      with every OTHER override then laid onto it bare.  It arrives derived,
      dated and answering an occurrence, so an override restating that is a
      no-op and one stating an OWN figure, a status, a date or
      ``is_override`` is a pre-cutover shape laid onto the engine's transfer
      for a frozen migration's probe.  It has no shadow legs: none is stored
      since plan step balance:X-bi-6-4d-2, so a probe that reads them first
      runs ``82f7b7ed4332``'s downgrade, which builds them (:func:`_legs_of`).
      ``pay_period_id`` picks the paycheck and is not restated afterwards.
    * **An AD-HOC transfer** (``transfer_template_id=None``) is constructed
      bare and returned UNFLUSHED, as before, with no legs; the two ad-hoc
      controls in ``test_the_constraints.py`` pass it explicitly and say why.

    Args:
        data: The ``seed_full_user_data`` fixture payload (it carries the second
            account a transfer needs -- ``ck_transfers_different_accounts`` -- and
            the transfer template, which carries a cadence).
        **overrides: Column values to set or replace.

    Returns:
        The :class:`~app.models.transfer.Transfer`: flushed on the definition
        arm, unflushed on the ad-hoc one.
    """
    template_id = overrides.pop("transfer_template_id", data["transfer_template"].id)
    if template_id is not None:
        return _transfer_of_definition(data, template_id, overrides)
    fields = {
        "user_id": data["user"].id,
        "from_account_id": data["account"].id,
        "to_account_id": data["savings_account"].id,
        "transfer_template_id": None,
        "pay_period_id": data["periods"][0].id,
        "scenario_id": data["scenario"].id,
        "status_id": ref_cache.status_id(StatusEnum.PROJECTED),
        "name": "Ownership control",
        "amount_ownership": AmountOwnership.own(Decimal("100.00")),
    }
    fields.update(overrides)
    return Transfer(**fields)


def _transfer_of_definition(data, template_id, overrides):
    """The definition arm of :func:`_make_transfer`: the engine's transfer.

    Args:
        data: The ``seed_full_user_data`` fixture payload.
        template_id: The definition whose transfer is wanted.
        overrides: The caller's remaining column values, laid onto the
            generated transfer bare after ``pay_period_id`` has chosen the
            paycheck.

    Returns:
        The flushed :class:`~app.models.transfer.Transfer`.
    """
    period_id = overrides.pop("pay_period_id", data["periods"][0].id)
    period = next(p for p in data["periods"] if p.id == period_id)
    xfer = generate_transfer_of(db.session.get(TransferTemplate, template_id), period)
    for column, value in overrides.items():
        setattr(xfer, column, value)
    db.session.flush()
    return xfer


def _legs_of(xfer):
    """Return ``(expense leg, income leg)``: the two shadow rows of *xfer*.

    No transfer has one since plan step balance:X-bi-6-4d-2, so a caller
    first runs ``82f7b7ed4332``'s downgrade, which builds two for every
    transfer; on a transfer holding none this raises ``KeyError``.  Their ids
    are found by SQL: the model has had no ``transfer_id`` since
    ``c956c1d83108``, below which the caller runs.
    """
    twin_ids = db.session.execute(text(
        "SELECT id FROM budget.transactions WHERE transfer_id = :t"
    ), {"t": xfer.id}).scalars().all()
    legs = {
        leg.transaction_type_id: leg
        for leg in db.session.query(Transaction).filter(
            Transaction.id.in_(twin_ids),
        )
    }
    return (
        legs[ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)],
        legs[ref_cache.txn_type_id(TxnTypeEnum.INCOME)],
    )
