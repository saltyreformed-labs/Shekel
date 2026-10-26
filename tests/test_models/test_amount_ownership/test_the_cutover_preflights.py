"""Two cutovers' upgrade pre-flights, driven directly: c8f3a5d2e714 names a template row whose
figure disagrees with its definition's series, and b7e4c1f38a20 asks each class of generated
transfer its own question (an ordinary transfer and a MANUAL loan payment against the series,
the manual one with its standing extra; a DERIVE payment only whether its loan resolves; an
overridden transfer is outside the population).
"""

from datetime import date
from decimal import Decimal

from app.extensions import db
from app.models.ref import TransactionType
from app.models.template_amount_version import TemplateAmountVersion
from app.models.transaction_template import TransactionTemplate
from app.models.amount_ownership import AmountOwnership
from tests._test_helpers import load_migration_module, make_every_period_rule
from app.models.loan_payment_settings import LoanPaymentSettings

from ._helpers import _make_transaction, _make_transfer


_TEMPLATE_CUTOVER = load_migration_module(
    "c8f3a5d2e714_a_template_row_reads_its_templates_series.py",
)


_TRANSFER_CUTOVER = load_migration_module(
    "b7e4c1f38a20_a_generated_transfer_reads_its_definition.py",
)


def _plain_template(seed_user):
    """An ordinary expense definition NO salary profile names.

    Migration ``c8f3a5d2e714``'s pre-flight skips every row of a definition an
    ACTIVE salary profile names (``_ACTIVE_SALARY_TEMPLATES_SQL``), so the
    cases' rows must be of one no profile names, or the probe would never
    see them.  It carries a cadence and no price series: the cases that need
    a series state one themselves.

    Args:
        seed_user: The ``seed_user`` fixture payload.

    Returns:
        The flushed :class:`~app.models.transaction_template.TransactionTemplate`.
    """
    expense = (
        db.session.query(TransactionType).filter_by(name="Expense").one()
    )
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Rent"].id,
        transaction_type_id=expense.id,
        name="X-au-e Rent",
        default_amount=Decimal("7.77"),
    )
    db.session.add(template)
    db.session.flush()
    make_every_period_rule(db.session, template)
    return template


# ``TestTheSalaryCutoverKnowsWhatItCannotRestore``,
# ``TestTheSalaryCutoverRestoresEachRowFromTheRightPlace``,
# ``TestTheTemplateCutoverKnowsWhatItCannotRestore``,
# ``TestTheTemplateCutoverRestoresEachRowFromTheRightPlace`` and
# ``TestTheTransferCutoverRestoresEachRowFromTheRightPlace`` -- thirteen cases
# driving ``settled_rows_whose_plan_is_not_recoverable`` and ``downgrade_rows``
# of the salary, template and transfer cutovers (``d7b2e6c1a483``,
# ``c8f3a5d2e714``, ``b7e4c1f38a20``), which read the row's own
# ``settled_amount`` / ``settled_basis_id`` -- were DELETED at plan step
# ``balance:X-bi-4b-2`` (developer ruling R-BAL84, 2026-09-20): their staged
# input, a settled row carrying those columns, cannot be built at head once
# migration ``45f10b870c8b`` deletes them.  **Those two arms of the three
# migrations are UNGRADED at head**: they run only on an old-dump restore (the
# test template replays the chain from base over an EMPTY database, which
# exercises no refusal or restore arm), and no test at head drives them.  What
# head grades instead is (a) the fact the probes stood for -- a stated figure
# is not the plan -- as ``Settlement.stated``
# (``test_settlement_record.py::TestPurchasesIffNoStoredFigure``), and (b) that
# ``45f10b870c8b``'s downgrade rebuilds the input those arms read, the columns,
# from the movements exactly (``test_the_record_is_its_movements``).
class TestTheTemplateCutoverRefusesRatherThanStrandingARow:
    """Migration ``c8f3a5d2e714``'s PRE-FLIGHT, driven directly.

    **No cutover in this family had a test of its UPGRADE until this one**, and
    an adversarial review of X-au-e is what said so: all three exposed their
    downgrade at module scope "so a test can drive it -- a guard nothing
    exercises is a guard nobody has seen work", and left the destructive half
    ungraded. The measurement behind "0 dateless, 0 empty series, `$0.00`
    differing" was taken on a clone; production moves between a measurement and
    a deploy, and each of those three violated by ONE row is a silently
    unpriceable row or a silently moved figure.

    Driven against a clone of production 2026-09-03: empty on all 525, and
    naming the row after one due date was nulled by hand.  **That nulled-date
    case is gone since plan step balance:X-bv-2** (ruling **R-BAL17**):
    ``ck_transactions_template_row_needs_due_date`` refuses the row at flush,
    so the guard's ``due_date IS NULL`` arm -- frozen migration code, and
    kept -- can no longer be handed its subject; the CHECK's DDL is that
    arm's successor and its own tests grade it.
    """

    def test_a_clean_population_strands_nothing(
        self, app, db, seed_user, seed_periods,
    ):
        """The ordinary shape: every row is priceable and agrees."""
        with app.app_context():
            template = _plain_template(seed_user)
            db.session.add(TemplateAmountVersion(
                transaction_template_id=template.id,
                effective_date=date(2026, 1, 1), amount=Decimal("7.77"),
            ))
            _make_transaction(
                seed_user, seed_periods,
                template_id=template.id,
                due_date=date(2026, 3, 1),
                amount_ownership=AmountOwnership.own(Decimal("7.77")),
            )
            db.session.commit()

            assert _TEMPLATE_CUTOVER.rows_the_declare_would_strand(
                db.session.connection(),
            ) == []

    def test_a_row_whose_FIGURE_DISAGREES_with_the_series_is_named(
        self, app, db, seed_user, seed_periods,
    ):
        """The cutover deletes a COPY; a disagreeing row would lose a FACT.

        This is the arm that makes the pre-flight more than a null check: the
        row is perfectly priceable, and declaring it would still change what
        it is worth.
        """
        with app.app_context():
            template = _plain_template(seed_user)
            db.session.add(TemplateAmountVersion(
                transaction_template_id=template.id,
                effective_date=date(2026, 1, 1), amount=Decimal("7.77"),
            ))
            txn = _make_transaction(
                seed_user, seed_periods,
                template_id=template.id,
                due_date=date(2026, 3, 1),
                amount_ownership=AmountOwnership.own(Decimal("999.00")),
            )
            db.session.commit()

            stranded = _TEMPLATE_CUTOVER.rows_the_declare_would_strand(
                db.session.connection(),
            )
            assert [row[0] for row in stranded] == [txn.id]
            assert "disagrees" in stranded[0][3]


def _state_series(data, amount, effective_on=date(2026, 1, 1)):
    """Add ONE amount version to the fixture's transfer template, directly.

    **Not through ``template_amount_service.set_amount``**, and the reason is a
    harness fact rather than a preference: the template belongs to the FIXTURE's
    session, so a write through its relationship inside a fresh
    ``app.app_context()`` appends an object that never flushes -- the ORM then
    answers the new price while the database still holds only the fixture's,
    and a probe reading SQL and an assertion reading the model disagree about
    the same template.  Measured while building these cases.  The transaction
    twin's cases add their versions the same way for the same reason.

    Dated ``2026-01-01`` by default, which is BEFORE every ``due_date`` these
    cases use and AFTER nothing -- the fixture's own version is stated at the
    owner's today, so a due date in March resolves to this one and the two
    versions together make the series' supersession observable rather than
    incidental.

    Args:
        data: The ``seed_full_user_data`` payload.
        amount: The price to state.
        effective_on: The date it takes effect.

    Returns:
        The fixture's transfer template, for a caller that goes on to attach
        loan-payment settings to it.
    """
    template = data["transfer_template"]
    db.session.add(TemplateAmountVersion(
        transfer_template_id=template.id,
        effective_date=effective_on, amount=amount,
    ))
    return template


class TestTheTransferCutoverRefusesRatherThanStrandingARow:
    """Migration ``b7e4c1f38a20``'s PRE-FLIGHT, driven directly.

    The transfer twin of :class:`TestTheTemplateCutoverRefusesRatherThanStrandingARow`,
    and it grades the half that has no sibling: **the probe asks different
    things of three CLASSES of row**, because the three have three producers
    (ruling **R-BAL10**).  An ordinary generated transfer and a MANUAL loan
    payment are priced by the definition's series and must agree with it; a
    DERIVE-mode payment is priced by the LOAN and is asked only whether that
    loan resolves.

    **The manual arm is graded against series + the standing extra, not the
    series alone**, and that is the distinction most able to go wrong silently:
    a probe comparing against the series would name every manual payment
    carrying an extra as stranded, and the upgrade would then refuse a
    population that is entirely correct.
    """

    def test_a_clean_population_strands_nothing(
        self, app, db, seed_full_user_data,
    ):
        """The ordinary shape: the row agrees with what its definition answers."""
        with app.app_context():
            data = seed_full_user_data
            _state_series(data, Decimal("100.00"))
            _make_transfer(
                data, due_date=date(2026, 3, 1),
                amount_ownership=AmountOwnership.own(Decimal("100.00")),
            )
            db.session.commit()

            assert _TRANSFER_CUTOVER.rows_the_declare_would_strand(
                db.session.connection(),
            ) == []

    def test_a_transfer_whose_figure_DISAGREES_is_named(
        self, app, db, seed_full_user_data,
    ):
        """The cutover deletes a COPY; a row where the two differ holds a FACT.

        Its figure is ``$250.00`` against a definition stating ``$100.00``, so
        emptying the column would move that row's amount by ``$150.00`` with
        nothing to say so.
        """
        with app.app_context():
            data = seed_full_user_data
            _state_series(data, Decimal("100.00"))
            xfer = _make_transfer(
                data, due_date=date(2026, 3, 1),
                amount_ownership=AmountOwnership.own(Decimal("250.00")),
            )
            db.session.commit()

            stranded = _TRANSFER_CUTOVER.rows_the_declare_would_strand(
                db.session.connection(),
            )
            assert [row[0] for row in stranded] == [xfer.id]
            assert "disagrees" in stranded[0][3]

    def test_a_MANUAL_payment_stores_the_BASE_and_is_graded_against_it(
        self, app, db, seed_full_user_data,
    ):
        """The standing extra is not in the stored figure, so it is not in the test.

        ``routes/loan/payment_transfer`` opens the series at the typed BASE and
        keeps the extra on the settings row -- *"added live to every payment, in
        BOTH modes"* -- so a manual payment's row stores exactly what an
        ordinary generated transfer's does.  Series ``$100.00``, standing extra
        ``$25.00``, row ``$100.00``: a CORRECT row, and the probe must not name
        it.

        **A first draft of the probe graded this arm against ``series + extra``
        and this case hand-built an ``own($125.00)`` row to match it** -- a shape
        no writer in ``app/`` produces.  The case was green and blind, and the
        probe it defended would have RAISED on every real manual payment
        carrying an extra: migrations auto-run in the deploy pipeline, so that
        is a failed deploy. Found by an adversarial review of this step.

        What declaring the row moved, when this migration ran at plan step
        ``X-au-f-2``, was the PARENT's answer, from ``$100.00`` to ``$125.00``
        -- the cash that leaves the bank (**R-BAL10**) -- while its two legs,
        then two shadow rows, already answered ``$125.00``, so the fold moved
        ``$0.00``.
        """
        with app.app_context():
            data = seed_full_user_data
            _state_series(data, Decimal("100.00"))
            # Added DIRECTLY, for the reason ``_state_series`` states: the
            # fixture's template is not in this context's session, so assigning
            # through its ``settings`` relationship never flushes and the probe
            # -- which reads SQL -- would not see the payment at all.
            db.session.add(LoanPaymentSettings(
                transfer_template_id=data["transfer_template"].id,
                derive_from_loan=False, extra_principal=Decimal("25.00"),
            ))
            _make_transfer(
                data, due_date=date(2026, 3, 1),
                amount_ownership=AmountOwnership.own(Decimal("100.00")),
            )
            db.session.commit()

            assert _TRANSFER_CUTOVER.rows_the_declare_would_strand(
                db.session.connection(),
            ) == []

    def test_a_DERIVE_payment_is_asked_for_a_LOAN_and_not_for_agreement(
        self, app, db, seed_full_user_data,
    ):
        """Its stored figure is a snapshot, so a difference is the point.

        A derive-mode payment stores what P&I + escrow came to when it was set
        up and has been free to drift ever since -- that drift IS what this
        cutover deletes.  Requiring agreement would refuse exactly the rows the
        step exists for, so the probe asks only whether the loan resolves.  Here
        the destination is the fixture's SAVINGS account, which carries no
        ``LoanParams``, so the row is named for the reason that actually makes
        it unpriceable.
        """
        with app.app_context():
            data = seed_full_user_data
            _state_series(data, Decimal("100.00"))
            # Added DIRECTLY; see the sibling case.
            db.session.add(LoanPaymentSettings(
                transfer_template_id=data["transfer_template"].id,
                derive_from_loan=True,
            ))
            xfer = _make_transfer(
                data, due_date=date(2026, 3, 1),
                amount_ownership=AmountOwnership.own(Decimal("999.99")),
            )
            db.session.commit()

            stranded = _TRANSFER_CUTOVER.rows_the_declare_would_strand(
                db.session.connection(),
            )
            assert [row[0] for row in stranded] == [xfer.id]
            assert "LoanParams" in stranded[0][3]

    def test_an_OVERRIDDEN_transfer_is_not_in_the_population_at_all(
        self, app, db, seed_full_user_data,
    ):
        """The owner's figure is kept, so it is neither declared nor graded.

        Its figure disagrees with the definition by ``$150.00`` -- the shape the
        second case above names -- and it is silent here, which is what says the
        exclusion is on the PREDICATE rather than on the probe's reasons.
        """
        with app.app_context():
            data = seed_full_user_data
            _state_series(data, Decimal("100.00"))
            _make_transfer(
                data, due_date=date(2026, 3, 1), is_override=True,
                amount_ownership=AmountOwnership.own(Decimal("250.00")),
            )
            db.session.commit()

            assert _TRANSFER_CUTOVER.rows_the_declare_would_strand(
                db.session.connection(),
            ) == []
