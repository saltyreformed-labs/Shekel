"""The pass's receipt: what happened, each effect counted, and which way the money went."""

from datetime import timedelta
from decimal import Decimal

from app.enums import StatusEnum
from app.services.row_valuation import settled_figure
from app.services.statement_match import IncomeCreation, NewEnvelope, SkipRequest
from app.services.statement_match._outcome import FiledMerchant

from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_rule,
    a_transaction,
    an_import,
    an_envelope,
    the_merchant_id,
)

from ._helpers import _batch, _match, _creation


class TestTheReceiptSaysWhatHappened:
    """A pass that reports only "done" is a pass nobody can check."""

    def test_it_counts_each_EFFECT_separately(self, app, db, seed_user):
        """Settling, re-dating and recording are three different acts.

        A single "3 items applied" would hide which: settling books money the
        projection was holding forward, moving a day moves money already
        booked, and recording adds a movement the app did not have.
        """
        with app.app_context():
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date
            settle_line = a_bank_line(
                seed_user, statement, amount="-180.00", posted_on=day,
            )
            projected = a_transaction(
                seed_user, name="Electricity", amount="180.00",
            )
            correct_line = a_bank_line(
                seed_user, statement, amount="-42.00", posted_on=day,
                sequence_in_group=1,
            )
            settled = a_transaction(
                seed_user, name="Water", amount="42.00",
                status=StatusEnum.DONE, settled_on=day + timedelta(days=2),
            )
            swipe = a_bank_line(
                seed_user, statement, amount="-9.99", posted_on=day,
                sequence_in_group=2,
            )

            outcome = _batch(
                seed_user,
                matches=[
                    _match(
                        seed_user, lines=[settle_line],
                        transactions=[projected],
                    ),
                    _match(
                        seed_user, lines=[correct_line], transactions=[settled],
                    ),
                ],
                creations=[_creation(
                    seed_user, swipe,
                    new_envelope=NewEnvelope(
                        name="Amazon",
                        category_id=seed_user["categories"]["Groceries"].id,
                    ),
                )],
            )

            assert outcome.applied_count == 3
            assert outcome.settled_count == 1
            assert outcome.corrected_count == 1
            assert outcome.recorded_count == 1
            assert outcome.envelopes_created == 1
            assert outcome.moved_nothing is False

    def test_only_the_CREATE_arm_names_the_merchant_it_filed_for(
        self, app, db, seed_user,
    ):
        """``AppliedItem.merchant`` is the create door's fact and nobody else's.

        Plan step ``bank_import:X-gx``, finding **BI-495**: the receipt's
        standing-rule offer is a filter over the items that name a merchant,
        so a match, an income or a skip that named one would be asked about
        as spending.  Four arms, one merchant-bearing creation, one
        merchant-less creation: exactly one item carries a
        :class:`FiledMerchant`, and it is the line's own merchant as the door
        read it.  FIRING CONTROL: pass ``merchant=None`` from the creations
        arm and the named line's item reads ``None``.
        """
        with app.app_context():
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date
            matched_line = a_bank_line(
                seed_user, statement, amount="-180.00", posted_on=day,
                merchant="Duke Energy",
            )
            projected = a_transaction(
                seed_user, name="Electricity", amount="180.00",
            )
            named = a_bank_line(
                seed_user, statement, amount="-9.99", posted_on=day,
                sequence_in_group=1, merchant="Amazon",
            )
            nameless = a_bank_line(
                seed_user, statement, amount="-4.50", posted_on=day,
                sequence_in_group=2,
            )
            deposit = a_bank_line(
                seed_user, statement, amount="0.15", posted_on=day,
                sequence_in_group=3, merchant="Dividend Earned",
                description="DIVIDEND EARNED (Dividend Earned)",
            )
            to_skip = a_bank_line(
                seed_user, statement, amount="-1.00", posted_on=day,
                sequence_in_group=4, merchant="Walmart",
            )
            envelope = an_envelope(seed_user)
            db.session.commit()

            outcome = _batch(
                seed_user,
                matches=[_match(
                    seed_user, lines=[matched_line], transactions=[projected],
                )],
                creations=[
                    _creation(seed_user, named, transaction_id=envelope.id),
                    _creation(seed_user, nameless, transaction_id=envelope.id),
                ],
                incomes=[IncomeCreation(line_id=deposit.id)],
                skips=[SkipRequest(line_id=to_skip.id)],
            )

            assert outcome.applied_count == 5, outcome.refused
            by_line = {item.line_ids: item.merchant for item in outcome.applied}
            assert by_line == {
                (matched_line.id,): None,
                (named.id,): FiledMerchant(
                    merchant_id=the_merchant_id(seed_user, "Amazon"),
                    name="Amazon",
                ),
                (nameless.id,): None,
                (deposit.id,): None,
                (to_skip.id,): None,
            }

    def test_a_pass_that_only_CONFIRMS_says_it_moved_nothing(
        self, app, db, seed_user,
    ):
        """An applied item is not the same as a changed record.

        A match on a row already carrying the bank's own day writes no column,
        so a receipt counting applied items would claim work that did not
        happen -- the distinction ``AcceptedMatch`` already draws for one act.
        """
        with app.app_context():
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date
            line = a_bank_line(
                seed_user, statement, amount="-180.00", posted_on=day,
            )
            row = a_transaction(
                seed_user, name="Electricity", amount="180.00",
                status=StatusEnum.DONE, settled_on=day,
            )

            outcome = _batch(seed_user, matches=[
                _match(seed_user, lines=[line], transactions=[row]),
            ])

            assert outcome.applied_count == 1
            assert outcome.moved_nothing is True

    def test_a_pass_that_only_REPRICES_does_NOT_say_it_moved_nothing(
        self, app, db, seed_user,
    ):
        """The receipt was not merely silent about an amount -- it was FALSE.

        Plan step **bank_import:X-f6d-1**, found by adversarial design review
        2026-08-22.  ``_apply_day`` decides its outcome on the DAY, so a
        repricing whose row ALREADY carries the bank's day reports
        ``"unchanged"`` and lands in neither the settled nor the corrected
        tally.  With no count of its own, this pass rewrote what a payment cost
        and the panel rendered *"Nothing moved. Everything that was applied
        confirmed a day you already had."*

        The fixture is the case above with ONE difference -- the figures
        disagree by four cents -- which is what makes it a control on the
        amount rather than on anything else.
        """
        with app.app_context():
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date
            line = a_bank_line(
                seed_user, statement, amount="-180.04", posted_on=day,
                merchant="Duke",
            )
            row = a_transaction(
                seed_user, name="Duke Energy", amount="180.00",
                status=StatusEnum.DONE, settled_on=day,
            )

            # -180.04 bank against a -180.00 row is -0.04, and every match
            # carries the difference it was reviewed against since plan step
            # bank_import:X-gj-1b.
            outcome = _batch(seed_user, matches=[
                _match(
                    seed_user, lines=[line], transactions=[row],
                    residual="-0.04",
                ),
            ])

            assert outcome.applied_count == 1
            assert outcome.settled_count == 0
            assert outcome.corrected_count == 0
            assert outcome.redated_count == 0
            assert outcome.repriced_count == 1
            assert outcome.moved_nothing is False
            assert "corrected the amount on 1 row(s)" in (
                outcome.applied[0].summary
            )
            db.session.expire_all()
            assert settled_figure(row) == Decimal("180.04")

    def test_an_EMPTY_pass_is_not_an_error(self, app, db, seed_user):
        """Ticking nothing and pressing Apply is an ordinary thing to do."""
        with app.app_context():
            outcome = _batch(seed_user)

            assert outcome.applied_count == 0
            assert outcome.refused_count == 0
            assert outcome.moved_nothing is True


class TestTheReceiptSaysWhichWayTheMoneyWENT:
    """A refund's receipt must not say the bank TOOK it.

    Plan step ``bank_import:X-gj-2b``, ruling **R-II**.  Under ruling **R-GH**
    the pass receipt is the ONLY notice the owner gets for money an act moved,
    so a sentence that inverts the direction is the mis-describing-text-over-a-
    real-movement shape this package has already paid for once.

    **Measured wrong before the fix**: ``Recorded $-42.00 your bank took on
    ...`` -- the wrong SIGN and the wrong VERB in one sentence, and it
    contradicted ``AppliedItem.amount`` on the same item, which negates onto
    the bank's convention. Nothing caught it because ``_story()`` in
    ``test_filing.py`` captures counts and reasons and never the summary.

    **Both directions, because the verb is derived from the sign** -- a fix
    that hard-coded *gave back* would pass a refund-only case.
    """

    def _applied(self, seed_user, line, envelope):
        """Apply one creation and return its single AppliedItem."""
        outcome = _batch(seed_user, creations=[
            _creation(seed_user, line, transaction_id=envelope.id),
        ])
        assert outcome.applied_count == 1, outcome.refused
        return outcome.applied[0]

    def test_a_REFUND_reads_as_money_the_bank_GAVE_BACK(
        self, app, db, seed_user,
    ):
        """The line the merchant credit arrives on."""
        with app.app_context():
            envelope = a_transaction(
                seed_user, name="Amazon", amount="0.00", is_envelope=True,
            )
            a_rule(seed_user, "Amazon", template_id=envelope.template_id)
            line = a_bank_line(
                seed_user, an_import(seed_user), amount="42.00",
                merchant="Amazon",
                posted_on=seed_user["bootstrap_period"].start_date,
            )
            db.session.commit()

            item = self._applied(seed_user, line, envelope)

            assert "gave back" in item.summary
            assert "took" not in item.summary
            # The FIGURE agrees with the verb, and with the bank's own sign
            # on the same item.
            assert "$42.00" in item.summary
            assert "$-42.00" not in item.summary
            assert item.amount == Decimal("42.00")

    def test_an_ORDINARY_swipe_still_reads_as_money_the_bank_TOOK(
        self, app, db, seed_user,
    ):
        """The control, so the verb is derived and not hard-coded."""
        with app.app_context():
            envelope = a_transaction(
                seed_user, name="Groceries", amount="60.00", is_envelope=True,
            )
            line = a_bank_line(
                seed_user, an_import(seed_user), amount="-10.89",
                posted_on=seed_user["bootstrap_period"].start_date,
            )
            db.session.commit()

            item = self._applied(seed_user, line, envelope)

            assert "took" in item.summary
            assert "gave back" not in item.summary
            assert "$10.89" in item.summary
            assert item.amount == Decimal("-10.89")
