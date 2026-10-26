"""Plan step ``balance:X-bi-6-4d-2``: a transfer side's payment hangs off the transfer.

Ruling **R-BAL88** (developer 2026-09-20, "Two side-keyed links"): a transfer
side's payment record names its transfer by one of two link columns, each in a
COMPOSITE key with the record's ``account_id`` onto that side's endpoint, under a
CHECK that exactly one parent is set.  *"The side IS which column is set, and the
account IS the endpoint -- structurally, no trigger.  The bug's row is
UNSTORABLE."*  Ruling **R-BAL107** ("Key transfer accounts to owner") keys each
endpoint to the transfer's owner, so a record is the owner's by the chain.
Ruling **R-BAL168** makes the side keys ``ON UPDATE CASCADE``: an endpoint move
carries the record.  And the deleted-row rule (rulings **R-CC89**, **R-CC92**)
gains its transfer arm, :data:`app.deleted_row_infrastructure.TRANSFER_ARM`.

Each case writes the state with raw SQL or the bare ORM, the way a writer
nobody enumerated would, against the database the suite runs on, and each
refusal sits beside its control: the like write the rule lets through.  The
records planted are KEPT
un-dated ones (a revert's, ruling R-BAL61) under a Projected transfer, the one
side-record state no settle verb is needed to reach.  A side's record states
no figure: its transfer holds the one the records moved, and holds a record
on both sides or on neither (the band's figure arm, graded at COMMIT), so a
case that commits records plants the revert's kept PAIR and its transfer's
figure (:func:`_kept_pair`).
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import exc as sa_exc
from sqlalchemy import text

from app import ref_cache
from app.enums import MovementFigureSourceEnum
from app.extensions import db as _db
from app.models.settled_figure import SettledFigure
from app.models.transaction_entry import TransactionEntry
from app.models.transfer import Transfer
from tests._test_helpers import (
    constraint_name_from,
    create_account_of_type,
    create_envelope_txn,
    create_transfer,
    refused_by_database_rule,
)

#: The arrival arm's transfer refusal, as the database words it.
_ARRIVAL_REFUSED = "transfer .* was deleted: a payment cannot be recorded under it"

#: The transfer hiding arm's refusal, as the database words it.
_HIDING_REFUSED = "transfer .* was deleted while it still holds a recorded payment"


def _accounts(seed_user):
    """Return ``(checking, savings, van loan)`` -- the worked example's accounts."""
    savings = create_account_of_type(
        seed_user, _db.session, "Savings", "Side Savings",
    )
    van = create_account_of_type(
        seed_user, _db.session, "Savings", "Side Van Fund",
    )
    _db.session.commit()
    return seed_user["account"], savings, van


def _transfer(seed_user, from_account, to_account):
    """A Projected $500.00 transfer between the two accounts, committed."""
    xfer = create_transfer(
        seed_user, _db.session, from_account, to_account,
        seed_user["bootstrap_period"], amount=Decimal("500.00"),
    )
    _db.session.commit()
    return xfer


def _record(xfer, *, income=False, account_id=None, covers=True, row_id=None):
    """Stage one side's kept, un-dated payment record of *xfer*; returns it unflushed.

    It states no figure and no source: its transfer holds both
    (:func:`_kept_pair`).

    Args:
        xfer: The transfer.
        income: ``True`` for the to-side link, ``False`` for the from-side.
        account_id: The record's account; the side's endpoint when ``None``.
        covers: The ``covers_settlement`` mark.
        row_id: A plan row to ALSO name as parent, for the one-parent cases.
    """
    if account_id is None:
        account_id = xfer.to_account_id if income else xfer.from_account_id
    entry = TransactionEntry(
        transaction_id=row_id,
        expense_transfer_id=None if income else xfer.id,
        income_transfer_id=xfer.id if income else None,
        account_id=account_id,
        owner_id=xfer.user_id,
        user_id=xfer.user_id,
        description="Side record",
        covers_settlement=covers,
    )
    _db.session.add(entry)
    return entry


def _kept_pair(xfer):
    """Stage both sides' kept records of *xfer* and the figure they moved; returns them.

    A revert keeps both records and the figure, ``500.00`` resolved, on the
    transfer (ruling R-BAL61); one record alone is a state the band's figure
    arm refuses at COMMIT.  Returns ``(from-side record, to-side record)``,
    unflushed.
    """
    xfer.settled_figure_pair = SettledFigure(
        Decimal("500.00"),
        ref_cache.movement_figure_source_id(MovementFigureSourceEnum.RESOLVED),
    )
    return _record(xfer), _record(xfer, income=True)


def _refused_by(constraint):
    """Expect the next flush to raise an IntegrityError on *constraint*."""
    with pytest.raises(sa_exc.IntegrityError) as excinfo:
        _db.session.flush()
    _db.session.rollback()
    assert constraint_name_from(excinfo.value) == constraint


class TestTheSideKeys:
    """The account IS the side's endpoint (ruling R-BAL88's worked example)."""

    def test_each_side_holds_its_record_on_its_endpoint(self, app, seed_user):
        """CONTROL: one record per side, each on its own endpoint, stores."""
        del app
        checking, savings, _van = _accounts(seed_user)
        xfer = _transfer(seed_user, checking, savings)
        _kept_pair(xfer)
        _db.session.commit()

        stored = _db.session.execute(text(
            "SELECT expense_transfer_id, income_transfer_id, account_id "
            "FROM budget.transaction_entries "
            "WHERE coalesce(expense_transfer_id, income_transfer_id) = :t "
            "ORDER BY account_id"
        ), {"t": xfer.id}).all()
        assert sorted(stored, key=lambda row: row[2]) == sorted([
            (xfer.id, None, checking.id), (None, xfer.id, savings.id),
        ], key=lambda row: row[2])

    def test_a_from_side_record_on_another_account_is_unstorable(self, app, seed_user):
        """$500.00 Checking -> Savings; its from-side record written on Van Fund."""
        del app
        checking, savings, van = _accounts(seed_user)
        xfer = _transfer(seed_user, checking, savings)
        _record(xfer, account_id=van.id)
        _refused_by("fk_transaction_entries_expense_side")

    def test_a_from_side_record_on_the_to_account_is_unstorable(self, app, seed_user):
        """The side is the link column, never inferred from the account."""
        del app
        checking, savings, _van = _accounts(seed_user)
        xfer = _transfer(seed_user, checking, savings)
        _record(xfer, account_id=savings.id)
        _refused_by("fk_transaction_entries_expense_side")

    def test_a_to_side_record_on_the_from_account_is_unstorable(self, app, seed_user):
        """The to-side twin of the case above."""
        del app
        checking, savings, _van = _accounts(seed_user)
        xfer = _transfer(seed_user, checking, savings)
        _record(xfer, income=True, account_id=checking.id)
        _refused_by("fk_transaction_entries_income_side")

    @pytest.mark.parametrize("income", [False, True], ids=["from-side", "to-side"])
    def test_a_side_holds_one_record(self, app, seed_user, income):
        """A second record on a side is refused by that side's unique index.

        Each side ALONE takes the second record, because each side's link has
        a unique index of its own and each must refuse on its own.  The case
        staged a second from-side record alone until plan step
        ``balance:X-bi-6-4d-2``'s 4d-4b review.
        """
        del app
        checking, savings, _van = _accounts(seed_user)
        xfer = _transfer(seed_user, checking, savings)
        _kept_pair(xfer)
        _db.session.commit()
        _record(xfer, income=income)
        _refused_by(
            "uq_transaction_entries_one_income_side_record" if income
            else "uq_transaction_entries_one_expense_side_record"
        )


class TestOneParent:
    """A movement is filed under exactly one parent, and a side link is a record."""

    def test_a_record_naming_a_row_and_a_side_is_unstorable(self, app, seed_user):
        """Both parents at once."""
        del app
        checking, savings, _van = _accounts(seed_user)
        xfer = _transfer(seed_user, checking, savings)
        row = create_envelope_txn(
            seed_user, _db.session, seed_user["bootstrap_period"],
            "Side Groceries", Decimal("80.00"),
        )
        _db.session.commit()
        _record(xfer, row_id=row.id)
        _refused_by("ck_transaction_entries_one_parent")

    def test_a_record_naming_both_sides_is_unstorable(self, app, seed_user):
        """Two side links: which side it is would be ambiguous."""
        del app
        checking, savings, _van = _accounts(seed_user)
        xfer = _transfer(seed_user, checking, savings)
        entry = _record(xfer)
        entry.income_transfer_id = xfer.id
        # The CHECK, not the to-side key the from-account also violates: a
        # CHECK is evaluated as the row is written, a foreign key after it.
        _refused_by("ck_transaction_entries_one_parent")

    def test_a_record_naming_no_parent_is_unstorable(self, app, seed_user):
        """``transaction_id`` NULL is a fact only because a side link is set."""
        del app
        checking, savings, _van = _accounts(seed_user)
        xfer = _transfer(seed_user, checking, savings)
        entry = _record(xfer)
        entry.expense_transfer_id = None
        _refused_by("ck_transaction_entries_one_parent")

    def test_a_side_link_that_is_not_a_record_is_unstorable(self, app, seed_user):
        """A transfer side holds its payment and nothing else."""
        del app
        checking, savings, _van = _accounts(seed_user)
        xfer = _transfer(seed_user, checking, savings)
        _record(xfer, covers=False)
        _refused_by("ck_transaction_entries_side_link_is_a_record")


class TestTheTransferOwnerKeys:
    """A transfer's accounts are its owner's (ruling R-BAL107)."""

    @pytest.mark.parametrize("side", ["from_account_id", "to_account_id"])
    def test_a_transfer_naming_another_users_account_is_unstorable(
        self, app, seed_user, second_user, side,
    ):
        """The R-BAL107 example: a transfer re-pointed at another user's account."""
        del app
        checking, savings, _van = _accounts(seed_user)
        xfer = _transfer(seed_user, checking, savings)
        with pytest.raises(sa_exc.IntegrityError) as excinfo:
            _db.session.execute(
                text(f"UPDATE budget.transfers SET {side} = :a WHERE id = :t"),
                {"a": second_user["account"].id, "t": xfer.id},
            )
        _db.session.rollback()
        assert constraint_name_from(excinfo.value) == (
            "fk_transfers_owner_from_account" if side == "from_account_id"
            else "fk_transfers_owner_to_account"
        )

    def test_a_transfer_between_its_owners_accounts_moves(self, app, seed_user):
        """CONTROL: re-pointing onto another of the owner's own accounts lands."""
        del app
        checking, savings, van = _accounts(seed_user)
        xfer = _transfer(seed_user, checking, savings)
        _db.session.execute(
            text("UPDATE budget.transfers SET to_account_id = :a WHERE id = :t"),
            {"a": van.id, "t": xfer.id},
        )
        _db.session.commit()
        assert _db.session.get(Transfer, xfer.id).to_account_id == van.id


class TestTheSideKeysCarryAndKeep:
    """ON UPDATE CASCADE carries a record (R-BAL168); NO ACTION keeps it (R-CC54)."""

    def test_an_endpoint_move_carries_the_sides_record_in_the_database(
        self, app, seed_user,
    ):
        """The database half of R-BAL168: the record follows its endpoint.

        One raw statement moves the from-account; no application code runs, so
        the record's new account is the key's cascade alone.  The to-side
        record stays where it is.
        """
        del app
        checking, savings, van = _accounts(seed_user)
        xfer = _transfer(seed_user, checking, savings)
        expense, income = _kept_pair(xfer)
        _db.session.commit()
        expense_id, income_id = expense.id, income.id

        _db.session.execute(
            text("UPDATE budget.transfers SET from_account_id = :a WHERE id = :t"),
            {"a": van.id, "t": xfer.id},
        )
        _db.session.commit()

        accounts = dict(_db.session.execute(text(
            "SELECT id, account_id FROM budget.transaction_entries "
            "WHERE id IN (:e, :i)"
        ), {"e": expense_id, "i": income_id}).all())
        assert accounts == {expense_id: van.id, income_id: savings.id}

    @pytest.mark.parametrize("income", [False, True], ids=["from-side", "to-side"])
    def test_deleting_a_transfer_holding_a_record_is_refused(
        self, app, seed_user, income,
    ):
        """A transfer holding a payment is history; only the removal act takes it off.

        Each side ALONE holds the record, because each side's link has a key
        of its own, ``NO ACTION`` on delete, and each must refuse on its own.
        The case held the from-side's record alone until plan step
        ``balance:X-bi-6-4d-2``'s 4d-4b review.

        The one record is staged inside the save, flushed and never
        committed: one record alone is a state the band's figure arm refuses
        at COMMIT, and the side key refuses the DELETE at its own statement.
        """
        del app
        checking, savings, _van = _accounts(seed_user)
        xfer = _transfer(seed_user, checking, savings)
        _record(xfer, income=income)
        _db.session.flush()
        with pytest.raises(sa_exc.IntegrityError) as excinfo:
            _db.session.execute(
                text("DELETE FROM budget.transfers WHERE id = :t"),
                {"t": xfer.id},
            )
        _db.session.rollback()
        assert constraint_name_from(excinfo.value) == (
            "fk_transaction_entries_income_side" if income
            else "fk_transaction_entries_expense_side"
        )


    def test_deleting_an_empty_transfer_lands(self, app, seed_user):
        """CONTROL: the same statement over a transfer holding nothing commits."""
        del app
        checking, savings, _van = _accounts(seed_user)
        xfer = _transfer(seed_user, checking, savings)
        xfer_id = xfer.id
        _db.session.execute(
            text("DELETE FROM budget.transfers WHERE id = :t"), {"t": xfer_id},
        )
        _db.session.commit()
        assert _db.session.execute(text(
            "SELECT count(*) FROM budget.transfers WHERE id = :t"
        ), {"t": xfer_id}).scalar_one() == 0

    @pytest.mark.parametrize("income", [False, True], ids=["from-side", "to-side"])
    def test_a_cascade_beside_an_unchanged_link_is_no_arrival(
        self, app, seed_user, income,
    ):
        """The arrival arm's early return, graded where it decides the outcome.

        A transfer hidden earlier in the same save, one endpoint then moved:
        PostgreSQL's cascade re-writes that side's record's account through an
        UPDATE that names the link column too, so the arrival trigger fires --
        and must not read the move as the record ARRIVING under the hidden
        transfer.  Each side's endpoint is moved ALONE, because the early
        return compares each link and each side's cascade must pass it on its
        own.  The case moved the from-account alone until plan step
        ``balance:X-bi-6-4d-2``'s 4d-4b review.  Both records come off before
        COMMIT, and the figure they moved with them, so the hiding arm has
        nothing to refuse; without the early return the endpoint UPDATE
        itself is refused.
        """
        del app
        checking, savings, van = _accounts(seed_user)
        xfer = _transfer(seed_user, checking, savings)
        from_record, to_record = _kept_pair(xfer)
        _db.session.commit()
        record_id = (to_record if income else from_record).id
        _hide_transfer(xfer.id)
        endpoint = "to_account_id" if income else "from_account_id"
        _db.session.execute(
            text(f"UPDATE budget.transfers SET {endpoint} = :a WHERE id = :t"),
            {"a": van.id, "t": xfer.id},
        )
        assert _db.session.execute(text(
            "SELECT account_id FROM budget.transaction_entries WHERE id = :e"
        ), {"e": record_id}).scalar_one() == van.id
        _take_the_pair_off(xfer.id)
        _db.session.commit()


def _hide_transfer(xfer_id):
    """Stage ``UPDATE ... SET is_deleted`` on transfer *xfer_id*; the caller commits."""
    _db.session.execute(
        text("UPDATE budget.transfers SET is_deleted = TRUE WHERE id = :t"),
        {"t": xfer_id},
    )


def _take_the_pair_off(xfer_id):
    """Stage both sides' records of *xfer_id* off, and the figure they moved; the caller commits.

    The delete door's removal at head: every record either side holds, and
    the transfer's figure released with them (a transfer holding no record
    states no figure above zero).
    """
    _db.session.execute(
        text("DELETE FROM budget.transaction_entries "
             "WHERE expense_transfer_id = :t OR income_transfer_id = :t"),
        {"t": xfer_id},
    )
    _db.session.execute(
        text("UPDATE budget.transfers SET settled_figure = NULL, "
             "settled_figure_source_id = NULL WHERE id = :t"),
        {"t": xfer_id},
    )


def _side_link(income):
    """The record's link column on the to-side (``income``) or the from-side."""
    return "income_transfer_id" if income else "expense_transfer_id"


def _sharing_the_side(seed_user, accounts, income):
    """A second $500.00 transfer sharing Checking -> Savings' account on one side, committed.

    It shares Savings (the to-side, ``income``) or Checking (the from-side)
    and has Van Fund on its other end, so the ad-hoc double-submit index
    does not see the two transfers as one, and a side's record can move
    between them.

    Args:
        seed_user: The owner.
        accounts: ``(checking, savings, van)``, from :func:`_accounts`.
        income: ``True`` to share the to-account, ``False`` the from-account.
    """
    checking, savings, van = accounts
    if income:
        return _transfer(seed_user, van, savings)
    return _transfer(seed_user, checking, van)


def _re_point(income, off_id, onto_id):
    """Stage one side's record moved by its link from transfer *off_id* onto *onto_id*."""
    link = _side_link(income)
    _db.session.execute(
        text(f"UPDATE budget.transaction_entries SET {link} = :onto WHERE {link} = :off"),
        {"onto": onto_id, "off": off_id},
    )


class TestTheDeletedRowTransferArm:
    """No payment under a deleted transfer, either way round (R-CC89, R-CC92)."""

    @pytest.mark.parametrize("income", [False, True], ids=["from-side", "to-side"])
    def test_a_record_arriving_under_a_deleted_transfer_is_refused(
        self, app, seed_user, income,
    ):
        """The arrival arm reads the TRANSFER's ``is_deleted``, on each side's link.

        Each side's record arrives ALONE, because the arm reads whichever of
        the two link columns the record sets and each must refuse on its
        own.  The case brought the from-side's record alone until plan step
        ``balance:X-bi-6-4d-2``'s vacuous-residue checkpoint, where an arm
        reading the from-side link only failed no test in the suite
        (measured there).
        """
        del app
        checking, savings, _van = _accounts(seed_user)
        xfer = _transfer(seed_user, checking, savings)
        _hide_transfer(xfer.id)
        _db.session.commit()
        _record(xfer, income=income)
        with refused_by_database_rule(_ARRIVAL_REFUSED):
            _db.session.flush()
        _db.session.rollback()

    @pytest.mark.parametrize("income", [False, True], ids=["from-side", "to-side"])
    def test_a_record_re_pointed_onto_a_deleted_transfer_is_refused(
        self, app, seed_user, income,
    ):
        """An UPDATE of the link is an arrival too, on each side's link.

        The record first lands under a live transfer, then its link is moved
        onto a hidden one sharing the record's account.  Each side's link is
        moved ALONE, because the arm reads either link and its early return
        compares each, so each must refuse on its own.  The case moved the
        from-side link alone until plan step ``balance:X-bi-6-4d-2``'s
        vacuous-residue checkpoint, where an arm reading the from-side link
        only, or an early return comparing it only, failed no test in the
        suite (measured there).
        :meth:`test_a_record_re_pointed_onto_a_live_transfer_passes_the_arrival_arm`
        is the control, per side.
        """
        del app
        accounts = _accounts(seed_user)
        live = _transfer(seed_user, accounts[0], accounts[1])
        _kept_pair(live)
        hidden = _sharing_the_side(seed_user, accounts, income)
        _hide_transfer(hidden.id)
        _db.session.commit()
        with refused_by_database_rule(_ARRIVAL_REFUSED):
            _re_point(income, live.id, hidden.id)
        _db.session.rollback()

    @pytest.mark.parametrize("income", [False, True], ids=["from-side", "to-side"])
    def test_a_record_re_pointed_onto_a_live_transfer_passes_the_arrival_arm(
        self, app, seed_user, income,
    ):
        """CONTROL: the same UPDATE onto a live transfer sharing the record's account.

        Per side since plan step ``balance:X-bi-6-4d-2``'s vacuous-residue
        checkpoint, beside the refusal it controls; until then it moved the
        from-side link alone.

        Planted as the refusal is (the kept pair, committed) and graded where
        the refusal is: at the ``UPDATE`` itself, which the arrival arm, a
        ``BEFORE`` trigger, lets through.  It is rolled back rather than
        committed: the move leaves each transfer holding one side's record,
        which the band's figure arm refuses at COMMIT (plan step
        ``balance:X-bi-6-4d-2``'s 4d-4 half), and that rule is not the one
        this control grades.
        """
        del app
        accounts = _accounts(seed_user)
        first = _transfer(seed_user, accounts[0], accounts[1])
        _kept_pair(first)
        second = _sharing_the_side(seed_user, accounts, income)
        _re_point(income, first.id, second.id)
        assert _db.session.execute(text(
            "SELECT count(*) FROM budget.transaction_entries "
            f"WHERE {_side_link(income)} = :s"
        ), {"s": second.id}).scalar_one() == 1
        _db.session.rollback()

    @pytest.mark.parametrize("income", [False, True], ids=["from-side", "to-side"])
    def test_hiding_a_transfer_that_holds_a_record_is_refused_at_commit(
        self, app, seed_user, income,
    ):
        """The hiding arm is deferred: the flush passes and COMMIT refuses.

        Each side ALONE holds the record, because the arm asks two link
        columns and each must refuse on its own.  The case held the
        from-side's record alone until plan step ``balance:X-bi-6-4d-2``'s
        vacuous-residue checkpoint, where an arm asking the from-side link
        only failed no test in the suite (measured there); each side's record
        is planted with the bare ORM, as every record in this module is.

        One side's record alone is a state the band's figure arm refuses at
        COMMIT too (plan step ``balance:X-bi-6-4d-2``'s 4d-4 half), so it is
        reached inside the save: the kept pair and its figure are committed,
        then ONE save hides the transfer FIRST and takes the OTHER side's
        record off.  PostgreSQL asks the deferred checks at COMMIT in the
        order their events were queued, so the hiding arm is asked before
        the figure arm and its refusal is the one COMMIT raises (measured:
        the two statements the other way round raise the figure arm's).  A
        hiding arm that misread a link would leave the figure arm's refusal
        to fire instead, so the refusal TEXT is what this case grades.
        """
        del app
        checking, savings, _van = _accounts(seed_user)
        xfer = _transfer(seed_user, checking, savings)
        _kept_pair(xfer)
        _db.session.commit()
        _hide_transfer(xfer.id)
        _db.session.execute(
            text("DELETE FROM budget.transaction_entries "
                 f"WHERE {_side_link(not income)} = :t"),
            {"t": xfer.id},
        )
        with refused_by_database_rule(_HIDING_REFUSED):
            _db.session.commit()
        _db.session.rollback()

    def test_taking_the_record_off_and_hiding_in_one_save_commits(
        self, app, seed_user,
    ):
        """CONTROL: the delete door's own shape -- money off, transfer hidden, one save."""
        del app
        checking, savings, _van = _accounts(seed_user)
        xfer = _transfer(seed_user, checking, savings)
        _kept_pair(xfer)
        _db.session.commit()
        _hide_transfer(xfer.id)
        _take_the_pair_off(xfer.id)
        _db.session.commit()
        assert _db.session.get(Transfer, xfer.id).is_deleted

    def test_an_empty_transfer_hides(self, app, seed_user):
        """CONTROL: a transfer holding nothing hides as it always did."""
        del app
        checking, savings, _van = _accounts(seed_user)
        xfer = _transfer(seed_user, checking, savings)
        _hide_transfer(xfer.id)
        _db.session.commit()
        assert _db.session.get(Transfer, xfer.id).is_deleted
