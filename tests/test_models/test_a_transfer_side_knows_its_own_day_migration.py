"""Migration ``d3b8f5a1c7e2``: a transfer side knows its own day.

Plan step ``balance:X-bi-6-4c-3``, rulings **R-BAL143** (relabel, by
predicate) and **R-BAL165** (a pre-step typed day stays typed on both sides).
The revision seeds ``ref.settled_day_bases.borrowed`` and relabels every
transfer side whose basis its writer did not earn -- on the shadow and its
covering movement together -- and REFUSES a relabel the day function would
never produce.  No day, figure or link moves.

Driven against the test database, which is built at head, through the
revision's own module-level pieces: :func:`classify_sides` grades each arm of
the predicate on the states the doors below plan step
``balance:X-bi-6-4d-2`` wrote (with the audit rows the database's triggers
write for them), :func:`refuse_unborrowable` fires on a side whose day is not
its sibling's, and the whole ``upgrade`` / ``downgrade`` pair is run over the
pre-step shapes, which the downgrade must restore exactly.

**Every settled pre-step pair is planted by SQL** since that step, which files
a transfer side's record under the TRANSFER and stops writing a twin's
status, day, basis and statement link -- so no door at head writes the shape
this revision reads (the twins settled, each holding a covering movement).
Each state is written as the pre-step doors wrote it: the parent's status,
each twin's status, day and basis in ONE UPDATE (the audit row the stamp arm
reads), a movement under each twin mirroring it, a correction writing twin and
movement together, and a tick linking both.  Setup only, approved by the
developer 2026-10-08 (rule 5, ruling **balance:R-BAL234**): no arm, basis or
day a case checks changed.  Until that step the service wrote these states.

**And no transfer has a twin at head** since plan step
``balance:X-bi-6-4d-2``, whose revision (:data:`_TWINS_DELETED`) deletes
them and refuses a new one.  So each case runs that revision's downgrade
ONCE, after the two above it (``c956c1d83108``'s, which puts back the
transfer column a twin names, then :data:`_SIDE_RECORD_FIGURE`'s), once its
transfers exist and before any plant (:func:`_twins_built`):
it builds each transfer's two twins, saying its transfer's status and, its
sides holding no record, no day -- the Projected, undated pair the create
door below that step wrote, which the plants then settle -- on the schema
every case here ran on until that step.

**Two arms are told apart by a further plant**: the test database's writes
and the migration run as ONE role, so every write onto a settled dateless row
reads as the backfill's (``entered_guess``) until its audit row's ``db_user``
is re-labelled; and a statement member that existed and was later removed is
planted as the audit INSERT the member's trigger would have written.
"""

import json
from datetime import timedelta
from decimal import Decimal
from unittest import mock

import pytest
from sqlalchemy import text

from app import ref_cache
from app.enums import MovementFigureSourceEnum, SettledDayBasisEnum, StatusEnum
from app.extensions import db
from app.models.account import AccountAnchorHistory
from app.models.transaction import Transaction
from app.services.settle_day import SettleDay, record_settle_day
from app.utils.dates import display_today
from tests._test_helpers import (
    an_asserted_day,
    an_entered_day,
    an_observed_day,
    create_savings_account,
    create_transfer,
    load_migration_module,
    open_books_before_the_first_assertion,
    run_migration_callable,
    step_down_through_the_transfer_column_drop,
)

_MIGRATION = load_migration_module(
    "d3b8f5a1c7e2_a_transfer_side_knows_its_own_day.py",
)

#: Plan step ``balance:X-bi-6-4d-2``'s revision, which deletes every twin and
#: whose downgrade builds them back (the module docstring).
_TWINS_DELETED = load_migration_module("82f7b7ed4332_a_transfer_is_one_row.py")

#: Plan step ``balance:X-bi-6-4d-2``'s head revision, which moves a side's
#: figure onto its transfer and installs the band's figure arm; its downgrade
#: runs first on the way down (the module docstring).
_SIDE_RECORD_FIGURE = load_migration_module(
    "b7d2e4f19a63_a_side_record_holds_no_figure.py",
)


class TestTheRevision:
    """Where the revision sits in the chain."""

    def test_revision_and_down_revision(self):
        """revision / down_revision pin the migration into the chain."""
        assert _MIGRATION.revision == "d3b8f5a1c7e2"
        assert _MIGRATION.down_revision == "c4a4e7d1b9f2"


#: Every planted transfer's amount, and so each side's record's.
_AMOUNT = Decimal("300.00")


def _transfer(seed_user, period, name):
    """Return a Projected $300.00 Checking -> Savings transfer named *name*."""
    savings = create_savings_account(
        seed_user, db.session, f"Savings {name}", Decimal("0.00"),
    )
    open_books_before_the_first_assertion(db.session, savings)
    xfer = create_transfer(
        seed_user, db.session, seed_user["account"], savings, period,
        amount=_AMOUNT, name=name,
    )
    db.session.commit()
    return xfer


def _twins_built():
    """Run :data:`_TWINS_DELETED`'s downgrade: two twins for every transfer (the module docstring).

    The two revisions above it go down first, as the way down does:
    ``c956c1d83108``'s puts back the transfer column the twins are built
    into (:func:`tests._test_helpers.step_down_through_the_transfer_column_drop`),
    and :data:`_SIDE_RECORD_FIGURE`'s withdraws the band's figure arm, which
    no revision below holds.  Once per case, after its transfers exist: a
    transfer made after it gets no twin, and a second run fails, since the
    refusal it drops is gone.  Committed, as every migration call is.
    """
    step_down_through_the_transfer_column_drop(db.session)
    run_migration_callable(_SIDE_RECORD_FIGURE.downgrade, db.session)
    run_migration_callable(_TWINS_DELETED.downgrade, db.session)


def _shadows(xfer):
    """Return ``(expense, income)`` shadows of *xfer*.

    Their ids by raw SQL, the rows then by the ORM: the model has had no
    ``transfer_id`` since ``c956c1d83108``, and the twins exist only below it.
    """
    twin_ids = db.session.execute(text(
        "SELECT id FROM budget.transactions WHERE transfer_id = :t"
    ), {"t": xfer.id}).scalars().all()
    rows = (
        db.session.query(Transaction).filter(Transaction.id.in_(twin_ids)).all()
    )
    return (
        next(r for r in rows if r.account_id == xfer.from_account_id),
        next(r for r in rows if r.account_id == xfer.to_account_id),
    )


def _arms(xfer):
    """Return ``(expense arm, income arm)`` as :func:`classify_sides` files them."""
    arm_of = {
        shadow_id: arm
        for shadow_id, transfer_id, arm in _MIGRATION.classify_sides(
            db.session.connection(),
        )
        if transfer_id == xfer.id
    }
    expense, income = _shadows(xfer)
    return arm_of[expense.id], arm_of[income.id]


def _ny_today():
    """Return the DATABASE's America/New_York date for this transaction's ``now()``.

    The stamp arm compares a day with its own audit row's ``executed_at`` in
    that zone, and ``executed_at`` is ``now()`` -- so a stamp is built on this
    day, read in the same transaction as the write it describes.
    """
    return db.session.execute(
        text("SELECT (now() AT TIME ZONE 'America/New_York')::date"),
    ).scalar_one()


def _settle_before_the_step(xfer, settle_day):
    """Settle *xfer* on *settle_day* in the pre-step shape, by SQL (the module docstring).

    The parent's status; each twin's status, day and basis in ONE UPDATE, so
    its audit row carries the status moving into the band beside the day; and
    under each twin a covering movement carrying the twin's day and basis
    (the design's M4) at the transfer's figure.  The transfer stays inside
    the band rule: it is settled and no record names it by a side link.
    """
    done = ref_cache.status_id(StatusEnum.DONE)
    basis = ref_cache.settled_day_basis_id(settle_day.basis)
    resolved = ref_cache.movement_figure_source_id(
        MovementFigureSourceEnum.RESOLVED,
    )
    twins = _shadows(xfer)
    db.session.execute(
        text("UPDATE budget.transfers SET status_id = :s WHERE id = :t"),
        {"s": done, "t": xfer.id},
    )
    for twin in twins:
        db.session.execute(text(
            "UPDATE budget.transactions SET status_id = :s, settled_on = :d, "
            "settled_day_basis_id = :b WHERE id = :id"
        ), {"s": done, "d": settle_day.day, "b": basis, "id": twin.id})
        db.session.execute(text(
            "INSERT INTO budget.transaction_entries (transaction_id, "
            "account_id, owner_id, user_id, amount, description, "
            "purchased_on, settled_on, settled_day_basis_id, "
            "covers_settlement, is_credit, figure_source_id, version_id) "
            "VALUES (:id, :account, :owner, :owner, :amount, :name, :d, :d, "
            ":b, TRUE, FALSE, :source, 1)"
        ), {
            "id": twin.id, "account": twin.account_id, "owner": twin.user_id,
            "amount": _AMOUNT, "name": twin.name, "d": settle_day.day,
            "b": basis, "source": resolved,
        })
    db.session.commit()


def _correct_before_the_step(xfer, settle_day, *, account_id=None):
    """Write *settle_day* onto *xfer*'s settled twins, by SQL, as a pre-step correction did.

    The twin's day and basis and its covering movement's together (the
    movement's ``purchased_on`` follows its day, as the seam's does).
    *account_id* narrows the write to the side on that account.
    """
    basis = ref_cache.settled_day_basis_id(settle_day.basis)
    for twin in _shadows(xfer):
        if account_id is not None and twin.account_id != account_id:
            continue
        values = {"d": settle_day.day, "b": basis, "id": twin.id}
        db.session.execute(text(
            "UPDATE budget.transactions SET settled_on = :d, "
            "settled_day_basis_id = :b WHERE id = :id"
        ), values)
        db.session.execute(text(
            "UPDATE budget.transaction_entries SET settled_on = :d, "
            "settled_day_basis_id = :b, purchased_on = :d "
            "WHERE transaction_id = :id AND covers_settlement"
        ), values)
    db.session.commit()


def _paid_press_before_the_step(xfer):
    """Settle *xfer* as the pre-step Paid press did: status and an ``entered`` today, one write.

    The day is read in the transaction that writes it, so it is the date of
    that write's own audit row (:func:`_ny_today`).
    """
    _settle_before_the_step(xfer, an_entered_day(_ny_today()))


def _link_the_expense_side(xfer):
    """Tick the expense side against Checking's latest assertion (the reconcile panel's link).

    As the pre-step tick's door (``transfer_service.record_clearing``) did:
    the side's twin and its covering movement take the link together.
    """
    anchor = (
        db.session.query(AccountAnchorHistory)
        .filter_by(account_id=xfer.from_account_id)
        .order_by(AccountAnchorHistory.id.desc())
        .first()
    )
    values = {"a": anchor.id, "id": _shadows(xfer)[0].id}
    db.session.execute(text(
        "UPDATE budget.transactions SET reconciled_by_id = :a WHERE id = :id"
    ), values)
    db.session.execute(text(
        "UPDATE budget.transaction_entries SET reconciled_by_id = :a "
        "WHERE transaction_id = :id AND covers_settlement"
    ), values)
    db.session.commit()


def _a_statement_once_named_the_expense_side(xfer):
    """Plant the audit INSERT a statement match member on the expense movement left.

    What every pre-step match wrote for the side it matched (the design's M3:
    one member per ``observed`` pair, on the Checking side).  Planted as the
    audit row rather than a member, because the predicate reads members EVER
    -- a member later removed still counts -- and that is the half this grades.
    """
    expense = _shadows(xfer)[0]
    (movement,) = [m for m in expense.entries if m.covers_settlement]
    db.session.execute(text(
        "INSERT INTO system.audit_log "
        "(table_schema, table_name, operation, row_id, new_data) "
        "VALUES ('budget', 'statement_match_members', 'INSERT', 1, "
        "CAST(:data AS jsonb))"
    ), {"data": json.dumps({"transaction_entry_id": movement.id})})
    db.session.commit()


class TestTheArms:
    """Each arm of the relabel predicate, on the state its writer leaves."""

    @pytest.mark.server_clock
    def test_a_pre_step_paid_press_is_a_stamp(self, app, seed_user, seed_periods_today):
        """Status into the band AND the write's own day, in one UPDATE: the press, not typing."""
        with app.app_context():
            xfer = _transfer(seed_user, seed_periods_today[3], "Stamp")
            _twins_built()
            _paid_press_before_the_step(xfer)

            assert _arms(xfer) == ("entered_stamp", "entered_stamp")

    @pytest.mark.server_clock
    def test_a_day_typed_onto_a_settled_pair_is_typed(
        self, app, seed_user, seed_periods_today,
    ):
        """A later write of another day onto a pair already settled: the owner's own."""
        with app.app_context():
            xfer = _transfer(seed_user, seed_periods_today[3], "Typed")
            _twins_built()
            _paid_press_before_the_step(xfer)
            _correct_before_the_step(
                xfer, an_entered_day(display_today() - timedelta(days=4)),
            )

            assert _arms(xfer) == ("entered_typed", "entered_typed")

    @pytest.mark.server_clock
    def test_a_day_written_onto_a_dateless_settled_pair_is_a_guess_or_a_repair(
        self, app, seed_user, seed_periods_today,
    ):
        """The backfill's write is a guess; the same write by the APP role is a typed repair."""
        with app.app_context():
            xfer = _transfer(seed_user, seed_periods_today[3], "Dateless")
            _twins_built()
            _paid_press_before_the_step(xfer)
            # The legacy shape (finding N-181): settled, no day, behind the
            # seam's back -- each covering movement un-dated with its row.
            for row in _shadows(xfer):
                record_settle_day(row, None)
                for movement in row.entries:
                    if movement.covers_settlement:
                        record_settle_day(movement, None)
            db.session.commit()
            _correct_before_the_step(
                xfer, an_entered_day(display_today() - timedelta(days=4)),
            )

            assert _arms(xfer) == ("entered_guess", "entered_guess")

            db.session.execute(text(
                "UPDATE system.audit_log SET db_user = 'shekel_app' "
                "WHERE table_schema = 'budget' AND table_name = 'transactions' "
                "AND row_id IN (SELECT id FROM budget.transactions "
                "WHERE transfer_id = :t)"
            ), {"t": xfer.id})
            db.session.commit()

            assert _arms(xfer) == ("entered_app_repair", "entered_app_repair")

    def test_an_observed_side_no_statement_named_borrows(
        self, app, seed_user, seed_periods_today,
    ):
        """The matcher stated the bank's day for BOTH sides; no member ever named either."""
        with app.app_context():
            xfer = _transfer(seed_user, seed_periods_today[3], "Copied")
            _twins_built()
            _settle_before_the_step(
                xfer, an_observed_day(display_today() - timedelta(days=4)),
            )

            assert _arms(xfer) == ("observed_relabel", "observed_relabel")

    def test_a_movement_a_statement_ONCE_named_keeps_its_observation(
        self, app, seed_user, seed_periods_today,
    ):
        """A member since removed still counts: the predicate reads the member audit INSERTs."""
        with app.app_context():
            xfer = _transfer(seed_user, seed_periods_today[3], "Named")
            _twins_built()
            _settle_before_the_step(
                xfer, an_observed_day(display_today() - timedelta(days=4)),
            )
            _a_statement_once_named_the_expense_side(xfer)

            assert _arms(xfer) == ("observed_kept", "observed_relabel")

    def test_an_asserted_side_is_kept_only_where_the_tick_linked_it(
        self, app, seed_user, seed_periods_today,
    ):
        """The tick links its own leg; the day copied to the other side borrows."""
        with app.app_context():
            xfer = _transfer(seed_user, seed_periods_today[3], "Ticked")
            _twins_built()
            _settle_before_the_step(
                xfer, an_asserted_day(display_today() - timedelta(days=4)),
            )
            _link_the_expense_side(xfer)

            assert _arms(xfer) == ("asserted_kept", "asserted_relabel")

    def test_a_borrowed_side_is_left_alone(self, app, seed_user, seed_periods_today):
        """A side this step's own code wrote is already labelled, and is counted as kept."""
        with app.app_context():
            xfer = _transfer(seed_user, seed_periods_today[3], "Pressed")
            _twins_built()
            _settle_before_the_step(xfer, SettleDay(
                day=display_today(), basis=SettledDayBasisEnum.BORROWED,
            ))

            assert _arms(xfer) == ("borrowed_kept", "borrowed_kept")

    @pytest.mark.server_clock
    def test_an_entered_day_no_audit_row_wrote_stays_typed(
        self, app, seed_user, seed_periods_today,
    ):
        """A day the audit log never saw written is kept as TYPED (R-BAL143's H3).

        The log starts 2026-05-06 and keeps 365 days, so a side can carry a day
        no audit row wrote.  The doubt lands on "typed": a typed day relabelled
        ``borrowed`` would be moved by the next statement.  Planted by deleting
        the day-writing audit rows the press left.
        """
        with app.app_context():
            xfer = _transfer(seed_user, seed_periods_today[3], "Unlogged")
            _twins_built()
            _paid_press_before_the_step(xfer)
            db.session.execute(text(
                "DELETE FROM system.audit_log "
                "WHERE table_schema = 'budget' AND table_name = 'transactions' "
                "AND 'settled_on' = ANY(changed_fields) "
                "AND row_id IN (SELECT id FROM budget.transactions "
                "WHERE transfer_id = :t)"
            ), {"t": xfer.id})
            db.session.commit()

            assert _arms(xfer) == ("entered_unwritten", "entered_unwritten")

            run_migration_callable(_MIGRATION.upgrade, db.session)
            bases = _bases_by_name()
            for shadow in _shadows(xfer):
                (movement,) = [m for m in shadow.entries if m.covers_settlement]
                assert bases[("t", shadow.id)] == "entered"
                assert bases[("e", movement.id)] == "entered"


class TestTheRefusal:
    """A relabel the day function would never produce is refused, naming the transfer."""

    def test_a_side_whose_day_is_not_its_siblings_is_refused_before_any_relabel(
        self, app, seed_user, seed_periods_today,
    ):
        """Two copied bank days that differ: neither can BORROW the other's.

        So the upgrade stops before it relabels a side.  Graded by a spy on
        :func:`relabel_borrowed` rather than by reading the rows after the
        refusal: the rollback that follows would undo a relabel written before
        it, so the rows cannot tell the order.
        """
        with app.app_context():
            xfer = _transfer(seed_user, seed_periods_today[3], "Parted")
            _twins_built()
            _settle_before_the_step(
                xfer, an_observed_day(display_today() - timedelta(days=4)),
            )
            _correct_before_the_step(
                xfer, an_observed_day(display_today() - timedelta(days=2)),
                account_id=xfer.to_account_id,
            )
            assert _arms(xfer) == ("observed_relabel", "observed_relabel")

            with mock.patch.object(
                _MIGRATION, "relabel_borrowed",
                wraps=_MIGRATION.relabel_borrowed,
            ) as relabel, pytest.raises(
                RuntimeError, match=f"First transfer ids: {xfer.id}",
            ):
                run_migration_callable(_MIGRATION.upgrade, db.session)
            db.session.rollback()

            assert relabel.call_count == 0, (
                "the upgrade relabelled before it refused"
            )

    def test_a_movement_not_mirroring_its_side_is_refused(
        self, app, seed_user, seed_periods_today,
    ):
        """A movement holding a label its shadow does not: the relabel would lower it.

        :func:`relabel_borrowed` writes each covering movement with its shadow,
        so a movement carrying evidence of its own would lose it.  No door
        writes that state (every movement mirrors its shadow, the design's M4);
        it is planted behind the seam's back.
        """
        with app.app_context():
            xfer = _transfer(seed_user, seed_periods_today[3], "Unmirrored")
            _twins_built()
            day = display_today() - timedelta(days=4)
            _settle_before_the_step(
                xfer, an_observed_day(day),
            )
            income = _shadows(xfer)[1]
            (movement,) = [m for m in income.entries if m.covers_settlement]
            record_settle_day(movement, an_entered_day(day))
            db.session.commit()
            assert _arms(xfer) == ("observed_relabel", "observed_relabel")

            with pytest.raises(RuntimeError, match=f"First transfer ids: {xfer.id}"):
                _MIGRATION.refuse_unborrowable(
                    db.session.connection(), [income.id],
                )

    def test_sides_on_one_day_pass(self, app, seed_user, seed_periods_today):
        """The control: the same copied day on both sides relabels without a refusal."""
        with app.app_context():
            xfer = _transfer(seed_user, seed_periods_today[3], "Together")
            _twins_built()
            _settle_before_the_step(
                xfer, an_observed_day(display_today() - timedelta(days=4)),
            )
            relabel = [shadow.id for shadow in _shadows(xfer)]

            _MIGRATION.refuse_unborrowable(db.session.connection(), relabel)


def _bases_by_name():
    """Return ``{(table, row id): basis name}`` for every dated transfer side and its movement."""
    rows = db.session.execute(text(
        "SELECT 't' AS tbl, t.id, b.name FROM budget.transactions t "
        "JOIN ref.settled_day_bases b ON b.id = t.settled_day_basis_id "
        "WHERE t.transfer_id IS NOT NULL "
        "UNION ALL "
        "SELECT 'e', e.id, b.name FROM budget.transaction_entries e "
        "JOIN budget.transactions t ON t.id = e.transaction_id "
        "JOIN ref.settled_day_bases b ON b.id = e.settled_day_basis_id "
        "WHERE t.transfer_id IS NOT NULL AND e.covers_settlement"
    )).all()
    return {(row.tbl, row.id): row.name for row in rows}


class TestTheRoundTrip:
    """``downgrade`` inverts ``upgrade`` on the pre-step pairs, save one shape.

    A pair BOTH of whose sides relabel comes back ``entered`` on both, which
    inverts a pair that was ``entered`` (the press below) and not one that
    was ``observed`` or ``asserted`` on both sides: that shape is pinned last,
    as the migration's docstring states it.
    """

    @pytest.mark.server_clock
    def test_up_then_down_restores_every_label_and_the_ref_row(
        self, app, seed_user, seed_periods_today,
    ):
        """Four pre-step pairs: a press, a typed day, a matched bank day, a linked tick.

        Each pair holds ONE day and ONE basis on both sides -- what every door
        wrote before this step.  This revision's first downgrade turns the
        planted pairs into exactly that and deletes the ref row; the upgrade
        relabels by predicate, shadows and movements together; the second
        downgrade must give back the first one's labels, byte for byte.
        """
        with app.app_context():
            period = seed_periods_today[3]
            press = _transfer(seed_user, period, "Press")
            typed = _transfer(seed_user, period, "Typed")
            copied = _transfer(seed_user, period, "Copied")
            ticked = _transfer(seed_user, period, "Ticked")
            _twins_built()
            _paid_press_before_the_step(press)
            _paid_press_before_the_step(typed)
            _correct_before_the_step(
                typed, an_entered_day(display_today() - timedelta(days=4)),
            )
            for xfer, day in (
                (copied, an_observed_day(display_today() - timedelta(days=3))),
                (ticked, an_asserted_day(display_today() - timedelta(days=2))),
            ):
                _settle_before_the_step(xfer, day)
            _a_statement_once_named_the_expense_side(copied)
            _link_the_expense_side(ticked)

            run_migration_callable(_MIGRATION.downgrade, db.session)
            before = _bases_by_name()
            assert "borrowed" not in set(before.values())
            assert db.session.execute(text(
                "SELECT count(*) FROM ref.settled_day_bases WHERE name = 'borrowed'"
            )).scalar_one() == 0

            run_migration_callable(_MIGRATION.upgrade, db.session)
            after = _bases_by_name()
            expected = {
                press: ("borrowed", "borrowed"),
                typed: ("entered", "entered"),
                copied: ("observed", "borrowed"),
                ticked: ("asserted", "borrowed"),
            }
            for xfer, (expense_basis, income_basis) in expected.items():
                expense, income = _shadows(xfer)
                for shadow, basis in ((expense, expense_basis), (income, income_basis)):
                    assert after[("t", shadow.id)] == basis, xfer.name
                    (movement,) = [
                        m for m in shadow.entries if m.covers_settlement
                    ]
                    assert after[("e", movement.id)] == basis, (
                        f"{xfer.name}: the movement did not follow its shadow"
                    )

            run_migration_callable(_MIGRATION.downgrade, db.session)
            assert _bases_by_name() == before

    def test_a_pair_both_of_whose_sides_relabel_comes_back_entered(
        self, app, seed_user, seed_periods_today,
    ):
        """The one shape the downgrade cannot invert, pinned as the docstring states it.

        Both sides ``observed`` and no member ever naming either: both relabel,
        and with no evidenced sibling left to copy from, the downgrade gives
        both ``entered``.  The matcher records a member for the side it
        matched, so no door wrote this; the 2026-09-30 dump holds none.
        """
        with app.app_context():
            xfer = _transfer(seed_user, seed_periods_today[3], "Unnamed")
            _twins_built()
            _settle_before_the_step(
                xfer, an_observed_day(display_today() - timedelta(days=4)),
            )

            run_migration_callable(_MIGRATION.downgrade, db.session)
            run_migration_callable(_MIGRATION.upgrade, db.session)
            run_migration_callable(_MIGRATION.downgrade, db.session)

            bases = _bases_by_name()
            for shadow in _shadows(xfer):
                assert bases[("t", shadow.id)] == "entered"
