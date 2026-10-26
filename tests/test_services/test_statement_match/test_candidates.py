"""What the matcher OFFERS, read against a real database.

:mod:`app.services.statement_match._candidates` is the read half: it turns the
account's rows into :class:`~app.services.statement_match.CandidateRow` values
priced and DATED as the app holds them.  Its sibling ``test_propose`` grades
the rules over those values and builds them by hand; nothing graded the values
themselves until plan step ``bank_import:X-f6a-3c-1`` made the WINDOW they
carry the proposer's only bound, and an adversarial review measured that a
test suite building rows by hand cannot see a producer that fills them wrongly.

**The scope arm is here for the same reason.**  That step re-keyed ownership
from a correlated subquery on ``pay_periods.user_id`` to the ids of the
owner's own derived calendar, which is what makes the window lookup total --
and a scope is exactly the kind of clause a hand-built value cannot exercise.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.enums import SettledDayBasisEnum, StatusEnum
from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.services import pay_calendar
from app.services.statement_match import (
    ReviewedRow,
    RowKind,
    as_reviewed,
    candidates_for,
)
from app.services.statement_match._pairing import DAY_WINDOW

from ._builders import (
    a_basis,
    a_later_period,
    a_purchase,
    a_transaction,
    an_assertion,
)
from tests._test_helpers import (
    create_account_of_type,
    create_transfer,
    last_covered_day,
    payback_row_of,
    rhythm_of,
)


def _candidate(seed_user, row_id, kind):
    """Return the candidate the matcher offers for one row, or ``None``."""
    rows = candidates_for(
        seed_user["account"].id,
        pay_calendar.calendar_for(seed_user["user"].id),
        a_basis(seed_user),
    ).rows
    return next(
        (r for r in rows if r.row_id == row_id and r.kind is kind), None,
    )


def _settled_candidate(seed_user, txn):
    """Return what the matcher offers for a SETTLED row: its payment.

    Plan step ``credit_card:CC-5-4a-1``, ruling **R-CC43** (developer
    2026-09-21): *the MOVEMENT is the subject of every settled match; a row
    is a candidate only while Projected.*  A settled row's candidate is a
    SETTLEMENT keyed by its covering movement, on the row's terms -- the
    row's window, the row's day basis -- so the window cases below ask the
    same questions of the same rows they asked through ``CC-5-3``, when the
    row itself was the candidate.
    """
    (movement,) = txn.covering_movements
    return _candidate(seed_user, movement.id, RowKind.SETTLEMENT)


class TestEachRowSaysWhetherItsFigureIsItsOwn:
    """``states_own_figure``, read off the row that produced it.

    Plan step **bank_import:X-f6d-1**.  The accept door has always refused a
    correction to a figure that is a fact about ANOTHER row (finding
    **N-252**), and it re-derived the census per act; the near-miss proposer
    needs the same answer and is pure, with no session to ask.  So the
    constructor states it and both read it -- which means the census is graded
    HERE, once, rather than only through the door's four sentences.

    **The two members are load-bearing and one of them was missed once**:
    ``transaction_service`` publishes ``settles_from_entries`` AND
    ``repays_card_spend``, and the transaction door's own backstop refuses only
    the first.
    """

    def test_an_ORDINARY_bill_states_its_own_figure(self, app, db, seed_user):
        """The common shape, and the one a near miss may correct."""
        txn = a_transaction(seed_user, name="Electricity", amount="180.00")
        db.session.flush()

        row = _candidate(seed_user, txn.id, RowKind.TRANSACTION)

        assert row.states_own_figure is True
        assert row.figure_is_correctable is True

    def test_an_ENVELOPE_HOLDING_PURCHASES_is_not_offered(
        self, app, db, seed_user,
    ):
        """Its figure IS its purchases, so the ROW is not a candidate at all.

        Through plan step ``balance:X-bi-4a``'s first cut the row was offered
        at its un-dated purchases and this census said its figure was not its
        own (``states_own_figure is False``); under ruling **R-BAL81** a row
        that settles from its purchases is worth ``0`` to the offer and
        ``transaction_candidate`` drops it, its purchases being the
        candidates.  The census's remaining subject is the CC payback
        (``test_a_CC_PAYBACK_does_not`` below).

        **Both halves of ``settles_from_entries`` matter**: an envelope with
        NO entries derives nothing and keeps its own figure, which the case
        below is the control for.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        purchase = a_purchase(seed_user, envelope, amount="25.00")
        db.session.flush()

        assert _candidate(seed_user, envelope.id, RowKind.TRANSACTION) is None
        offered = _candidate(seed_user, purchase.id, RowKind.PURCHASE)
        assert offered is not None
        assert offered.cash_amount == Decimal("-25.00")

    def test_an_EMPTY_envelope_still_states_its_own(self, app, db, seed_user):
        """Production's ``Kayla's Spending Money``: envelope-tracked, 0 entries.

        ``tracks_purchases`` alone would claim it and settle it at `$0.00`;
        the predicate asks for entries too, and this is what says so.
        """
        envelope = a_transaction(
            seed_user, name="Fuel", amount="100.00", is_envelope=True,
        )
        db.session.flush()

        row = _candidate(seed_user, envelope.id, RowKind.TRANSACTION)

        assert row.states_own_figure is True

    def test_a_CC_PAYBACK_does_not(self, app, db, seed_user):
        """The member a first draft of the census MISSED.

        A payback's figure is a fact about the row it repays, and
        ``entry_credit_workflow.sync_entry_payback`` re-states it on every
        entry mutation.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        a_later_period(seed_user)
        # Through the app's own producer: a payback carries the payback link
        # as its ONE pricing link (``ck_transactions_one_pricing_link``) and
        # is priced by the row it repays, in the paycheck after it.
        payback = payback_row_of(
            db.session, seed_user, envelope, Decimal("60.00"),
            seed_user["bootstrap_period"].start_date,
        )

        row = _candidate(seed_user, payback.id, RowKind.TRANSACTION)

        assert row.states_own_figure is False
        assert row.figure_is_correctable is False

    def test_a_PURCHASE_states_its_own_figure(self, app, db, seed_user):
        """A purchase is what the derived rows are made OF."""
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        purchase = a_purchase(seed_user, envelope, amount="25.00")
        db.session.flush()

        row = _candidate(seed_user, purchase.id, RowKind.PURCHASE)

        assert row.states_own_figure is True
        assert row.figure_is_correctable is True


class TestWhichRowsTheBankNeverShowsByThemselves:
    """``not_shown_alone``, the caveat the review panel prints per row.

    Plan step **bank_import:X-gc**, finding **N-345**'s design half.  The panel
    headed *Rows you recorded and the bank never showed* asserts that each of
    its rows is *a payment your records claim happened and your bank did not
    make*.  That inference needs the row's money to have been a bank line of
    its OWN, and for two shapes it never is: a CC payback leaves inside one
    lump payment to the card, and an envelope holding purchases is what the
    bank showed the purchases of.

    **The rows stay in the panel and only the CLAIM is withdrawn**, which
    ``test_statement_reconcile
    .TestThePaneTagsARowTheBankNeverShowsAloneOnItsOwn`` pins on the render:
    the MATCH pane's candidate list is what the owner assembles a group from,
    and ruling **R-GJ** leaves the group match as a parked card payment's only
    arm.  *That citation named ``test_statement_matches.py`` and was wrong when
    written -- the render cases were the workbench's -- and plan step
    ``bank_import:X-gi-2`` then deleted both modules, so the pair was rewritten
    against the surviving pane in that same step rather than left uncovered.*

    The class above grades ``states_own_figure`` itself; this grades that the
    caveat is derived from it and from nothing else, so the two cannot drift.
    """

    def test_a_CC_PAYBACK_carries_the_caveat(self, app, db, seed_user):
        """18 of the developer's own 67 panel rows, measured 2026-08-25."""
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        a_later_period(seed_user)
        payback = payback_row_of(
            db.session, seed_user, envelope, Decimal("60.00"),
            seed_user["bootstrap_period"].start_date,
        )

        row = _candidate(seed_user, payback.id, RowKind.TRANSACTION)

        assert row.not_shown_alone is not None
        assert row.not_shown_alone.label == "not a line of its own"
        assert "never shows it as a line by itself" in (
            row.not_shown_alone.sentence
        )
        # It ends in the ACT, not the diagnosis: this row is what a parked
        # Capital One line is grouped against, and the panel is where that
        # group is built.
        assert "Tick it here together with the line" in (
            row.not_shown_alone.sentence
        )

    def test_an_ENVELOPE_HOLDING_PURCHASES_is_not_offered_at_all(
        self, app, db, seed_user,
    ):
        """The other member cannot reach the caveat: it is never a candidate.

        The bank showed the purchases inside this row, not the row.  Through
        plan step ``balance:X-bi-4a``'s first cut the row was offered at its
        un-dated purchases and carried this caveat, graded here rather than
        left to the payback case because the two reached it through
        DIFFERENT published predicates (``settles_from_entries`` and
        ``repays_card_spend``).  Under ruling **R-BAL81** such a row is worth
        ``0`` to the offer and is dropped, so the caveat has ONE live subject,
        the payback above; this case pins the drop, and that the purchase
        inside the row is what is offered instead.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        purchase = a_purchase(seed_user, envelope, amount="25.00")
        db.session.flush()

        assert _candidate(seed_user, envelope.id, RowKind.TRANSACTION) is None
        assert _candidate(seed_user, purchase.id, RowKind.PURCHASE) is not None

    def test_an_ORDINARY_bill_carries_NONE(self, app, db, seed_user):
        """The discriminating half: a bill IS a line of its own.

        Without this the caveat could be returned unconditionally and every
        assertion above would still pass -- which would withdraw the panel's
        alarm from every row on it, including the payments the bank really did
        fail to make.
        """
        txn = a_transaction(seed_user, name="Electricity", amount="180.00")
        db.session.flush()

        row = _candidate(seed_user, txn.id, RowKind.TRANSACTION)

        assert row.not_shown_alone is None

    def test_a_PURCHASE_carries_NONE(self, app, db, seed_user):
        """A debit swipe is exactly what the statement shows line by line."""
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        purchase = a_purchase(seed_user, envelope, amount="25.00")
        db.session.flush()

        row = _candidate(seed_user, purchase.id, RowKind.PURCHASE)

        assert row.not_shown_alone is None

    def test_an_INCOME_ALLOWANCE_carries_NONE_and_that_is_deliberate(
        self, app, db, seed_user,
    ):
        """The KNOWN GAP, pinned so it is a decision rather than a surprise.

        The developer's ``Phone Allowance`` (`$39.54`) and ``Health Insurance
        Allowance`` (`$100.00`) arrive INSIDE one payroll deposit, so the bank
        never shows them separately either -- and no fact on the row says so.
        The app therefore cannot prove it, and the caveat is not claimed: the
        alternative was a list of row names, which is the allowlist this
        project removes rather than writes.  They stay under the panel's alarm
        caption, and the exception queue (``bank_import:X-gf``) is where being
        buried among 67 rows is answered.
        """
        allowance = a_transaction(
            seed_user, name="Phone Allowance", amount="39.54", income=True,
        )
        db.session.flush()

        row = _candidate(seed_user, allowance.id, RowKind.TRANSACTION)

        assert row.states_own_figure is True
        assert row.not_shown_alone is None


class TestTheWindowEachRowCarries:
    """Every candidate says which days the app believes its money moved."""

    def test_an_unsettled_transaction_carries_its_WHOLE_pay_period(
        self, app, seed_user,
    ):
        """Both ends, from the DERIVED calendar rather than the stored columns.

        ``pay_periods.end_date`` is a stored copy of a derivable fact that plan
        step ``pay_calendar:C4`` drops, so a bound reading it would have to be
        rewritten by that step; this reads the same span off the calendar.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = a_transaction(seed_user, name="Electricity")
            db.session.commit()

            row = _candidate(seed_user, txn.id, RowKind.TRANSACTION)

            assert row is not None
            assert row.expected_window == (period.start_date, last_covered_day(period))

    def test_a_settled_transaction_carries_the_day_it_SETTLED(
        self, app, seed_user,
    ):
        """An observation beats a belief, and the row still has both."""
        with app.app_context():
            settled_on = seed_user["bootstrap_period"].start_date
            txn = a_transaction(
                seed_user, name="Electricity", settled_on=settled_on,
                status=StatusEnum.DONE,
            )
            db.session.commit()

            row = _settled_candidate(seed_user, txn)

            assert row is not None
            assert row.expected_window == (settled_on, settled_on)

    def test_an_unsettled_purchase_carries_the_day_it_was_MADE(
        self, app, seed_user,
    ):
        """A purchase's budget clock is ONE day, so both ends are that day."""
        with app.app_context():
            made_on = seed_user["bootstrap_period"].start_date
            parent = a_transaction(
                seed_user, name="Groceries", is_envelope=True,
            )
            purchase = a_purchase(
                seed_user, parent, amount="30.00", purchased_on=made_on,
            )
            db.session.commit()

            row = _candidate(seed_user, purchase.id, RowKind.PURCHASE)

            assert row is not None
            assert row.expected_window == (made_on, made_on)


class TestEveryRowCarriesThePaycheckItIsBUDGETEDIn:
    """A candidate names the paycheck it is budgeted in, whichever table it is from.

    Plan step ``bank_import:X-gz``, ruling **R-BI9**.  The MATCH pane prints
    every row's budgeted placement, and a PURCHASE's was on no field: the row
    carried its purchase day and nothing about its envelope's paycheck, so the
    only day the pane could print for it was the settle stamp.  These grade
    the PRODUCER -- a hand-built row cannot show a constructor that fills the
    field from the wrong parent, or not at all.
    """

    def test_a_transaction_carries_its_OWN_period(self, app, seed_user):
        """The same period its window is read from, whole."""
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = a_transaction(seed_user, name="Electricity")
            db.session.commit()

            row = _candidate(seed_user, txn.id, RowKind.TRANSACTION)

            assert row is not None
            assert row.period is not None
            assert row.period.period_id == period.id
            assert row.purchased_on is None
            # The window is still the period's two ends, DERIVED from it.
            assert row.expected_window == (
                row.period.start_date, row.period.end_date,
            )

    def test_a_purchase_carries_its_ENVELOPE_s_period_and_its_own_day(
        self, app, seed_user,
    ):
        """The worked case: bought BEFORE the paycheck it is budgeted in opened.

        Entry 68 on the developer's own books -- ``Gas: Bjs``, `$47.61`,
        purchased 2026-07-13, in the ``Gas`` envelope of the paycheck opening
        2026-07-16 -- against the 2026-07-14 bank line.  The pane needs BOTH
        facts, and they are two: the purchase day is the window and the
        placement is the envelope's paycheck, which here starts three days
        after the money was spent.
        """
        with app.app_context():
            later = a_later_period(seed_user)
            made_on = later.start_date - timedelta(days=3)
            envelope = a_transaction(
                seed_user, name="Gas", is_envelope=True, period=later,
            )
            purchase = a_purchase(
                seed_user, envelope, amount="47.61", purchased_on=made_on,
            )
            db.session.commit()

            row = _candidate(seed_user, purchase.id, RowKind.PURCHASE)

            assert row is not None
            assert row.purchased_on == made_on
            assert row.period is not None
            assert row.period.period_id == later.id, (
                "the purchase's placement must be its ENVELOPE's paycheck, "
                f"not {row.period.period_id} -- the pane would print the "
                "wrong paycheck beside a correct purchase day"
            )
            assert row.period.start_date == later.start_date
            # Its window is still the day it was MADE, not the paycheck: a
            # purchase made three days before its paycheck opened is dated by
            # the day it was made.
            assert row.expected_window == (made_on, made_on)


class TestAReconciledDayIsABoundAndNotAnObservation:
    """A row TICKED on the reconcile panel spans; one settled otherwise points.

    **The two settle days this package can meet are different facts, and
    reading both as observations cost real money.**  The panel stamps the day
    the owner asserted the BALANCE for, which
    ``reconcile_service._purchases.record_settled_days`` documents as *"an
    UPPER BOUND on the true posting day"*; a statement match stamps the day the
    bank actually posted.  Measured on the developer's dev database
    2026-08-21: 59 of 61 reconciled purchases sat more than
    :data:`~app.services.statement_match._pairing.DAY_WINDOW` days past their
    purchase day, so a point at the bound put every one out of reach of its own
    bank line -- and the import recorded **50 duplicate purchases worth
    `$3,590.00`** rather than matching what the app already held.

    ``TestTheWindowEachRowCarries`` above grades the days a row carries; this
    grades which KIND of fact the settle day is, because that is what decides
    between a span and a point.
    """

    def test_a_reconciled_PURCHASE_opens_its_window_at_the_day_it_was_made(
        self, app, seed_user,
    ):
        """The defect itself: 30 days of bound, not a point 30 days out.

        The gap is deliberately wider than ``DAY_WINDOW`` (14), because inside
        that span the old rule and the new one agree and the case would pass
        against the bug.
        """
        with app.app_context():
            made_on = seed_user["bootstrap_period"].start_date
            asserted_for = made_on + timedelta(days=30)
            assertion = an_assertion(seed_user, observed_on=asserted_for)
            parent = a_transaction(
                seed_user, name="Groceries", is_envelope=True,
            )
            purchase = a_purchase(
                seed_user, parent, amount="18.64", purchased_on=made_on,
                settled_on=asserted_for, reconciled_by=assertion,
                settle_day_basis=SettledDayBasisEnum.ASSERTED,
            )
            db.session.commit()

            row = _candidate(seed_user, purchase.id, RowKind.PURCHASE)

            assert row is not None
            assert row.settle_day_basis is SettledDayBasisEnum.ASSERTED
            # Made on the 1st, asserted for the 31st: the money moved somewhere
            # in those 30 days and the app cannot say where.
            assert row.expected_window == (made_on, asserted_for)

    def test_a_reconciled_TRANSACTION_still_carries_only_its_settle_day(
        self, app, seed_user,
    ):
        """A BILL ticked on the panel keeps its point -- developer decision.

        The panel stamps a bill exactly as it stamps a purchase, so the
        argument for widening both is the same.  **The evidence is not, and
        neither is the risk** (developer decision 2026-08-22, after two
        independent adversarial reviews): ``budget.transactions`` carries zero
        reconciled rows, so that arm would ship on argument alone -- and a
        purchase has a database floor where a bill has only its pay-period
        start, which ``expected_window`` refuses to read as a point precisely
        because it is a budgeting fact rather than an observation.

        This asserts the SPAN a widened bill would open, so the case that made
        the decision cannot come back silently.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            asserted_for = last_covered_day(period) + timedelta(days=30)
            assertion = an_assertion(seed_user, observed_on=asserted_for)
            txn = a_transaction(
                seed_user, name="Electricity", settled_on=asserted_for,
                status=StatusEnum.DONE, reconciled_by=assertion,
                settle_day_basis=SettledDayBasisEnum.ASSERTED,
            )
            db.session.commit()

            row = _settled_candidate(seed_user, txn)

            assert row is not None
            # The FACT still travels -- it is the window rule that declines to
            # act on it -- so turning the arm on later is one predicate, not a
            # re-derivation.
            assert row.settle_day_basis is SettledDayBasisEnum.ASSERTED
            assert row.expected_window == (asserted_for, asserted_for)
            # What widening would have opened: a span in which
            # ``_days_outside`` scores every day zero, so same-amount lines
            # months apart all become legal top-ranked pairings.
            would_have_spanned = (asserted_for - period.start_date).days
            assert would_have_spanned > 2 * DAY_WINDOW

    def test_a_row_settled_WITHOUT_a_tick_still_carries_the_point(
        self, app, seed_user,
    ):
        """The other half of the partition, so the span is not the default.

        A statement match stamps the bank's own posting day and releases the
        link (ruling **R-FL**), which is precisely the row whose day IS an
        observation -- widening that one would re-admit the loose matching
        ``expected_window`` exists to refuse.

        **The row SAYS ``observed`` rather than being read as one because it
        carries no link** (plan step **X-az**).  That inference is finding
        **N-332**, and it could not see the third case at all: a day the owner
        typed carries no link either and is not an observation.
        """
        with app.app_context():
            settled_on = seed_user["bootstrap_period"].start_date
            txn = a_transaction(
                seed_user, name="Electricity", settled_on=settled_on,
                status=StatusEnum.DONE,
                settle_day_basis=SettledDayBasisEnum.OBSERVED,
            )
            db.session.commit()

            row = _settled_candidate(seed_user, txn)

            assert row is not None
            assert row.settle_day_basis is SettledDayBasisEnum.OBSERVED
            assert row.expected_window == (settled_on, settled_on)

    def test_a_tick_EARLIER_than_the_row_s_own_period_keeps_the_point(
        self, app, seed_user,
    ):
        """A bound below the floor bounds nothing, so the tighter answer wins.

        Ticking a row against an assertion made BEFORE its pay period opens
        says the money moved by that day and says nothing about a floor.
        Opening the window at the period would then run it BACKWARDS; the
        point is the honest reading, and it is the direction a half-stated
        fact has to fail in on a money path.
        """
        with app.app_context():
            later = a_later_period(seed_user)
            asserted_for = seed_user["bootstrap_period"].start_date
            assertion = an_assertion(seed_user, observed_on=asserted_for)
            txn = a_transaction(
                seed_user, name="Electricity", period=later,
                settled_on=asserted_for, status=StatusEnum.DONE,
                reconciled_by=assertion,
                settle_day_basis=SettledDayBasisEnum.ASSERTED,
            )
            db.session.commit()

            row = _settled_candidate(seed_user, txn)

            assert row is not None
            assert row.settle_day_basis is SettledDayBasisEnum.ASSERTED
            # expected_on (the later period's start) is AFTER settled_on, so
            # the span would be inverted; the point stands instead.
            assert later.start_date > asserted_for
            assert row.expected_window == (asserted_for, asserted_for)


class TestEveryOFFEREDRowCanCarryItsOwnTokenBack:
    """The screen must not render a tick the schema will refuse.

    Plan step ``bank_import:X-f6d-3``.  A candidate's reviewed state travels
    to the browser as one string and comes back through
    :meth:`~app.services.statement_match.ReviewedRow.from_token`, whose figure
    pattern bounds what it will read: twelve integer digits and six decimal
    places.  Every priced candidate descends from ``Numeric(12, 2)`` or from
    ``round_money`` today, so there is real headroom -- **but nothing in the
    tree fails if that stops being true**, and the failure would be silent at
    render time and total at Apply: the token renders, the schema refuses it,
    and that proposal can never be accepted from a browser at all.

    Named by adversarial security review 2026-08-23, which measured the
    round trip over 100 emitted tokens and asked for a standing control rather
    than a one-off measurement.
    """

    def test_every_priced_candidate_round_trips_through_its_token(
        self, app, db, seed_user,
    ):
        """Over the real offer set, across both row kinds and both signs."""
        # The WIDEST and NARROWEST figures the schema can hold, plus both
        # signs: ``Numeric(12, 2)`` tops out at ten integer digits, and a
        # `$0.01` purchase is the other end.  A fixture of ordinary `$180.00`
        # rows would round-trip whatever the pattern said.
        envelope = a_transaction(
            seed_user, name="Groceries", amount="1234567890.12",
            is_envelope=True,
        )
        a_purchase(seed_user, envelope, amount="0.01")
        a_transaction(seed_user, name="Paycheck", amount="2473.38",
                      income=True)
        # The third kind (plan step ``credit_card:CC-5-4a-1``): a settled
        # row is offered as its payment, whose token carries the MOVEMENT's
        # id and revision.
        a_transaction(
            seed_user, name="Electricity", amount="148.32",
            status=StatusEnum.DONE,
            settled_on=seed_user["bootstrap_period"].start_date,
        )
        # The fourth kind (leaf ``balance:X-bi-6-4c-1``): a still-planned
        # transfer is offered as its LEG, keyed by the transfer.
        create_transfer(
            seed_user, db.session, seed_user["account"],
            create_account_of_type(
                seed_user, db.session, "Savings", "Savings",
                anchor_balance=Decimal("100.00"),
                observed_on=seed_user["bootstrap_period"].start_date,
            ),
            seed_user["bootstrap_period"], amount=Decimal("250.00"),
        )
        db.session.flush()
        calendar = pay_calendar.calendar_for(seed_user["user"].id)

        offered = candidates_for(
            seed_user["account"].id, calendar, a_basis(seed_user),
        ).rows

        assert offered, "the fixture offered nothing, so this graded nothing"
        kinds = {row.kind for row in offered}
        assert kinds == set(RowKind), (
            f"only {kinds} were exercised; a kind whose builder forgot the "
            "revision would not be graded here"
        )
        for row in offered:
            reviewed = as_reviewed(row)
            assert ReviewedRow.from_token(reviewed.token) == reviewed, (
                f"{row.label!r} at {row.cash_amount} emits a token its own "
                "reader refuses, so its tick can never be accepted"
            )


class TestTheCalendarIsTheOwnershipSCOPE:
    """A row this reader returns names a period the calendar was built from.

    That property is what the SCAN's own ``pay_period_id`` filter buys, and
    both arms carry it -- ``_transaction_candidates`` and
    ``_purchase_candidates``.  ``_transaction_candidates`` also holds a Python
    guard (``if period is None: return None``), so for that arm the filter is
    defence in depth; ``_purchase_candidates`` has none, because
    ``purchase_candidate`` declines no row (it reads the calendar for the
    envelope's period since plan step ``bank_import:X-gz``, and a purchase is
    dated by its own day whether or not that lookup answers), so there the
    filter is the only thing keeping an undatable row out of a money-offering
    set.

    **The scope is a PERIOD SET, not an owner comparison**, and it has been
    since pay-calendar plan step C4-a-4.  A first version of this docstring
    said the reader "dereferences the answer without a guard, so a row whose
    period the calendar does not carry would raise"; it does not raise, it
    declines.
    """

    def test_every_offered_row_is_datable_by_the_calendar_it_was_scoped_to(
        self, app, seed_user,
    ):
        """The totality argument, asserted rather than reasoned about."""
        with app.app_context():
            a_transaction(seed_user, name="Electricity")
            parent = a_transaction(
                seed_user, name="Groceries", is_envelope=True,
            )
            a_purchase(seed_user, parent, amount="30.00")
            db.session.commit()

            calendar = pay_calendar.calendar_for(seed_user["user"].id)
            rows = candidates_for(
                seed_user["account"].id, calendar, a_basis(seed_user),
            ).rows

            assert rows
            assert all(row.expected_window is not None for row in rows)

    def test_a_row_in_ANOTHER_owner_s_period_cannot_EXIST_to_be_offered(
        self, app, seed_user, second_user,
    ):
        """The shape this class used to scope with is now UNSTORABLE.

        **This case used to build a foreign-owner row and assert the scope
        excluded it.**  Plan step ``pay_calendar:C13-a`` made that row
        unstorable -- the builder states this owner, the period is somebody
        else's, and ``fk_transactions_owner_period`` refuses the INSERT -- so
        what it can still grade is the refusal.  The SCOPE is graded by
        :func:`test_a_row_in_a_period_the_CALENDAR_LACKS_is_not_offered`
        below, which this step added for exactly that reason: without it,
        deleting either arm's filter leaves this suite green.

        **The row is a ONE-OFF** (``template=False``, the producer's row since
        plan step balance:X-bi-7c) and the builder refuses another owner's
        paycheck BEFORE any write, as the engine's builder does for a row of
        a recurring definition (``RecurrenceWindowError``) -- one tier above
        the key.  The key itself, ``fk_transactions_owner_period`` over
        ``(pay_period_id, user_id)`` on every row, is graded on a bare row by
        ``test_models/test_c13a_transaction_owner_key.py``; this case grades
        that the one-off builder cannot even ask for the trespasser (ruling
        **R-BAL59**, X-bi-7c's rule-5 batch).
        """
        with app.app_context():
            theirs = PayPeriod(
                user_id=second_user["user"].id,
                start_date=date(2024, 1, 5) + timedelta(days=14),
            )
            db.session.add(theirs)
            db.session.flush()
            with pytest.raises(AssertionError, match="not in user"):
                a_transaction(
                    seed_user, name="Not yours", period=theirs, template=False,
                )
            assert db.session.query(Transaction).filter_by(
                pay_period_id=theirs.id,
            ).count() == 0

    def test_a_row_in_a_period_the_CALENDAR_LACKS_is_not_offered(
        self, app, seed_user,
    ):
        """Neither arm offers a row the calendar it was scoped to cannot date.

        **This replaces coverage plan step ``pay_calendar:C13-a`` removed.**
        The case it replaces fed the scan a FOREIGN-OWNER row, which the
        composite keys now refuse, and with it gone both arms' period filters
        could be deleted with this module green -- measured, 869 passed.

        The input is the one the keys still permit and the reconcile suite
        already uses for the same clause
        (``test_reconcile_service.test_the_outstanding_set
        .TestTheScopeIsTheCALENDARsNotTheTables``):
        the owner's OWN period, missing from the calendar the request was
        scoped to.  Not hypothetical -- it is what a concurrent writer produces
        between a request's calendar read and its scan.

        **What this case fires on, measured rather than claimed** (2026-09-02,
        each filter deleted in turn):

        * ``_purchase_candidates``' filter -- **this case FAILS.**  That arm
          has no guard behind it: ``purchase_candidate`` declines no row
          (since plan step ``bank_import:X-gz`` it reads the calendar for
          the envelope's period, but a purchase is dated by its own day and
          the constructor stays total), so the filter is the only thing
          keeping an undatable purchase out of a money-offering set.  This is
          the arm that was uncovered.
        * ``_transaction_candidates``' filter -- this case still passes, and
          so does every other.  The guard at ``if period is None: return None``
          declines the row anyway, so that filter is defence in depth and
          cannot be graded through the public return AT ALL.  Saying so is the
          point: a future reader who deletes it will see a green suite, and
          this paragraph is the only warning there is.
        """
        with app.app_context():
            later = a_later_period(seed_user)
            bill = a_transaction(
                seed_user, name="Internet", amount="60.00", period=later,
            )
            envelope = a_transaction(
                seed_user, name="Groceries", is_envelope=True, period=later,
            )
            purchase = a_purchase(seed_user, envelope, amount="30.00")
            db.session.commit()

            whole = pay_calendar.calendar_for(seed_user["user"].id)
            short = pay_calendar.PayCalendar.from_paydays(
                [
                    (period.period_id, period.start_date)
                    for period in whole.periods
                    if period.period_id != later.id
                ],
                whole.eras,
                seed_user["user"].id,
                history_opens_on=None,
            )
            rows = candidates_for(
                seed_user["account"].id, short, a_basis(seed_user),
            ).rows
            offered = {(row.kind, row.row_id) for row in rows}

            assert (RowKind.TRANSACTION, bill.id) not in offered
            assert (RowKind.PURCHASE, purchase.id) not in offered
            # ...and the same reader DOES offer a row the short calendar holds,
            # so the assertions above cannot pass on an empty offer set.
            inside = a_transaction(seed_user, name="Electricity")
            db.session.commit()
            assert any(
                row.row_id == inside.id and row.kind is RowKind.TRANSACTION
                for row in candidates_for(
                    seed_user["account"].id, short, a_basis(seed_user),
                ).rows
            )
