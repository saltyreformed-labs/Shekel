"""The ledger's side of a match: a transaction, a purchase under one, and an asserted balance."""

from decimal import Decimal

from app import ref_cache
from app.enums import MovementFigureSourceEnum, SettledDayBasisEnum, StatusEnum, TxnTypeEnum
from app.extensions import db
from app.models.account import AccountAnchorHistory
from app.models.transaction_entry import TransactionEntry
from app.models.transaction_template import TransactionTemplate
from tests._test_helpers import (
    cover_bare_settled_row,
    figure_source_columns,
    generate_row_of,
    make_every_period_rule,
    one_off_row_of,
    settle_day_columns,
    state_template_price,
)


def a_transaction(
    seed_user,
    *,
    name="Electricity",
    amount="180.00",
    income=False,
    settled_on=None,
    status=StatusEnum.PROJECTED,
    is_envelope=False,
    period=None,
    category=None,
    template=True,
    reconciled_by=None,
    settle_day_basis=None,
    account=None,
):
    """Stage and return one transaction on the seeded checking account.

    Args:
        seed_user: The seeded user bundle.
        name: The row's name; also its template's, so each call is distinct.
        amount: Its estimated amount, as a string.
        income: Whether it is an income row (positive into the account).
        settled_on: Its recorded settle day, or ``None``.  A row carrying one
            also gets the SETTLEMENT RECORD a settle writes beside it: **the
            COVERING MOVEMENT** (plan step ``balance:X-bi-3a``; the record's
            one home since ``X-bi-4b-2`` deleted the row's own figure
            columns), written here through
            :func:`tests._test_helpers.cover_bare_settled_row`, the seam's own
            writer, as :func:`tests._test_helpers.add_txn` does, at the row's
            own estimate on the ``resolved`` source -- which is what
            ``transaction_service.settle_transaction`` resolves for a row that
            owns its amount.  Since ruling **R-BAL81** a settled row is worth what that
            movement moves, so a record laid bare WITHOUT it is a row the
            matcher prices at ``0`` and never offers -- a state no door
            writes, and one this package graded 27 cases against through
            ``X-bi-4a``'s first cut, when the matcher still priced the bare
            record itself (measured 2026-09-18: 45 failures in this package
            under R-BAL81 became 18 on this change alone).
        status: Its status.
        is_envelope: Whether it tracks purchases.
        period: The pay period to file it under; the bootstrap one by default.
        category: The category it files under; Groceries by default.
        template: Whether a recurring definition owns it.  ``True`` states
            the definition's price and cadence and lets the ENGINE write its
            row in *period* (plan step balance:X-cf), so the row is derived
            and *amount* is what its series answers; the status and settle
            columns below are then laid onto that row bare.  ``False`` builds
            an AD-HOC row (``template_id`` NULL), which is what
            ``_create._create_envelope`` produces and therefore the only shape
            a NEW-ENVELOPE merchant answer converges onto -- naming a template
            is a different answer with its own resolution.
        reconciled_by: The :func:`an_assertion` this row was TICKED against on
            the reconcile panel, or ``None`` for a row settled any other way.
            **It no longer decides what KIND of day *settled_on* is** (plan step
            **X-az**): that column answers WHICH statement was seen to show the
            money, and reading it as the day's provenance is finding **N-332**.
            Pass *settle_day_basis* to say what the day is.
        settle_day_basis: WHICH KIND of day *settled_on* is
            (:class:`~app.enums.SettledDayBasisEnum`), or ``None`` for
            ``entered`` -- the owner's own record, which is what a row settled
            through an edit box or a Mark Paid carries.  A fixture standing in
            for a reconcile-panel tick says ``ASSERTED``; one standing in for a
            bank match says ``OBSERVED``.
        account: The account to file it on; the seeded checking one by default.
            **A case needs this where the ACCOUNT is the variable** rather than
            the row -- an account whose books open INSIDE the pay calendar is
            a shape the seeded one cannot be restated into, so a destination on
            it has to be built here.  ``a_scope`` and ``an_import`` beside this
            already take the same parameter for the same reason.

    Returns:
        The staged :class:`~app.models.transaction.Transaction`.
    """
    type_id = ref_cache.txn_type_id(
        TxnTypeEnum.INCOME if income else TxnTypeEnum.EXPENSE,
    )
    account_id = (account or seed_user["account"]).id
    category_id = (category or seed_user["categories"]["Groceries"]).id
    # The settlement's ROW facts laid on bare (see the module docstring): the
    # day and its kind, and which statement showed it.  What moved is the
    # covering movement written after the flush below.
    settlement = dict(
        status_id=ref_cache.status_id(status),
        **settle_day_columns(settled_on, settle_day_basis),
        reconciled_by_id=reconciled_by.id if reconciled_by else None,
    )
    if template:
        definition = TransactionTemplate(
            user_id=seed_user["user"].id,
            account_id=account_id,
            category_id=category_id,
            transaction_type_id=type_id,
            name=name,
            is_envelope=is_envelope,
        )
        db.session.add(definition)
        db.session.flush()
        state_template_price(definition, Decimal(amount))
        # The definition first, then the cadence onto it (plan step R-F6).
        make_every_period_rule(db.session, definition)
        txn = generate_row_of(
            definition, period or seed_user["bootstrap_period"],
        )
        for column, value in settlement.items():
            setattr(txn, column, value)
    else:
        # A ONE-OFF through the producer (plan step balance:X-bi-7c): a
        # rule-less definition plus its placed row, priced at *amount* and
        # dated on the paycheck's start, with the settlement laid on bare as
        # for the engine's row above.  This built a link-less row owning its
        # figure until that step -- the shape the cutover (X-bi-7d) deletes.
        txn = one_off_row_of(
            period or seed_user["bootstrap_period"], name=name, amount=amount,
            user_id=seed_user["user"].id, account_id=account_id,
            scenario_id=seed_user["scenario"].id,
            transaction_type_id=type_id, category_id=category_id,
            is_envelope=is_envelope,
        )
        for column, value in settlement.items():
            setattr(txn, column, value)
    db.session.flush()
    if settled_on:
        cover_bare_settled_row(db.session, txn, amount)
    return txn


def a_purchase(
    seed_user, parent, *, amount="25.00", description="Kroger",
    purchased_on=None, settled_on=None, is_credit=False, reconciled_by=None,
    settle_day_basis=None,
):
    """Stage and return one purchase under *parent*.

    Args:
        seed_user: The seeded user bundle.
        parent: The envelope transaction it belongs to.
        amount: Its amount, as a string.
        description: What it is called.
        purchased_on: The day it was made.
        settled_on: The day the bank took it, or ``None``.
        is_credit: Whether it went on a card (and so never touches checking).
        settle_day_basis: WHICH KIND of day *settled_on* is
            (:class:`~app.enums.SettledDayBasisEnum`), or ``None`` for
            ``entered``.  See :func:`a_transaction`; a purchase ticked on the
            reconcile panel is ``ASSERTED`` and one the bank stated is
            ``OBSERVED``, and it is the basis rather than *reconciled_by* that
            decides the window (plan step **X-az**).
        reconciled_by: The :func:`an_assertion` this purchase was TICKED
            against on the reconcile panel, or ``None`` for one settled any
            other way.  **It no longer decides what KIND of day *settled_on*
            is** (plan step **X-az**), for the reason :func:`a_transaction`'s
            twin parameter states: that column answers WHICH statement was seen
            to show the money, and reading it as the day's provenance is finding
            **N-332**.  Pass *settle_day_basis*.

    Returns:
        The staged :class:`~app.models.transaction_entry.TransactionEntry`.
    """
    entry = TransactionEntry(
        # WHO WROTE the figure, for a bare builder that bypasses the doors: a
        # purchase this builder dates ``observed`` stands for one the matcher
        # wrote, so it carries the bank's figure, and every other one a
        # person's.  The DOORS no longer infer this from the day -- the writer
        # states it with the figure (plan step **X-bi-3e-1**, ruling
        # **R-BAL61**) -- so the pairing here is the builder's own modelling
        # choice, not a rule of the code under test.
        **figure_source_columns(
            MovementFigureSourceEnum.OBSERVED
            if settle_day_basis is SettledDayBasisEnum.OBSERVED else None
        ),
        transaction_id=parent.id,
        account_id=parent.account_id,
        owner_id=parent.user_id,
        user_id=seed_user["user"].id,
        amount=Decimal(amount),
        description=description,
        purchased_on=purchased_on or seed_user["bootstrap_period"].start_date,
        **settle_day_columns(settled_on, settle_day_basis),
        is_credit=is_credit,
        reconciled_by_id=reconciled_by.id if reconciled_by else None,
    )
    db.session.add(entry)
    db.session.flush()
    return entry


def an_assertion(
    seed_user, *, observed_on=None, balance="1000.00", account=None,
):
    """Stage and return one asserted balance, for a row to be RECONCILED to.

    **What the reconcile panel's tick points a row at**, and the thing that
    makes a settle day a BOUND rather than an observation
    (:attr:`~app.services.statement_match.CandidateRow.expected_window`).  A
    fixture cannot fake the link with a bare integer: ``fk_transaction_entries
    _reconciled_by`` and its transaction twin reference
    ``account_anchor_history (account_id, id)``, so the assertion has to exist.

    Args:
        seed_user: The seeded user bundle.
        observed_on: The civil day this balance is asserted FOR -- the day the
            panel stamps onto every row ticked against it.  The bootstrap
            period's start by default.
        balance: The asserted figure, as a string.  Nothing here reads it; it
            is stated because the column is NOT NULL.
        account: The account it is asserted for; the seeded checking one by
            default.

    Returns:
        The staged :class:`~app.models.account.AccountAnchorHistory`.
    """
    row = AccountAnchorHistory(
        account_id=(account or seed_user["account"]).id,
        anchor_balance=Decimal(balance),
        observed_on=observed_on or seed_user["bootstrap_period"].start_date,
    )
    db.session.add(row)
    db.session.flush()
    return row
