"""The new-envelope arm: a line that fits no envelope gets one, closed from its own purchases and
filed in the period of the day it was made; and the name a recorded purchase takes.
"""

from datetime import timedelta
from decimal import Decimal

from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import status_seam
from app.services.statement_match import NewEnvelope
from tests._test_helpers import last_covered_day, resolved_amount

from tests.test_services.test_statement_match._builders import a_bank_line, an_import

from ._helpers import _closed_from_purchases, _record


class TestTheNewEnvelopeArm:
    """A line that fits no envelope gets one, rather than going unrecorded.

    Measured on the developer's own data: the 2026-03-26 period holds three
    envelopes and all three closed at a fixed figure, so 8 lines worth
    `$662.13` have no existing destination at all -- and several merchants
    (Lowe's, a parks fee, two subscription services) have no envelope in any
    period.
    """

    def test_it_creates_an_ENVELOPE_and_puts_the_purchase_in_it(
        self, app, db, seed_user,
    ):
        """An envelope, never a bare row.

        A plain transaction would be a budget line that is also its own single
        payment, so the next statement line for the same merchant would have
        nowhere to go.
        """
        with app.app_context():
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date + timedelta(days=5)
            line = a_bank_line(
                seed_user, statement, amount="-31.41", posted_on=day,
                description="POINT OF SALE DEBIT L340 LOWES #00907 (Lowe's)",
            )

            recorded = _record(seed_user, line, new_envelope=NewEnvelope(
                name="Lowe's",
                category_id=seed_user["categories"]["Groceries"].id,
            ))
            db.session.flush()

            envelope = db.session.get(Transaction, recorded.transaction_id)
            assert envelope.tracks_purchases
            assert envelope.name == "Lowe's"
            # A placed row of a rule-less DEFINITION since leaf 7b-3 of
            # balance:X-bi-7b (ruling R-BAL24): it states no figure of its
            # own and is priced by the definition's one version, $0.00 --
            # nothing budgeted it (``_container._NO_BUDGET``).  It carried
            # OWN $0.00 as a link-less row until then.
            assert envelope.is_placed is True
            assert envelope.template.is_envelope is True
            assert envelope.template.recurs is False
            assert envelope.estimated_amount is None
            assert resolved_amount(envelope) == Decimal("0.00")
            assert recorded.template_id == envelope.template_id
            assert recorded.envelope_created is True
            assert [entry.id for entry in envelope.entries] == [
                recorded.entry_id,
            ]

    def test_the_new_envelope_is_CLOSED_from_its_purchases(
        self, app, db, seed_user,
    ):
        """Left Projected it would be carry-forward BAIT.

        ``carry_forward_unpaid`` moves every projected row out of a source
        period and settles it, so an envelope budgeting `$0.00` against a
        purchase already spent would roll a NEGATIVE leftover into a later
        period.  Closing it from its own entries records the figure the bank
        stated and reserves nothing.
        """
        with app.app_context():
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date + timedelta(days=5)
            line = a_bank_line(seed_user, statement, amount="-31.41",
                               posted_on=day)

            recorded = _record(seed_user, line, new_envelope=NewEnvelope(
                name="Lowe's",
                category_id=seed_user["categories"]["Groceries"].id,
            ))
            db.session.flush()

            envelope = db.session.get(Transaction, recorded.transaction_id)
            assert envelope.status.is_settled
            assert envelope.settled_on == day
            assert status_seam.recorded_settlement(envelope) == status_seam.Settlement(
                None, None,
            )

    def test_it_lands_in_the_period_of_the_day_it_was_MADE(
        self, app, db, seed_user,
    ):
        """The budget clock places the row, not the clearing day.

        A swipe made on one period's last day and posted on the next period's
        first belongs to the budget it was made under -- and filing it by the
        posting day would raise the entry list's out-of-period warning
        (``entry_service.entry_list_view``, which asks
        ``DerivedPeriod.covers``) on a row this door had just built.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            statement = an_import(seed_user)
            line = a_bank_line(
                seed_user, statement, amount="-31.41",
                posted_on=last_covered_day(period) + timedelta(days=1),
                transaction_on=last_covered_day(period),
            )

            recorded = _record(seed_user, line, new_envelope=NewEnvelope(
                name="Lowe's",
                category_id=seed_user["categories"]["Groceries"].id,
            ))
            db.session.flush()

            envelope = db.session.get(Transaction, recorded.transaction_id)
            assert envelope.pay_period_id == period.id

    def test_the_purchase_is_named_for_the_MERCHANT(
        self, app, db, seed_user,
    ):
        """The merchant the bank NAMED, not its whole line.

        The app's own purchases are called "Walmart" and "Food Lion"; a row
        called ``POINT OF SALE DEBIT L340 DATE 08-13 Amazon.com*5H2RA5V...``
        would be the only one in the entries list nobody can read.  The bank's
        full wording is not lost -- it stays on the statement line, which the
        match ties to this purchase.

        The merchant is the RECORDED column since plan step X-f6a-3d, not a
        parse of the description: what reads it here is
        :func:`~app.services.statement_match.merchant_label`, and what fills it
        is the adapter (graded in ``test_secu_csv``).
        """
        with app.app_context():
            envelope = _closed_from_purchases(seed_user)
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date + timedelta(days=5)
            line = a_bank_line(
                seed_user, statement, amount="-57.96", posted_on=day,
                description=(
                    "POINT OF SALE DEBIT L340 DATE 08-13 "
                    "Amazon.com*5H2RA5VAmzn.com/bil (Amazon)"
                ),
                merchant="Amazon",
            )

            recorded = _record(seed_user, line, transaction_id=envelope.id)
            db.session.flush()

            entry = db.session.get(TransactionEntry, recorded.entry_id)
            assert entry.description == "Amazon"

    def test_a_line_naming_no_merchant_is_named_by_its_DESCRIPTION(
        self, app, db, seed_user,
    ):
        """The LABEL is total even though the key is not.

        A source that names no merchant records ``NULL``, which keys no
        destination policy -- but ``transaction_entries.description`` is NOT
        NULL and this door calls ``create_entry`` directly, so the name has to
        come from somewhere.  Falling back to the description is the honest
        answer, and it is why the label and the key are different readers:
        SECU's OFX truncates 326 of 361 descriptions to the same 32
        characters, which is a usable name and a ruinous key.
        """
        with app.app_context():
            envelope = _closed_from_purchases(seed_user)
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date + timedelta(days=5)
            line = a_bank_line(seed_user, statement, amount="-57.96",
                               posted_on=day, description="ACH DEBIT GEICO",
                               merchant=None)

            recorded = _record(seed_user, line, transaction_id=envelope.id)
            db.session.flush()

            entry = db.session.get(TransactionEntry, recorded.entry_id)
            assert entry.description == "ACH DEBIT GEICO"
