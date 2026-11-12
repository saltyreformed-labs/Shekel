"""Settling a row: the settle instant and day a fixture states, the figure's source, and the record
each side of a transfer holds.
"""

from datetime import datetime as _real_datetime


_UNSET_SETTLED_ON = object()


def settle_instant_on(day):
    """Return a deterministic event instant on a given civil date (noon UTC).

    A test-side helper for pinning an ASSERTION's recording instant to a
    specific day without a wall-clock read -- :func:`reassert_balance_on` takes
    one.  Noon UTC is the same civil day in the display zone (Eastern), so a
    day pinned this way reads back as that day.

    **It no longer serves a SETTLE, and that is plan step X-f1** (ruling R-EC).
    A settled transaction stores its civil day in ``transactions.settled_on``,
    so :func:`create_settled_transfer` and
    :func:`create_settled_cash_transaction` take the DAY directly and a caller
    that used to wrap it here passes it plain.  What remains is the assertion
    side, where ``created_at`` is genuinely an instant.

    Args:
        day: The civil :class:`datetime.date` to place the event on.

    Returns:
        A timezone-aware :class:`datetime.datetime` at noon UTC on *day*.
    """
    from datetime import time, timezone  # pylint: disable=import-outside-toplevel
    return _real_datetime.combine(day, time(12, 0), tzinfo=timezone.utc)


def default_settle_day(period, status_id):
    """Return the settle day a BARE-built fixture row takes for *status_id*.

    **One statement of the rule, for the two bare ``Transaction`` builders that
    need it** (:func:`add_txn` and ``test_calendar_service``'s module-local
    twin).  A row built in a settled status must carry a settle day -- a settled
    row without one is the state
    :func:`app.utils.balance_predicates.settled_day` refuses -- and the day is
    the pay period's ``start_date``, because that is precisely what every reader
    derived for such a row before plan step X-f1: these builders never set
    ``paid_at``, so ``to_display_civil_date(None, period.start_date)`` answered
    the period start.  Stamping ``display_today()`` instead would silently move
    that cash to today and re-date whatever figure the fixture was built to
    grade.

    Args:
        period: The :class:`~app.models.pay_period.PayPeriod` the row sits in.
        status_id: The ``ref.statuses.id`` the row is built in.

    Returns:
        ``period.start_date`` for a settled status, ``None`` for any other.
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep
    # avoidance as the loan helpers above.
    from app.utils.balance_predicates import settled_status_ids

    return period.start_date if status_id in settled_status_ids() else None


def typed(amount):
    """Return *amount* as a figure a PERSON stated.

    The :class:`~app.services.stated_figure.StatedFigure` every human door
    builds (plan step **X-bi-3e-1**, ruling **R-BAL69**), for a fixture that
    plays one: the add-purchase form, the entry PATCH, the popovers, the
    reconcile panel.  A test that means the BANK stated the figure says
    :func:`observed`; the two names keep the writer visible at every call
    site, which is the fact the step exists to record.

    Args:
        amount: The figure, a ``Decimal``.

    Returns:
        The ``typed`` :class:`~app.services.stated_figure.StatedFigure`.
    """
    from app.enums import MovementFigureSourceEnum
    from app.services.stated_figure import StatedFigure
    return StatedFigure(amount=amount, source=MovementFigureSourceEnum.TYPED)


def observed(amount):
    """Return *amount* as a figure the BANK's line stated.

    :func:`typed`'s twin for a fixture that plays the statement matcher, the
    one writer that states ``observed`` (plan step **X-bi-3e-1**, ruling
    **R-BAL61**).

    Args:
        amount: The figure, a ``Decimal``.

    Returns:
        The ``observed`` :class:`~app.services.stated_figure.StatedFigure`.
    """
    from app.enums import MovementFigureSourceEnum
    from app.services.stated_figure import StatedFigure
    return StatedFigure(
        amount=amount, source=MovementFigureSourceEnum.OBSERVED,
    )


def settlement_if_settling(txn, new_status_id, submitted=None):
    """Return the :class:`Settlement` a fixture owes the seam, or ``None``.

    A fixture that drives ``status_seam.apply_status_change`` directly is
    settling a row the long way round, and since plan step X-au-c3 a settle
    states WHAT MOVED as well as when.  This answers what such a fixture means
    -- and it answers ARM FOR ARM the way the real verbs do, because a fixture
    producing a record no door can write builds a row the app could never have
    created, and every test over that row grades the wrong branch in silence.

    The three arms, mirroring
    ``transaction_service._settle.settle_transaction`` and
    ``transfer_service._settle.settle``:

      * an ENVELOPE (``settles_from_entries``) records the ``purchases`` basis
        and stores NO figure -- its own entries state it.  Taking the
        ``derived`` arm here stored the PLAN instead, so
        ``row_valuation.settled_figure`` answered a stored column for every
        fixture-built settled envelope where the app answers from its children;
      * a RETAINED ``corrected`` record outlives the settle that made it and a
        re-settle honours it, so it is threaded into
        :meth:`~app.services.status_seam.Settlement.from_settle`;
      * the ECHO rule (finding **N-231**): a *submitted* figure equal to what
        the row would book anyway is not a correction, so it records
        ``derived`` rather than manufacturing a human's figure nobody typed.

    ``None`` for a move that does not ENTER the settled band, which the seam
    requires: a record offered beside a Projected / Credit / Cancelled status is
    refused, and leaving the band releases the ASSERTION while KEEPING the
    record.

    **What the settle BOOKS is the app's own published rule**
    (``transaction_service.settle_amount`` over a basis built the way the verb
    builds one), not the row's ``estimated_amount`` column.  It WAS that column
    until plan step balance:X-au-e, on the stated ground that "such rows own
    their plan, so the two agree" -- true while a generated row stored a figure
    and false the moment one stopped.  A generated row is DERIVED now and its
    column is NULL, so the old spelling handed the seam
    ``Settlement(amount=None, source=None)`` and every fixture that settles a
    generated row died on the record's own refusal: *a 'derived' settlement
    must state the figure that moved*.  Reading the rule instead is what makes
    this helper's promise -- that it answers ARM FOR ARM the way the real verbs
    do -- true again rather than true of the rows fixtures happened to build.

    Args:
        txn: The row being moved, at its PRE-move status.
        new_status_id: The status the fixture is moving it to.
        submitted: A figure a human typed, when the fixture is exercising a
            correction.

    Returns:
        The ``Settlement`` to hand the seam, or ``None``.
    """
    # pylint: disable=import-outside-toplevel  -- the lazy-app-import
    # convention every helper in this module follows.
    from app.services.status_seam import Settlement, recorded_settlement
    from app.services.cash_ledger import derived_amount_basis
    from app.services.transaction_service import (
        settle_amount,
        settles_from_entries,
    )
    from app.utils.balance_predicates import enters_settled_band

    if not enters_settled_band(txn, new_status_id):
        return None
    if settles_from_entries(txn):
        return Settlement(amount=None, source=None)
    # ONE basis for the whole act, exactly as ``settle_transaction`` builds it,
    # and ``settle_amount``'s second arm IS the retained correction -- so this
    # asks the same rule once rather than re-branching on
    # ``honoured_correction`` beside it.
    booked = settle_amount(
        txn, derived_amount_basis(txn.account.user_id, txn.scenario_id),
    )
    # A fixture's figure is a PERSON's, stated as such (plan step X-bi-3e-1,
    # ruling R-BAL69): the verbs take the figure and its writer as one value.
    correction = (
        typed(submitted) if submitted is not None and submitted != booked
        else None
    )
    return Settlement.from_settle(booked, correction, recorded_settlement(txn))


def settled_day_basis_id(basis):
    """Return one ``ref.settled_day_bases`` id, for a fixture that states it.

    The narrow companion of :func:`settle_day_columns` (plan step **X-az**),
    for a fixture writing the pair through raw SQL -- a simulated concurrent
    ``UPDATE`` -- where there is no model instance for the pair writer to
    take.

    Resolved through ``ref_cache`` like every other ref value, so a fixture
    names the BASIS and never an id.

    Args:
        basis: The :class:`app.enums.SettledDayBasisEnum` member to resolve.

    Returns:
        The ``ref.settled_day_bases.id``.
    """
    # pylint: disable=import-outside-toplevel  -- the lazy-app-import
    # convention every helper in this module follows.
    from app import ref_cache

    return ref_cache.settled_day_basis_id(basis)


def an_entered_day(day):
    """Return *day* as a settle day the OWNER stated -- the ``entered`` basis.

    The three ``*_day`` builders below are what a test hands a settle door now
    that the door takes the PAIR rather than a bare ``date`` (plan step
    **X-az**): the day, and how that day is known.  They exist so a call site
    reads as the fact it is asserting instead of as a two-line construction, and
    so the BASIS is visible at the site -- which is the whole subject of the
    step, and would be invisible if a helper defaulted it.

    ``entered`` is what a manual Mark Paid on an ordinary row, a full-edit date
    box and every fixture standing in for one records: the owner's own day,
    with no bank document behind it.  (A Paid press on a TRANSFER records
    ``borrowed`` on both sides since plan step ``balance:X-bi-6-4c-3``: it
    states no day, see :func:`create_settled_transfer`.)

    Args:
        day: The civil ``date``.

    Returns:
        Its :class:`app.services.settle_day.SettleDay` on the ``entered`` basis.
    """
    # pylint: disable=import-outside-toplevel  -- same lazy-app-import
    # convention every helper in this module follows.
    from app.enums import SettledDayBasisEnum
    from app.services.settle_day import SettleDay

    return SettleDay(day=day, basis=SettledDayBasisEnum.ENTERED)


def on_both_sides(from_account_id, to_account_id, settle_day):
    """Return ``side_days`` stating ONE day for BOTH sides of a transfer.

    What the one-box transfer popover meant before plan step
    ``balance:X-bi-6-4c-3``: one day, stated for the whole transfer, on one
    basis (ruling **R-BAL165** reads a pre-step typed day as "typed on both
    ends").  A test written against that door re-expresses its pair day
    through this, and so leaves every stored row exactly as the old door wrote
    it; a test whose subject is ONE side's day states a single
    :class:`~app.services.transfer_service.SideDay` instead, and the other side
    borrows it.

    Args:
        from_account_id: The account the money leaves (the expense side).
        to_account_id: The account it arrives at (the income side).
        settle_day: The :class:`~app.services.settle_day.SettleDay` both sides
            state -- :func:`an_entered_day` and its two siblings build one.

    Returns:
        A two-item tuple for a transfer verb's ``side_days``.
    """
    # pylint: disable=import-outside-toplevel  -- same lazy-app-import
    # convention every helper in this module follows.
    from app.services.transfer_service import SideDay

    return (
        SideDay(from_account_id, settle_day), SideDay(to_account_id, settle_day),
    )


def an_asserted_day(day):
    """Return *day* as the day a BALANCE was asserted for -- an upper BOUND.

    What the reconcile panel records: the owner asserted a balance for this day
    and this money was inside it, so the true posting day is on or BEFORE it.
    :func:`an_entered_day` carries why these builders exist.

    Args:
        day: The civil ``date`` the balance was asserted for.

    Returns:
        Its :class:`app.services.settle_day.SettleDay` on the ``asserted``
        basis.
    """
    # pylint: disable=import-outside-toplevel  -- same lazy-app-import
    # convention every helper in this module follows.
    from app.enums import SettledDayBasisEnum
    from app.services.settle_day import SettleDay

    return SettleDay(day=day, basis=SettledDayBasisEnum.ASSERTED)


def an_observed_day(day):
    """Return *day* as a day a BANK STATEMENT showed the money post.

    What the statement matcher records.  :func:`an_entered_day` carries why
    these builders exist.

    Args:
        day: The civil ``date`` the bank posted the movement.

    Returns:
        Its :class:`app.services.settle_day.SettleDay` on the ``observed``
        basis.
    """
    # pylint: disable=import-outside-toplevel  -- same lazy-app-import
    # convention every helper in this module follows.
    from app.enums import SettledDayBasisEnum
    from app.services.settle_day import SettleDay

    return SettleDay(day=day, basis=SettledDayBasisEnum.OBSERVED)


def settle_day_columns(settled_on, basis=None):
    """Return the settle-day COLUMN PAIR a bare-built fixture row owes.

    Plan step **X-az**: ``settled_on`` and ``settled_day_basis_id`` are one
    fact in two columns, welded by each table's
    ``ck_*_settle_day_basis_pairing`` BICONDITIONAL, so a bare
    ``Transaction(settled_on=...)`` that names only the day is an
    ``IntegrityError`` at flush rather than a row.  (Its twin for the RECORD,
    ``settlement_columns``, laid the row's own ``settled_amount`` /
    ``settled_basis_id`` until plan step ``balance:X-bi-4b-2`` deleted those
    columns; the record is the covering movement
    :func:`cover_bare_settled_row` writes.)

    Every bare builder goes through this rather than spelling the pair, for the
    reason the app has ONE writer for it
    (:func:`app.services.settle_day.record_settle_day`): a fixture that states
    half of a pair builds a row no door could have written, and every test over
    that row grades a state the app cannot reach.

    **The default basis is ``entered``**, which is what a bare-built settled row
    MEANS: nobody imported a statement and nobody reconciled a balance to make
    it, so the day is the fixture's own assertion -- exactly what a manual Mark
    Paid records.  A suite grading the matcher's window rule passes
    ``SettledDayBasisEnum.ASSERTED`` or ``.OBSERVED`` explicitly, because there
    the KIND of day is the subject.

    Args:
        settled_on: The civil day, or ``None`` for a row that carries none.
        basis: The :class:`~app.enums.SettledDayBasisEnum` member, or ``None``
            for the ``entered`` default.  Ignored when *settled_on* is ``None``,
            where the pair is both-NULL.

    Returns:
        ``{"settled_on": ..., "settled_day_basis_id": ...}``, ready to splat
        into a model constructor.
    """
    # pylint: disable=import-outside-toplevel  -- same lazy-app-import
    # convention every helper in this module follows.
    from app import ref_cache
    from app.enums import SettledDayBasisEnum

    if settled_on is None:
        return {"settled_on": None, "settled_day_basis_id": None}
    member = SettledDayBasisEnum.ENTERED if basis is None else basis
    return {
        "settled_on": settled_on,
        "settled_day_basis_id": ref_cache.settled_day_basis_id(member),
    }


def figure_source_columns(member=None):
    """Return the FIGURE-SOURCE column a bare-built purchase owes.

    The figure's twin of :func:`settle_day_columns` (plan step **X-bi-3a**,
    ruling **R-BAL39**): ``transaction_entries.figure_source_id`` is NOT NULL
    with no default, because both of the app's writers of a movement state who
    wrote its figure and a stored guess is the shape ruling R-IY deletes.  A
    bare ``TransactionEntry(...)`` that says nothing is therefore an
    ``IntegrityError`` at flush rather than a row, and every bare builder goes
    through this rather than spelling the id.

    **The default is ``typed``**, which is what a bare-built purchase MEANS:
    nobody imported a statement line and no settle resolved it, so a person
    stated the figure -- exactly what the entry form records.  A suite grading
    the source itself passes ``MovementFigureSourceEnum.OBSERVED`` or
    ``.RESOLVED`` explicitly, because there the source is the subject.

    Args:
        member: The :class:`~app.enums.MovementFigureSourceEnum` member, or
            ``None`` for the ``typed`` default.

    Returns:
        ``{"figure_source_id": ...}``, ready to splat into the constructor.
    """
    # pylint: disable=import-outside-toplevel  -- same lazy-app-import
    # convention every helper in this module follows.
    from app import ref_cache
    from app.enums import MovementFigureSourceEnum

    chosen = MovementFigureSourceEnum.TYPED if member is None else member
    return {"figure_source_id": ref_cache.movement_figure_source_id(chosen)}


def transfer_side_record(db_session, transfer_id, account_id):
    """Return the record of a transfer's side on *account_id*, or ``None``.

    The movement filed under that side (plan step ``balance:X-bi-6-4d-2``,
    ruling **R-BAL88**): it hangs off the TRANSFER by ``expense_transfer_id``
    / ``income_transfer_id``, on its endpoint, at most one per side.  It hung
    off the side's shadow row until then (``shadow.covering_movements``).
    The row-shaped twin of :func:`transfer_side_journal_filter`, and
    independent SQL over the side links for that helper's reason (ruling
    **R-BAL167** class 4): a test reading a side's record through
    ``transfer_legs``, the producer it grades, could not catch its fault.

    Args:
        db_session: The test ``db.session``.
        transfer_id: The ``budget.transfers`` id.
        account_id: The account the side is on.

    Returns:
        The side's :class:`~app.models.transaction_entry.TransactionEntry`,
        or ``None`` when the side holds none (an unsettled side never paid,
        or a ``$0.00`` close, ruling **R-BAL141**).
    """
    # pylint: disable=import-outside-toplevel -- same lazy-app-import
    # convention every helper in this module follows.
    from app.extensions import db
    from app.models.transaction_entry import TransactionEntry

    return (
        db_session.query(TransactionEntry)
        .filter(
            db.or_(
                TransactionEntry.expense_transfer_id == transfer_id,
                TransactionEntry.income_transfer_id == transfer_id,
            ),
            TransactionEntry.account_id == account_id,
        )
        .one_or_none()
    )


def side_records_by_key(xfer):
    """Return ``{(transfer id, account id): record}`` for each side of *xfer* holding one.

    The map ``transfer_legs.covering_movements_by_leg`` answered until ruling
    **plan#1984** deleted it, built from what replaced it: each side's leg
    reads its record off the transfer's list for that side
    (``TransferLeg.record``), both lists loaded in one statement
    (``transfer_legs.transfer_sides``).  The cases that asserted that
    map assert it unchanged through this, keyed by the side's ``cell_key``,
    which is the record's account (a record's account IS its side's
    endpoint, by the side keys).

    Args:
        xfer: The transfer, loaded.

    Returns:
        The map, holding a key only for a side that holds a record.
    """
    # pylint: disable=import-outside-toplevel -- same lazy-app-import
    # convention every helper in this module follows.
    from app.services import transfer_legs

    return {
        leg.cell_key: leg.record
        for leg in transfer_legs.transfer_sides(xfer)
        if leg.record is not None
    }


def transfer_side_figure(db_session, transfer_id, account_id):
    """Return what a transfer's side on *account_id* RECORDS, or ``None``.

    The side-shaped twin of ``row_valuation.settled_figure(shadow)``, which a
    test asked of the side's shadow until plan step ``balance:X-bi-6-4d-2``
    stopped keeping a shadow's status (ruling **R-BAL167** class 4): ``None``
    while the TRANSFER is not settled, and once it is, the figure the
    TRANSFER stores -- its one home since that step's 4d-4 half (rulings
    **R-BAL365**, **R-BAL366**), ``0.00`` for a ``$0.00`` close.  Until then
    it read the side's record's ``amount`` and inferred ``0.00`` from a side
    holding none, the inference ruling **R-BAL368** deletes.  The answers the
    shadow gave while its status mirrored its transfer's, so a re-pointed
    assertion keeps its expected figure.  Read off the stored column, never
    through the producers a test grades.

    Args:
        db_session: The test ``db.session``.
        transfer_id: The ``budget.transfers`` id.
        account_id: The account the side is on: one of the transfer's two
            endpoints (a transfer's figure is one, so the side names which
            account the caller is asserting about, and a wrong one fails).

    Returns:
        The stored ``Decimal``, or ``None`` when the transfer is not settled.
    """
    # pylint: disable=import-outside-toplevel -- same lazy-app-import
    # convention every helper in this module follows.
    from app.models.transfer import Transfer

    transfer = db_session.get(Transfer, transfer_id)
    assert account_id in (transfer.from_account_id, transfer.to_account_id), (
        f"account {account_id} is neither side of transfer {transfer_id}"
    )
    if not transfer.status.is_settled:
        return None
    return transfer.settled_figure


def transfer_side_settle_day(db_session, transfer_id, account_id):
    """Return the settle day a transfer's side on *account_id* records, or ``None``.

    The side-shaped twin of ``settle_day.recorded_settle_day(shadow)`` (ruling
    **R-BAL167** class 4, plan step ``balance:X-bi-6-4d-2``): the
    :class:`~app.services.settle_day.SettleDay` on the side's record, or
    ``None`` when the side holds no record or its record carries no day (a
    side never paid, a revert's kept record, a ``$0.00`` close).

    **Decoded by its own query, never the app's decoder** (the X-bi-6-4d-2
    leaf review's L-7): it read the record through
    ``settle_day.recorded_settle_day``, the column decoder the writer's own
    day resolution uses, so a decoder that mis-mapped a basis would have
    agreed with itself here.  It reads the day and the basis's ``ref`` NAME
    straight off the tables, and :class:`~app.enums.SettledDayBasisEnum`'s
    values ARE those names, as :func:`transfer_side_record` and
    :func:`transfer_side_figure` are already independent SQL.

    Args:
        db_session: The test ``db.session``.
        transfer_id: The ``budget.transfers`` id.
        account_id: The account the side is on.

    Returns:
        The side's ``SettleDay``, or ``None``.
    """
    # pylint: disable=import-outside-toplevel -- same lazy-app-import
    # convention every helper in this module follows.
    from app.enums import SettledDayBasisEnum
    from app.extensions import db
    from app.models.ref import SettledDayBasis
    from app.models.transaction_entry import TransactionEntry
    from app.services.settle_day import SettleDay

    stored = (
        db_session.query(TransactionEntry.settled_on, SettledDayBasis.name)
        .outerjoin(
            SettledDayBasis,
            SettledDayBasis.id == TransactionEntry.settled_day_basis_id,
        )
        .filter(
            db.or_(
                TransactionEntry.expense_transfer_id == transfer_id,
                TransactionEntry.income_transfer_id == transfer_id,
            ),
            TransactionEntry.account_id == account_id,
        )
        .one_or_none()
    )
    if stored is None or stored.settled_on is None:
        return None
    return SettleDay(
        day=stored.settled_on, basis=SettledDayBasisEnum(stored.name),
    )


def purchases_of(txn):
    """Return *txn*'s entries that are PURCHASES: its rows less the seam's mirror.

    Plan step **X-bi-3a**: a settled bill or an envelope closed empty holds
    one covering movement, written by the status seam and never by a person,
    so "this row took no purchase" is graded over the entries that are not
    that mirror -- the app's own reading, ``Transaction.purchases`` (plan
    step **X-bi-3e-2**, ruling **R-BAL68**), which this helper hand-rolled
    until that reading existed.  A refused purchase still leaves the row with
    exactly the movement it had.

    Args:
        txn: The :class:`~app.models.transaction.Transaction`, or its id.

    Returns:
        The purchases, in ``entries`` order.
    """
    # pylint: disable=import-outside-toplevel  -- same lazy-app-import
    # convention every helper in this module follows.
    from app.extensions import db
    from app.models.transaction import Transaction

    row = txn if isinstance(txn, Transaction) else db.session.get(Transaction, txn)
    return row.purchases
