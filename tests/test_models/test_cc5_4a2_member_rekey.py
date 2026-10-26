"""``2eabfa596ee0`` re-keys every row member onto its payment, REFUSES, and round-trips.

Plan step **credit_card:CC-5-4a-2**, rulings **R-CC43** and **R-CC45**.  The
migration's own grade -- Definition of Done item 7, the shape
``test_cc5_1_movement_account_key`` uses: the SHIPPED ``upgrade`` /
``downgrade`` driven over a database that holds accepted acts, because the
test template is built empty and the re-key's join would otherwise touch no
member.

**How a row member is staged.**  The app writes none any more (the accept
door names movements since ``CC-5-4a-1``), so each case accepts a real match
through the door, DOWNGRADES this revision (which restores the column and
re-keys nothing), and rewrites the member back to the shape production's 103
pre-``CC-5-4a-1`` acts hold -- ``transaction_id`` set to the row,
``transaction_entry_id`` NULL -- before driving the upgrade.

**The ids are made to diverge** (the review class ``CC-5-4a-1`` paid for: a
door reading a row by a movement's id passed every case while the fixture's
first entry and first row were both id 1): four purchases are recorded in an
envelope first, so no bill's payment id is any bill's row id.

**The controls that FIRE, and the mutation each was shown to fire under**
(``docs/plans/verification.md`` standard 4): recorded in the leaf's commit.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from app import ref_cache
from app.enums import SettledDayBasisEnum, StatusEnum, TxnTypeEnum
from app.extensions import db as _db
from app.models.statement_match import StatementMatch, StatementMatchMember
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transaction_template import TransactionTemplate
from app.services import definition_edit
from app.services.settle_day import SettleDay
from app.services.transaction_service import (
    StatedRecord,
    apply_requested_status,
    settle_transaction,
)
# Pylint: ``shekel-private-module-import`` -- the statement-match builders are
# the one way a test stages a bank line, a scope and an accepted act as the
# app does (the convention ``test_cc5_4a4_row_keeps_its_movements`` keeps).
# pylint: disable=shekel-private-module-import
from app.services.statement_match import accept_match
from tests._test_helpers import (
    add_entry,
    create_account_of_type,
    generate_row_of,
    load_migration_module,
    make_expense_template,
    one_off_row_of,
    run_migration_callable as _run,
)
from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_scope,
    a_submission,
    an_envelope,
    an_import,
)

#: This step's own revision, loaded so its SHIPPED callables are what runs.
_M = load_migration_module("2eabfa596ee0_a_match_member_is_a_line_or_a_movement.py")

_HOTEL = Decimal("120.00")


def _installed(session):
    """Return which of the row arm's schema objects the database holds."""
    return {
        "column": session.execute(text(
            "SELECT count(*) FROM information_schema.columns "
            " WHERE table_schema='budget' "
            "   AND table_name='statement_match_members' "
            "   AND column_name='transaction_id'"
        )).scalar(),
        "key": session.execute(text(
            "SELECT count(*) FROM pg_constraint "
            " WHERE conname='fk_statement_match_members_transaction_account'"
        )).scalar(),
        "index": session.execute(text(
            "SELECT count(*) FROM pg_indexes "
            " WHERE indexname='uq_statement_match_members_transaction'"
        )).scalar(),
        "check": session.execute(text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            " WHERE conname='ck_statement_match_members_one_subject'"
        )).scalar(),
    }


def _is_upgraded(session):
    state = _installed(session)
    return (
        (state["column"], state["key"], state["index"]) == (0, 0, 0)
        and "transaction_id" not in state["check"]
    )


#: The three objects exactly as ``c1e7d4b3a850`` left them, read off the
#: 2026-09-22 17:06 production dump with ``pg_get_constraintdef`` /
#: ``pg_get_indexdef`` -- what the downgrade claims to restore.
_ORIGINAL = {
    "check": (
        "CHECK ((((((bank_statement_line_id IS NOT NULL))::integer + "
        "((transaction_id IS NOT NULL))::integer) + "
        "((transaction_entry_id IS NOT NULL))::integer) = 1))"
    ),
    "key": (
        "FOREIGN KEY (transaction_id, account_id) REFERENCES "
        "budget.transactions(id, account_id) ON DELETE CASCADE"
    ),
    "index": (
        "CREATE UNIQUE INDEX uq_statement_match_members_transaction ON "
        "budget.statement_match_members USING btree (transaction_id) "
        "WHERE (transaction_id IS NOT NULL)"
    ),
}


def _is_downgraded(session):
    """Whether the row arm is back EXACTLY as ``c1e7d4b3a850`` defined it."""
    state = _installed(session)
    return (
        state["column"] == 1
        and state["check"] == _ORIGINAL["check"]
        and session.execute(text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            " WHERE conname='fk_statement_match_members_transaction_account'"
        )).scalar() == _ORIGINAL["key"]
        and session.execute(text(
            "SELECT indexdef FROM pg_indexes "
            " WHERE indexname='uq_statement_match_members_transaction'"
        )).scalar() == _ORIGINAL["index"]
    )


def _day(seed_user, offset=1):
    """A settle day past the fixture accounts' origination assertion."""
    return seed_user["bootstrap_period"].start_date + timedelta(days=offset)


def _diverge_ids(seed_user):
    """Record four purchases first, so no later payment's id is any row's.

    One envelope row and four movements: every bill after it takes row id
    ``n`` and a payment id at least ``n + 3``, so across two bills neither
    payment can equal either row.
    """
    envelope = an_envelope(seed_user)
    for amount in ("18.64", "7.10", "3.25", "41.00"):
        add_entry(
            _db.session, seed_user, envelope, Decimal(amount),
            seed_user["bootstrap_period"].start_date,
        )
    _db.session.commit()


def _settled_bill(seed_user, name="Hotel", *, day_offset=1):
    """One engine-generated `$120.00` bill on checking, settled on its day."""
    template = make_expense_template(
        _db.session, seed_user, amount=str(_HOTEL), name=name,
        category_key="Rent",
    )
    row = generate_row_of(template, seed_user["bootstrap_period"])
    _db.session.commit()
    settle_transaction(row, settle_day=SettleDay(
        day=_day(seed_user, day_offset), basis=SettledDayBasisEnum.ENTERED,
    ))
    _db.session.commit()
    return row


def _payment(row):
    """The row's one covering movement, re-read."""
    _db.session.expire(row)
    (movement,) = row.covering_movements
    return movement


def _matched(seed_user, row, *, line=None, statement=None, day_offset=1):
    """Accept a match of *row* against one `-$120.00` line; return the act id."""
    if line is None:
        line = a_bank_line(
            seed_user, statement or an_import(seed_user), amount="-120.00",
            posted_on=_day(seed_user, day_offset),
        )
        _db.session.commit()
    scope = a_scope(seed_user)
    accepted = accept_match(
        a_submission(scope, lines=[line], transactions=[row]), scope,
    )
    _db.session.commit()
    return accepted.match_id


def _as_the_past_recorded_it(match_id, row_id):
    """Rewrite *match_id*'s app member to name the ROW, the pre-CC-5-4a-1 shape.

    Only callable at the DOWNGRADED schema, where the column exists.
    """
    _db.session.execute(text(
        "UPDATE budget.statement_match_members "
        "   SET transaction_id = :row_id, transaction_entry_id = NULL "
        " WHERE match_id = :match_id AND transaction_entry_id IS NOT NULL"
    ), {"row_id": row_id, "match_id": match_id})
    _db.session.commit()


def _members(match_id):
    """Return ``{(bank_statement_line_id, transaction_entry_id, account_id)}``."""
    return set(_db.session.execute(text(
        "SELECT bank_statement_line_id, transaction_entry_id, account_id "
        "  FROM budget.statement_match_members WHERE match_id = :match_id"
    ), {"match_id": match_id}).all())


def _row_members():
    return _db.session.execute(text(
        "SELECT id, transaction_id FROM budget.statement_match_members "
        " WHERE transaction_id IS NOT NULL ORDER BY id"
    )).all()


class TestTheUpgradeReKeysEveryRowMemberOntoItsPayment:
    """The stored past, re-keyed: a member is a bank line or a movement."""

    def test_each_row_member_takes_its_rows_payment_and_the_column_goes(
        self, app, db, seed_user, capsys,
    ):
        """Two old-shape acts, each re-keyed onto ITS row's payment, not a neighbour's.

        Two rows so a re-key that took one payment for every member -- or a
        row's id as a movement's -- cannot pass; the ids diverge
        (module docstring) so a row id and a payment id cannot agree by
        accident.  The printed count is the operator's measurement.
        """
        _diverge_ids(seed_user)
        hotel = _settled_bill(seed_user, "Hotel", day_offset=1)
        flight = _settled_bill(seed_user, "Flight", day_offset=2)
        statement = an_import(seed_user)
        hotel_act = _matched(seed_user, hotel, statement=statement, day_offset=1)
        flight_act = _matched(seed_user, flight, statement=statement, day_offset=2)
        hotel_pay, flight_pay = _payment(hotel), _payment(flight)
        assert {hotel_pay.id, flight_pay.id}.isdisjoint({hotel.id, flight.id}), (
            "the fixture's payment ids coincide with row ids; the re-key's "
            "join could pass by accident"
        )
        before = {hotel_act: _members(hotel_act), flight_act: _members(flight_act)}

        _run(_M.downgrade, db.session)
        assert _is_downgraded(db.session)
        _as_the_past_recorded_it(hotel_act, hotel.id)
        _as_the_past_recorded_it(flight_act, flight.id)
        assert [row_id for _id, row_id in _row_members()] == [hotel.id, flight.id]

        capsys.readouterr()
        _run(_M.upgrade, db.session)

        assert _is_upgraded(db.session)
        assert _members(hotel_act) == before[hotel_act]
        assert _members(flight_act) == before[flight_act]
        assert (None, hotel_pay.id, seed_user["account"].id) in _members(hotel_act)
        assert "re-keyed 2 row member(s) onto their payment" in capsys.readouterr().out

    def test_a_payment_dated_off_its_rows_day_is_re_keyed_and_counted(
        self, app, db, seed_user, capsys,
    ):
        """The one state under which a re-keyed act reads differently, printed.

        The seam mirrors a row's day onto its payment, so production holds none
        (0 of 103); a payment moved off it is still the subject (ruling
        **R-CC43**) and is re-keyed, and the operator is told how many.
        """
        _diverge_ids(seed_user)
        hotel = _settled_bill(seed_user)
        act = _matched(seed_user, hotel)
        payment = _payment(hotel)
        _run(_M.downgrade, db.session)
        _as_the_past_recorded_it(act, hotel.id)
        db.session.execute(text(
            "UPDATE budget.transaction_entries "
            "   SET settled_on = settled_on + 2 WHERE id = :id"
        ), {"id": payment.id})
        db.session.commit()

        capsys.readouterr()
        _run(_M.upgrade, db.session)

        assert (None, payment.id, seed_user["account"].id) in _members(act)
        assert (
            "re-keyed 1 row member(s) onto their payment "
            "(1 dated on another day than its row)"
        ) in capsys.readouterr().out

    def test_the_downgrade_restores_the_schema_and_re_keys_nothing(
        self, app, db, seed_user,
    ):
        """A movement member stays one: the revision below writes and reads it.

        And the upgrade after it re-keys zero, which is the round trip.
        """
        _diverge_ids(seed_user)
        hotel = _settled_bill(seed_user)
        act = _matched(seed_user, hotel)
        members = _members(act)

        _run(_M.downgrade, db.session)
        assert _is_downgraded(db.session)
        assert _members(act) == members
        assert _row_members() == []

        _run(_M.upgrade, db.session)
        assert _is_upgraded(db.session)
        assert _members(act) == members


class TestTheUpgradeREFUSESARowMemberItCannotReKey:
    """Ruling **R-CC45**: a member with no payment refuses; nothing is guessed.

    Each refusal is asserted by its OWN sentence, so a bare ``raises`` that the
    key or the index would also satisfy cannot stand in for the explicit
    count; the schema reads DOWNGRADED after the rollback and the member
    still names its row, which grades that the refused upgrade left the
    database as it found it.  (That the refusal runs BEFORE any write is the
    code's order -- ``refuse_unkeyable_row_members`` is the upgrade's first
    statement -- and not this assertion's: the rollback would undo a write
    too.)
    """

    @staticmethod
    def _refused(db):
        with pytest.raises(RuntimeError) as caught:
            _run(_M.upgrade, db.session)
        db.session.rollback()
        return str(caught.value)

    def test_a_row_with_no_payment_refuses(self, app, db, seed_user):
        """A Projected row named by an old act -- a Credit or Cancelled close
        matched before today's refusals has the same shape: no payment."""
        _diverge_ids(seed_user)
        hotel = _settled_bill(seed_user)
        act = _matched(seed_user, hotel)
        unpaid = generate_row_of(
            make_expense_template(
                db.session, seed_user, amount="45.00", name="Unpaid",
                category_key="Rent",
            ),
            seed_user["bootstrap_period"],
        )
        db.session.commit()
        _run(_M.downgrade, db.session)
        _as_the_past_recorded_it(act, unpaid.id)
        (member_id, _row), = _row_members()

        message = self._refused(db)

        assert message.startswith("CC-5-4a-2 refuses to re-key")
        assert (
            f"1 row member(s) whose row holds no covering movement "
            f"(ids [{member_id}]" in message
        )
        assert _is_downgraded(db.session)
        assert _row_members() == [(member_id, unpaid.id)]

    def test_a_payment_on_another_account_refuses(self, app, db, seed_user):
        """The row's payment moved to the card after the act named the row.

        The movement key cannot hold the payment to checking's act, and the
        revision does not decide which statement showed the money.
        """
        _diverge_ids(seed_user)
        card = create_account_of_type(
            seed_user, db.session, "Credit Card", "Rewards Card",
            anchor_balance=Decimal("-500.00"),
            observed_on=seed_user["bootstrap_period"].start_date,
        )
        hotel = _settled_bill(seed_user)
        act = _matched(seed_user, hotel)
        _run(_M.downgrade, db.session)
        _as_the_past_recorded_it(act, hotel.id)
        apply_requested_status(
            hotel, hotel.status_id,
            stated=StatedRecord(tender_account_id=card.id),
        )
        db.session.commit()
        assert _payment(hotel).account_id == card.id
        (member_id, _row), = _row_members()

        message = self._refused(db)

        assert (
            f"1 row member(s) whose covering movement is on another account "
            f"than the act's (ids [{member_id}]" in message
        )
        assert _is_downgraded(db.session)
        assert _row_members() == [(member_id, hotel.id)]

    def test_a_payment_another_act_names_refuses(self, app, db, seed_user):
        """ONE ACT PER ROW, asserted: the row by its old member, its payment by a new one."""
        _diverge_ids(seed_user)
        hotel = _settled_bill(seed_user)
        statement = an_import(seed_user)
        old_act = _matched(seed_user, hotel, statement=statement)
        _run(_M.downgrade, db.session)
        _as_the_past_recorded_it(old_act, hotel.id)
        second_line = a_bank_line(
            seed_user, statement, amount="-120.00", posted_on=_day(seed_user),
            sequence_in_group=1,
        )
        db.session.commit()
        new_act = StatementMatch(
            account_id=seed_user["account"].id, user_id=seed_user["user"].id,
            applied_by_rule=False,
        )
        db.session.add(new_act)
        db.session.flush()
        for subject in (
            {"bank_statement_line_id": second_line.id},
            {"transaction_entry_id": _payment(hotel).id},
        ):
            db.session.add(StatementMatchMember(
                match_id=new_act.id, account_id=seed_user["account"].id,
                **subject,
            ))
        db.session.commit()
        (member_id, _row), = _row_members()

        message = self._refused(db)

        assert (
            f"1 row member(s) whose covering movement another member already "
            f"names (ids [{member_id}]" in message
        )
        assert _is_downgraded(db.session)

    def test_the_same_member_re_keys_once_its_row_holds_its_own_payment(
        self, app, db, seed_user,
    ):
        """THE CONTROL for the three refusals: one old-shape member, nothing wrong.

        Without it each refusal would pass as well against a revision that
        refused every database.
        """
        _diverge_ids(seed_user)
        hotel = _settled_bill(seed_user)
        act = _matched(seed_user, hotel)
        _run(_M.downgrade, db.session)
        _as_the_past_recorded_it(act, hotel.id)

        _run(_M.upgrade, db.session)

        assert _is_upgraded(db.session)
        assert (None, _payment(hotel).id, seed_user["account"].id) in _members(act)


class TestThePaymentIsFoundByItsMarkNotByItsParent:
    """The re-key joins on ``covers_settlement``: a purchase is not a payment."""

    def test_a_row_holding_a_purchase_beside_its_payment_re_keys_onto_the_payment(
        self, app, db, seed_user,
    ):
        """The schema admits a settled row holding BOTH (``status_seam._covering``'s
        module docstring); the member takes the one the seam marked."""
        _diverge_ids(seed_user)
        hotel = _settled_bill(seed_user)
        act = _matched(seed_user, hotel)
        payment = _payment(hotel)
        purchase = TransactionEntry(
            transaction_id=hotel.id, account_id=hotel.account_id,
            owner_id=hotel.user_id, user_id=hotel.user_id,
            amount=Decimal("5.00"), description="Minibar",
            purchased_on=seed_user["bootstrap_period"].start_date,
            figure_source_id=payment.figure_source_id,
        )
        db.session.add(purchase)
        db.session.commit()
        _run(_M.downgrade, db.session)
        _as_the_past_recorded_it(act, hotel.id)

        _run(_M.upgrade, db.session)

        app_members = {
            entry for _line, entry, _account in _members(act) if entry is not None
        }
        assert app_members == {payment.id}

    def test_a_row_holding_only_purchases_is_refused_as_holding_no_payment(
        self, app, db, seed_user,
    ):
        """The shape of the ruling's first example, a bill closed from its purchases.

        Staged as an envelope holding a purchase and no payment -- the same
        shape the refusal reads, whether or not the row was ever closed.  The
        row has movements, and none is its payment; the refusal names it
        as holding no covering movement rather than letting the re-key write a
        member naming nothing (which the one-subject check would refuse by
        name only).
        """
        _diverge_ids(seed_user)
        envelope = an_envelope(seed_user, name="Dining")
        add_entry(
            db.session, seed_user, envelope, Decimal("30.00"),
            seed_user["bootstrap_period"].start_date,
        )
        db.session.commit()
        line = a_bank_line(
            seed_user, an_import(seed_user), amount="-30.00", posted_on=_day(seed_user),
        )
        db.session.commit()
        _run(_M.downgrade, db.session)
        act = StatementMatch(
            account_id=seed_user["account"].id, user_id=seed_user["user"].id,
            applied_by_rule=False,
        )
        db.session.add(act)
        db.session.flush()
        db.session.execute(text(
            "INSERT INTO budget.statement_match_members "
            "  (match_id, account_id, bank_statement_line_id, transaction_id) "
            "VALUES (:act, :account, :line, NULL), (:act, :account, NULL, :row)"
        ), {
            "act": act.id, "account": seed_user["account"].id,
            "line": line.id, "row": envelope.id,
        })
        db.session.commit()
        (member_id, _row), = _row_members()

        message = TestTheUpgradeREFUSESARowMemberItCannotReKey._refused(db)

        assert (
            f"1 row member(s) whose row holds no covering movement "
            f"(ids [{member_id}]" in message
        )
        assert _is_downgraded(db.session)

    def test_a_purchase_on_another_account_beside_the_payment_is_no_refusal(
        self, app, db, seed_user,
    ):
        """Only the PAYMENT's account is asked: a card swipe under the row is not it."""
        _diverge_ids(seed_user)
        card = create_account_of_type(
            seed_user, db.session, "Credit Card", "Rewards Card",
            anchor_balance=Decimal("-500.00"),
            observed_on=seed_user["bootstrap_period"].start_date,
        )
        hotel = _settled_bill(seed_user)
        act = _matched(seed_user, hotel)
        payment = _payment(hotel)
        db.session.add(TransactionEntry(
            transaction_id=hotel.id, account_id=card.id,
            owner_id=hotel.user_id, user_id=hotel.user_id,
            amount=Decimal("5.00"), description="Minibar",
            purchased_on=seed_user["bootstrap_period"].start_date,
            figure_source_id=payment.figure_source_id,
        ))
        db.session.commit()
        _run(_M.downgrade, db.session)
        _as_the_past_recorded_it(act, hotel.id)

        _run(_M.upgrade, db.session)

        assert (None, payment.id, seed_user["account"].id) in _members(act)

    def test_a_purchase_another_act_names_under_the_row_is_no_refusal(
        self, app, db, seed_user,
    ):
        """Only the PAYMENT's claims are asked: another act's purchase under the row is not it."""
        _diverge_ids(seed_user)
        hotel = _settled_bill(seed_user)
        statement = an_import(seed_user)
        act = _matched(seed_user, hotel, statement=statement)
        payment = _payment(hotel)
        purchase = TransactionEntry(
            transaction_id=hotel.id, account_id=hotel.account_id,
            owner_id=hotel.user_id, user_id=hotel.user_id,
            amount=Decimal("5.00"), description="Minibar",
            purchased_on=seed_user["bootstrap_period"].start_date,
            figure_source_id=payment.figure_source_id,
        )
        db.session.add(purchase)
        db.session.flush()
        other_line = a_bank_line(
            seed_user, statement, amount="-5.00", posted_on=_day(seed_user),
        )
        other_act = StatementMatch(
            account_id=seed_user["account"].id, user_id=seed_user["user"].id,
            applied_by_rule=False,
        )
        db.session.add(other_act)
        db.session.flush()
        for subject in (
            {"bank_statement_line_id": other_line.id},
            {"transaction_entry_id": purchase.id},
        ):
            db.session.add(StatementMatchMember(
                match_id=other_act.id, account_id=seed_user["account"].id,
                **subject,
            ))
        db.session.commit()
        _run(_M.downgrade, db.session)
        _as_the_past_recorded_it(act, hotel.id)

        _run(_M.upgrade, db.session)

        assert (None, payment.id, seed_user["account"].id) in _members(act)
        assert (None, purchase.id, seed_user["account"].id) in _members(other_act.id)


class TestADefinitionsAccountMoveNoLongerMeetsTheMemberKey:
    """Ledger row **CC-356**'s own predicate, graded both sides of the re-key.

    A REVERTED bill keeps its payment, un-dated (ruling **R-CC42**), and an
    act recorded before ``CC-5-4a-1`` named the ROW.  Moving the row's account
    -- the assignment the definition passes make
    (``recurrence_engine._maintain``, ruling **R-CC36**) -- met
    ``fk_statement_match_members_transaction_account`` and raised.  Re-keyed,
    the act names the payment, which stays where its money moved, so the move
    touches no member.
    """

    def test_the_move_raised_on_the_row_member_and_passes_on_the_payment(
        self, app, db, seed_user,
    ):
        """THE CONTROL first (the defect, reproduced), then the same move re-keyed."""
        import sqlalchemy  # pylint: disable=import-outside-toplevel
        _diverge_ids(seed_user)
        other = create_account_of_type(
            seed_user, db.session, "Checking", "Second Checking",
        )
        hotel = _settled_bill(seed_user)
        act = _matched(seed_user, hotel)
        apply_requested_status(hotel, ref_cache.status_id(StatusEnum.PROJECTED))
        db.session.commit()
        payment = _payment(hotel)
        assert payment.settled_on is None
        _run(_M.downgrade, db.session)
        _as_the_past_recorded_it(act, hotel.id)

        hotel.account_id = other.id
        with pytest.raises(sqlalchemy.exc.IntegrityError) as caught:
            db.session.flush()
        assert "fk_statement_match_members_transaction_account" in str(caught.value)
        db.session.rollback()

        _run(_M.upgrade, db.session)
        members = _members(act)
        hotel.account_id = other.id
        db.session.commit()

        db.session.expire_all()
        assert hotel.account_id == other.id
        assert _payment(hotel).account_id == seed_user["account"].id
        assert _members(act) == members
        assert (None, payment.id, seed_user["account"].id) in members

    def test_the_definition_door_moves_the_row_once_the_act_names_its_payment(
        self, app, db, seed_user,
    ):
        """CC-356 through its own DOOR, not the assignment it writes.

        ``definition_edit.propagate_to_unruled_rows`` -- the rule-less
        definition's edit, which moves every Projected row's account with the
        definition (``recurrence_engine.propagate_to_unruled_definition``,
        ruling **R-CC36**).  THE CONTROL: on the old-shape member the door
        itself raises on the row-member key; re-keyed, the same door commits
        and touches no member.
        """
        import sqlalchemy  # pylint: disable=import-outside-toplevel
        _diverge_ids(seed_user)
        checking = seed_user["account"]
        other = create_account_of_type(
            seed_user, db.session, "Checking", "Second Checking",
        )
        row = one_off_row_of(
            seed_user["bootstrap_period"], name="Hotel", amount=_HOTEL,
            user_id=seed_user["user"].id, account_id=checking.id,
            scenario_id=seed_user["scenario"].id,
            transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        )
        db.session.commit()
        settle_transaction(row, settle_day=SettleDay(
            day=_day(seed_user), basis=SettledDayBasisEnum.ENTERED,
        ))
        db.session.commit()
        act = _matched(seed_user, row)
        apply_requested_status(row, ref_cache.status_id(StatusEnum.PROJECTED))
        db.session.commit()
        payment = _payment(row)
        template_id = row.template_id
        _run(_M.downgrade, db.session)
        _as_the_past_recorded_it(act, row.id)

        template = db.session.get(TransactionTemplate, template_id)
        template.account_id = other.id
        with pytest.raises(sqlalchemy.exc.IntegrityError) as caught:
            definition_edit.propagate_to_unruled_rows(template)
        assert "fk_statement_match_members_transaction_account" in str(caught.value)
        db.session.rollback()

        _run(_M.upgrade, db.session)
        members = _members(act)
        template = db.session.get(TransactionTemplate, template_id)
        template.account_id = other.id
        definition_edit.propagate_to_unruled_rows(template)
        db.session.commit()

        db.session.expire_all()
        assert db.session.get(Transaction, row.id).account_id == other.id
        assert _payment(row).account_id == checking.id
        assert _members(act) == members
        assert (None, payment.id, checking.id) in members
