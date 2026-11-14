"""The envelopes a purchase joins, the acts that file a bank line, and the review pass.

The acts go through the create door.  With them, an unexplained outflow, the owner's pay
periods, the review pass itself and its creation bars, amount basis and merchant answers,
the register's accepted acts, and the submission a screen rendered from a pass would post
back.
"""

from datetime import timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import TxnTypeEnum
from app.extensions import db
from app.models.pay_period import PayPeriod
from app.services.cash_ledger import derived_amount_basis
from app.services import statement_match
from app.services.statement_match import (
    CreationBars,
    MerchantAnswers,
    MatchSubmission,
    MintedEnvelopes,
    NewEnvelope,
    PurchaseCreation,
    ReviewScope,
    ReviewedDifference,
    ReviewedRow,
    RowKind,
    as_reviewed,
)
from app.services.one_off import OneOffToPlace, place_one_off
from app.services.pay_calendar import calendar_for
from tests._test_helpers import last_covered_day

from ._ledger_rows import a_transaction
from ._statement_rows import an_import, a_bank_line


def a_one_off_envelope(seed_user, name="Home Improvement", category=None):
    """Return a Projected envelope the owner made AT THE GRID, through the producer.

    The shape plan step ``balance:X-bi-7b`` introduced: a RULE-LESS definition
    plus its placed row (``one_off.place_one_off``, the door the grid's two
    create routes call), so the row carries a ``template_id`` and its
    ``recurs`` is ``False``.  Built through the producer rather than by hand
    so a case that grades how bank import reads such a row grades the row the
    app actually writes.  Committed, so a scope derived afterwards sees it.

    Args:
        seed_user: The seeded user bundle.
        name: The envelope's name -- the definition's, which the row reads.
        category: The category it files under; Groceries by default.

    Returns:
        The placed :class:`~app.models.transaction.Transaction`.
    """
    category = category or seed_user["categories"]["Groceries"]
    period = calendar_for(seed_user["user"].id).period_by_id(
        seed_user["bootstrap_period"].id,
    )
    row = place_one_off(
        OneOffToPlace(
            user_id=seed_user["user"].id,
            account_id=seed_user["account"].id,
            transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
            name=name,
            amount=Decimal("0.00"),
            category_id=category.id,
            is_envelope=True,
        ),
        period,
        scenario_id=seed_user["scenario"].id,
    )
    db.session.commit()
    return row


def an_envelope(seed_user, name="Groceries"):
    """Return a Projected envelope a purchase may join.

    Args:
        seed_user: The seeded user bundle.
        name: The envelope's name.

    Returns:
        The staged :class:`~app.models.transaction.Transaction`.
    """
    return a_transaction(
        seed_user, name=name, amount="500.00", is_envelope=True,
    )


def an_unexplained_outflow(
    seed_user, merchant="Amazon", amount="-57.96", sequence=0,
    source_category=None,
):
    """Record one unexplained outflow from *merchant*.

    **Shared by the two route modules** since plan step ``bank_import:X-gf-2``
    (finding **N-33**'s shape): the queue asks about a merchant with no answer
    and the register shows one already answered, and both need a line for it.

    Args:
        seed_user: The seeded user bundle.
        merchant: What the bank names the merchant, which is the rule key.
        amount: Signed, negative OUT of the account.
        sequence: The ordinal completing the line's identity.
        source_category: The BANK's own category string, verbatim, or ``None``
            for a source stating none.  Ruling **R-GJ** reads it for one narrow
            purpose: a merchant a source files as a payment to a credit card
            has no create arm until the owner answers for it.

    Returns:
        The staged
        :class:`~app.models.statement_import.BankStatementLine`.
    """
    statement = an_import(seed_user)
    return a_bank_line(
        seed_user, statement, amount=amount,
        posted_on=seed_user["bootstrap_period"].start_date,
        description=f"POINT OF SALE DEBIT L340 THING ({merchant})",
        merchant=merchant, sequence_in_group=sequence,
        source_category=source_category,
    )


def filed_by(seed_user, line, envelope, *, by_rule, scope=None, answers=None):
    """Record one bank *line* as a purchase in *envelope*, and say WHO did it.

    **The one fact under test in several places is ``by_rule``** (ruling
    **bank_import:R-GT**): it decides which settled tab holds the act, and it
    is what the hero's *N filed by rules* counts.  Staged through the real
    create door rather than by writing the two rows, so a case cannot pass
    against a shape the door would never produce.

    **It takes the LINE rather than making one**, because every caller of this
    cares WHICH import recorded it -- :func:`an_unexplained_outflow` stages a
    fresh import per line, which is the opposite of what a case about one
    import's own lines needs.

    Args:
        seed_user: The seeded user bundle.
        line: The recorded :class:`~app.models.statement_import
            .BankStatementLine` to file.  Must already be COMMITTED, because
            the scope is derived from the database.
        envelope: The budget line to file it into.
        by_rule: Whether a STANDING RULE performed the act rather than a
            person ticking it.
        scope: The pass to file against, or ``None`` to derive one.
        bars: The creation bars, or ``None`` to derive them.

            **Both exist so a caller staging MANY acts can derive ONCE**, and
            that is a cost the register's own bound case has already paid for
            in as many words: at 51 rows a per-row derivation ran the pass 53
            times and made that the slowest test in the suite, timing it out in
            CI at the 30 s per-test budget.  A caller staging one line takes
            the default and reads exactly as before.

            **It is also what the app does.**  A render builds ONE
            :class:`~app.services.statement_match._scope.ReviewScope` and files
            every line of a pass against it; a fresh pass per line is a shape
            no door has, so a case built that way is measuring something the
            app never does.

    Returns:
        The created purchase.
    """
    return statement_match.create_purchase_from_line(
        PurchaseCreation(line_id=line.id, transaction_id=envelope.id),
        a_scope(seed_user) if scope is None else scope,
        MintedEnvelopes.none_yet(),
        an_answers(seed_user) if answers is None else answers,
        applied_by_rule=by_rule,
    )


def filed_acts(seed_user, how_many, *, by_rule):
    """Record *how_many* bank lines as purchases, each in its OWN envelope.

    The staging both of ruling **R-GX**'s bound cases need (plan step
    ``bank_import:X-gj-1c``): the settled tabs bound at
    :data:`~app.services.statement_match.REGISTER_LIMIT`, so a case about the
    bound has to hold one act more than that, and it is stated once here
    rather than twice because the two cases differ only in which tier they
    then read -- the service's page value, or the rendered route.

    **Each act gets an envelope of its own, and that was a MEASUREMENT rather
    than a style choice.**  ``posting_service.sync_transaction_postings``
    reconciled a transaction's whole purchase FAMILY on every entry change
    (ruling **R-FM**) until plan task plan#3097 took it out of the entry doors,
    so filing the *k*-th purchase into an envelope that already held *k-1* of
    them called ``_posting_purchases.emit_purchase_deltas`` *k* times --
    quadratic in the number of acts.  Measured 2026-08-31 on this box, 51
    acts: **1,326 emit calls (51x52/2) and 4.50 s into one envelope, against
    51 calls and 1.13 s spread across 51**.  CI then pinned ``-n 12`` on a
    hosted runner with ``nproc`` far under 12 (its shards run ``-n logical``
    since ``bank_import:X-gy``), and that oversubscription multiplied
    the concentrated shape past ``pytest.ini``'s 30 s per-test budget: four
    cases timed out at 28.94-29.03 s having taken 4.7 s here.  **The app's
    quadratic was real and was NOT this fixture's to fix** -- it is finding
    **N-406**, filed with this measurement -- but an envelope holding 51
    purchases in one pay period is a shape the app does not produce either:
    measured on a clone of the developer's own database 2026-08-31, the
    largest budget line holds **13** and the mean over the 63 rows carrying
    any is **2.92**.  The fixture was paying for a concentration it never had
    to stage.

    **Nothing about the bound depends on WHERE the money landed**, which is
    why spreading it costs no coverage: the settled tabs render one unnamed
    section whatever the destinations are
    (:func:`~app.services.statement_match._card_sections.act_sections`), and both
    cases assert over counts of acts.

    Args:
        seed_user: The seeded user bundle.
        how_many: How many acts to stage.
        by_rule: Whether a STANDING RULE performed them rather than a person
            ticking them (ruling **bank_import:R-GT**), which is what decides
            WHICH settled tab holds them.

    Returns:
        The created purchases, in the order they were filed.
    """
    pots = [
        an_envelope(seed_user, name=f"Envelope {ordinal}")
        for ordinal in range(how_many)
    ]
    lines = [
        an_unexplained_outflow(
            seed_user, merchant=f"Shop {ordinal}", amount="-10.00",
            sequence=ordinal,
        )
        for ordinal in range(how_many)
    ]
    db.session.commit()
    # ONE derivation for all of them, which is what the app does and what
    # keeps this affordable -- see :func:`filed_by`.
    scope, answers = a_scope(seed_user), an_answers(seed_user)
    purchases = [
        filed_by(
            seed_user, line, pot, by_rule=by_rule, scope=scope,
            answers=answers,
        )
        for pot, line in zip(pots, lines)
    ]
    db.session.commit()
    return purchases


def a_later_period(seed_user):
    """Stage and return the pay period AFTER the bootstrap one.

    On the owner's own calendar, because a period the calendar does not know is
    not a period the offer set can reach -- a case built on one would pass for
    the wrong reason.

    Args:
        seed_user: The seeded user bundle.

    Returns:
        The staged :class:`~app.models.pay_period.PayPeriod`.
    """
    bootstrap = seed_user["bootstrap_period"]
    period = PayPeriod(
        user_id=seed_user["user"].id,
        start_date=last_covered_day(bootstrap) + timedelta(days=1),
    )
    db.session.add(period)
    db.session.flush()
    return period


def a_payday_on(seed_user, start_date):
    """Stage and return one pay period of the owner's, OPENING on *start_date*.

    :func:`a_later_period`'s general form, and it exists for the shape that
    helper cannot build: it opens the next period the day after the bootstrap's
    stored ``end_date``, where the derived end (the day before the NEXT payday)
    and the stored one COINCIDE -- so a case asserting on a derived span and
    built on it would pass against the stored column it is meant to catch.

    Its own stored ``end_date`` is a fortnight, which is what keeps the owner's
    cadence at 14 while this row is the last one: no seeded owner has a
    ``budget.pay_schedule`` row, so
    ``pay_schedule_service.resolve_schedule`` infers the cadence from the last
    period's stored LENGTH.

    ``period_index`` is the next free one rather than a position, because the
    derivation renumbers by payday order anyway and the column only has to
    satisfy ``uq_pay_periods_user_index`` -- which is what lets a caller stage
    paydays whose id order and date order disagree.

    Args:
        seed_user: The seeded user bundle.
        start_date: The payday this period opens on.

    Returns:
        The staged :class:`~app.models.pay_period.PayPeriod`.
    """
    period = PayPeriod(user_id=seed_user["user"].id, start_date=start_date)
    db.session.add(period)
    db.session.flush()
    return period


def a_scope(seed_user, account=None):
    """Return the derived pass these doors act inside.

    **Built at the point of USE, never once per test**, and that is the
    property under test as much as a convenience:
    :class:`~app.services.statement_match.ReviewScope` holds the account's rows
    and their prices as they stood when it was derived, so a case that stages a
    row and then re-uses an older scope is asserting against a state the app
    would never have.  Every call here therefore re-derives.

    Args:
        seed_user: The seeded user bundle.
        account: The account being reviewed; the seeded checking one by
            default.

    Returns:
        The :class:`~app.services.statement_match.ReviewScope`.
    """
    return ReviewScope.build(
        seed_user["user"].id, (account or seed_user["account"]).id,
    )


def accepted_acts(seed_user, account=None):
    """Return the accepted acts the REGISTER lists, whole and unbounded.

    Plan step ``bank_import:X-gf-2``.  These were ``review_set(scope).accepted``
    until the register took them off the review screen (ruling
    **bank_import:R-GX**), and going through the settled tabs' own reader
    rather than around it is the point: it is what the surface renders, bound
    and ordered as the surface orders it.  *It went through
    ``register_set(...).accepted`` until plan step ``bank_import:X-gi-3``
    deleted that assembly with the register's model; ``accepted_register`` is
    the half it was assembled from, and the value returned is unchanged.*

    **Unbounded on purpose.**  A case here stages two or three acts, so the
    bound could never fire -- and passing ``None`` says the assertion is about
    the acts and not about the cut, which is
    ``TestTheRegisterBoundsWhatItRenders``'s own subject.

    Args:
        seed_user: The seeded user bundle.
        account: The account being reviewed; the seeded checking one by
            default.

    Returns:
        The :class:`~app.services.statement_match.AcceptedGroup` values, every
        act that no longer holds first and then newest first.  **That ORDER is
        not what ``review_set(scope).accepted`` gave**, which was plain
        newest-first: a case staging several acts and indexing ``[0]`` is
        asking for a different one than it used to.  No case does today, and
        the register's order is the one the screen renders.
    """
    return statement_match.accepted_register(
        seed_user["user"].id, (account or seed_user["account"]).id, None,
    ).shown


def a_bars(seed_user, account=None):
    """Return the pass's creation BARS, for a test calling the door directly.

    Ruling **R-GJ**, plan step ``bank_import:X-ga``.
    :func:`~app.services.statement_match.apply_reviewed` derives one per
    REQUEST and threads it, so a case calling
    :func:`~app.services.statement_match.create_purchase_from_line` on its own
    is standing in for that caller and must supply the same thing.

    **DERIVED rather than an empty value**, and that is the property under test
    as much as a convenience: a helper that handed the door a
    ``CreationBars`` with nothing in it would make every case here blind to the
    refusal the step exists to add, which is exactly the *fixture that bypasses
    the production door* shape this suite has been burned by before.  Built at
    the point of USE, for the reason :func:`a_scope` is.

    Args:
        seed_user: The seeded user bundle.
        account: The account being reviewed; the seeded checking one by
            default.

    Returns:
        The :class:`~app.services.statement_match.CreationBars`.
    """
    return CreationBars.build(
        seed_user["user"].id, (account or seed_user["account"]).id,
    )


def an_answers(seed_user, account=None):
    """Return what the owner has said about this account's merchants.

    The :class:`~app.services.statement_match.MerchantAnswers` the pass derives
    once and every act reads -- the stated rules AND ruling **R-GJ**'s bars,
    from one read.  :func:`a_bars` is still here for the cases that assert on
    the bars alone; this is what the write doors take (plan step
    ``bank_import:X-gj-2b-2``).

    Args:
        seed_user: The seeded user bundle.
        account: The account being reviewed; the seeded checking one by
            default.

    Returns:
        The :class:`~app.services.statement_match.MerchantAnswers`.
    """
    return MerchantAnswers.build(
        seed_user["user"].id, (account or seed_user["account"]).id,
    )


def a_purchase_in_a_minted_envelope(seed_user, amount="-25.00", statement=None):
    """Record one bank line as a purchase in an envelope the create door mints.

    A one-off "Public Library" Groceries envelope, minted by
    ``statement_match.create_purchase_from_line`` with the line's purchase
    filed in it: the act, its two creation records, the envelope and the
    purchase, as the door writes them.  The staging the withdrawal and
    row-delete cases share (CC-5-4a-4's third review, L5).  Flushes.

    Args:
        seed_user: The ``seed_user`` fixture dict.
        amount: The bank line's signed amount.
        statement: The import that shows the line; a new one when ``None``,
            so a case adding a second line to the same statement passes it.

    Returns:
        ``(line, created)`` -- the bank line and the door's result.
    """
    line = a_bank_line(
        seed_user, statement or an_import(seed_user), amount=amount,
        posted_on=seed_user["bootstrap_period"].start_date,
    )
    created = statement_match.create_purchase_from_line(
        PurchaseCreation(
            line_id=line.id,
            new_envelope=NewEnvelope(
                name="Public Library",
                category_id=seed_user["categories"]["Groceries"].id,
            ),
        ),
        a_scope(seed_user),
        MintedEnvelopes.none_yet(),
        an_answers(seed_user),
        applied_by_rule=False,
    )
    db.session.flush()
    return line, created


def a_basis(seed_user):
    """Return the pass's amount basis, for a test calling a producer directly.

    :meth:`~app.services.statement_match.ReviewScope.build` derives one per
    pass and threads it (plan step X-au-j); a case that calls
    :func:`~app.services.statement_match.candidates_for` on its own is standing
    in for that caller and must supply the same thing.  Built from the seeded
    scenario rather than resolved, because these fixtures own it.

    Args:
        seed_user: The seeded user bundle.

    Returns:
        The :class:`~app.services.cash_ledger.AmountBasis` for that owner.
    """
    return derived_amount_basis(seed_user["user"].id, seed_user["scenario"].id)


def a_submission(
    scope, *, lines=(), transactions=(), entries=(), residual=None,
    attributed=None, transfers=(),
):
    """Return the submission a screen rendered from *scope* would post back.

    **It reads the reviewed state out of the SCOPE rather than inventing one**,
    which is what makes these tests exercise the real two-moment flow: the
    screen renders a candidate, the owner ticks it, and the door reconciles
    what was ticked with what it finds (finding **N-336**, plan step
    ``bank_import:X-f6d-3``).  A helper that stamped a figure of its own would
    make every case agree with itself by construction, and the staleness guard
    would be untested by every test that uses it.

    A row the scope does NOT offer is carried as the ORM row states it.  That
    is not a fallback that hides a mistake: those cases are the ones asserting
    the door refuses a row it never offered, and ``resolve_rows`` refuses them
    before the reconciliation is reached -- so what the token says about them
    cannot change the outcome, and a helper that raised here would make the
    refusal untestable.

    **A transaction is ticked AS THE SCREEN OFFERS IT** (plan step
    ``credit_card:CC-5-4a-1``, ruling **R-CC43**, developer 2026-09-21: *the
    MOVEMENT is the subject of every settled match; a row is a candidate only
    while Projected*).  A Projected row is offered as itself and ticked as a
    TRANSACTION; a settled row is offered as its covering movement and ticked
    as a SETTLEMENT, so a case that settles a bill and then matches it names
    what the owner would tick -- the payment -- and its assertions about the
    day, the figure and the ledger grade the same act they always did.  A
    row offered neither way falls back to the not-offerable token above.
    Developer confirmation 2026-09-21 (rule 5): "Confirm A, B and C as rule-5 re-expressions under
    R-CC43 -- the member names the payment; a day moves through the seam that mirrors it; a settled
    row is ticked as its payment."

    **A still-planned TRANSFER is ticked as its LEG on the scope's account**
    (leaf ``balance:X-bi-6-4c-1``, rulings **R-BAL87**, **R-BAL159**): each
    of *transfers* names ``(LEG, transfer id)``, the key the screen offers,
    where a case handed the transfer's shadow row in *transactions* before
    that leaf.  A transfer the scope does not offer as a leg takes the same
    not-offerable fallback, keyed by the transfer and revised by its counter.
    Developer approval 2026-09-30 (rule 5): "Yes, inputs only (Recommended)".

    **A PAID transfer is ticked as its side's RECORD on the scope's account**
    (leaf ``balance:X-bi-6-4d-2``), the leg's twin of the settled row above:
    a side whose money has moved is offered as its dated record, a
    SETTLEMENT (ruling **R-BAL80**), which hangs off the transfer by its side
    link since that leaf, where a case handed the side's shadow row in
    *transactions* until then (its ``covering_movements`` is empty now).
    Ruling **R-BAL167**'s class 4, a shared helper re-expressed.

    Args:
        scope: The pass being submitted against
            (:func:`a_scope`).
        lines: Bank line rows.
        transactions: Transaction rows, each resolved to the subject the
            screen offers for it.
        entries: Purchase rows.
        transfers: Transfer rows, each ticked as its leg on the scope's
            account (leaf ``balance:X-bi-6-4c-1``), or as that side's record
            once it is paid (leaf ``balance:X-bi-6-4d-2``).
        residual: The difference the screen showed and the owner ticked, as a
            string or a ``Decimal``; ``None`` for the ordinary case where they
            accepted none (plan step ``bank_import:X-f6d-4``).  **Stated by the
            caller rather than computed here**, and that is the point: the door
            reconciles what the screen said against its own arithmetic, so a
            helper that derived the figure would make every case agree by
            construction and leave the reconciliation ungraded -- the same
            reason the reviewed row state is read off the scope rather than
            invented.
        attributed: Which member carries that difference, as a
            ``(kind, orm_row)`` pair, or ``None`` for the ordinary case where
            the owner named none (plan step ``bank_import:X-gj-3a``).

            **Resolved out of the rows this submission already carries**,
            which is what the pane does: each option's value carries one of
            the ticked rows' own tokens, so the pointer and the row it points
            at are one value.  A pair naming a row this submission does not
            carry takes the same not-offerable fallback the loop above takes
            -- deliberately, since the cases asserting the door refuses such a
            pointer are the ones that need to build it.

            **Carried INSIDE the consent beside the figure** since plan step
            ``bank_import:X-gp`` (:class:`~app.services.statement_match
            .ReviewedDifference`), so a member with no figure is a shape the
            wire cannot express and this helper REFUSES rather than builds:
            a builder that quietly dropped the member would let a case claim
            it graded *named a member, agreed to nothing* while the door saw
            neither.

    Raises:
        AssertionError: When *attributed* is given without *residual*.

    Returns:
        The :class:`~app.services.statement_match.MatchSubmission`.
    """
    offered = {
        (row.kind, row.row_id): row for row in scope.candidates.rows
    }
    wanted = (
        [(RowKind.TRANSACTION, txn) for txn in transactions]
        + [(RowKind.PURCHASE, entry) for entry in entries]
        + [(RowKind.LEG, transfer) for transfer in transfers]
    )
    rows = set()
    for kind, orm_row in wanted:
        candidate = offered.get((kind, orm_row.id))
        if candidate is None and kind is RowKind.TRANSACTION:
            # The row's payment, where the screen offers that instead
            # (ruling **R-CC43**; the docstring's second paragraph).
            candidate = next(
                (
                    offered[(RowKind.SETTLEMENT, movement.id)]
                    for movement in orm_row.covering_movements
                    if (RowKind.SETTLEMENT, movement.id) in offered
                ),
                None,
            )
        if candidate is None and kind is RowKind.LEG:
            # The paid side's record, where the screen offers that instead
            # (the docstring's fourth paragraph): only the side on the
            # scope's account can be offered here.
            candidate = next(
                (
                    offered[(RowKind.SETTLEMENT, movement.id)]
                    for movement in (
                        *orm_row.expense_movements, *orm_row.income_movements,
                    )
                    if (RowKind.SETTLEMENT, movement.id) in offered
                ),
                None,
            )
        if candidate is not None:
            rows.add(as_reviewed(candidate))
            continue
        rows.add(ReviewedRow(
            kind=kind,
            row_id=orm_row.id,
            cash_amount=Decimal("0.00"),
            version_id=orm_row.version_id,
        ))
    assert residual is not None or attributed is None, (
        "a member cannot be named without a figure: the consent is one value "
        "carrying both since plan step bank_import:X-gp"
    )
    return MatchSubmission(
        line_ids=frozenset(line.id for line in lines),
        rows=frozenset(rows),
        consent=(
            None if residual is None
            else ReviewedDifference(
                figure=Decimal(str(residual)),
                on_row=_attribution(rows, attributed),
            )
        ),
    )


def _attribution(rows, attributed):
    """Return the submitted row an attribution names, or ``None``.

    Args:
        rows: The reviewed rows this submission carries.
        attributed: A ``(kind, orm_row)`` pair, or ``None``.

    Returns:
        The matching :class:`~app.services.statement_match.ReviewedRow` from
        *rows*, so the pointer is one of the submission's own values; or one
        built from the ORM row where the submission does not carry it, which
        is the shape a crafted body has.
    """
    if attributed is None:
        return None
    kind, orm_row = attributed
    for row in rows:
        if row.kind is kind and row.row_id == orm_row.id:
            return row
    return ReviewedRow(
        kind=kind, row_id=orm_row.id, cash_amount=Decimal("0.00"),
        version_id=orm_row.version_id,
    )
