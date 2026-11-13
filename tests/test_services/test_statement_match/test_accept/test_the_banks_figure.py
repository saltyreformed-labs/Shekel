"""The bank's figure is the record: a one-to-one match and a settled purchase take it, and what a
one-to-one match still refuses.
"""

from datetime import timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import MovementFigureSourceEnum, SettledDayBasisEnum, StatusEnum, TxnTypeEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.transaction import Transaction
from app.services import entry_service, status_seam, transaction_service
from app.services.row_valuation import settled_figure

from tests._test_helpers import an_entered_day, observed, typed

from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_purchase,
    a_transaction,
    an_import,
)
from app.services.settle_day import SettleDay
from app.models.amount_ownership import AmountOwnership

from ._helpers import _posted_cash_by_day, _submit


class TestAOneToOneMatchTakesTheBanksFigure:
    """Ruling **R-GD(a)** and finding **N-335**: the bank's figure IS the record.

    **The defect these exist for, measured on the developer's own dev database
    2026-08-22.**  Bank line 285 (`ACH DEBIT GEICO PREM COLL`, `-178.29`,
    posted 07-02) sat THREE CENTS from transaction 2461 (`178.32`, settled
    07-06).  The exact-amount predicate offered nothing, the accept door would
    have refused it anyway, and the cheapest act the review screen had left was
    to record the line as a NEW purchase -- so the ledger booked `-178.29` on
    07-02 AND `-178.32` on 07-06, `$356.61` for one `$178.29` movement.

    A refusal is not neutral when the screen beside it offers to duplicate.

    **A case left this class at plan step ``balance:X-bi-4a``**:
    ``test_a_row_carrying_a_CARD_purchase_corrects_its_GROSS`` built a
    ``derived`` bill holding a card purchase and graded that the bank's
    figure corrected the row's GROSS with the card purchase still subtracted.
    Ruling **R-BAL78** makes a stated figure beside purchases unrepresentable
    (a row holding purchases records its money AS them; the settle verb and
    the seam refuse a figure over them, and the entry doors refuse a purchase
    on a stored-figure row -- ``test_entry_service`` grades both), so the
    row that case corrected cannot exist and the case had no subject left.
    What a card purchase does to a row's cash leg is the card arc's
    (``credit_card:CC-5``), on the card's own account.
    """

    @staticmethod
    def _geico(seed_user, statement, bank_day, app_day):
        """Stage the developer's own case: a bill settled three cents off."""
        txn = a_transaction(
            seed_user, name="Geico", amount="178.32",
            status=StatusEnum.DONE, settled_on=app_day,
        )
        line = a_bank_line(
            seed_user, statement, amount="-178.29", posted_on=bank_day,
        )
        return txn, line

    def test_the_three_cents_is_a_CORRECTION_not_a_refusal(
        self, app, db, seed_user,
    ):
        """The whole finding, as one accepted match."""
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        txn, line = self._geico(
            seed_user, statement, bank_day, bank_day + timedelta(days=4),
        )

        # Stating 0.03 is what -178.29 bank against a -178.32 row comes to, and every
        # match carries the difference it was reviewed against since
        # plan step bank_import:X-gj-1b -- the near tier's own card
        # renders it as a hidden field (the stated_difference filter).
        accepted = _submit(
            seed_user, lines=[line], transactions=[txn], residual="0.03",
        )

        assert accepted.match_id is not None, "the match must be RECORDED"
        assert settled_figure(txn) == Decimal("178.29"), (
            "the row must book what the BANK took, not what the app guessed"
        )
        assert txn.settled_on == bank_day

    def test_the_correction_says_it_is_one(self, app, db, seed_user):
        """A corrected figure is stored as STATED, not as resolved.

        The movement's source is what makes *did the bank disagree with this
        row* a stored answer instead of one re-derived by comparing against a
        recomputation that may since have moved.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        txn, line = self._geico(
            seed_user, statement, bank_day, bank_day + timedelta(days=4),
        )

        # Stating 0.03 is what -178.29 bank against a -178.32 row comes to, and every
        # match carries the difference it was reviewed against since
        # plan step bank_import:X-gj-1b -- the near tier's own card
        # renders it as a hidden field (the stated_difference filter).
        _submit(
            seed_user, lines=[line], transactions=[txn], residual="0.03",
        )

        assert status_seam.recorded_settlement(txn).stated
        # And WHO wrote it: the matcher's transaction arm is the one door
        # that states a figure as the BANK's (plan step X-bi-3e-1, ruling
        # R-BAL61), graded end to end on the row's covering movement --
        # ``typed`` would also read as stated, so the reading alone cannot
        # tell the two writers apart.
        (movement,) = txn.covering_movements
        assert movement.amount == Decimal("178.29")
        assert movement.figure_source_id == ref_cache.movement_figure_source_id(
            MovementFigureSourceEnum.OBSERVED,
        )

    def test_a_settled_PAYCHECK_five_cents_off_is_corrected_the_same_way(
        self, app, db, seed_user,
    ):
        """The income arm of finding BAL-523, on the developer's own payroll line.

        A settled paycheck is worth its covering movement in its own direction
        (``+figure``, ruling **R-BAL81**), and the bank's figure corrects it
        exactly as it corrects a bill: the record and the movement both read
        the bank's `$2,473.43`.  Through plan step ``balance:X-bi-4a``'s
        first cut ``_landing.corrected_figure`` added ``off_statement_sum``,
        whose posted-purchase term walked the FAMILY and summed the paycheck's
        own dated covering movement as a purchase that had posted, so the
        correction booked `2,473.43 + 2,473.38 = 4,946.81` onto the movement
        and the post-apply check refused the act -- production's payroll
        line 259 against rows 1625 + 787, 2026-09-18 23:15 UTC, in the
        observer's record.  The bill cases above cover the expense arm; this
        is the control for the income one, and it is RED on the walk over
        ``entries``.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        paycheck = a_transaction(
            seed_user, name="Payroll", amount="2473.38", income=True,
            status=StatusEnum.RECEIVED, settled_on=bank_day,
        )
        [movement] = paycheck.covering_movements
        assert movement.amount == Decimal("2473.38")
        line = a_bank_line(
            seed_user, statement, amount="2473.43", posted_on=bank_day,
        )

        accepted = _submit(
            seed_user, lines=[line], transactions=[paycheck], residual="0.05",
        )

        assert accepted.match_id is not None
        assert status_seam.recorded_settlement(paycheck) == status_seam.Settlement(
            Decimal("2473.43"), MovementFigureSourceEnum.OBSERVED,
        )
        assert [m.amount for m in paycheck.covering_movements] == [
            Decimal("2473.43"),
        ]
        assert status_seam.covered_cash_leg(paycheck, paycheck.account_id) == Decimal("2473.43")

    def test_an_AGREEING_match_writes_no_correction(self, app, db, seed_user):
        """The control, and it is what makes the test above mean anything.

        A row the bank agrees with must keep its DERIVED basis: if every match
        wrote ``corrected``, the basis would say nothing and the assertion
        above would pass for the wrong reason.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        txn = a_transaction(
            seed_user, name="Geico", amount="178.32",
            status=StatusEnum.DONE, settled_on=bank_day,
        )
        line = a_bank_line(
            seed_user, statement, amount="-178.32", posted_on=bank_day,
        )

        _submit(seed_user, lines=[line], transactions=[txn])

        assert status_seam.recorded_settlement(txn) == status_seam.Settlement(
            Decimal("178.32"), MovementFigureSourceEnum.RESOLVED,
        )

    def test_the_ACCOUNT_reads_the_banks_figure_and_not_both(
        self, app, db, seed_user,
    ):
        """The money assertion: one movement, booked once, at the bank's figure.

        This is the property finding **N-335** measures the loss of -- the app
        held `$356.61` against a `$178.29` payment because the line became a
        second row instead of correcting the first.

        **It asserts the POSTED LEG rather than the balance, and that is not
        laziness.**  The bank day here is the anchor's own day, and until the
        cutover (`balance:X-f3c`) an assertion RESETS the ledger to the figure
        it names -- so `balance_at` reads `1000.00` either side of this match
        whatever the row books, and a balance assertion would grade the anchor
        instead of the correction.  What the ledger posted for the row is the
        fact this test is about, and it is exact.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        txn, line = self._geico(
            seed_user, statement, bank_day, bank_day + timedelta(days=4),
        )
        # Stating 0.03 is what -178.29 bank against a -178.32 row comes to, and every
        # match carries the difference it was reviewed against since
        # plan step bank_import:X-gj-1b -- the near tier's own card
        # renders it as a hidden field (the stated_difference filter).
        _submit(
            seed_user, lines=[line], transactions=[txn], residual="0.03",
        )
        db.session.flush()

        posted = _posted_cash_by_day(db, txn, seed_user["account"])
        assert sum(posted.values()) == Decimal("-178.29"), (
            "the row must book what the BANK took, exactly once -- the whole "
            "of what N-335 measures the loss of"
        )


class TestWhatAOneToOneMatchSTILLRefuses:
    """The four indeterminacies R-GD(a) did NOT dissolve.

    Each is a FIRING CONTROL: written to fail if its clause were deleted, which
    is what `docs/plans/verification.md` asks of a refusal on a money door.
    """

    def test_a_SIGN_disagreement_is_refused(self, app, db, seed_user):
        """Money leaving is not money arriving, whatever the magnitudes do.

        The old sum test caught this by accident; with the sum test gone it
        needs its own clause, or a `+180.00` deposit would silently "correct" a
        `-180.00` bill.
        """
        statement = an_import(seed_user)
        txn = a_transaction(seed_user, name="Electricity", amount="180.00")
        line = a_bank_line(seed_user, statement, amount="180.00")

        with pytest.raises(ValidationError, match="not the same movement"):
            _submit(seed_user, lines=[line], transactions=[txn])

        assert txn.settled_on is None

    def test_an_ENVELOPE_is_refused(self, app, db, seed_user):
        """Its figure IS its purchases, so it is not offered at all.

        Through plan step ``balance:X-bi-4a``'s first cut the row reached
        ``_reject_uncorrectable_row``'s "no figure of its own" clause; under
        ruling **R-BAL81** a row that settles from its purchases is worth
        ``0`` and is not a candidate, so it is refused as unavailable one
        refusal earlier.  That clause's remaining subject is the CC payback,
        the next case.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        a_purchase(
            seed_user, envelope, amount="25.00", purchased_on=bank_day,
        )
        db.session.flush()
        line = a_bank_line(
            seed_user, statement, amount="-30.00", posted_on=bank_day,
        )

        with pytest.raises(ValidationError, match="no longer available"):
            _submit(seed_user, lines=[line], transactions=[envelope])

        assert envelope.settled_on is None

    def test_a_CC_PAYBACK_is_refused(self, app, db, seed_user):
        """Finding **N-252**'s class, and the one a first draft MISSED.

        A payback's figure is a fact about the row it repays, and
        ``entry_credit_workflow.sync_entry_payback`` re-states it on every entry
        mutation -- so a ``corrected`` record written here is silently reverted
        by the next sibling write.  The transaction door's own backstop refuses
        only the ENVELOPE half of this class (the payback is refused at the
        PATCH route instead), so without this clause the correction reaches the
        column.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        payback = Transaction(
            account_id=seed_user["account"].id,
            user_id=seed_user['bootstrap_period'].user_id,
            pay_period_id=seed_user["bootstrap_period"].id,
            scenario_id=seed_user["scenario"].id,
            status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            name="CC Payback: Groceries",
            category_id=seed_user["categories"]["Groceries"].id,
            transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
            amount_ownership=AmountOwnership.own(Decimal("60.00")),
            credit_payback_for_id=envelope.id,
        )
        db.session.add(payback)
        db.session.flush()
        line = a_bank_line(
            seed_user, statement, amount="-55.00", posted_on=bank_day,
        )

        with pytest.raises(ValidationError, match="no figure of its own"):
            _submit(seed_user, lines=[line], transactions=[payback])

        assert payback.settled_on is None
        assert status_seam.recorded_settlement(payback) is None


class TestASettledPurchaseTakesTheBanksFigure:
    """Ruling **R-GE** (2026-08-22): the same evidence R-FX already accepted.

    ``entry_service._reject_settled_parent`` refuses a purchase's ``amount``
    under a settled parent -- finding **N-229**, widened to the settled BAND at
    `balance:X-au-c3` -- because re-pricing a row whose money has moved would
    move it again.  That reason holds for the act it was written about: a human
    typing a different number on their own second thoughts.

    **A bank line is not that act**, which is the argument R-FX accepted one
    door over when it ruled that the same evidence justifies ADDING a purchase
    to a settled row.  2 of the developer's own 10 near misses are exactly this
    -- `Groceries: Walmart`, `$121.12` against the bank's `$121.16`.

    **The BOUND is the door, not the row**, and the second test is its firing
    control: the permission rides on the figure's own stated SOURCE -- the
    matcher states ``observed`` with the figure (plan step X-bi-3e-1, ruling
    **R-BAL69**; it rode on the settle day's basis until then) -- so a caller
    without a statement cannot reach it.
    """

    @staticmethod
    def _settled_envelope_with_a_purchase(seed_user, day):
        """Stage an envelope CLOSED FROM one purchase the bank will correct.

        Through the verb, so the close is a ``purchases`` record with no
        covering movement (ruling **R-BAL78**: a stated figure beside
        purchases is unrepresentable) -- the state the developer's two
        near misses are in.  Laid on bare with ``settled_on`` this was a
        ``derived`` close holding a purchase, and once ``a_transaction``
        covered its settled rows, a mirror beside the purchase as well.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", amount="121.12", is_envelope=True,
        )
        purchase = a_purchase(
            seed_user, envelope, amount="121.12", description="Walmart",
            purchased_on=day,
        )
        transaction_service.settle_transaction(
            envelope, settle_day=an_entered_day(day),
        )
        db.session.flush()
        assert envelope.covering_movements == []
        return envelope, purchase

    def test_the_purchase_is_RECOSTED_from_a_match(self, app, db, seed_user):
        """The developer's own Walmart case, four cents out."""
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date
        _, purchase = self._settled_envelope_with_a_purchase(seed_user, day)
        line = a_bank_line(
            seed_user, statement, amount="-121.16", posted_on=day,
        )

        # Stating -0.04 is what -121.16 bank against a -121.12 purchase comes to, and every
        # match carries the difference it was reviewed against since
        # plan step bank_import:X-gj-1b -- the near tier's own card
        # renders it as a hidden field (the stated_difference filter).
        _submit(
            seed_user, lines=[line], entries=[purchase], residual="-0.04",
        )

        assert purchase.amount == Decimal("121.16"), (
            "the purchase must take the figure the BANK showed"
        )
        assert purchase.settled_on == day

    def test_the_HAND_EDIT_door_still_refuses(self, app, db, seed_user):
        """R-GE's bound, as a firing control.

        Delete the source test in ``entry_service._refusals.cost_fields_changing``
        and this passes -- which is what makes it worth writing.  The
        permission must be reachable ONLY with a figure the bank's line
        stated; an owner typing into the popover still meets N-229's refusal,
        unchanged.
        """
        day = seed_user["bootstrap_period"].start_date
        _, purchase = self._settled_envelope_with_a_purchase(seed_user, day)

        with pytest.raises(ValidationError):
            entry_service.update_entry(
                purchase.id, seed_user["user"].id, figure=typed(Decimal("121.16")),
            )

        assert purchase.amount == Decimal("121.12")

    def test_an_ENTERED_day_does_not_buy_the_permission(
        self, app, db, seed_user,
    ):
        """The narrower control: it is the figure's SOURCE that permits.

        A caller submitting a settle day beside the figure must not inherit the
        permission just for having submitted one -- only a figure the bank's
        line stated carries the evidence, and ``typed`` is what every hand
        door states.
        """
        day = seed_user["bootstrap_period"].start_date
        _, purchase = self._settled_envelope_with_a_purchase(seed_user, day)

        with pytest.raises(ValidationError):
            entry_service.update_entry(
                purchase.id, seed_user["user"].id,
                figure=typed(Decimal("121.16")), settle_day=an_entered_day(day),
            )

        assert purchase.amount == Decimal("121.12")

    def test_an_OBSERVED_day_beside_a_typed_figure_does_not_buy_it_either(
        self, app, db, seed_user,
    ):
        """The direction the re-spelling changed (plan step X-bi-3e-1).

        Until this step the permission was read off the DAY: an ``observed``
        settle day in the call released ``amount`` whoever wrote the figure.
        The figure states its own writer now (ruling **R-BAL69**), and a
        person's figure beside the bank's day is still a person's -- the
        BAL-508 shape, and the one a day-based predicate lets through.  Put
        the predicate back on the day and this passes.
        """
        day = seed_user["bootstrap_period"].start_date
        _, purchase = self._settled_envelope_with_a_purchase(seed_user, day)

        with pytest.raises(ValidationError):
            entry_service.update_entry(
                purchase.id, seed_user["user"].id,
                figure=typed(Decimal("121.16")),
                settle_day=SettleDay(
                    day=day, basis=SettledDayBasisEnum.OBSERVED,
                ),
            )

        assert purchase.amount == Decimal("121.12")

    def test_the_banks_figure_alone_buys_it(self, app, db, seed_user):
        """The accepting arm, at the door the matcher reaches.

        Without it the two refusals above prove nothing about the release:
        a figure the bank's line stated re-costs the settled purchase, which
        is exactly R-GE's rule with the evidence read off the figure.
        """
        day = seed_user["bootstrap_period"].start_date
        _, purchase = self._settled_envelope_with_a_purchase(seed_user, day)

        # The figure ALONE: no settle day in the call, so the old day-based
        # predicate would refuse this and the source-based one admits it.
        entry_service.update_entry(
            purchase.id, seed_user["user"].id,
            figure=observed(Decimal("121.16")),
        )

        assert purchase.amount == Decimal("121.16")
        assert purchase.figure_source_id == ref_cache.movement_figure_source_id(
            MovementFigureSourceEnum.OBSERVED,
        )
