"""What the screen may offer: the set the door will accept and nothing more, each destination
carrying the span its pay calendar derives (pay-calendar plan step C4-a-4).
"""

from datetime import timedelta
from decimal import Decimal

import pytest

from app.enums import MovementFigureSourceEnum, StatusEnum
from app.models.transaction import Transaction
from app.services import pay_calendar, statement_match, status_seam, transaction_service
from tests._test_helpers import eras_of, last_covered_day, typed

from tests.test_services.test_statement_match._builders import a_basis, a_payday_on, a_transaction

from ._helpers import _closed_from_purchases, _offerable


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
        """THE money clause.  See the refusal test for the measurement.

        That test is ``test_what_the_door_refuses.py``'s
        ``test_an_envelope_the_screen_could_not_OFFER_is_refused``.
        """
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
