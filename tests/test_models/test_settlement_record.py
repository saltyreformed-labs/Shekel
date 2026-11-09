"""The settlement RECORD: what a row asserts, and what it merely remembers.

Plan step **X-au-c3**: *a row is a PLAN -- ``estimated_amount`` priced by
``amount_source_id`` -- until its money moves, and a RECORD of what moved once it
has.*  The two are NOT one fact with one lifetime, and believing they were is
the error this module grades the correction of.  WHAT MOVED is the row's
COVERING MOVEMENT -- a row of ``budget.transaction_entries`` carrying the
figure and who wrote it (``status_seam._covering``), the record's ONE home
since plan step ``balance:X-bi-4b-2`` (ruling **R-BAL80**; through
``X-bi-4b-1`` the row's own ``settled_amount`` / ``settled_basis_id`` carried a
second copy, written by the seam and, from 4b-1, read by nothing); ``settled_on``
(with ``reconciled_by_id``) is the ASSERTION that it moved on a named day, and a
revert withdraws the assertion while KEEPING what moved (the movement, un-dated,
since ``X-bi-3e-2``).

**The reader reads the row's ENTRIES** -- ``row_valuation.settled_figure`` --
and a settled row holding none is the ``$0.00`` record (ruling **R-BAL82**): a
movement of nothing is not one, so a close of nothing has no entry.  Its SQL
twin, ``posting_reads.settled_figure_clause``, had no reader in ``app/`` since
leaf ``balance:X-bi-6-4a``, and its parity class here
(``TestTheSQLTierReadsTheSameHomeAsPython``) went at plan step plan#2914 with
the stored ledger's readers.  The cases that read a figure lay the movement
(:func:`~tests._test_helpers.cover_bare_settled_row`), and the case that once
graded the reader's REFUSAL of a settled row recording nothing grades its
answer.

**The record's invariants are the CONSTRUCTOR's**
(:class:`app.services.status_seam.Settlement`), and this module is where that
is graded: a settle door BUILDS a ``Settlement`` to hand the seam, so a
malformed record cannot be constructed and therefore cannot be written.  Two
CHECKs stated the storable half of the pairing while the row carried the
columns -- ``ck_transactions_settled_amount_needs_basis`` (a stored figure
names its provenance) and ``ck_transactions_settle_day_needs_a_record`` (a row
asserting a settle DAY records what moved), both IMPLICATIONS after a draft's
BICONDITIONAL (``ck_transactions_settlement_recorded``) made every revert
destroy the user's figure -- and went with the columns; the states they
refused are now either unconstructible (a figure with no writer) or legal (a
dated close of nothing).

**Every test here is a FIRING CONTROL** (``docs/plans/verification.md`` standard
4).  A test that merely asserted a rule EXISTS would pass against a rule
admitting everything, so each one below writes the state the rule is supposed
to refuse and asserts the refusal, or writes the legitimate state and asserts
what the reader reads.

The shapes under test, and the real writer each stands for:

* **a figure stating no writer, and a writer stating no figure** -- the
  constructor invariant, from both sides; a door that writes what moved and
  forgets who said so is the overload ``actual_amount`` carried;
* **a settled row asserting a DAY and holding no entry** -- the ``$0.00``
  record, dated: legal, and read as ``0``;
* **a Projected row still holding its un-dated movement** -- the RETAINED
  state a revert leaves, which a draft refused and which is worth nothing to a
  balance because the STATUS decides;
* **the whole record together** -- the legitimate act, read off the movement
  even where the row's plan says otherwise;
* **entering the settled band with no record at all** -- the seam's own
  refusal, which is what makes "a settled row states what moved" a property of
  the seam rather than a convention its callers keep.
"""

from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import (
    MovementFigureSourceEnum,
    StatusEnum,
)
from app.extensions import db
from app.models.ref import TransactionType
from app.models.amount_ownership import AmountOwnership
from app.models.transaction import Transaction
from app.services.row_valuation import settled_figure
from app.services.status_seam import (
    Settlement,
    apply_status_change,
    recorded_settlement,
)
from tests._test_helpers import (
    bare_expense_template,
    cover_bare_settled_row,
    settle_day_columns,
)

def _make_transaction(seed_user, seed_periods, **overrides):
    """Return an UNFLUSHED Projected expense row, with *overrides* applied.

    Deliberately bare: these tests write the row's status and day pair
    directly, because the door helpers exist precisely to make the graded
    states unreachable and a control that went through them would grade the
    helper instead of the rule.

    Args:
        seed_user: The ``seed_user`` fixture payload.
        seed_periods: The ``seed_periods`` fixture list.
        **overrides: Column values to set or replace -- the settle day and the
            status, which are what every test here varies.

    Returns:
        The unflushed :class:`~app.models.transaction.Transaction`.

    **BARE on purpose; its pricing link is a rule-less definition of the
    owner's** (plan step ``balance:X-bi-7d-1``).  The subject here is a
    CONSTRAINT of ``budget.transactions``, and a control that reached the row
    through a door would grade the door -- so the row is still constructed by
    hand.  What it stopped being is LINK-LESS: the family's cutover
    (``X-bi-7d-2``) re-cuts ``ck_transactions_one_pricing_link`` to ``= 1``,
    so every row staged here names its own definition
    (:func:`~tests._test_helpers.bare_expense_template`) and carries the
    paycheck's start as the day it is due and the occurrence it answers --
    the shape ``one_off.place_row_of`` writes -- unless the case states
    otherwise.  The link is never the subject.
    """
    expense_type = (
        db.session.query(TransactionType).filter_by(name="Expense").one()
    )
    definition = bare_expense_template(
        db.session, seed_user, name="Settlement control definition",
    )
    fields = {
        "user_id": seed_periods[0].user_id,
        "template_id": definition.id,
        "pay_period_id": seed_periods[0].id,
        "scenario_id": seed_user["scenario"].id,
        "account_id": seed_user["account"].id,
        "status_id": ref_cache.status_id(StatusEnum.PROJECTED),
        "name": "Settlement control",
        "category_id": seed_user["categories"]["Rent"].id,
        "transaction_type_id": expense_type.id,
        "estimated_amount": Decimal("300.00"),
        "due_date": seed_periods[0].start_date,
        "occurs_on": seed_periods[0].start_date,
    }
    fields.update(overrides)
    # **The settle DAY carries its basis unless the caller states one** (plan
    # step **X-az**).  These builders write bare columns on purpose -- a control
    # routed through a door would grade the door -- but a row is only bare on
    # the axis its test is ABOUT: a day with no basis violates
    # ``ck_*_settle_day_basis_pairing`` before it can reach the constraint the
    # test is grading, so the pair is completed here and a test that means to
    # break it says ``settled_day_basis_id`` outright.
    if "settled_day_basis_id" not in overrides:
        fields.update(settle_day_columns(fields.get("settled_on")))
    # **The amount-ownership pair is ONE attribute** (plan step X-au-k), so the
    # figure this builder splats becomes the row's OWNERSHIP at the last
    # moment -- after every line above that reads it as a column.
    fields["amount_ownership"] = AmountOwnership.own(fields.pop("estimated_amount"))
    return Transaction(**fields)


class TestTheRecordIsTheCoveringMovement:
    """What moved lives on the movement; the assertion lives on the row.

    **A figure and its provenance share a lifetime; the settle DAY does not --
    in ONE direction.**  The covering movement says what the bank took and
    who said so: a fact about the ROW.  ``settled_on`` and ``reconciled_by_id``
    assert that it moved on a named day and a named statement showed it, and
    a revert withdraws exactly that.  A draft of plan step X-au-c3 paired the
    day with the record as a BICONDITIONAL (``ck_transactions_settlement_
    recorded``), which welded the two lifetimes together and made every revert
    destroy the user's figure; the surviving implication
    (``ck_transactions_settle_day_needs_a_record``) went with the row's figure
    columns at ``X-bi-4b-2``, because a dated settled row holding no movement
    is the ``$0.00`` record (ruling **R-BAL82**) and not a row recording
    nothing.
    """

    def test_a_dated_settled_row_holding_no_entry_is_the_zero_record(
        self, app, db, seed_user, seed_periods,
    ):
        """The state ``ck_transactions_settle_day_needs_a_record`` refused: legal now.

        A settled row asserting the day its money moved and holding no entry
        is a close of nothing on that day.  Stored, read as ``0``, and
        recorded as ``Settlement(None, None)`` -- its entries are its record,
        and there are none.
        """
        with app.app_context():
            txn = _make_transaction(
                seed_user, seed_periods,
                status_id=ref_cache.status_id(StatusEnum.DONE),
                settled_on=seed_periods[0].start_date,
            )
            db.session.add(txn)
            db.session.flush()

            assert txn.id is not None
            assert txn.entries == []
            assert settled_figure(txn) == Decimal("0")
            assert recorded_settlement(txn) == Settlement(None, None)
            db.session.rollback()

    def test_an_undated_movement_on_a_projected_row_is_the_REVERTED_state(
        self, app, db, seed_user, seed_periods,
    ):
        """What the row keeps after a revert -- legal, and worth nothing.

        **A draft of this step REFUSED this state and that was the defect.**
        ``ck_transactions_settlement_recorded`` paired the day with the record,
        so withdrawing the assertion had to destroy the figure -- and the
        full-edit popover instructs the user to revert in order to edit, so
        following the app's own advice deleted a number they had read off a
        statement.

        The row is Projected here, carrying the movement it recorded when it
        last settled, un-dated (plan step X-bi-3e-2).  Two assertions, and the
        second is the one that makes the first safe: the record reads the
        movement, and no valuation counts it, because ``settled_figure`` asks
        the STATUS rather than the record.
        """
        with app.app_context():
            txn = _make_transaction(
                seed_user, seed_periods,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                settled_on=None,
            )
            db.session.add(txn)
            db.session.flush()
            cover_bare_settled_row(
                db.session, txn, "300.00", submitted="300.00",
            )

            (movement,) = txn.covering_movements
            assert movement.settled_on is None
            assert recorded_settlement(txn) == Settlement(
                Decimal("300.00"), MovementFigureSourceEnum.TYPED,
            )
            assert settled_figure(txn) is None
            db.session.rollback()

    def test_a_SETTLED_row_holding_no_entry_records_ZERO_and_never_its_plan(
        self, app, db, seed_user, seed_periods,
    ):
        """The state every settled row was in before X-au-c3, read as ``$0.00``.

        Settled with no record and no entry: every reader used to fall back to
        the row's PLAN here, which is the silent substitution X-au-c3 removed
        -- a forecast published as a fact about money that has already moved.
        Through ``X-bi-4a`` the reader REFUSED this row (``AmountUnresolvable``,
        "records no settlement"); since plan step ``balance:X-bi-4b-1`` the
        record is the row's entries and a settled row holding none IS the
        ``$0.00`` record (ruling **R-BAL82**: a movement of nothing is not
        one, so a close of nothing has no entry), which is what the reader
        answers.  What this case still grades is the substitution: the answer is
        ``0``, not the ``$300.00`` plan.

        **The row carries no settle DAY**: a settled row with no day is
        ``integrity_check`` DC-11's first arm, not this reader's.
        """
        with app.app_context():
            txn = _make_transaction(
                seed_user, seed_periods,
                status_id=ref_cache.status_id(StatusEnum.DONE),
                settled_on=None,
            )
            db.session.add(txn)
            db.session.flush()

            assert settled_figure(txn) == Decimal("0")

    def test_the_whole_record_together_is_read_off_the_movement(
        self, app, db, seed_user, seed_periods,
    ):
        """The legitimate write, without which the controls above prove nothing.

        Settled, dated, and carrying the movement the seam lays: the figure a
        person typed, read off the movement and not the ``$300.00`` plan.
        """
        with app.app_context():
            txn = _make_transaction(
                seed_user, seed_periods,
                status_id=ref_cache.status_id(StatusEnum.DONE),
                settled_on=seed_periods[0].start_date,
            )
            db.session.add(txn)
            db.session.flush()
            cover_bare_settled_row(
                db.session, txn, "300.00", submitted="287.31",
            )

            assert txn.id is not None
            assert settled_figure(txn) == Decimal("287.31")
            assert recorded_settlement(txn) == Settlement(
                Decimal("287.31"), MovementFigureSourceEnum.TYPED,
            )
            db.session.rollback()

    # ``test_a_figure_with_no_basis_is_refused``, ``test_a_settle_day_with_no_
    # record_is_refused``, ``test_a_purchases_record_stores_no_figure_and_is_
    # accepted`` and ``test_an_unsettled_row_carrying_a_figure_is_refused``
    # graded the two CHECKs over the row's own figure columns and went with
    # them at plan step ``balance:X-bi-4b-2``: a figure with no writer is
    # unconstructible (``TestPurchasesIffNoStoredFigure``), a record beside an
    # unsettled status is the seam's refusal
    # (``TestTheSeamRefusesAnUnrecordedSettle``), and the ``purchases`` record
    # is ``Settlement(None, None)`` with no movement (``test_covering_movement``).


class TestPurchasesIffNoStoredFigure:
    """The record's pairing, enforced in ``Settlement``'s constructor.

    A settle door builds a ``Settlement`` to hand the seam, so a malformed
    record cannot be constructed and therefore cannot be written; these are
    the negative controls.  (While the row carried its own figure columns a
    CHECK backstopped the storable half; the constructor is the one home since
    plan step ``balance:X-bi-4b-2``, and its third rule -- no negative figure
    -- is graded in ``test_transaction_constraints`` beside the plan's CHECK.)

    **The record's stated field is the figure's SOURCE since plan step
    X-bi-3e-1** (ruling **R-BAL69**), so the rule reads *a figure names its
    writer, and a record with no figure names none*.  The two refusals below
    are the two halves of that biconditional, and ``stated`` is the one
    reading derived from the source.
    """

    def test_a_figure_stating_no_writer_is_refused(self, app):
        """A figure with nobody said to have written it is refused.

        The writer this stands for is a settle that "helpfully" caches the entry
        sum on the parent -- which is exactly what ``settle_from_entries`` used
        to do, and what needed ``entry_service`` to re-derive the column on every
        entry change afterwards: a ``purchases`` record carrying a figure.
        """
        with app.app_context():
            with pytest.raises(ValueError, match="must say who wrote it"):
                Settlement(amount=Decimal("48.98"), source=None)

    def test_a_writer_with_no_figure_is_refused(self, app):
        """A record naming a writer and storing no figure is refused.

        The mirror, and the state :func:`app.services.row_valuation.settled_figure`
        REFUSES to read: answering ``None`` there would send the caller to the
        row's PLAN, which is the fallback this whole step removes.  Every
        source member is refused, the settle's own ``resolved`` included.
        """
        with app.app_context():
            for source in MovementFigureSourceEnum:
                with pytest.raises(ValueError, match="stores no figure"):
                    Settlement(amount=None, source=source)

    def test_both_legitimate_shapes_construct(self, app):
        """The accepting cases, without which the two refusals prove nothing."""
        with app.app_context():
            stored = Settlement(
                amount=Decimal("48.98"),
                source=MovementFigureSourceEnum.RESOLVED,
            )
            assert stored.amount == Decimal("48.98")

            from_entries = Settlement(amount=None, source=None)
            assert from_entries.amount is None

    @pytest.mark.parametrize(
        ("source", "stated"),
        [
            (None, False),
            (MovementFigureSourceEnum.RESOLVED, False),
            (MovementFigureSourceEnum.TYPED, True),
            (MovementFigureSourceEnum.OBSERVED, True),
        ],
    )
    def test_stated_is_a_person_or_the_bank_and_never_the_settle_itself(
        self, app, source, stated,
    ):
        """``stated`` is what a re-settle HONOURS, stated once (X-bi-4b-1).

        ``Settlement.from_settle`` keeps a retained record and
        ``status_seam.honoured_correction`` publishes it exactly when somebody
        stated the figure -- a person (``typed``) or the bank's line
        (``observed``); the settle's own ``resolved`` pricing is re-derived,
        and a ``purchases`` record states nothing.  It read ``basis is
        CORRECTED`` off the row's own basis column through ``X-bi-4a``, and
        a ``basis`` property derived from the source answered the seam's
        column write through ``X-bi-4b-1``; both went at ``X-bi-4b-2``.
        """
        with app.app_context():
            record = Settlement(
                amount=None if source is None else Decimal("48.98"),
                source=source,
            )
            assert record.stated is stated

    # ``test_the_reader_refuses_a_record_written_around_the_rule`` -- a
    # ``derived`` record with no stored figure, which ``settled_figure`` refused
    # rather than reading the plan -- is DELETED with its subject at plan step
    # ``balance:X-bi-4b-1``: the reader no longer reads the row's columns, so
    # a record "written around the rule" in them is invisible to it, and what
    # it reads (the entries) has no malformed shape to refuse.  The
    # constructor's own two refusals above are the whole of the guard now.


class TestTheSeamRefusesAnUnrecordedSettle:
    """Entering the settled band with no record is a programming error."""

    def test_entering_the_band_with_no_settlement_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """The seam demands a record, so no door can settle a row silently.

        A ``ValueError`` rather than a ``ValidationError``: no form can express
        it, so it is a mistake at the call site and not a user's.  Without this
        the door would write the status alone and the row would land dated with
        no movement -- a close of nothing published as the row's money, and
        before this step worse than that, because the reader fell back to the
        plan and published a forecast as a fact.
        """
        with app.app_context():
            txn = _make_transaction(seed_user, seed_periods)
            db.session.add(txn)
            db.session.flush()
            status_before = txn.status_id

            with pytest.raises(ValueError, match="no settlement record"):
                apply_status_change(
                    txn, ref_cache.status_id(StatusEnum.DONE),
                )

            # A refused call leaves the row untouched, which is the ordering the
            # seam's own refusals are placed for.
            assert txn.status_id == status_before
            assert txn.settled_on is None
            db.session.rollback()

    def test_a_record_offered_for_an_unsettled_status_is_refused(
        self, app, db, seed_user, seed_periods,
    ):
        """The other direction: a row records what moved only while it is settled.

        A ``ValidationError`` here rather than a ``ValueError``, because it is
        the twin of the settle-day refusal and reaches the route as a 400.
        """
        with app.app_context():
            from app.exceptions import ValidationError  # noqa: PLC0415

            txn = _make_transaction(
                seed_user, seed_periods,
                status_id=ref_cache.status_id(StatusEnum.DONE),
                settled_on=seed_periods[0].start_date,
            )
            db.session.add(txn)
            db.session.flush()
            cover_bare_settled_row(db.session, txn, "300.00")

            with pytest.raises(ValidationError, match="not a settled status"):
                apply_status_change(
                    txn, ref_cache.status_id(StatusEnum.PROJECTED),
                    settlement=Settlement(
                        amount=Decimal("300.00"),
                        source=MovementFigureSourceEnum.RESOLVED,
                    ),
                )
            db.session.rollback()


# ``TestTheUpgradeRefusesASettledRowWithNoFigure`` -- two cases that renamed
# ``settled_amount`` back to ``actual_amount`` to drive migration
# ``e4b8a71c0f36``'s pre-flight ``refuse_settled_rows_without_a_plan`` -- was
# DELETED at plan step ``balance:X-bi-4b-2`` (developer ruling R-BAL84,
# 2026-09-20): its staged input, the row's own figure column, cannot be built
# at head once migration ``45f10b870c8b`` deletes it.  **That pre-flight is
# UNGRADED at head**: it runs only on an old-dump restore (the test template
# replays the chain from base over an empty database, where it refuses
# nothing), and no test at head drives it.  What head grades instead is the
# state it refused, a settled row storing no figure, which is the ``$0.00``
# record now (``TestTheRecordIsTheCoveringMovement::
# test_a_SETTLED_row_holding_no_entry_records_ZERO_and_never_its_plan``), and
# that ``45f10b870c8b``'s downgrade rebuilds the column the pre-flight read
# from the movements exactly (``test_the_record_is_its_movements``).
