"""The producer-free accessors refuse a row whose amount they cannot resolve (a derived
transaction, a Projected row that owns its figure, a derived transfer), and a settlement record,
an excluded status or a soft delete answers for a derived row instead.
"""

from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import AmountSourceEnum, StatusEnum
from app.exceptions import AmountUnresolvable
from app.models.amount_ownership import AmountOwnership
from tests._test_helpers import cover_bare_settled_row, make_every_period_rule
from app.services.cash_ledger import derived_amount_basis, resolve_transfer_amount
from app.services.row_valuation import settled_contribution
from app.models.transfer_template import TransferTemplate

from ._helpers import _make_transaction, _make_transfer


class TestTheCheapAccessorRefusesAnUnsettledRow:
    """The producer-free accessors refuse a row whose amount they cannot resolve.

    They read the row and nothing else, and the SALARY rule's producer needs the
    owner's whole pay-period set to answer at all
    (``income_service.SalaryPricing`` runs the paycheck engine over every
    period), so no accessor of this shape can hold that derivation.  Answering
    ``None`` would put one into a money path; answering zero would remove real
    money from a balance in silence.

    **The subject moved at plan step X-au-c2 and the rule did not.**  These
    cases graded ``Transaction.effective_amount`` and
    ``Transfer.effective_amount``, both now deleted; they grade
    ``row_valuation.settled_contribution`` and
    ``cash_ledger.resolve_transfer_amount``, which is where the refusal lives.
    Keeping them is the point: the refusal is what makes the per-kind cutovers
    (X-au-d..X-au-i) safe to ship one at a time, because a reader they have not
    routed fails LOUDLY rather than publishing a wrong number.

    **THE TRANSACTION HALF'S REFUSAL WIDENED AT PLAN STEP X-bx, and the class
    is renamed for it** (finding **BAL-465**).  It was "refuses a DERIVED row",
    and that was the accidental half of the guarantee: the accessor fell
    through to ``estimated_amount``, so a row was refused only when the column
    happened to be empty.  A Projected row that OWNS its figure was priced
    silently -- and the accessor's every reader asks what a row's money DID, so
    that answer reported a movement which had not happened.  It refuses on the
    STATUS now, so both cases below refuse and neither depends on the column.

    **The reader case went with its reader** (ruling **R-BAL81**, plan step
    ``balance:X-bi-4a``).  A third case drove ``cash_ledger.settled_cash_leg``
    -- which guarded on ``is_balance_contributing`` alone, so the refusal one
    call down was the whole of what stopped a `$100.00` Projected bill being
    booked as confirmed cash -- to show the refusal reaching a real reader.
    That function is deleted: a settled row is worth what its covering
    movement moves, a stored figure, and no cash-ledger reader prices a row
    through this accessor now.  Every surviving reader restricts its rows to
    a settled status in SQL, so the refusal is graded where it lives.
    """

    def test_a_derived_transaction_refuses(
        self, app, db, seed_user, seed_periods
    ):
        """A Projected row with no figure of its own raises rather than answering."""
        with app.app_context():
            txn = _make_transaction(
                seed_user, seed_periods,
                amount_ownership=AmountOwnership.derived(
                    ref_cache.amount_source_id(AmountSourceEnum.TEMPLATE),
                ),
            )
            db.session.add(txn)
            db.session.flush()

            with pytest.raises(
                AmountUnresolvable, match="has not settled",
            ):
                _ = settled_contribution(txn)

    def test_a_projected_row_that_OWNS_its_figure_refuses_too(
        self, app, db, seed_user, seed_periods
    ):
        """The case plan step X-bx added, and the one that was silently WRONG.

        This row carries ``estimated_amount`` of ``$100.00``, so the deleted
        fall-through answered ``$100.00`` here -- a figure this accessor's
        readers would have booked as CONFIRMED cash, for a row whose money has
        not moved.  Nothing about the column decides it: the row has not
        SETTLED, so it recorded nothing and there is nothing to report.

        The refusal names the producer that DOES answer, because the next
        caller to trip this needs to be told what to call instead.
        """
        with app.app_context():
            txn = _make_transaction(
                seed_user, seed_periods,
                amount_ownership=AmountOwnership.own(Decimal("100.00")),
            )
            db.session.add(txn)
            db.session.flush()

            # The column IS populated -- this is not the derived shape above.
            assert txn.estimated_amount == Decimal("100.00")

            with pytest.raises(
                AmountUnresolvable, match="cash_ledger.contribution_of",
            ):
                _ = settled_contribution(txn)

    def test_a_derived_transfer_refuses(self, app, db, seed_full_user_data):
        """The transfer twin refuses on the same shape.

        The transfer is the engine's, so it carries the date its definition
        derives and the refusal is the one this case is about -- its
        definition states no price for that day -- rather than any other.

        **It builds its OWN definition since plan step X-au-f**, and that is
        this case being kept alive rather than tidied.  X-au-f gave the shared
        ``seed_full_user_data`` transfer template a price SERIES -- every
        app-side create door states one, and without it every generated transfer
        in the suite became unpriceable -- which silently disarmed this control:
        the refusal simply stopped firing and the case read DID NOT RAISE.  A
        template with no version is the state the refusal is ABOUT, so the case
        now constructs one instead of borrowing a fixture that no longer has it.
        """
        with app.app_context():
            template = TransferTemplate(
                user_id=seed_full_user_data["user"].id,
                from_account_id=seed_full_user_data["account"].id,
                to_account_id=seed_full_user_data["savings_account"].id,
                name="States No Price",
                default_amount=Decimal("100.00"),
            )
            db.session.add(template)
            db.session.flush()
            # A cadence and no price: the engine writes its transfer (derived,
            # on its own date) and nothing can then answer for it.
            make_every_period_rule(db.session, template)
            xfer = _make_transfer(
                seed_full_user_data, transfer_template_id=template.id,
            )

            with pytest.raises(
                AmountUnresolvable, match="price series is EMPTY",
            ):
                _ = resolve_transfer_amount(
                    xfer,
                    derived_amount_basis(
                        seed_full_user_data["user"].id,
                        seed_full_user_data["scenario"].id,
                    ),
                )

    def test_a_settlement_record_answers_for_a_derived_row(
        self, app, db, seed_user, seed_periods
    ):
        """A derived row that has SETTLED answers from its record, not a refusal.

        The ruling plan step X-au-c owed and this leaf makes structural: what a
        row RECORDED as having moved outranks any derivation of what it was
        expected to be, so the refusal arm sits BELOW it.  Getting the order
        wrong would refuse every settled row a per-kind cutover
        (X-au-d..X-au-i) has emptied the plan of.

        **The row it grades changed at plan step X-au-c3.**  It was a PROJECTED
        derived row carrying ``actual_amount = 412.55`` -- the shape the
        production clone had -- and the five rows in that state were promoted
        into their PLAN by migration ``e4b8a71c0f36``, because a figure now
        RECORDS a settle.  The state is not UNCONSTRUCTIBLE, and saying it was
        (of ``ck_transactions_settled_amount_needs_basis``, corrected after
        adversarial review 2026-08-17) misread that CHECK: it pairs a figure
        with its provenance and says nothing about status, so an unsettled row
        carrying BOTH is legal and is what a revert leaves behind.  Such a row
        is worth its PLAN, which is the status gate's doing.  The ORDER under test is the same one, on
        the row that can still hold both: a settled row whose plan is DERIVED
        (no ``estimated_amount``) and whose record states ``$412.55``.
        """
        with app.app_context():
            settled_on = seed_periods[0].start_date
            txn = _make_transaction(
                seed_user, seed_periods,
                status_id=ref_cache.status_id(StatusEnum.DONE),
                amount_ownership=AmountOwnership.derived(
                    ref_cache.amount_source_id(AmountSourceEnum.TEMPLATE),
                ),
                settled_on=settled_on,
            )
            db.session.add(txn)
            db.session.flush()
            # The record is the covering movement (X-bi-4b-1; the one home
            # since X-bi-4b-2): the stated $412.55, under a plan the row
            # cannot price itself.
            cover_bare_settled_row(db.session, txn, "0", submitted="412.55")

            assert settled_contribution(txn) == Decimal("412.55")

    def test_an_excluded_status_answers_zero_for_a_derived_row(
        self, app, db, seed_user, seed_periods
    ):
        """A Cancelled derived row is worth zero, not a refusal.

        The status gates stay ABOVE the refusal: a row that contributes nothing
        needs no amount resolved, so cancelling an unpriceable row is a way OUT
        of the state rather than a second error.
        """
        with app.app_context():
            txn = _make_transaction(
                seed_user, seed_periods,
                status_id=ref_cache.status_id(StatusEnum.CANCELLED),
                amount_ownership=AmountOwnership.derived(
                    ref_cache.amount_source_id(AmountSourceEnum.TEMPLATE),
                ),
            )
            db.session.add(txn)
            db.session.flush()

            assert settled_contribution(txn) == Decimal("0")

    def test_a_soft_deleted_derived_row_answers_zero(
        self, app, db, seed_user, seed_periods
    ):
        """A soft-deleted derived row is worth zero, for the same reason."""
        with app.app_context():
            txn = _make_transaction(
                seed_user, seed_periods,
                amount_ownership=AmountOwnership.derived(
                    ref_cache.amount_source_id(AmountSourceEnum.TEMPLATE),
                ),
                is_deleted=True,
            )
            db.session.add(txn)
            db.session.flush()

            assert settled_contribution(txn) == Decimal("0")
