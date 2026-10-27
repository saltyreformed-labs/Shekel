"""The row's figure columns are gone, and the migration that deleted them round-trips.

Plan step **balance:X-bi-4b-2**, rulings **R-BAL80**, **R-BAL82**, **R-BAL83**.
Migration ``45f10b870c8b`` deletes ``budget.transactions.settled_amount`` /
``settled_basis_id``, their three CHECKs, the FK and ``ref.settlement_bases``:
since ``X-bi-4b-1`` every reader of the settlement record asks the row's
covering movement, and the same fact in two homes is two sources (rule 14).

Three locks land here:

* **The columns, the CHECKs, the FK and the catalogue are gone** --
  ``information_schema`` and ``pg_constraint`` confirm it, so a migration or
  model edit that re-introduces a stored settled figure is caught the moment
  it lands.

* **The downgrade rebuilds the columns from the movements, exactly, and the
  upgrade takes them away again** -- the old C15-5 round-trip pattern, run
  against the live test database through the migration's own ``downgrade``
  and ``upgrade`` (:func:`~tests._test_helpers.run_migration_callable`, the
  ``_LATER`` idiom of ``test_one_level_relation_migration``).  Seven shapes
  are staged THROUGH THE DOORS -- a resolved bill, a typed correction, a
  ``$0.00`` close, an envelope closed from its purchases, a typed close
  reverted (the retained movement), a settled transfer pair and a row that
  never settled -- and the rebuilt columns are read by SQL, because the ORM
  no longer maps them: each row takes what the seam below would have
  written for it, every reader's answer is byte-identical across the round
  trip, and the second upgrade prints the same count.  The transfer pair is
  settled through the door and then put back into the shape this revision
  reads (``TestTheRoundTrip._stage``): ``82f7b7ed4332``'s downgrade builds its
  twins, since plan step ``balance:X-bi-6-4d-2`` stores none, and its records
  are re-parented under them by SQL, since plan step ``balance:X-bi-6-4d-2``
  files a side's record under the TRANSFER.

* **Every refusal is DRIVEN** (``docs/plans/verification.md`` standard 4):
  the three fail-closed predicates of the upgrade and the one of the
  downgrade, each staged by SQL on the downgraded schema (no door writes any
  of them) and each shown to fire, with the ``$0.00`` exemptions shown NOT
  to.

**What this module does NOT grade, stated so it is not read as more.**
``tests/test_models/test_covering_cutover.py`` -- 25 functions (28 cases)
driving migration ``ad573b07bede``'s refusals, its cover write, its total-rule
proof and the deploy resync's ledger-net equality -- was DELETED whole at this
step (developer ruling **R-BAL84**, 2026-09-20), with the five cutover-arm
classes of ``test_amount_ownership.py`` and the pre-flight class of
``test_settlement_record.py``: every one staged a settled row carrying the two
columns this migration deletes, a state no door at head can produce.  Those
migrations' refusal, cover and restore ARMS are ungraded at head: they run only
on an old-dump restore, and a template build replays the chain over an empty
database.  The STATES they refused have homes -- a dateless settled row and an
undated movement are ``integrity_check`` DC-11's two arms
(``test_integrity_check/test_the_consistency_checks.py``); a shadow, broken or
whole, is a row the database refuses since plan step ``balance:X-bi-6-4d-2``
(``test_a_transfer_is_one_row/test_the_upgrade.py::TestTheUpgrade::
test_the_database_refuses_a_twin``); a settled row recording nothing is the
``$0.00`` record (``test_settlement_record.py``); a movement disagreeing with
its row is this module's second predicate; and the seam's own write per shape is
the ``test_covering_movement`` package -- and the ledger-net equality the resync case
graded is the posting-ledger reconciliation oracles'
(``test_posting_ledger_*_reconciliation.py``) and this step's clone rehearsal
(the migration's docstring).  The per-class list is in the commit that
deleted them.
"""
# pylint: disable=redefined-outer-name
# Rationale: ``redefined-outer-name`` is the canonical pytest fixture
# pattern; bodies bind fixtures by name.
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import text

from app import ref_cache
from app.enums import MovementFigureSourceEnum, StatusEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.services import status_seam, transaction_service
from app.services.entry_service import EntryDetails, create_entry
from app.services.row_valuation import settled_figure
from tests._test_helpers import (
    an_entered_day,
    create_savings_account,
    create_settled_transfer,
    generate_row_of,
    load_migration_module,
    make_expense_template,
    run_migration_callable as _run,
    step_down_through_the_transfer_column_drop,
    typed,
)

_M = load_migration_module("45f10b870c8b_the_record_is_its_movements.py")

#: Plan step ``balance:X-bi-6-4d-2``'s revision, which deletes every transfer
#: twin and refuses a new one; its downgrade builds a transfer's two, which the
#: round trip's transfer pair needs.
_TWINS_DELETED = load_migration_module("82f7b7ed4332_a_transfer_is_one_row.py")

#: Plan step ``balance:X-bi-6-4d-2``'s last revision, which empties a side's
#: record of the figure its transfer holds; its downgrade copies the figure
#: back onto the records, the shape every revision below holds.
_SIDE_RECORD_FIGURE = load_migration_module(
    "b7d2e4f19a63_a_side_record_holds_no_figure.py",
)

_THE_CHECKS = (
    "ck_transactions_settled_amount",
    "ck_transactions_settled_amount_needs_basis",
    "ck_transactions_settle_day_needs_a_record",
)


def _sql(statement, **params):
    """Run one SELECT and return every row."""
    return db.session.execute(text(statement), params).all()


def _exec(statement, **params):
    """Run one writing statement (no rows come back)."""
    db.session.execute(text(statement), params)


def _columns():
    """The column names of ``budget.transactions``, from the catalogue."""
    return {
        row[0] for row in _sql(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'budget' AND table_name = 'transactions'",
        )
    }


def _constraints():
    """The CHECK and FK constraint names on ``budget.transactions``."""
    return {
        row[0] for row in _sql(
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid = 'budget.transactions'::regclass "
            "  AND contype IN ('c', 'f')",
        )
    }


def _catalogue_exists():
    """Whether ``ref.settlement_bases`` exists."""
    return _sql("SELECT to_regclass('ref.settlement_bases')")[0][0] is not None


def _rebuilt(row_id):
    """Return ``(settled_amount, basis name)`` as the downgrade rebuilt them."""
    (amount, basis) = _sql(
        "SELECT t.settled_amount, sb.name FROM budget.transactions t "
        "LEFT JOIN ref.settlement_bases sb ON sb.id = t.settled_basis_id "
        "WHERE t.id = :id", id=row_id,
    )[0]
    return amount, basis


def _bill(seed_user, period, amount="148.32", *, name="Electric", is_envelope=False):
    """One engine-generated expense row of a fresh definition."""
    template = make_expense_template(
        db.session, seed_user, amount=amount,
        name=name, category_key="Rent", is_envelope=is_envelope,
    )
    return generate_row_of(template, period)


def _settle(txn, *, submitted=None):
    """Settle *txn* through the verb, so the seam writes its record."""
    transaction_service.settle_transaction(txn, submitted=submitted)
    db.session.flush()


def _revert(txn):
    """Put *txn* back to Projected through the door production reverts by."""
    transaction_service.apply_requested_status(
        txn, ref_cache.status_id(StatusEnum.PROJECTED),
    )
    db.session.flush()


def _readers(row_ids):
    """Every reader's answer for *row_ids*: what the round trip must not move."""
    db.session.expire_all()
    answers = {}
    for row_id in row_ids:
        row = db.session.get(Transaction, row_id)
        answers[row_id] = (
            row.status_id, row.settled_on, settled_figure(row),
            status_seam.recorded_settlement(row),
            status_seam.honoured_correction(row),
        )
    return answers


# -- the columns are gone ----------------------------------------------------


def test_the_figure_columns_their_checks_the_fk_and_the_catalogue_are_gone(
    app,
):
    """X-bi-4b-2: nothing of the row's own figure record remains in the schema."""
    with app.app_context():
        columns = _columns()
        assert "settled_amount" not in columns, (
            "budget.transactions.settled_amount still exists -- plan step "
            "balance:X-bi-4b-2 deleted it (migration 45f10b870c8b); the "
            "record is the row's covering movement, not a column."
        )
        assert "settled_basis_id" not in columns, (
            "budget.transactions.settled_basis_id still exists -- it went "
            "with settled_amount at migration 45f10b870c8b."
        )
        constraints = _constraints()
        for name in _THE_CHECKS + ("fk_transactions_settled_basis_id",):
            assert name not in constraints, f"{name} still exists"
        assert not _catalogue_exists(), (
            "ref.settlement_bases still exists -- the catalogue went with the "
            "column it classified."
        )
        # The day pair and the clearing link are NOT X-bi-4's (R-BAL80).
        assert {"settled_on", "settled_day_basis_id", "reconciled_by_id"} <= columns


def test_the_migration_declares_its_revision_and_a_working_downgrade():
    """The migration names itself, and its downgrade names every artefact.

    The parent revision is deliberately NOT pinned here: it is named in the
    migration's own header and nowhere else, so a re-parent (two leaves
    branching from one revision, the second to merge re-parents onto the
    first) stays a one-line change.
    """
    assert _M.revision == "45f10b870c8b"
    assert _M.down_revision
    with open(_M.__file__, encoding="utf-8") as handle:
        downgrade_source = handle.read().split("def downgrade():", 1)[1]
    for artefact in _THE_CHECKS + (
        "fk_transactions_settled_basis_id", '"settlement_bases"',
        '"settled_amount"', '"settled_basis_id"',
    ):
        assert artefact in downgrade_source, artefact


# -- the round trip ----------------------------------------------------------


@pytest.mark.xdist_group("record_columns_ddl")
class TestTheRoundTrip:
    """Head -> down -> up, over every shape the seam writes."""

    @staticmethod
    def _stage(seed_user, seed_periods):
        """Stage the seven shapes through the doors; return ``{name: id}``.

        **The transfer pair is put back into the pre-step shape** since plan
        step ``balance:X-bi-6-4d-2``, which files each side's record under the
        TRANSFER (``expense_transfer_id`` / ``income_transfer_id``), and plan
        step ``balance:X-bi-6-4d-2`` (``82f7b7ed4332``), which deletes every
        twin and refuses a new one -- so no door writes the shape this
        revision reads any more (each twin settled, holding its covering
        movement).  The pair is settled through the door, then
        ``c956c1d83108``'s downgrade puts back the column a twin names its
        transfer by
        (:func:`tests._test_helpers.step_down_through_the_transfer_column_drop`;
        the model has none, so the twins are found by SQL), then
        ``b7d2e4f19a63``'s downgrade copies its transfer's figure back onto
        both records (at head a side's record holds none), then
        ``82f7b7ed4332``'s downgrade builds its two twins, each with the
        transfer's status and its side record's day and basis, and both
        records are re-parented under their twins in one statement (the shape
        the band rule admits as a ``$0.00`` close), which that downgrade does
        not do: it leaves each record under the transfer.  The re-parent is
        the setup the developer approved 2026-10-09 (rule 5, ruling R-BAL167);
        the twins come from ``82f7b7ed4332``'s downgrade, the release's own way
        down, which is no R-BAL167 class.  No assertion changed.
        """
        period = seed_periods[0]
        resolved = _bill(seed_user, period, name="Resolved bill")
        _settle(resolved)
        corrected = _bill(seed_user, period, "500.00", name="Corrected bill")
        _settle(corrected, submitted=typed(Decimal("245.32")))
        nothing = _bill(seed_user, period, "0.00", name="Close of nothing")
        _settle(nothing)
        envelope = _bill(seed_user, period, "100.00", name="Envelope", is_envelope=True)
        create_entry(
            envelope.id, seed_user["user"].id,
            EntryDetails(
                figure=typed(Decimal("38.88")), description="Kroger",
                purchased_on=period.start_date,
            ),
        )
        _settle(envelope)
        reverted = _bill(seed_user, period, "120.00", name="Reverted typed")
        _settle(reverted, submitted=typed(Decimal("95.50")))
        _revert(reverted)
        never = _bill(seed_user, period, name="Never settled")
        savings = create_savings_account(
            seed_user, db.session, "Round-trip Savings", Decimal("0.00"),
        )
        transfer = create_settled_transfer(
            seed_user, db.session, seed_user["account"], savings, period,
            amount=Decimal("200.00"),
        )
        db.session.commit()
        step_down_through_the_transfer_column_drop(db.session)
        _run(_SIDE_RECORD_FIGURE.downgrade, db.session)
        _run(_TWINS_DELETED.downgrade, db.session)
        _exec(
            "UPDATE budget.transaction_entries e SET transaction_id = t.id, "
            "expense_transfer_id = NULL, income_transfer_id = NULL "
            "FROM budget.transactions t "
            "WHERE t.transfer_id = :t "
            "  AND (e.expense_transfer_id = :t OR e.income_transfer_id = :t) "
            "  AND e.account_id = t.account_id",
            t=transfer.id,
        )
        db.session.commit()
        legs = _sql(
            "SELECT id FROM budget.transactions WHERE transfer_id = :t "
            "ORDER BY id",
            t=transfer.id,
        )
        return {
            "resolved": resolved.id, "corrected": corrected.id,
            "nothing": nothing.id, "envelope": envelope.id,
            "reverted": reverted.id, "never": never.id,
            "leg_a": legs[0].id, "leg_b": legs[1].id,
        }

    def test_down_rebuilds_the_columns_from_the_movements_and_up_removes_them(
        self, db, seed_user, seed_periods, capsys,
    ):
        """The shipped downgrade and upgrade, graded on the rows they leave.

        Run in the ``db`` fixture's own app context rather than a nested one:
        a nested context scopes a SECOND session, and the fixture's session
        then sits idle-in-transaction holding ``ACCESS SHARE`` on the tables
        the seed touched while the DDL waits for ``ACCESS EXCLUSIVE``.
        """
        ids = self._stage(seed_user, seed_periods)
        before = _readers(ids.values())
        # The staged shapes read as the module docstring says, before any DDL.
        assert before[ids["resolved"]][2] == Decimal("148.32")
        assert before[ids["corrected"]][2] == Decimal("245.32")
        assert before[ids["nothing"]][2] == Decimal("0.00")
        assert before[ids["envelope"]][2] == Decimal("38.88")
        assert before[ids["reverted"]][2] is None
        assert before[ids["reverted"]][3] == status_seam.Settlement(
            Decimal("95.50"), MovementFigureSourceEnum.TYPED,
        )
        assert before[ids["never"]][3] is None
        db.session.commit()

        _run(_M.downgrade, db.session)

        assert {"settled_amount", "settled_basis_id"} <= _columns()
        assert set(_THE_CHECKS) | {"fk_transactions_settled_basis_id"} <= _constraints()
        assert _catalogue_exists()
        assert [row[0] for row in _sql(
            "SELECT name FROM ref.settlement_bases ORDER BY id",
        )] == ["derived", "corrected", "purchases"]
        # What the seam below would have written for each shape.
        assert _rebuilt(ids["resolved"]) == (Decimal("148.32"), "derived")
        assert _rebuilt(ids["corrected"]) == (Decimal("245.32"), "corrected")
        assert _rebuilt(ids["nothing"]) == (None, "purchases")
        assert _rebuilt(ids["envelope"]) == (None, "purchases")
        assert _rebuilt(ids["reverted"]) == (Decimal("95.50"), "corrected")
        assert _rebuilt(ids["never"]) == (None, None)
        assert _rebuilt(ids["leg_a"]) == (Decimal("200.00"), "derived")
        assert _rebuilt(ids["leg_b"]) == (Decimal("200.00"), "derived")
        # And the readers, which ask the movements, moved nothing.
        assert _readers(ids.values()) == before

        _run(_M.upgrade, db.session)

        columns = _columns()
        assert "settled_amount" not in columns and "settled_basis_id" not in columns
        assert not (set(_THE_CHECKS) & _constraints())
        assert "fk_transactions_settled_basis_id" not in _constraints()
        assert not _catalogue_exists()
        assert _readers(ids.values()) == before
        # Six settled rows: four bills (the reverted one is out of the band,
        # the never-settled one never entered it) and the transfer's two legs.
        assert "6 settled row(s) read their entries" in capsys.readouterr().out


# -- the refusals, driven -----------------------------------------------------


@pytest.mark.xdist_group("record_columns_ddl")
class TestTheUpgradeRefusesALossyRow:
    """The three fail-closed predicates, each staged by SQL on the downgraded schema.

    The downgrade rebuilds the columns in agreement with the movements, so
    every case below tampers ONE thing afterwards -- a write no door makes --
    and drives :func:`_M.refuse_lossy_rows` against it.  The accepting case
    first: a clean downgraded database passes every predicate, without which
    the refusals would be satisfied by a guard that refused everything.
    """

    @staticmethod
    def _downgraded(db, seed_user, seed_periods, *, submitted=None):
        """A settled bill through the door, then the schema stepped down."""
        txn = _bill(seed_user, seed_periods[0], "100.00")
        _settle(txn, submitted=submitted)
        db.session.commit()
        _run(_M.downgrade, db.session)
        return txn.id

    @staticmethod
    def _refuses(match):
        with pytest.raises(RuntimeError, match=match):
            _M.refuse_lossy_rows(db.session.connection())

    def test_a_clean_downgraded_database_passes_every_predicate(
        self, db, seed_user, seed_periods,
    ):
        self._downgraded(db, seed_user, seed_periods)
        assert _M.refuse_lossy_rows(db.session.connection()) is None

    def test_a_settled_figure_differing_from_the_entry_sum_is_refused(
        self, db, seed_user, seed_periods,
    ):
        """Predicate 1: the column says one thing, the entries another."""
        row_id = self._downgraded(db, seed_user, seed_periods)
        _exec(
            "UPDATE budget.transactions SET settled_amount = 100.01 "
            "WHERE id = :id", id=row_id,
        )
        self._refuses(r"^1 settled budget\.transactions row\(s\) store a non-zero figure")

    def test_a_stored_zero_beside_a_later_purchase_is_exempt(
        self, db, seed_user, seed_periods,
    ):
        """Predicate 1's exemption (R-BAL82): a ``$0.00`` close that took a purchase.

        Staged as the doors write it -- an envelope closed, a typed ``$0.00``
        over the close, then a ``$12.00`` purchase admitted onto it (the
        ruling's worked 'Water' row) -- so the downgrade rebuilds
        ``(NULL, purchases)``; the pre-4b-2 seam wrote ``(0, corrected)`` for
        the typed close, and that is what the exemption is for, so the column
        is set to it by SQL.  The entries sum to ``12.00`` and the row passes.
        """
        txn = _bill(seed_user, seed_periods[0], "45.00", name="Water", is_envelope=True)
        _settle(txn)
        transaction_service.apply_requested_status(
            txn, txn.status_id,
            stated=transaction_service.StatedRecord(figure=typed(Decimal("0.00"))),
        )
        db.session.flush()
        create_entry(
            txn.id, seed_user["user"].id,
            EntryDetails(
                figure=typed(Decimal("12.00")), description="Water top-up",
                purchased_on=seed_periods[0].start_date,
                settle_day=an_entered_day(seed_periods[0].start_date),
            ),
        )
        db.session.commit()
        assert settled_figure(txn) == Decimal("12.00")
        _run(_M.downgrade, db.session)
        _exec(
            "UPDATE budget.transactions SET settled_amount = 0, "
            "settled_basis_id = (SELECT id FROM ref.settlement_bases "
            "WHERE name = 'corrected') WHERE id = :id", id=txn.id,
        )
        assert _M.refuse_lossy_rows(db.session.connection()) is None

    def test_a_covering_movement_disagreeing_with_the_row_is_refused(
        self, db, seed_user, seed_periods,
    ):
        """Predicate 2: NULL beside a movement counts, and no ``$0.00`` exemption."""
        row_id = self._downgraded(db, seed_user, seed_periods)
        _exec(
            "UPDATE budget.transactions SET settled_amount = NULL, "
            "settled_basis_id = (SELECT id FROM ref.settlement_bases "
            "WHERE name = 'purchases') WHERE id = :id", id=row_id,
        )
        self._refuses(r"^1 covering movement\(s\) disagree")
        _exec(
            "UPDATE budget.transactions SET settled_amount = 0, "
            "settled_basis_id = (SELECT id FROM ref.settlement_bases "
            "WHERE name = 'corrected') WHERE id = :id", id=row_id,
        )
        self._refuses(r"^1 covering movement\(s\) disagree")

    def test_a_reverted_correction_no_movement_carries_is_refused(
        self, db, seed_user, seed_periods,
    ):
        """Predicate 3: out of the band, ``corrected``, non-zero, and no movement."""
        txn = _bill(seed_user, seed_periods[0], "120.00")
        _settle(txn, submitted=typed(Decimal("95.50")))
        _revert(txn)
        db.session.commit()
        row_id = txn.id
        _run(_M.downgrade, db.session)
        assert _rebuilt(row_id) == (Decimal("95.50"), "corrected")
        # The retained movement stands, so the row passes ...
        assert _M.refuse_lossy_rows(db.session.connection()) is None
        # ... until nothing carries the correction.
        _exec(
            "DELETE FROM budget.transaction_entries "
            "WHERE transaction_id = :id AND covers_settlement", id=row_id,
        )
        self._refuses(
            r"^1 budget\.transactions row\(s\) outside the settled band retain",
        )

    def test_a_reverted_zero_correction_is_exempt(
        self, db, seed_user, seed_periods,
    ):
        """Predicate 3's exemption (R-BAL82): a ``$0.00`` typed figure is retained by nothing."""
        txn = _bill(seed_user, seed_periods[0], "120.00")
        _settle(txn, submitted=typed(Decimal("0.00")))
        _revert(txn)
        db.session.commit()
        row_id = txn.id
        _run(_M.downgrade, db.session)
        # No movement was ever written for it, so the downgrade rebuilds
        # NULL / NULL; the pre-4b-2 seam's ``(0, corrected)`` is set by SQL.
        _exec(
            "UPDATE budget.transactions SET settled_amount = 0, "
            "settled_basis_id = (SELECT id FROM ref.settlement_bases "
            "WHERE name = 'corrected') WHERE id = :id", id=row_id,
        )
        assert _M.refuse_lossy_rows(db.session.connection()) is None


@pytest.mark.xdist_group("record_columns_ddl")
class TestTheDowngradeRefusesADatedRowOutOfTheBand:
    """The downgrade's one refusal: a settle day on an unsettled row with no movement."""

    def test_it_refuses_before_re_creating_the_check_that_would(
        self, db, seed_user, seed_periods,
    ):
        txn = _bill(seed_user, seed_periods[0])
        _settle(txn)
        db.session.commit()
        row_id = txn.id
        # No door writes this: the seam clears the day on the way out of the
        # band.  A Projected row keeping its day, its movement gone.
        _exec(
            "UPDATE budget.transactions SET status_id = :projected "
            "WHERE id = :id",
            projected=ref_cache.status_id(StatusEnum.PROJECTED), id=row_id,
        )
        _exec(
            "DELETE FROM budget.transaction_entries "
            "WHERE transaction_id = :id AND covers_settlement", id=row_id,
        )
        db.session.commit()

        with pytest.raises(RuntimeError, match=r"^1 budget\.transactions row\(s\) outside the settled band carry a settle day"):
            _run(_M.downgrade, db.session)
        # Nothing was committed: the schema is still the head's.
        db.session.rollback()
        assert "settled_amount" not in _columns()
