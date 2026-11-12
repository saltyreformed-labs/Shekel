"""Rows the engine writes: a projected transaction, the one row a template or transfer definition
generates in a period, a one-off's row, and a bare settled row's covering movement.
"""

from decimal import Decimal

from ._settle_days_and_sides import _UNSET_SETTLED_ON, default_settle_day, settle_day_columns


def add_txn(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    db_session, seed_user, period, name, amount,
    status_enum=None, is_income=False,
    due_date=None, category_key=None, is_deleted=False,
    settled_amount=None, account=None, scenario=None,
    settled_on=_UNSET_SETTLED_ON,
):
    """Create a projected (default) transaction on the seed user's account.

    The shared bare-Transaction builder for the dashboard suites (the
    route, shared-helper, and pulse-producer tests all built an identical
    ``_add_txn`` -- a duplicate-code finding).  Builds an income or expense
    row with optional actual amount, due date, category, soft-delete flag,
    and status.  Flushes; the caller commits.

    **A row built in a SETTLED status gets a settle day here, and the default
    is the pay period's ``start_date``** (plan step X-f1).  It is deliberately
    the period start and not today: this builder never set ``paid_at``, so
    every settled row it produced used to reach the readers as
    ``to_display_civil_date(None, period.start_date)`` -- the period start --
    which is the day every hand-computed figure in the suites that consume it
    was written against.  It is also the day migration ``a3f7c8e21b64``'s
    backfill gave the real rows in the same shape.  Stamping ``display_today()``
    instead would silently move that cash to today and re-date whatever
    balance, period subtotal or ledger entry the fixture was built to grade.

    It stays a BARE builder -- it does not route through
    ``status_seam.apply_status_change`` -- because that is its purpose: several
    suites need "a settled row the posting writer has never seen", and the seam
    would additionally refuse ``Projected -> Settled``, which is not a legal
    transaction transition but is a legitimate STARTING state for a fixture.
    What it may not produce is an INCOHERENT row: a settled row with no settle
    day is the state ``balance_predicates.settled_day`` refuses, and every
    reader of it would raise -- **and since plan step ``balance:X-bi-4a`` a
    settled row with no COVERING MOVEMENT is one too** (ruling **R-BAL80**):
    the fold and the posting writer read a settled row's money as its
    movements alone, so a bare row carrying only the record columns would be
    money no reader can see, the pre-``X-bi-3d`` shape the cutover migration
    exists to end.  So a settled row built here also carries the covering
    movement the seam would have written for it, through the seam's own
    writer (``sync_covering_movement``, the ONE producer of the mirror) and
    not a fixture's restatement; the writer has still never seen it, which is
    what the suites that need that state rely on.

    Args:
        db_session: The test ``db.session``.
        seed_user: The ``seed_user`` fixture dict (supplies the account,
            scenario, and categories).
        period: The :class:`~app.models.pay_period.PayPeriod` to place the
            transaction in.
        name: The transaction name.
        amount: The estimated amount (str or Decimal-coercible).
        status_enum: The :class:`~app.enums.StatusEnum` member; defaults to
            ``StatusEnum.PROJECTED`` (resolved lazily to avoid importing
            the enum at module-load time).
        is_income: When True, an income row; otherwise an expense row.
        due_date: The transaction's due date, or ``None``.
        category_key: A key into ``seed_user["categories"]`` to set the
            category, or ``None`` for no category.
        is_deleted: The soft-delete flag.
        actual_amount: The actual (tier-3) amount, or ``None``.
        account: The :class:`~app.models.account.Account` the row lands on;
            defaults to ``seed_user["account"]`` (the Checking account).
        scenario: The :class:`~app.models.scenario.Scenario` the row lives in;
            defaults to the seed user's baseline.  Pass a non-baseline scenario
            to exercise multi-scenario isolation.
        settled_on: The civil day the row's money moved.  Omit it for the
            default described above (``period.start_date`` for a settled
            status, ``None`` for any other).  Pass a day to place the cash
            somewhere specific -- the shape a fixture needs when it grades WHICH
            day a balance steps on.  Passing ``None`` explicitly builds the
            broken settled-with-no-day row on purpose, which is what a negative
            control for :func:`app.utils.balance_predicates.settled_day` needs.

    Returns:
        The created :class:`~app.models.transaction.Transaction` (flushed).
    """
    # pylint: disable=import-outside-toplevel  -- same circular-dep
    # avoidance as the loan helpers above.
    from app import ref_cache
    from app.enums import StatusEnum, TxnTypeEnum
    if status_enum is None:
        status_enum = StatusEnum.PROJECTED
    account = seed_user["account"] if account is None else account
    scenario = seed_user["scenario"] if scenario is None else scenario
    status_id = ref_cache.status_id(status_enum)
    if settled_on is _UNSET_SETTLED_ON:
        settled_on = default_settle_day(period, status_id)
    type_id = (
        ref_cache.txn_type_id(TxnTypeEnum.INCOME)
        if is_income
        else ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    )
    cat_id = None
    if category_key and category_key in seed_user["categories"]:
        cat_id = seed_user["categories"][category_key].id

    # The row is the PRODUCER's (plan step balance:X-bi-7c): a rule-less
    # definition priced at *amount* plus its placed row, dated on the
    # paycheck's start unless *due_date* says otherwise, exactly what the grid
    # writes for a one-off -- where this built a link-less row owning its
    # figure, the shape the cutover (X-bi-7d-2) deleted.  What stays BARE is the
    # state laid on top: the status, the soft-delete flag and the settle
    # columns, which is this builder's purpose (see above).
    txn = one_off_row_of(
        period, name=name, amount=amount,
        user_id=period.user_id, account_id=account.id,
        scenario_id=scenario.id, transaction_type_id=type_id,
        category_id=cat_id, due_date=due_date,
    )
    bare_state = dict(
        status_id=status_id,
        is_deleted=is_deleted,
        # The settle DAY and the basis that says how it is known, as one pair
        # (plan step **X-az**).  ``entered`` is what a bare-built settled row
        # means: no statement was imported and no balance reconciled to make it.
        **settle_day_columns(settled_on),
    )
    for column, value in bare_state.items():
        setattr(txn, column, value)
    db_session.flush()
    # The settlement RECORD (plan step X-au-c3; its one home the covering
    # movement since ``balance:X-bi-4b-2``).  A settled row states what moved
    # -- the typed figure when the caller gave one, else the row's own -- and
    # a row built WITHOUT a settle day records nothing, as the seam writes
    # nothing for a row outside the band.  (A row that carries the record
    # with no day is legal -- it is the RETAINED state -- but no factory
    # MEANS that; a fixture wanting it settles a row and then reverts it, as
    # the app does.)
    if settled_on is not None:
        cover_bare_settled_row(db_session, txn, amount, settled_amount)
    return txn


def independent_settled_figure():
    """Return a SQL expression for a row's settled figure, spelled independently.

    **An oracle's spelling of the record, for the integration suites that
    reconcile the posted ledger against the transaction source of truth**
    (``test_posting_ledger_*_reconciliation``).  The record is the row's
    entries since plan step ``balance:X-bi-4b-2`` deleted the row's own
    ``settled_amount`` (ruling **R-BAL80**), which those oracles read through
    ``COALESCE(settled_amount, estimated_amount)`` before; the app's own SQL
    twin is ``posting_reads.settled_figure_clause``, and an oracle that
    called it would grade one producer against itself, so this is a second
    spelling ON PURPOSE -- a correlated sum over ``budget.transaction_entries``
    written here, in the suite, and nowhere in ``app/``.  ``COALESCE`` to
    zero: a settled row with no entry is the ``$0.00`` record (R-BAL82).

    Returns:
        A correlated scalar subquery over :class:`TransactionEntry`, to be
        used inside a query whose FROM clause holds ``Transaction``.
    """
    # pylint: disable=import-outside-toplevel  -- the module convention.
    from sqlalchemy import func, select

    from app.models.transaction import Transaction
    from app.models.transaction_entry import TransactionEntry

    total = (
        select(func.sum(TransactionEntry.amount))
        .where(TransactionEntry.transaction_id == Transaction.id)
        .correlate(Transaction)
        .scalar_subquery()
    )
    return func.coalesce(total, Decimal("0"))


def cover_bare_settled_row(db_session, txn, amount, submitted=None):
    """Write the covering movement a bare-built settled row owes (R-BAL80).

    **The one door a bare-built settled row's RECORD goes through** (plan
    step ``balance:X-bi-4a``; the record's only home since ``X-bi-4b-2``
    deleted the row's own figure columns): the seam's own writer, over the
    record a settle would make, the plan's figure ``resolved`` when nobody
    typed one, else the typed figure ``typed`` (the same two arms
    ``Settlement.from_settle`` takes for a live settle).  A ``$0.00`` figure
    writes none, as the seam writes none -- the row's entries are its record
    (ruling **R-BAL82**).  :func:`add_txn` calls it; a suite that lays a
    settled row bare itself calls it after its flush, or every reader values
    the row as a close of nothing.

    Args:
        db_session: The test session; flushed after the write.
        txn: The settled row, flushed, its settle day laid.
        amount: The row's plan figure.
        submitted: The figure a fixture recorded as a human's correction, or
            ``None``.
    """
    # pylint: disable=import-outside-toplevel  -- the module convention.
    from app.enums import MovementFigureSourceEnum
    from app.services.status_seam import Settlement
    from app.services.status_seam._covering import sync_covering_movement

    if submitted is None:
        settlement = Settlement(
            amount=Decimal(str(amount)),
            source=MovementFigureSourceEnum.RESOLVED,
        )
    else:
        settlement = Settlement(
            amount=Decimal(str(submitted)),
            source=MovementFigureSourceEnum.TYPED,
        )
    sync_covering_movement(txn, now_settled=True, settlement=settlement)
    db_session.flush()


def generate_row_of(template, period):
    """Generate the ONE row of *template* in *period* through the engine.

    **The suite's one builder for a row of a definition** (plan step
    balance:X-cf, developer ruling 2026-09-11, the row-level twin of
    :func:`state_template_price`'s lesson).  A row that names a recurring
    definition has exactly one constructor in the application:
    ``recurrence_engine._generate._new_row``, which splats
    :class:`~app.services.recurrence_engine.DerivedRowFields` -- the account,
    name, category, type, amount OWNERSHIP and due date the definition derives
    -- onto the identity columns, with ``occurs_on`` from the cadence walk.
    This helper does not restate that line; it CALLS it, through
    :func:`~app.services.recurrence_engine.generate_for_template` with a
    window of exactly one paycheck (``GenerationSchedule.for_period_ids``,
    the shape carry-forward's generate branch uses), and hands back what the
    engine wrote.  So a fixture built here cannot disagree with the engine
    about what such a row IS, and a column the engine derives tomorrow lands
    on every fixture the day it lands on ``DerivedRowFields``.

    **What the hand-built rows were.**  Sixty-eight sites in thirty-nine files
    constructed ``Transaction(template_id=..., ...)`` by hand: undated (the
    state the CHECK plan step balance:X-bv-2 binds refuses), and most of
    them OWNING a figure with ``is_override=False`` -- the pre-X-au-e
    shape, which no producer has written since a generated row became derived.
    A control that hand-builds its subject stops grading the producer, and
    every one of those sites was grading a row the application cannot make.

    Two consequences follow from getting the engine's row, and each is the
    point rather than a cost:

    * **It is DERIVED.**  It stores no figure; its definition's price series
      answers for it on its own due date, so a fixture states the figure it
      expects through :func:`state_template_price` on the template.  A reader
      that asks the raw ``estimated_amount`` column gets ``None`` here exactly
      as it does on production, which is what such a reader deserves.
    * **It answers an OCCURRENCE** (``occurs_on``), so
      ``idx_transactions_template_scenario_occurrence`` holds over it, and a
      second ask for the same paycheck writes nothing: the engine's claim
      predicate (``_recurrence_common.OccurrenceClaims``) sees the first row
      before the index ever could, which is the 0-rows refusal below.

    A fixture wanting the OWNER's row -- a figure the human authored -- takes
    ownership of the generated row the way the re-price door does
    (:func:`repriced_by_the_owner`), and one wanting a row the owner MOVED
    moves this one the way the move door does (:func:`moved_by_the_owner`),
    rather than building one.  A fixture wanting a SETTLED row settles this
    one, as the app does; the engine only ever writes Projected.

    The row is generated into the owner's BASELINE scenario, which is what
    every door that generates passes; a fixture for another scenario is a
    scenario test's subject and authors the pass itself.  Because the
    baseline is the template owner's by construction, the engine's
    cross-user scenario gate cannot be reached from here.

    Args:
        template: The flushed
            :class:`~app.models.transaction_template.TransactionTemplate`.  It
            must carry a cadence (``recurrence_rule``): a definition with rows
            is one that repeats, and :func:`make_every_period_rule` is the
            ordinary way a fixture gives it one.
        period: The :class:`~app.models.pay_period.PayPeriod` row the
            generated row is funded in.  Must be one of the owner's saved
            periods.

    Returns:
        The :class:`~app.models.transaction.Transaction` the engine created,
        flushed.

    Raises:
        ValueError: *template* has no cadence, or the engine wrote no row in
            *period* -- its rule names no occurrence in that paycheck, or a
            row already claims it -- or wrote more than one, which a cadence
            firing several times inside one paycheck does and which is a
            generation test's subject rather than a fixture's.  **The 2+
            refusal leaves the rows it names FLUSHED**: the engine had
            already written them when the count was taken.
        RecurrenceWindowError: *period* is not one of the owner's saved
            periods (``GenerationSchedule.for_period_ids``), which is how
            another owner's paycheck is refused before any INSERT.
        BaselineMissingError: The owner has no baseline scenario
            (``BalanceContext.scenario_id``).
    """
    # Pylint: ``import-outside-toplevel`` -- this module imports no app
    # symbols at top level (its collection-time-safety convention).
    # pylint: disable=import-outside-toplevel
    from app.services import recurrence_engine
    return _the_one_row_the_engine_writes(recurrence_engine, template, period)


def one_off_row_of(  # pylint: disable=too-many-arguments
    period, *, name, amount, user_id, account_id, scenario_id,
    transaction_type_id, category_id=None, is_envelope=False,
    companion_visible=False, due_date=None,
):
    """Place the ONE row of a new one-off in *period* through the producer.

    **The suite's one builder for a ONE-OFF's row** (plan step
    balance:X-bi-7c), the twin of :func:`generate_row_of` for the other
    shape a plan item's row can take.  A one-off is a RULE-LESS DEFINITION
    plus its placed row (ruling **R-BAL20**; plan step ``balance:X-bi-7b``),
    and the application has exactly one constructor for it:
    :func:`app.services.one_off.place_one_off`, which mints the definition,
    opens its one-version price series dated on the row's due date
    (**R-BAL21**) and places the row through
    :func:`~app.services.one_off.place_row_of` -- both grid create doors,
    ``mint_uncategorized`` and bank import's envelope mint call it.  This
    helper does not restate any of that; it CALLS it, with the paycheck
    resolved through the OWNER's real calendar (``pay_calendar.calendar_for
    (user_id).period_by_id``, the read :func:`derived_span` makes for a
    period's own owner), and hands back what the producer wrote.  So a fixture built here cannot
    disagree with the app about what a one-off IS, and a column the
    producer derives tomorrow lands on every fixture the day it lands on
    ``unruled_row_fields``.

    **What the hand-built rows were.**  A link-less ``Transaction(...)`` --
    ``template_id`` NULL, OWNING its figure, its flags in its own sealed
    cells, undated in most cases -- is the shape the cutover
    (``balance:X-bi-7d-2``, migration ``596408fab6f1``) deleted: it minted
    every such production row a definition (34 on the 2026-09-18 restore),
    dated the undated on their paycheck's start and declared each
    ``TEMPLATE``-priced, then re-cut ``ck_transactions_one_pricing_link`` to
    ``= 1`` so the shape is unstorable.  Every hand-built site moved onto
    this builder before that (7c) so the CHECK binds on rows the app's own
    door wrote; the sites whose SUBJECT was the legacy shape itself -- the
    accessors' own-cell arm, the frozen flag cells -- built it by hand on
    ``legacy_link_less_row_of`` until 7d-2 retired them with the arm.  The
    one link-less row shape left is a CC payback, which has a builder of
    its own (:func:`payback_row_of`): a transfer's shadows were the other
    until plan step ``balance:X-bi-6-4d-2`` deleted them.

    Two consequences follow from getting the producer's row, each the point
    rather than a cost, exactly as for :func:`generate_row_of`:

    * **It is DERIVED.**  It stores no figure; its definition's one version
      answers for it on its own due date (amount rule 3), so *amount* here
      is what that version says.  A reader that asks the raw
      ``estimated_amount`` column gets ``None``, as it does on production
      for every row the grid has made since 7b-1.  A fixture wanting the
      OWNER's figure on the row takes ownership as the re-price door does
      (:func:`repriced_by_the_owner`); one wanting the definition's price
      corrected restates it (``one_off.restate_price``).
    * **It is DATED, and answers that day** (``due_date`` and ``occurs_on``,
      rulings **R-BAL22** / **R-BAL25**): the paycheck's start unless
      *due_date* says otherwise, so ``idx_transactions_template_scenario_
      occurrence`` holds over it.  A fixture that wants a row on the
      "anytime this period" shelf wants a row no definition prices -- a CC
      payback (:func:`payback_row_of`; the producer dates none), the one
      undated row shape left since the cutover and plan step
      ``balance:X-bi-6-4d-2``, which deleted the transfer shadows (a
      transfer with no ``due_date`` is not a row).  On the 2026-09-18
      production restore the paybacks were undated (31 of 31) and no
      shadow was.

    The row is placed PROJECTED, which is the only state the producer
    writes.  A fixture wanting a SETTLED row settles this one as the app
    does (``status_seam.apply_status_change``; :func:`add_txn` lays a bare
    settled state on for the suites that need a row the posting writer has
    never seen), and one wanting it soft-deleted or annotated sets
    ``is_deleted`` / ``notes`` on the returned row: those are the row's own
    columns, not the definition's.

    Args:
        period: The :class:`~app.models.pay_period.PayPeriod` row the one-off
            is placed in.  Must be one of the owner's saved periods.
        name: What the plan item is called (the definition's name, which the
            row reads).
        amount: What it is planned to cost, as a :class:`~decimal.Decimal`
            or anything ``Decimal(str(...))`` reads; ``0.00`` is legal.
        user_id: The owner.
        account_id: The account the money moves through.
        scenario_id: The scenario the row is placed in.
        transaction_type_id: Expense or income (``ref_cache.txn_type_id``).
        category_id: What the money is, or ``None`` (the bank's
            uncategorised row; every grid door requires one).
        is_envelope: Whether the definition's rows take purchase entries.
        companion_visible: Whether a companion may see its rows.
        due_date: The day it falls, or ``None`` for the paycheck's start.

    Returns:
        The placed :class:`~app.models.transaction.Transaction`, flushed,
        with ``row.template`` its rule-less definition.

    Raises:
        AssertionError: *period* is not in *user_id*'s calendar -- another
            owner's paycheck, or one never flushed -- refused BEFORE the
            definition or the row is written, one tier above the composite
            key ``fk_transactions_owner_period`` that would refuse the row's
            INSERT after the definition had already been minted.
    """
    # pylint: disable=import-outside-toplevel  -- the module convention.
    from app.services.one_off import OneOffToPlace, place_one_off
    from app.services.pay_calendar import calendar_for

    placed_in = calendar_for(user_id).period_by_id(period.id)
    assert placed_in is not None, (
        f"pay period {period.id} is not in user {user_id}'s calendar -- it "
        f"was never flushed, or it belongs to someone else"
    )
    return place_one_off(
        OneOffToPlace(
            user_id=user_id,
            account_id=account_id,
            transaction_type_id=transaction_type_id,
            name=name,
            amount=Decimal(str(amount)),
            category_id=category_id,
            is_envelope=is_envelope,
            companion_visible=companion_visible,
        ),
        placed_in,
        scenario_id=scenario_id,
        due_date=due_date,
    )


def generate_transfer_of(template, period):
    """Generate the ONE transfer of *template* in *period* through the engine.

    **The suite's one builder for a transfer of a definition** -- plan step
    balance:X-ch, ruling R-BAL17 applied to the twin table, finding BAL-488
    -- and :func:`generate_row_of` one table over.  A transfer that names a
    recurring definition has exactly one constructor in the application:
    ``transfer_recurrence._create_from_definition``, reached from the generate
    pass's ``_new_row`` and from the maintain pass's create arm alike, which
    splats ``DerivedTransferFields`` (the two accounts, name, category, amount
    OWNERSHIP and due date the definition derives) into a
    ``transfer_service.TransferSpec`` and so gets the transfer from the one
    door that writes ``budget.transfers`` (with its two shadow legs, until
    plan step ``balance:X-bi-6-4d-2`` deleted the shadows).  This
    helper CALLS the generate pass, with a window of exactly one paycheck,
    and hands back what the engine wrote; every promise
    :func:`generate_row_of` makes and every refusal the shared private body
    fires is the same here.

    **What the hand-built transfers were** (BAL-488, measured 2026-09-12 on
    the full suite when X-bv-2's twin CHECK was bound): twenty-three sites in
    eleven files constructed ``Transfer(transfer_template_id=...)`` or a
    ``TransferSpec`` naming a template by hand -- undated, and OWNING a
    figure beside the link, the pre-X-au-f shape no producer writes.
    Forty-four tests grade transfers the application cannot make.

    What follows from the engine's transfer, and each is the point:

    * **It is DERIVED.**  It stores no figure; its
      definition's series answers for it on its own due date, so a fixture
      states the figure it expects on the template (:func:`state_template_price`;
      :func:`make_transfer_template` already does).  A reader that asks the
      raw ``amount`` column gets ``None``, as on production.
    * **It answers an OCCURRENCE** (``occurs_on``), so
      ``idx_transfers_template_scenario_occurrence`` holds over it and a
      second ask for the same paycheck writes nothing (the 0-rows refusal).
    * **It is Projected**; the engine writes nothing else.  A fixture wanting
      a SETTLED transfer of a definition settles this one through
      ``transfer_service.update_transfer`` (a settling ``status_id``, the
      shape :func:`create_settled_transfer` uses), and one wanting the OWNER's
      transfer takes ownership through the same door with ``is_override=True``
      beside the figure -- the two acts ``routes/transfers/mutations``
      performs -- because every write of a transfer goes through that service
      (``CLAUDE.md``, Architecture), which also kept a transfer's three rows
      equal while it had shadows.

    Args:
        template: The flushed
            :class:`~app.models.transfer_template.TransferTemplate`.  It must
            carry a cadence (``recurrence_rule``) and, to be worth anything,
            a stated price; :func:`make_transfer_template` gives it both.
        period: The :class:`~app.models.pay_period.PayPeriod` row the
            generated transfer is funded in.  Must be one of the owner's
            saved periods.

    Returns:
        The :class:`~app.models.transfer.Transfer` the engine created,
        flushed.

    Raises:
        ValueError: As :func:`generate_row_of` -- no cadence; the engine
            wrote no transfer in *period* (its rule names no occurrence
            there, or one already claims it) or wrote more than one.
        RecurrenceWindowError: *period* is not one of the owner's saved
            periods.
        BaselineMissingError: The owner has no baseline scenario.
        ValidationError: From ``transfer_service.create_transfer``, which the
            transaction engine has no counterpart of -- the definition's
            endpoints are refused (an amortizing loan as the SOURCE, the same
            account at both ends), or the occurrence precedes the
            destination loan's origination (ruling R-C).
        NotFoundError: From the same door -- an endpoint or the template
            itself is not the owner's.
    """
    # pylint: disable=import-outside-toplevel  -- see generate_row_of
    from app.services import transfer_recurrence
    return _the_one_row_the_engine_writes(transfer_recurrence, template, period)


def _the_one_row_the_engine_writes(engine, template, period):
    """The body :func:`generate_row_of` and :func:`generate_transfer_of` share.

    The two engines expose the same ``generate_for_template(template,
    schedule, scenario_id)`` and the two definitions the same
    ``recurrence_rule``, so what differs between a row of a definition and a
    transfer of one is the engine handed in and nothing else -- one statement
    of the window, the refusals and the count, rather than two that agree
    today (``CLAUDE.md`` rule 14).

    Args:
        engine: ``app.services.recurrence_engine`` or
            ``app.services.transfer_recurrence``.
        template: The flushed definition, carrying a cadence.
        period: The owner's saved :class:`~app.models.pay_period.PayPeriod`.

    Returns:
        The one row the engine wrote in *period*.

    Raises:
        ValueError: No cadence, or the engine wrote a number of rows other
            than one.  See the public builders for the causes.
        RecurrenceWindowError: *period* is not one of the owner's saved periods.
        BaselineMissingError: The owner has no baseline scenario.
    """
    # pylint: disable=import-outside-toplevel  -- see generate_row_of
    from app.services.balance_at import BalanceContext
    from app.services.generation_schedule import GenerationSchedule
    if template.recurrence_rule is None:
        raise ValueError(
            f"template {template.id} ({template.name!r}) has no cadence, so "
            "the engine generates nothing from it; give it one "
            "(make_every_period_rule) before asking for its row"
        )
    ctx = BalanceContext.build(template.user_id)
    created = engine.generate_for_template(
        template, GenerationSchedule.for_period_ids(ctx, [period.id]),
        ctx.scenario_id,
    )
    if len(created) != 1:
        raise ValueError(
            f"the engine wrote {len(created)} rows of template {template.id} "
            f"({template.name!r}) in pay period {period.id}, not one: its "
            "rule names no occurrence in that paycheck or a row already "
            "claims it (0), or it fires more than once there (2+)"
        )
    return created[0]
