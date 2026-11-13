"""The CREATE door: a bank line the app has no row for BECOMES a purchase.

Plan step **bank_import:X-f6a-3b**, ruling **R-FS**'s third shape.  **It MOVES
MONEY, and differently from its sibling**: accepting a match re-dates a movement
the app already held, and recording a line here ADDS one it did not have at all.

Measured on the developer's own 2026-08-16 statement against a 2026-08-18
production clone: after every proposal the matcher offers, **91 unmatched
outflows** remain that no match can ever explain, 74 of them card swipes worth
`$3,383.49` -- the app records a
period's groceries as one envelope and the bank records every swipe.

**What the door promises, and what each class below pins:**

* the account's recorded outflow GROWS by exactly what the bank took;
* the purchase is born carrying BOTH of the bank's days (**R-FW**);
* the line stops being unexplained, through the SAME match door every other
  correspondence goes through (**R-FT**, **R-FV**);
* the destination set the screen offers is the set the door will accept -- an
  envelope it does not offer is refused, not silently accepted;
* a new envelope is CLOSED from its own purchases, because a projected row in a
  past period is carry-forward bait.

Every refusal below is a FIRING CONTROL, written to fail if the refusal were
deleted.
"""

from datetime import timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import MovementFigureSourceEnum, StatusEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.category import Category
from app.models.statement_match import StatementMatch
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import (
    balance_at,
    pay_calendar,
    statement_match,
    status_seam,
    transaction_service,
)
from app.services.balance_at import BalanceContext
from app.services.statement_match import NewEnvelope, PurchaseCreation

# Pylint: protected-access -- MintedEnvelopes is an internal collaboration
# between two PRIVATE modules of this package and has no importer outside
# it, so exporting it would be the surface rule 13 forbids; a test for a
# module reaches into it, which is the allowance every sibling here takes.
from app.services.statement_match import _create  # pylint: disable=protected-access
from tests._test_helpers import (
    eras_of,
    last_covered_day,
    open_books_before_the_first_assertion,
    resolved_amount,
    typed,
)

from ._builders import (
    a_bank_line,
    a_basis,
    a_later_period,
    a_payday_on,
    a_purchase,
    a_rule,
    a_scope,
    a_transaction,
    accepted_acts,
    an_answers,
    an_import,
)


def _closed_from_purchases(seed_user, *, name="Groceries", amount="500.00",
                           purchase="120.00", period=None):
    """Return an envelope CLOSED at the sum of its own purchases.

    The state 29 of the developer's 37 closed envelopes are in, and the only
    settled state a new purchase may be added to: a ``purchases`` settlement
    stores no figure, so the row's cost IS its entries and a new one raises it
    by exactly its own amount.

    Args:
        seed_user: The seeded user bundle.
        name: The envelope's name.
        amount: Its budget.
        purchase: The one purchase it closes at.
        period: The pay period to file it under.

    Returns:
        The settled :class:`~app.models.transaction.Transaction`.
    """
    envelope = a_transaction(
        seed_user, name=name, amount=amount, is_envelope=True, period=period,
    )
    a_purchase(seed_user, envelope, amount=purchase, description="Walmart")
    transaction_service.settle_from_entries(envelope)
    db.session.flush()
    return envelope


def _offerable(seed_user):
    """Return the destinations the screen may offer RIGHT NOW.

    Two producers rather than one since plan step **X-f6a-3c-2**, and the split
    is the point:
    :func:`~app.services.statement_match.destinations_for` answers what the
    account COULD offer, which does not change while a review pass runs, and
    :func:`~app.services.statement_match.current_destinations` re-asks each
    row as it stands now, which is exactly what the pass changes.  A screen
    and a write door both narrow the first by the second -- which is what stops
    a shared, once-derived offer set handing a pass's fourth item an envelope
    its third has just settled.

    Args:
        seed_user: The seeded user bundle.

    Returns:
        The offerable :class:`~app.services.statement_match.PurchaseDestination`
        values.
    """
    return statement_match.current_destinations(
        statement_match.destinations_for(
            seed_user["account"].id,
            pay_calendar.calendar_for(seed_user["user"].id),
        ),
    )


def _balance_on(seed_user, day):
    """Return the checking account's balance as of *day*."""
    ctx = BalanceContext(
        user_id=seed_user["user"].id,
        scenario=seed_user["scenario"], as_of=day,
    )
    return balance_at.balance_at(seed_user["account"], ctx, day)


def _record(seed_user, line, minted=None, applied_by_rule=False, **destination):
    """Record *line* as a purchase, into whichever destination is named.

    Args:
        seed_user: The seeded user bundle.
        line: The recorded bank line to record.
        minted: What this REQUEST has already created.  ``None`` gives each
            call its OWN empty registry, which is the HAND path -- one line,
            one request -- and is what every case here means unless it says
            otherwise.  A case about a SWEEP passes one registry to several
            calls, because that is what one press is.
        applied_by_rule: Which consent this act had (ruling **R-GT**).
            **Defaulted here and NOT at the door**, which is the split the door
            itself argues for: the production signature is keyword-only with no
            default, so no writer can claim consent by omission, and a HELPER
            that spelled it at 40 call sites would say nothing at any of them.
            Every case in this module is the review screen's own destination
            select unless it says otherwise, which is ``False``.
        **destination: The ``PurchaseCreation`` destination fields.

    Returns:
        The :class:`~app.services.statement_match.CreatedPurchase`.
    """
    return statement_match.create_purchase_from_line(
        PurchaseCreation(
            line_id=line.id,
            **destination,
        ),
        # DERIVED HERE, so every call sees the rows this test has staged.
        a_scope(seed_user),
        minted if minted is not None else _create.MintedEnvelopes.none_yet(),
        # ...and the ANSWERS this test has staged, for the same reason: a
        # helper handing the door an empty ``CreationBars`` would make every
        # case below blind to ruling R-GJ's refusal.
        an_answers(seed_user),
        applied_by_rule=applied_by_rule,
    )


class TestRecordingALineAddsTheMovement:
    """The money property: the app now holds what the bank showed."""

    def test_the_balance_falls_by_what_the_bank_took(
        self, app, db, seed_user,
    ):
        """A movement the app did not have is a movement the app now has.

        This is the whole point of the step and the reason it is marked MOVES
        MONEY.  Accepting a match cannot do this -- it only re-dates a row the
        app already held -- which is why 74 of the developer's own lines
        survive every proposal.
        """
        with app.app_context():
            envelope = _closed_from_purchases(seed_user)
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date + timedelta(days=5)
            line = a_bank_line(
                seed_user, statement, amount="-57.96", posted_on=day,
                description="POINT OF SALE DEBIT L340 WAL-MART (Walmart)",
            )
            before = _balance_on(seed_user, day + timedelta(days=2))

            _record(seed_user, line, transaction_id=envelope.id)
            db.session.flush()

            after = _balance_on(seed_user, day + timedelta(days=2))
            assert after == before - Decimal("57.96")

    @pytest.mark.parametrize("applied_by_rule", [False, True])
    def test_the_act_records_WHICH_consent_it_had(
        self, app, db, seed_user, applied_by_rule,
    ):
        """Ruling **R-GT**, at this package's SECOND writer of a match act.

        Both doors reach ``record_match``, and ``applied_by_rule`` is
        keyword-only with no default the whole way down, so each states the
        fact for itself.  **Plan step ``bank_import:X-ge`` is what made this a
        PAIR**: until it shipped, this door had one entrance -- the review
        screen's per-line destination select, under a human press -- and stated
        ``False`` as a literal; it now has a second, an import filing a NEW
        swipe under a standing rule the owner already gave.

        **BOTH arms, and the false one is not decoration.**  The column is what
        the receipt and the review screen's badge partition on, so a door that
        wrote ``True`` for everything would report every act the owner pressed
        as one the app performed for them -- which is a false claim about
        consent in the direction that matters, and one no case asserting only
        the rule arm could see.  The act is otherwise IDENTICAL, which is the
        point: same purchase, same destination, same days.
        """
        with app.app_context():
            envelope = _closed_from_purchases(seed_user)
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date + timedelta(days=5)
            line = a_bank_line(
                seed_user, statement, amount="-57.96", posted_on=day,
                description="POINT OF SALE DEBIT L340 WAL-MART (Walmart)",
            )

            _record(
                seed_user, line, transaction_id=envelope.id,
                applied_by_rule=applied_by_rule,
            )
            db.session.flush()

            assert db.session.query(StatementMatch).one().applied_by_rule is (
                applied_by_rule
            )

    def test_the_envelope_s_recorded_cost_grows_by_the_purchase(
        self, app, db, seed_user,
    ):
        """A ``purchases`` close IS its entries, so the row cost more.

        Not a side effect to tolerate: the bank is the evidence that this
        envelope paid for something the records did not name.
        """
        with app.app_context():
            envelope = _closed_from_purchases(seed_user, purchase="120.00")
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date + timedelta(days=5)
            line = a_bank_line(seed_user, statement, amount="-57.96",
                               posted_on=day)

            _record(seed_user, line, transaction_id=envelope.id)
            db.session.flush()
            db.session.expire(envelope)

            assert envelope.covering_movements == [], (
                "a purchases settlement records no figure of its own"
            )
            assert sum(
                (entry.amount for entry in envelope.entries), Decimal("0"),
            ) == Decimal("177.96")

    def test_the_purchase_carries_BOTH_of_the_banks_days(
        self, app, db, seed_user,
    ):
        """Ruling **R-FW**: a purchase has a budget clock and a cash clock.

        The day it was MADE is what the bank states inside a card line's
        description (182 of the developer's 361 lines); the day it POSTED is
        when the money left.  Both are the bank's, and both are written in one
        ``create_entry`` call -- so a purchase the bank has already taken never
        exists, even briefly, as an outstanding one.
        """
        with app.app_context():
            envelope = _closed_from_purchases(seed_user)
            statement = an_import(seed_user)
            posted = seed_user["bootstrap_period"].start_date + timedelta(days=5)
            line = a_bank_line(
                seed_user, statement, amount="-57.96", posted_on=posted,
                transaction_on=posted - timedelta(days=3),
            )

            recorded = _record(seed_user, line, transaction_id=envelope.id)
            db.session.flush()

            entry = db.session.get(TransactionEntry, recorded.entry_id)
            assert entry.settled_on == posted
            assert entry.purchased_on == posted - timedelta(days=3)
            assert recorded.made_on == posted - timedelta(days=3)

    def test_the_purchase_says_the_BANK_wrote_its_figure(
        self, app, db, seed_user,
    ):
        """Ruling **R-BAL61**, at the one site that states ``observed``.

        The born-purchase builder (``_create._born_purchase``) is one of the
        two places in the app that state a figure as the bank's, and the
        door no longer infers it from the ``observed`` day beside it (plan
        step X-bi-3e-1) -- so the label is graded here, through the review
        screen's own create path, on the stored column.  Change the builder
        to say ``typed`` and this fails while every day assertion still
        passes.
        """
        with app.app_context():
            envelope = _closed_from_purchases(seed_user)
            statement = an_import(seed_user)
            posted = seed_user["bootstrap_period"].start_date + timedelta(days=5)
            line = a_bank_line(
                seed_user, statement, amount="-57.96", posted_on=posted,
            )

            recorded = _record(seed_user, line, transaction_id=envelope.id)
            db.session.flush()

            entry = db.session.get(TransactionEntry, recorded.entry_id)
            assert entry.amount == Decimal("57.96")
            assert entry.figure_source_id == ref_cache.movement_figure_source_id(
                MovementFigureSourceEnum.OBSERVED,
            )

    def test_a_line_stating_no_made_day_takes_the_posting_day(
        self, app, db, seed_user,
    ):
        """179 of 361 lines state none, so this is the majority arm.

        The posting day is the tightest bound a source stating nothing
        supports: money cannot clear before it moves.
        """
        with app.app_context():
            envelope = _closed_from_purchases(seed_user)
            statement = an_import(seed_user)
            posted = seed_user["bootstrap_period"].start_date + timedelta(days=5)
            line = a_bank_line(seed_user, statement, amount="-57.96",
                               posted_on=posted, transaction_on=None)

            recorded = _record(seed_user, line, transaction_id=envelope.id)
            db.session.flush()

            entry = db.session.get(TransactionEntry, recorded.entry_id)
            assert entry.purchased_on == posted
            assert entry.settled_on == posted

    def test_the_line_stops_being_unexplained(self, app, db, seed_user):
        """Recorded through the SAME match door as every correspondence.

        Ruling **R-FT**'s table and ruling **R-FV**'s identity-only rule are
        not re-implemented here: the door builds a
        :class:`~app.services.statement_match.MatchSubmission` and hands it to
        :func:`~app.services.statement_match.accept_match`, so a re-import
        cannot re-offer the line and an undo has something to delete.
        """
        with app.app_context():
            envelope = _closed_from_purchases(seed_user)
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date + timedelta(days=5)
            line = a_bank_line(seed_user, statement, amount="-57.96",
                               posted_on=day)
            before = statement_match.review_set(a_scope(seed_user))
            assert line.id in {ln.line_id for ln in before.unmatched}

            recorded = _record(seed_user, line, transaction_id=envelope.id)
            db.session.flush()

            after = statement_match.review_set(a_scope(seed_user))
            assert line.id not in {ln.line_id for ln in after.unmatched}
            assert recorded.match_id in {
                group.match_id for group in accepted_acts(seed_user)
            }


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


class TestTheDoorRefusesAnIMPOSSIBLEDayLine:
    """Plan step ``bank_import:X-gm``, finding **N-325**.

    The bank dates the line MADE after it POSTED, so its money left before it
    was spent.  ``entry_service._reject_settled_before_purchase`` has always
    refused the resulting pair, backed by
    ``ck_transaction_entries_settled_not_before_purchase`` -- but it refuses it
    LAST, after ``resolve_destination`` may have minted an envelope for a
    purchase that will never exist, and it says so in the entry door's own
    words rather than the bank line's.  This door refuses it FIRST and in the
    sentence the card shows.

    **Both halves are graded here**, because the reason for the new refusal is
    entirely the ORDER and the WORDING -- an assertion that "it raises" would
    pass against the old tree.
    """

    @staticmethod
    def _an_impossible_line(seed_user):
        """Stage one line the bank dates made a day AFTER it took the money."""
        day = seed_user["bootstrap_period"].start_date + timedelta(days=5)
        return a_bank_line(
            seed_user, an_import(seed_user), amount="-31.41", posted_on=day,
            transaction_on=day + timedelta(days=1),
            description="POINT OF SALE DEBIT L340 LOWES #00907 (Lowe's)",
        )

    def test_it_is_refused_BEFORE_an_envelope_is_minted(
        self, app, db, seed_user,
    ):
        """The order is the point, so the case is written on the minting arm.

        Submitted against a NEW ENVELOPE, the old tree resolved the
        destination, created the ``budget.transactions`` row, and only then met
        ``create_entry``'s refusal.  Nothing may be staged now.
        """
        with app.app_context():
            line = self._an_impossible_line(seed_user)
            before = db.session.query(Transaction).count()

            # **NO message match here**, and the omission is deliberate: the
            # entry door refuses this pair too, so a case that asserted the
            # WORDING would fail on the wording before it ever reached the
            # count -- which is the claim.  The sentence is graded by
            # test_the_DOOR_and_the_CARD_say_the_same_thing below, and the
            # two together are what the removed refusal has to break.
            with pytest.raises(ValidationError):
                _record(seed_user, line, new_envelope=NewEnvelope(
                    name="Lowe's",
                    category_id=seed_user["categories"]["Groceries"].id,
                ))

            # The query AUTOFLUSHES, so a staged envelope is counted here;
            # this sees what the old order left behind rather than only what
            # a commit would have kept.
            assert db.session.query(Transaction).count() == before
            assert db.session.query(StatementMatch).count() == 0

    def test_a_line_dated_made_BEFORE_it_posted_is_recorded(
        self, app, db, seed_user,
    ):
        """The other side, so the refusal is not refusing every line.

        Without it a door that raised unconditionally would pass the case
        above, and the whole CREATE act would be dead with the suite green.
        """
        with app.app_context():
            day = seed_user["bootstrap_period"].start_date + timedelta(days=5)
            line = a_bank_line(
                seed_user, an_import(seed_user), amount="-31.41",
                posted_on=day, transaction_on=day - timedelta(days=1),
                description="POINT OF SALE DEBIT L340 LOWES #00907 (Lowe's)",
            )

            recorded = _record(seed_user, line, new_envelope=NewEnvelope(
                name="Lowe's",
                category_id=seed_user["categories"]["Groceries"].id,
            ))
            db.session.flush()

            assert db.session.get(Transaction, recorded.transaction_id)

    def test_the_DOOR_and_the_CARD_say_the_same_thing(
        self, app, db, seed_user,
    ):
        """ONE sentence, which is the whole reason it lives in ``_scope``.

        The screen withholds the ADD control on
        ``_leftovers._add_refusal`` and the door raises on
        ``_scope.reject_impossible_days``; a second spelling is what
        ``no_period_refusal`` records having had to delete one refusal over.
        The door appends *Nothing was changed* and nothing else, so the card's
        whole sentence is a prefix of the door's.
        """
        with app.app_context():
            a_transaction(seed_user, name="Groceries", is_envelope=True)
            line = self._an_impossible_line(seed_user)
            db.session.commit()

            withheld = statement_match.review_set(
                a_scope(seed_user),
            ).creatable[0].withheld
            with pytest.raises(ValidationError) as raised:
                _record(seed_user, line, new_envelope=NewEnvelope(
                    name="Lowe's",
                    category_id=seed_user["categories"]["Groceries"].id,
                ))

            assert withheld
            assert str(raised.value) == f"{withheld}  Nothing was changed."


class TestWhatTheCreateDoorRefuses:
    """Every refusal, each written to fail if the refusal were deleted."""

    def test_an_UNCLAIMED_INFLOW_line_is_refused(self, app, db, seed_user):
        """A deposit no rule claims is not a refund, so this door refuses it.

        **The backstop is no longer the schema** (ruling **bank_import:R-II**,
        plan step ``bank_import:X-gj-2b-1``):
        ``ck_transaction_entries_positive_amount`` is ``amount <> 0``, so a
        negative purchase is writable and this refusal is the DOOR's own.
        **And it is no longer about the DIRECTION either** (plan step
        ``bank_import:X-gj-2b-2``): what it refuses is an inflow the owner has
        said NOTHING about, because filing one would name a container they
        never gave, which is the guess ruling **R-HX** refused. The case below
        holds the other arm. 16 of the developer's own unexplained lines are
        inflows, three of them card refunds, so this is the ordinary
        shape rather than a crafted request.
        """
        with app.app_context():
            envelope = _closed_from_purchases(seed_user)
            statement = an_import(seed_user)
            line = a_bank_line(
                seed_user, statement, amount="2573.42",
                posted_on=seed_user["bootstrap_period"].start_date,
            )

            with pytest.raises(ValidationError, match="not a refund"):
                _record(seed_user, line, transaction_id=envelope.id)
            assert db.session.query(TransactionEntry).count() == 1

    def test_a_CLAIMED_inflow_is_NOT_refused(self, app, db, seed_user):
        """The other side of the same refusal, asserted beside it.

        **The refusal is about the ANSWER and not the direction** since plan
        step ``bank_import:X-gj-2b-2``, so a case asserting only that inflows
        are refused would stay green if the door went back to refusing every
        one of them -- which would silently un-build the refund act. A credit
        whose merchant the owner has placed is a refund and the door takes it.
        """
        with app.app_context():
            envelope = _closed_from_purchases(seed_user)
            a_rule(
                seed_user, "Amazon",
                template_id=envelope.template_id,
            )
            statement = an_import(seed_user)
            line = a_bank_line(
                seed_user, statement, amount="28.29", merchant="Amazon",
                posted_on=seed_user["bootstrap_period"].start_date,
            )

            recorded = _record(seed_user, line, transaction_id=envelope.id)

            assert recorded.amount == Decimal("-28.29")

    def test_an_ALREADY_MATCHED_line_is_refused(self, app, db, seed_user):
        """Refused HERE rather than by the unique index after a write.

        ``uq_statement_match_members_line`` refuses the second act either way,
        and so does ``accept_match``'s own ``_load_lines`` -- but BOTH arrive
        after ``create_entry`` has already staged a purchase.  So the assertion
        that matters is the COUNT, not the raise: deleting this refusal leaves
        an orphan purchase behind on a page the owner merely double-submitted.
        Measured -- without the count assertion this test passed with the
        refusal deleted.
        """
        with app.app_context():
            envelope = _closed_from_purchases(seed_user)
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date + timedelta(days=5)
            line = a_bank_line(seed_user, statement, amount="-57.96",
                               posted_on=day)
            _record(seed_user, line, transaction_id=envelope.id)
            db.session.flush()
            after_first = db.session.query(TransactionEntry).count()

            with pytest.raises(ValidationError, match="already matched"):
                _record(seed_user, line, transaction_id=envelope.id)

            assert db.session.query(TransactionEntry).count() == after_first, (
                "a refused second submission staged a purchase anyway"
            )

    def test_a_line_on_ANOTHER_account_is_refused(
        self, app, db, seed_user, seed_second_user,
    ):
        """The scope is the account, and a statement is one bank's record of one.

        Reaching another account's line by id would book money against a
        statement that never showed it.

        **The COUNT is the control and the raise is not**, for the reason the
        already-matched test states: ``accept_match`` scopes its own lines too,
        so deleting this clause still raises -- after ``create_entry`` has put
        a purchase from a foreign statement onto this owner's envelope.
        Measured: without the count assertion this test passed with the clause
        deleted.
        """
        with app.app_context():
            envelope = _closed_from_purchases(seed_user)
            statement = an_import(seed_second_user)
            line = a_bank_line(
                seed_second_user, statement, amount="-57.96",
                posted_on=seed_second_user["bootstrap_period"].start_date,
            )
            before = db.session.query(TransactionEntry).count()

            with pytest.raises(ValidationError, match="no longer on this"):
                _record(seed_user, line, transaction_id=envelope.id)

            assert db.session.query(TransactionEntry).count() == before, (
                "a foreign statement line staged a purchase on this account"
            )

    def test_an_envelope_the_screen_could_not_OFFER_is_refused(
        self, app, db, seed_user,
    ):
        """One scope for the reader and the writer.

        A row closed at a FIXED figure cannot record a new purchase: its
        stored figure was fixed before the purchase was weighed, so the
        purchase's own movement would be counted beside the covering
        movement that already carries the whole close (ruling **R-BAL80**).
        Measured on a production clone under the row-leg fold of the time,
        `-163.95` became `+203.67` -- an expense row publishing an inflow --
        while the anchor true-up moved `$0.00`, so the spending was never
        recorded at all.
        """
        with app.app_context():
            fixed = a_transaction(
                seed_user, name="Mint Mobile", amount="132.69",
                is_envelope=True, status=StatusEnum.DONE,
                settled_on=seed_user["bootstrap_period"].start_date,
            )
            statement = an_import(seed_user)
            line = a_bank_line(
                seed_user, statement, amount="-57.96",
                posted_on=seed_user["bootstrap_period"].start_date,
            )
            offered = _offerable(seed_user)
            assert fixed.id not in {d.transaction_id for d in offered}
            # The row holds its covering movement and nothing else.
            assert fixed.purchases == []
            before = db.session.query(TransactionEntry).count()

            with pytest.raises(ValidationError, match="not one this purchase"):
                _record(seed_user, line, transaction_id=fixed.id)
            assert db.session.query(TransactionEntry).count() == before
            assert fixed.purchases == []

    def test_ANOTHER_USERS_category_is_refused(
        self, app, db, seed_user, seed_second_user,
    ):
        """The IDOR probe every create door in this project performs.

        A foreign ``category_id`` satisfies the foreign key -- the row exists
        -- and would link another user's category onto this owner's budget.
        """
        with app.app_context():
            statement = an_import(seed_user)
            line = a_bank_line(
                seed_user, statement, amount="-57.96",
                posted_on=seed_user["bootstrap_period"].start_date,
            )
            foreign = (
                db.session.query(Category)
                .filter(Category.user_id == seed_second_user["user"].id)
                .first()
            )

            with pytest.raises(ValidationError, match="not one of yours"):
                _record(seed_user, line, new_envelope=NewEnvelope(
                    name="Lowe's", category_id=foreign.id,
                ))
            assert db.session.query(Transaction).filter(
                Transaction.name == "Lowe's",
            ).count() == 0

    def test_naming_BOTH_destinations_is_refused(self, app, db, seed_user):
        """A purchase has exactly one parent.

        Stated as a refusal rather than a precedence rule: a door that silently
        preferred an arm would record something the owner did not ask for.
        """
        with app.app_context():
            envelope = _closed_from_purchases(seed_user)
            statement = an_import(seed_user)
            line = a_bank_line(
                seed_user, statement, amount="-57.96",
                posted_on=seed_user["bootstrap_period"].start_date,
            )

            with pytest.raises(ValidationError, match="exactly one place"):
                _record(
                    seed_user, line, transaction_id=envelope.id,
                    new_envelope=NewEnvelope(
                        name="Lowe's",
                        category_id=seed_user["categories"]["Groceries"].id,
                    ),
                )

    def test_naming_NEITHER_destination_is_refused(self, app, db, seed_user):
        """The same rule's other half."""
        with app.app_context():
            statement = an_import(seed_user)
            line = a_bank_line(
                seed_user, statement, amount="-57.96",
                posted_on=seed_user["bootstrap_period"].start_date,
            )

            with pytest.raises(ValidationError, match="exactly one place"):
                _record(seed_user, line)

    def test_a_line_MADE_after_it_POSTED_is_refused(self, app, db, seed_user):
        """Money does not leave an account before it is spent.

        **The rule is ``entry_service``'s and this door does not restate it.**
        A first version added its own ``_reject_impossible_days`` so the
        message could name the line rather than the purchase, and so the
        refusal preceded the new-envelope write.  Both reasons failed: the SECU
        CSV reader provably cannot produce the pair (it resolves the stated day
        to the most recent one at or before the posting day), so the guard was
        rule 13's speculative shape; and the door already depends on the
        request-level rollback for every other ``entry_service`` refusal, so
        ordering bought nothing it did not already owe.  Removed after
        adversarial design review 2026-08-19.

        What is graded here is that such a line becomes no purchase and leaves
        no budget line behind -- which is the property, whichever rule states
        it.
        """
        with app.app_context():
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date + timedelta(days=5)
            line = a_bank_line(
                seed_user, statement, amount="-57.96", posted_on=day,
                transaction_on=day + timedelta(days=1),
            )

            with pytest.raises(
                ValidationError, match="cannot reach your bank before",
            ):
                _record(seed_user, line, new_envelope=NewEnvelope(
                    name="Lowe's",
                    category_id=seed_user["categories"]["Groceries"].id,
                ))
            db.session.rollback()
            assert db.session.query(Transaction).filter(
                Transaction.name == "Lowe's",
            ).count() == 0

    def test_a_day_NO_PAY_PERIOD_covers_is_refused(self, app, db, seed_user):
        """130 of the developer's own 361 lines predate their first payday.

        There is no budget for such a purchase to belong to, and
        ``transactions.pay_period_id`` is NOT NULL -- so the honest answer is a
        sentence naming the day rather than a row filed under the nearest
        period, which would misplace real money.

        **The books are opened before the line first, and that is what keeps
        this case reachable at all** (plan step balance:X-f3c-2b-2b).  A
        default fixture account opens its books the day before the bootstrap
        period, so a line 400 days earlier is inside its opening equity and
        meets the books refusal instead -- correctly, because that money is
        already counted.  Books that open BEFORE the budget did is the real
        shape this arm serves: it is what finding **N-368**'s import will
        leave on the developer's own Checking account.
        """
        with app.app_context():
            statement = an_import(seed_user)
            day = (
                seed_user["bootstrap_period"].start_date
                - timedelta(days=400)
            )
            open_books_before_the_first_assertion(
                db.session, seed_user["account"], also_before=day,
            )
            line = a_bank_line(
                seed_user, statement, amount="-57.96", posted_on=day,
            )

            with pytest.raises(ValidationError, match="No pay period covers"):
                _record(seed_user, line, new_envelope=NewEnvelope(
                    name="Lowe's",
                    category_id=seed_user["categories"]["Groceries"].id,
                ))


class TestTheSpanADestinationCarriesIsDERIVED:
    """Pay-calendar plan step **C4-a-4**: the picker came off the ORM row.

    ``destinations_for`` read ``txn.pay_period`` for each offered envelope's
    paycheck, which is ``pay_periods.end_date`` -- a stored copy of the day
    before the NEXT payday, with nothing reconciling the two, and plan step
    C4-c dropped it.  It now scopes its scan by
    :meth:`~app.services.pay_calendar.PayCalendar.saved_by_id`'s own keys and
    indexes that SAME mapping, so the span it publishes is DERIVED and the
    lookup cannot miss a row the scan returned.

    **Every case here stages a payday that is NOT one stored period-length
    after the previous one**, through :func:`a_payday_on`, because on a
    schedule written forward the stored end and the derived end are equal --
    so a case built on the ordinary shape passes against the column it is
    meant to catch.  On production both agree today (63 periods, 0
    disagreements), which is what makes this a control rather than a repair.
    """

    @staticmethod
    def _by_id(seed_user):
        """Return what the screen may offer, keyed by budget line."""
        return {
            destination.transaction_id: destination
            for destination in _offerable(seed_user)
        }

    def test_the_span_ends_the_day_before_the_NEXT_payday(
        self, app, db, seed_user,
    ):
        """THE control: the stored column and the derivation disagree by seven days.

        A payday eight days after the bootstrap period's stored ``end_date``
        makes that period seven days longer than the row says.  The screen
        must show the paycheck the owner is actually inside, which is the
        derived one.
        """
        with app.app_context():
            bootstrap = seed_user["bootstrap_period"]
            stored_end = last_covered_day(bootstrap)
            a_payday_on(seed_user, stored_end + timedelta(days=8))
            envelope = a_transaction(
                seed_user, name="Groceries", amount="500.00", is_envelope=True,
            )

            derived_end = stored_end + timedelta(days=7)
            assert derived_end != stored_end
            assert self._by_id(seed_user)[envelope.id].period.end_date == (
                derived_end
            )

    def test_the_span_is_the_calendar_it_was_GIVEN_and_not_one_it_RE_READS(
        self, app, db, seed_user,
    ):
        """THE control for this leaf's central claim, which had none.

        Every argument for the accessor is that ONE mapping is both the scan's
        scope and the per-row lookup, so the two cannot come apart.  Nothing
        measured it: a mutant that filters from the threaded calendar and
        resolves each span from a FRESHLY DERIVED one survives every other
        case here, because in all of them the two calendars agree.  Named by
        adversarial test-quality review 2026-08-31.

        This is that disagreement, built WITHOUT concurrency: the calendar is
        derived FIRST, a payday is then recorded, and the producer is handed
        the earlier value.  A span read off the handed calendar ends where its
        CADENCE projects; one read off a fresh derivation ends the day before
        the new payday.  That is the READ-COMMITTED interleaving of finding
        **N-358** made deterministic -- what a concurrent payday INSERT
        between a command's two reads would do, staged in one thread.
        """
        with app.app_context():
            envelope = a_transaction(
                seed_user, name="Groceries", amount="500.00", is_envelope=True,
            )
            bootstrap = seed_user["bootstrap_period"]
            # Derived BEFORE the write, which is the whole case.
            handed = pay_calendar.calendar_for(seed_user["user"].id)
            a_payday_on(seed_user, last_covered_day(bootstrap) + timedelta(days=8))

            offered = {
                d.transaction_id: d for d in statement_match.destinations_for(
                    seed_user["account"].id, handed,
                )
            }

            projected_end = bootstrap.start_date + timedelta(days=13)
            re_read_end = last_covered_day(bootstrap) + timedelta(days=7)
            assert projected_end != re_read_end
            assert offered[envelope.id].period.end_date == projected_end

    def test_two_envelopes_in_ONE_period_are_ordered_by_NAME(
        self, app, db, seed_user,
    ):
        """The second half of the promised order, which nothing asserted.

        "Oldest pay period first and THEN BY NAME" is what this producer
        publishes, and every other case here stages one envelope per period --
        so dropping the label from the sort key left the whole suite green and
        the ``<select>`` inside one paycheck ordered by whatever the planner
        returned.  Named by adversarial test-quality review 2026-08-31.

        Staged in REVERSE alphabetical order so insertion order and the
        promised order disagree.
        """
        with app.app_context():
            later = a_transaction(
                seed_user, name="Water", amount="80.00", is_envelope=True,
            )
            earlier = a_transaction(
                seed_user, name="Groceries", amount="500.00", is_envelope=True,
            )
            assert later.id < earlier.id

            ordered = [
                d.transaction_id for d in _offerable(seed_user)
                if d.transaction_id in {later.id, earlier.id}
            ]
            assert ordered == [earlier.id, later.id]

    def test_the_LABEL_a_reviewer_READS_carries_the_derived_end(
        self, app, db, seed_user,
    ):
        """The span is not an internal: it is what the chooser prints.

        ``PurchaseDestination.label`` is what the destination ``<select>``
        renders, and the same envelope name recurs every period -- so the
        span is the whole of how a reviewer tells one Groceries from the
        next.  Asserted through the label rather than only through the field,
        because the field could be right while the derivation stopped
        reaching the string.
        """
        with app.app_context():
            bootstrap = seed_user["bootstrap_period"]
            stored_end = last_covered_day(bootstrap)
            a_payday_on(seed_user, stored_end + timedelta(days=8))
            envelope = a_transaction(
                seed_user, name="Groceries", amount="500.00", is_envelope=True,
            )

            label = self._by_id(seed_user)[envelope.id].label
            assert label.endswith(
                f"({bootstrap.start_date} - "
                f"{stored_end + timedelta(days=7)})"
            )
            assert str(stored_end) not in label

    def test_the_period_it_carries_is_the_ROWs_OWN_paycheck(
        self, app, db, seed_user,
    ):
        """Indexed by the row's ``pay_period_id``, over a calendar of three.

        A mapping indexed by anything else -- the first key, the line's
        period -- would answer a real ``DerivedPeriod`` rather than raise, so
        the identity is asserted rather than left to the absence of a
        ``KeyError``.
        """
        with app.app_context():
            bootstrap = seed_user["bootstrap_period"]
            middle = a_payday_on(
                seed_user, last_covered_day(bootstrap) + timedelta(days=8),
            )
            last = a_payday_on(
                seed_user, last_covered_day(bootstrap) + timedelta(days=30),
            )
            here = a_transaction(
                seed_user, name="Groceries", amount="500.00",
                is_envelope=True, period=middle,
            )
            there = a_transaction(
                seed_user, name="Gas", amount="120.00",
                is_envelope=True, period=last,
            )

            offered = self._by_id(seed_user)
            assert offered[here.id].period.period_id == middle.id
            assert offered[there.id].period.period_id == last.id

    def test_a_row_filed_in_ANOTHER_owners_paycheck_is_NOT_offered(
        self, app, db, seed_user, second_user,
    ):
        """Ownership, and it is the CALENDAR that states it.

        **The premise this case was written on is gone, and the case says so
        rather than being deleted quietly.**  It read: "``budget.transactions``
        carries no ``user_id`` -- its owner IS its pay period's, and nothing in
        the schema requires that owner to be its ACCOUNT's".  Plan step
        ``pay_calendar:C13-a`` gave the row a ``user_id`` and two composite
        keys, so the trespasser is refused at the INSERT and never reaches the
        scope.  The scope's calendar-id arm is unchanged and still correct --
        and it is NOT one of finding **P75**'s nineteen, which counts reads
        that REFUSE and excludes scopes by name.  What grades it now is
        ``test_candidates.TestTheCalendarIsTheOwnershipSCOPE``'s short-calendar
        case, added by this step for that reason.

        The two owners' bootstrap periods still open on the SAME civil day.
        That assertion is KEPT and its reason has changed: it used to be what
        made the old scope test about the period's identity rather than its
        dates, and a foreign-key violation is indifferent to dates either way.
        What it holds now is the FIXTURE -- if the two owners' calendars ever
        stop coinciding, the case below stops standing in for the shape it
        names.

        A row that IS this owner's is offered in the same breath, so a fixture
        that had stopped producing offers at all could not pass this.

        **The row is a ONE-OFF** (``template=False``, the producer's row since
        plan step balance:X-bi-7c, exactly what ``_create._create_envelope``
        writes) and the builder refuses another owner's paycheck BEFORE any
        write, as the engine's builder does for a row of a recurring
        definition (``RecurrenceWindowError``), one tier above the key.  The
        key itself, ``fk_transactions_owner_period`` over ``(pay_period_id,
        user_id)`` on every row, is graded on a bare row by
        ``test_models/test_c13a_transaction_owner_key.py`` (ruling **R-BAL59**,
        X-bi-7c's rule-5 batch).
        """
        with app.app_context():
            foreign_period = second_user["bootstrap_period"]
            assert foreign_period.start_date == (
                seed_user["bootstrap_period"].start_date
            )
            with pytest.raises(AssertionError, match="not in user"):
                a_transaction(
                    seed_user, name="Groceries", amount="500.00",
                    is_envelope=True, period=foreign_period, template=False,
                )
            assert db.session.query(Transaction).filter_by(
                pay_period_id=foreign_period.id, user_id=seed_user["user"].id,
            ).count() == 0

            mine = a_transaction(
                seed_user, name="Gas", amount="120.00", is_envelope=True,
            )
            assert mine.id in self._by_id(seed_user)

    def test_an_EMPTY_calendar_admits_NOTHING_rather_than_everything(
        self, app, db, seed_user,
    ):
        """A scope with no periods is empty, not absent.

        The producer's ownership clause IS the calendar's saved ids since
        C4-a-4, so an owner holding no paydays scopes the scan by an empty
        mapping -- which SQLAlchemy renders as a false predicate rather than
        as no clause at all.  Reachable: plan step ``balance:X-ad-a`` stopped
        registration writing a bootstrap payday, so a brand-new owner holds
        none.  Asserted against a calendar built with no paydays rather than
        by emptying the table, because the account's anchor names a period and
        deleting it would be measuring the fixture.

        The control beside it is the same account through its REAL calendar,
        without which "returned nothing" could mean the account has nothing to
        offer.
        """
        with app.app_context():
            a_transaction(
                seed_user, name="Groceries", amount="500.00", is_envelope=True,
            )
            empty = pay_calendar.PayCalendar.from_paydays([], eras_of([], 14), seed_user["user"].id, history_opens_on=None,
            )

            assert statement_match.destinations_for(
                seed_user["account"].id, empty,
            ) == []
            assert statement_match.destinations_for(
                seed_user["account"].id,
                pay_calendar.calendar_for(seed_user["user"].id),
            ) != []

    def test_the_ORDER_is_by_PAYDAY_and_not_by_period_id(
        self, app, db, seed_user,
    ):
        """"Oldest pay period first" is what the producer promises.

        It sorted on ``pay_period_id`` until C4-a-4, which is the same order
        only while paydays are appended forward -- and plan step
        ``pay_calendar:C6`` inserts one MID-SCHEDULE by design, giving the
        newest row the newest id in the middle of the sequence.  Staged here
        by recording the LATER payday first, so the two orders disagree and
        the assertion can tell them apart.

        **The bootstrap's last covered day is read ONCE, before either payday
        is written**, and since plan step ``pay_calendar:C4-c`` it has to be: a
        DERIVED end MOVES when a neighbouring payday is written, where the
        stored column could not.  Recording ``later`` pulls the bootstrap's end
        back from its cadence projection to the day before that payday, so a
        second read would place ``middle`` AFTER ``later`` -- the fixture would
        stage the very order it exists to disagree with.
        """
        with app.app_context():
            bootstrap = seed_user["bootstrap_period"]
            bootstrap_end = last_covered_day(bootstrap)
            later = a_payday_on(
                seed_user, bootstrap_end + timedelta(days=30),
            )
            middle = a_payday_on(
                seed_user, bootstrap_end + timedelta(days=8),
            )
            assert middle.id > later.id
            assert middle.start_date < later.start_date

            in_middle = a_transaction(
                seed_user, name="Groceries", amount="500.00",
                is_envelope=True, period=middle,
            )
            in_later = a_transaction(
                seed_user, name="Gas", amount="120.00",
                is_envelope=True, period=later,
            )

            ordered = [d.transaction_id for d in _offerable(seed_user)]
            assert ordered.index(in_middle.id) < ordered.index(in_later.id)


class TestTheDestinationMustBeInTheLINEsOwnPeriod:
    """The security guard X-f6a-3c-2 added, which had NO test at all.

    Measured by adversarial test-quality review 2026-08-19: deleting
    ``if destination.period.period_id == pay_period_id`` from
    ``_create._existing_envelope`` left the whole suite green.  A security fix
    with no test is a security fix that gets refactored away.

    What the clause stops, in its own docstring's words: a crafted request
    filing a swipe into a Groceries envelope eighteen months forward, or
    raising a closed past envelope's recorded cost in a period the line has
    nothing to do with.  ``destinations_for`` deliberately returns budget lines
    across EVERY period on the account -- the screen renders only the line's
    own -- so this clause is the whole of what keeps the door's set the
    screen's set.
    """

    def test_an_envelope_in_ANOTHER_period_is_refused(
        self, app, db, seed_user,
    ):
        """The crafted request, refused, with nothing written."""
        with app.app_context():
            other = a_later_period(seed_user)
            elsewhere = a_transaction(
                seed_user, name="Groceries", amount="500.00",
                is_envelope=True, period=other,
            )
            statement = an_import(seed_user)
            line = a_bank_line(
                seed_user, statement, amount="-57.96",
                posted_on=seed_user["bootstrap_period"].start_date,
            )

            # It IS offerable -- just not for THIS line.
            assert elsewhere.id in {
                d.transaction_id for d in _offerable(seed_user)
            }

            with pytest.raises(ValidationError, match="not one this purchase"):
                _record(seed_user, line, transaction_id=elsewhere.id)

            db.session.flush()
            assert elsewhere.entries == []

    def test_the_SAME_envelope_in_the_line_s_own_period_is_accepted(
        self, app, db, seed_user,
    ):
        """The control, without which the case above could pass vacuously.

        Same shape, same door, same figures -- only the period differs.  A
        refusal that fired for any other reason would fail here too.
        """
        with app.app_context():
            here = a_transaction(
                seed_user, name="Groceries", amount="500.00", is_envelope=True,
            )
            statement = an_import(seed_user)
            line = a_bank_line(
                seed_user, statement, amount="-57.96",
                posted_on=seed_user["bootstrap_period"].start_date,
            )

            recorded = _record(seed_user, line, transaction_id=here.id)

            db.session.flush()
            assert recorded.transaction_id == here.id
            assert [e.amount for e in here.entries] == [Decimal("57.96")]


class TestWhatTheScreenMayOFFER:
    """``destinations_for`` -- the set the door will accept, and nothing more.

    Every clause is one of ``create_entry``'s or ``accept_match``'s, so the
    screen cannot render a chooser whose submission is refused.  That failure
    -- an Accept button that can never succeed -- is one this arc has now
    found three times.
    """

    @staticmethod
    def _offered(seed_user):
        """Return the ids the screen may offer for the seeded account."""
        return {
            destination.transaction_id
            for destination in _offerable(seed_user)
        }

    def test_a_PROJECTED_envelope_is_offered(self, app, db, seed_user):
        """The open case: a purchase joins a budget still being spent."""
        with app.app_context():
            envelope = a_transaction(
                seed_user, name="Groceries", amount="500.00", is_envelope=True,
            )
            assert envelope.id in self._offered(seed_user)

    def test_an_envelope_CLOSED_AT_ITS_PURCHASES_is_offered(
        self, app, db, seed_user,
    ):
        """29 of the developer's 37 closed envelopes are in this state.

        Its figure IS its entries, so a new purchase raises what the row cost
        by exactly the figure the bank showed and the row's own cash leg does
        not move.
        """
        with app.app_context():
            envelope = _closed_from_purchases(seed_user)
            assert envelope.id in self._offered(seed_user)

    def test_an_envelope_closed_at_a_FIXED_FIGURE_is_not(
        self, app, db, seed_user,
    ):
        """THE money clause.  See the refusal test for the measurement."""
        with app.app_context():
            envelope = a_transaction(
                seed_user, name="Mint Mobile", amount="132.69",
                is_envelope=True, status=StatusEnum.DONE,
                settled_on=seed_user["bootstrap_period"].start_date,
            )
            assert status_seam.recorded_settlement(envelope).source is (
                MovementFigureSourceEnum.RESOLVED
            )
            assert envelope.id not in self._offered(seed_user)

    def test_an_envelope_closed_at_NOTHING_is_offered(self, app, db, seed_user):
        """A ``$0.00`` close holds no covering movement, so it is a destination.

        Ruling **R-BAL82** (plan step ``balance:X-bi-4b-1``): the money
        clause reads the covering movement, and a movement of nothing is not
        one -- so a ``$0.00`` close is a close with no entries, a later
        purchase under it is counted exactly once, and the door
        (``_reject_settled_addition``) admits it.  The column read this
        replaced filed the same row under "a fixed figure" and refused it.
        """
        with app.app_context():
            envelope = a_transaction(
                seed_user, name="Waived Fee", amount="45.00",
                is_envelope=True, status=StatusEnum.DONE,
                settled_on=seed_user["bootstrap_period"].start_date,
            )
            transaction_service.apply_requested_status(
                envelope, envelope.status_id,
                stated=transaction_service.StatedRecord(figure=typed(Decimal("0.00"))),
            )
            db.session.flush()
            assert envelope.covering_movements == []
            assert envelope.id in self._offered(seed_user)

    def test_a_CANCELLED_envelope_is_not(self, app, db, seed_user):
        """A cancelled row records no cash, so a purchase under it posts none."""
        with app.app_context():
            envelope = a_transaction(
                seed_user, name="Strawberry Picking", amount="60.00",
                is_envelope=True, status=StatusEnum.CANCELLED,
            )
            assert envelope.id not in self._offered(seed_user)

    def test_a_NON_ENVELOPE_row_is_not(self, app, db, seed_user):
        """``create_entry`` refuses a parent that does not track purchases."""
        with app.app_context():
            bill = a_transaction(seed_user, name="Electricity", amount="180.00")
            assert bill.id not in self._offered(seed_user)

    def test_an_INCOME_row_is_not(self, app, db, seed_user):
        """Money coming in is not a purchase."""
        with app.app_context():
            deposit = a_transaction(
                seed_user, name="Paycheck", amount="2473.38", income=True,
                is_envelope=True,
            )
            assert deposit.id not in self._offered(seed_user)

    def test_an_envelope_closed_at_its_purchases_is_never_MATCHED_as_a_row(
        self, app, db, seed_user,
    ):
        """The one envelope a purchase may join cannot be a match's own row.

        Through plan step ``balance:X-bi-4a``'s first cut this case matched
        such an envelope to a line AS A ROW (it was offered at its un-dated
        purchases) and graded that the screen then stopped offering it as a
        destination, because ``accept_match`` refuses a purchase whose parent
        another match names (finding **N-317**, a guard wider than this door
        needs, left whole on the developer's ruling of 2026-08-19).  Under
        ruling **R-BAL81** the row is worth ``0`` to the offer and is not a
        candidate -- its purchase is -- so no match can name it while it holds
        one; the row stays the destination the case above says it is.  The
        already-matched clause keeps its live subject, an envelope matched
        EMPTY (``test_batch.test_one_derivation_per_pass
        .TestOneDerivationStAYSCorrectAcrossThePass
        .test_a_creation_cannot_target_an_envelope_a_match_claimed``).
        """
        with app.app_context():
            envelope = _closed_from_purchases(seed_user)
            leg = statement_match.candidates_for(
                seed_user["account"].id,
                pay_calendar.calendar_for(seed_user["user"].id),
                a_basis(seed_user),
            )
            offered = {(row.kind, row.row_id) for row in leg.rows}
            assert (statement_match.RowKind.TRANSACTION, envelope.id) not in offered
            assert {
                (statement_match.RowKind.PURCHASE, purchase.id)
                for purchase in envelope.purchases
            } <= offered

            assert envelope.id in self._offered(seed_user)
