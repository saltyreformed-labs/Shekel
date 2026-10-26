"""A line's clearing link names a statement of its OWN account, structurally.

Plan step **X-f3a-1**, ruling **R-FL**: *whether a line is INSIDE a declared
balance is a RECORDED FACT, not a comparison of two dates.*  Migration
``d5b8e2c74a19`` gives ``budget.transactions`` and
``budget.transaction_entries`` a ``reconciled_by_id`` naming the
``account_anchor_history`` row whose statement showed the line.

**Every test here is a FIRING CONTROL** (``docs/plans/verification.md`` standard
4).  No production row carries a link as of this step -- nothing is backfilled,
deliberately -- so nothing in the app exercises these keys, and a test that
merely asserted a constraint EXISTS would pass against one that admitted
everything.  Each test below writes the state the schema is supposed to refuse
and asserts the refusal by NAME at the database tier, which is the only tier
that can see a writer bypassing the ORM.

The shapes under test, and what each one would cost if it were writable:

* **a link to ANOTHER account's statement** -- the defect a single-column
  ``REFERENCES account_anchor_history (id)`` could not prevent.  Clearing is a
  per-account question (a checking statement shows a transfer's outgoing leg,
  the savings statement shows the incoming one), so such a link would record
  that a statement nobody read showed this money;
* **a link to the row's OWN account's statement** -- the legitimate act, which
  must be ACCEPTED, because a key that refuses the correct write is worse than
  none;
* **no link at all** -- ``MATCH SIMPLE``'s arm, which is what lets a composite
  key sit beside a nullable column: every row in the database is in this state
  today and none of them may be refused;
* **an entry on another OWNER's account, or claiming an owner that is not
  its row's** -- ``fk_transaction_entries_owner_account`` and
  ``fk_transaction_entries_owner_transaction`` (plan step
  ``credit_card:CC-5-1``, ruling **R-BAL76**), which replaced the
  parent-account co-location that held a movement's account EQUAL to its
  parent's: a movement's account is its own since that step (a card purchase
  in a checking envelope sits on the card), so the legitimate write the old
  key refused -- another account of the SAME owner -- must be ACCEPTED, and
  a parent's account move no longer carries its movements;
* **deleting an assertion a line still names** -- the ``ON DELETE RESTRICT``,
  which refuses rather than silently converting a recorded observation into
  "never observed";
* **the backfill**, driven through the migration's own
  ``BACKFILL_ENTRY_ACCOUNT_SQL`` so this test and the production migration
  cannot drift.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa
import sqlalchemy.exc

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db as _db
from app.models.account import AccountAnchorHistory
from app.models.ref import TransactionType
from app.models.amount_ownership import AmountOwnership
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import entry_service, status_seam
from tests._test_helpers import (
    typed,
    an_entered_day,
    append_only_guard_lifted,
    bare_expense_template,
    figure_source_columns,
    settle_day_columns,
    settlement_if_settling,
)
from tests._test_helpers import load_migration_module
from app.services.settle_day import record_settle_day

_MIGRATION = load_migration_module("d5b8e2c74a19_clearing_is_a_recorded_fact.py")


def _opening_of(account_id: int) -> AccountAnchorHistory:
    """Return *account_id*'s opening assertion.

    Every account has one: ``account_service.create_account`` writes it in the
    same call that creates the row, which is the invariant migration
    ``cfb15e782f86`` established.

    Args:
        account_id: The account whose assertion to fetch.

    Returns:
        Its earliest :class:`~app.models.account.AccountAnchorHistory` row.
    """
    return (
        _db.session.query(AccountAnchorHistory)
        .filter_by(account_id=account_id)
        .order_by(AccountAnchorHistory.observed_on, AccountAnchorHistory.id)
        .first()
    )


def _cleared_by(assertion: AccountAnchorHistory) -> dict:
    """Return the field pair a line CLEARED by *assertion* carries.

    Both columns, always, because ``ck_*_cleared_needs_settle_day`` pairs them:
    a statement cannot have shown money that never moved, so a fixture that set
    only the link would be refused before it reached the key it means to test --
    which is how six tests in this module failed the first time they ran.

    The day is the assertion's OWN, which is what
    ``reconcile_service.record_settled_days`` stamps: the statement's day as an
    upper bound on the true posting day.

    Args:
        assertion: The statement the line is cleared by.

    Returns:
        ``{"reconciled_by_id": ..., "settled_on": ...}``, to splat into
        :func:`_make_transaction` or :func:`_make_entry`.
    """
    return {
        "reconciled_by_id": assertion.id,
        "settled_on": assertion.observed_on,
    }


def _make_transaction(data, **overrides) -> Transaction:
    """Return an unflushed Projected expense on the fixture's checking account.

    Args:
        data: The ``seed_full_user_data`` fixture payload.
        **overrides: Column values to set or replace -- notably
            ``reconciled_by_id``, the column under test.

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
        _db.session.query(TransactionType).filter_by(name="Expense").one()
    )
    definition = bare_expense_template(
        _db.session, data, name="Clearing control definition",
    )
    fields = {
        "user_id": data["periods"][0].user_id,
        "template_id": definition.id,
        "pay_period_id": data["periods"][0].id,
        "scenario_id": data["scenario"].id,
        "account_id": data["account"].id,
        "status_id": ref_cache.status_id(StatusEnum.PROJECTED),
        "name": "Clearing control",
        "category_id": data["categories"]["Rent"].id,
        "transaction_type_id": expense_type.id,
        "estimated_amount": Decimal("300.00"),
        "due_date": data["periods"][0].start_date,
        "occurs_on": data["periods"][0].start_date,
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
    # The row's settlement RECORD is its covering movement, a row of the
    # entries table (plan step ``balance:X-bi-4b-2``); nothing of this table
    # carries it, so a bare row built here records nothing -- the ``$0.00``
    # record for a settled one (ruling R-BAL82) -- which is fine on this
    # axis: every case here grades the CLEARING LINK, never money.
    # **The amount-ownership pair is ONE attribute** (plan step X-au-k), so the
    # figure this builder splats becomes the row's OWNERSHIP at the last
    # moment -- after every line above that reads it as a column.
    fields["amount_ownership"] = AmountOwnership.own(fields.pop("estimated_amount"))
    return Transaction(**fields)


def _make_entry(data, parent: Transaction, **overrides) -> TransactionEntry:
    """Return an unflushed purchase against *parent*.

    Args:
        data: The ``seed_full_user_data`` fixture payload.
        parent: The flushed parent transaction.
        **overrides: Column values to set or replace.

    Returns:
        The unflushed :class:`~app.models.transaction_entry.TransactionEntry`.
    """
    fields = {
        "transaction_id": parent.id,
        "account_id": parent.account_id,
        "owner_id": parent.user_id,
        "user_id": data["user"].id,
        "amount": Decimal("25.00"),
        "description": "Clearing control",
        "purchased_on": date(2026, 1, 5),
        "is_credit": False,
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
    # WHO WROTE the figure, for the same reason (plan step **X-bi-3a**): the
    # column is NOT NULL with no default, and the source is not this suite's
    # subject.
    if "figure_source_id" not in overrides:
        fields.update(figure_source_columns())
    return TransactionEntry(**fields)


class TestATransactionsClearingLinkIsScopedByAccount:
    """``fk_transactions_reconciled_by`` admits only this account's statements."""

    def test_another_accounts_statement_is_refused(
        self, app, db, seed_full_user_data,
    ):
        """A checking row may not name the savings account's assertion.

        The defect the composite key exists for.  A single-column FK to
        ``account_anchor_history (id)`` accepts this write, and nothing later
        could tell the resulting link from one the user made: the line would
        claim a statement showed it that was never about this account.
        """
        with app.app_context():
            savings_opening = _opening_of(
                seed_full_user_data["savings_account"].id,
            )
            txn = _make_transaction(
                seed_full_user_data, **_cleared_by(savings_opening),
            )
            db.session.add(txn)
            with pytest.raises(
                sqlalchemy.exc.IntegrityError,
                match="fk_transactions_reconciled_by",
            ):
                db.session.flush()
            db.session.rollback()

    def test_its_own_accounts_statement_is_accepted(
        self, app, db, seed_full_user_data,
    ):
        """The legitimate link -- this account's own assertion -- is allowed.

        Without this the suite could not tell a correct key from one that
        refuses every link, which would make the panel's write door a 500.
        """
        with app.app_context():
            opening = _opening_of(seed_full_user_data["account"].id)
            txn = _make_transaction(
                seed_full_user_data, **_cleared_by(opening),
            )
            db.session.add(txn)
            db.session.flush()

            assert txn.reconciled_by_id == opening.id
            db.session.rollback()

    def test_no_link_at_all_is_accepted(self, app, db, seed_full_user_data):
        """An unlinked row is unaffected by the composite key.

        ``MATCH SIMPLE`` is what makes a composite foreign key legal beside a
        nullable column, and it is not a detail: EVERY row in the database is in
        this state the moment the migration lands, because nothing is
        backfilled.  A key that refused them would be a failed deploy.
        """
        with app.app_context():
            txn = _make_transaction(seed_full_user_data)
            db.session.add(txn)
            db.session.flush()

            assert txn.reconciled_by_id is None
            db.session.rollback()


class TestAPurchasesClearingLinkIsScopedByAccount:
    """``fk_transaction_entries_reconciled_by``, the purchase twin."""

    def test_another_accounts_statement_is_refused(
        self, app, db, seed_full_user_data,
    ):
        """A purchase against a checking envelope may not name savings'.

        The same defect one table down, and it is the one that would bite
        first: the reconcile panel's purchase arm is a bulk ``UPDATE``, so a
        scope it lost would stamp every matching row at once.
        """
        with app.app_context():
            parent = _make_transaction(seed_full_user_data)
            db.session.add(parent)
            db.session.flush()
            savings_opening = _opening_of(
                seed_full_user_data["savings_account"].id,
            )

            entry = _make_entry(
                seed_full_user_data, parent,
                purchased_on=savings_opening.observed_on,
                **_cleared_by(savings_opening),
            )
            db.session.add(entry)
            with pytest.raises(
                sqlalchemy.exc.IntegrityError,
                match="fk_transaction_entries_reconciled_by",
            ):
                db.session.flush()
            db.session.rollback()

    def test_its_parents_accounts_statement_is_accepted(
        self, app, db, seed_full_user_data,
    ):
        """The legitimate link is allowed, so the panel's tick can land."""
        with app.app_context():
            parent = _make_transaction(seed_full_user_data)
            db.session.add(parent)
            db.session.flush()
            opening = _opening_of(seed_full_user_data["account"].id)

            entry = _make_entry(
                seed_full_user_data, parent,
                # Bought on or before the statement day, which
                # ``ck_transaction_entries_settled_not_before_purchase``
                # requires: money cannot leave the account before it was spent.
                purchased_on=opening.observed_on,
                **_cleared_by(opening),
            )
            db.session.add(entry)
            db.session.flush()

            assert entry.reconciled_by_id == opening.id
            db.session.rollback()


class TestAMovementsAccountIsItsOwnAndItsOwnerIsItsRows:
    """The two owner keys of plan step ``credit_card:CC-5-1`` (ruling **R-BAL76**).

    **Re-expressed 2026-09-20 with the developer's confirmation** (CLAUDE.md
    rule 5, under R-BAL76): through that step this class graded
    ``fk_transaction_entries_parent_account``, which held a movement's account
    EQUAL to its parent's and cascaded a parent's move onto its movements.
    The card's first door writes a movement whose account is NOT its parent's
    -- a card purchase inside a checking envelope -- so the key is dropped and
    what the schema holds instead is OWNERSHIP:
    ``fk_transaction_entries_owner_transaction`` keeps ``owner_id`` the row's
    and ``fk_transaction_entries_owner_account`` keeps the account that
    owner's.  Each key is graded ALONE below (the other satisfied), because a
    row that breaks both is refused by whichever PostgreSQL evaluates first
    and would grade neither by name.

    The clearing key this file is about rests on ``account_id`` exactly as it
    did: a movement clears against a statement of the account its money moved
    through, which since this step is the movement's OWN account -- what a
    card statement clearing a card purchase needs.
    """

    def test_another_account_of_the_same_owner_is_writable(
        self, app, db, seed_full_user_data,
    ):
        """The design's card swipe: a purchase on savings under a checking row.

        The legitimate write the dropped key refused, which must now be
        ACCEPTED -- a key that refuses the correct write is worse than none.
        Both owner keys are satisfied: the row and the savings account are the
        same owner's.
        """
        with app.app_context():
            parent = _make_transaction(seed_full_user_data)
            db.session.add(parent)
            db.session.flush()
            savings = seed_full_user_data["savings_account"]
            assert savings.id != parent.account_id

            entry = _make_entry(
                seed_full_user_data, parent, account_id=savings.id,
            )
            db.session.add(entry)
            db.session.flush()

            assert entry.account_id == savings.id
            assert entry.owner_id == parent.user_id
            assert entry.account.id == savings.id
            db.session.rollback()

    def test_another_owners_account_is_refused_by_name(
        self, app, db, seed_full_user_data, seed_second_user,
    ):
        """A movement may not sit on an account its row's owner does not hold.

        ``owner_id`` is the row's (so the transaction-owner key is satisfied)
        and the account is the SECOND user's, so exactly one key fails and it
        is named.  This is what makes a movement on another owner's account
        unwritable rather than gated at a door (R-BAL76), and it is what the
        card's ownership gate -- against the ROW's owner, never the caller --
        leans on.
        """
        with app.app_context():
            parent = _make_transaction(seed_full_user_data)
            db.session.add(parent)
            db.session.flush()
            foreign = seed_second_user["account"]
            assert foreign.user_id != parent.user_id

            entry = _make_entry(
                seed_full_user_data, parent, account_id=foreign.id,
            )
            db.session.add(entry)
            with pytest.raises(
                sqlalchemy.exc.IntegrityError,
                match="fk_transaction_entries_owner_account",
            ):
                db.session.flush()
            db.session.rollback()

    def test_an_owner_that_is_not_the_rows_is_refused_by_name(
        self, app, db, seed_full_user_data, seed_second_user,
    ):
        """``owner_id`` may not name anyone but the parent row's owner.

        The account and the stated owner AGREE (both the second user's, so
        the account-owner key is satisfied) and only the row disagrees, so
        exactly one key fails and it is named.  Either key alone would leave
        the other parent free to be anyone's; the pair is what makes the two
        parents unable to disagree.
        """
        with app.app_context():
            parent = _make_transaction(seed_full_user_data)
            db.session.add(parent)
            db.session.flush()
            other = seed_second_user

            entry = _make_entry(
                seed_full_user_data, parent,
                account_id=other["account"].id,
                owner_id=other["user"].id,
            )
            db.session.add(entry)
            with pytest.raises(
                sqlalchemy.exc.IntegrityError,
                match="fk_transaction_entries_owner_transaction",
            ):
                db.session.flush()
            db.session.rollback()

    def test_a_parents_account_move_leaves_its_purchases_where_their_money_moved(
        self, app, db, seed_full_user_data,
    ):
        """No cascade: a parent's account UPDATE does not move its movements.

        Through plan step ``credit_card:CC-5-1`` the dropped key CASCADED a
        parent's account move onto its entries (plan step X-bi-3c, ruling
        R-BAL46); a movement records where money moved, and re-pointing the
        row's EXPECTATION does not change that fact.  Driven at the database
        tier -- a raw UPDATE of the parent, the ORM told nothing -- so it
        grades the schema and not any applier.  The one writer that DOES move
        a leg's movement with its leg, the transfer endpoint move, assigns it
        by hand and is graded in ``test_covering_movement/test_a_paycheck_and_a_transfer.py``
        (``TestATransfersMovementsFollowItsLifecycle``).
        """
        with app.app_context():
            parent = _make_transaction(seed_full_user_data)
            db.session.add(parent)
            db.session.flush()
            entry = _make_entry(seed_full_user_data, parent)
            db.session.add(entry)
            db.session.flush()
            checking_id = parent.account_id
            savings_id = seed_full_user_data["savings_account"].id

            db.session.execute(
                sa.text(
                    "UPDATE budget.transactions SET account_id = :account "
                    "WHERE id = :id"
                ),
                {"account": savings_id, "id": parent.id},
            )
            stayed = db.session.execute(
                sa.text(
                    "SELECT account_id FROM budget.transaction_entries "
                    "WHERE id = :id"
                ),
                {"id": entry.id},
            ).scalar_one()
            assert stayed == checking_id
            db.session.rollback()

    def test_the_service_door_writes_the_parents_account(
        self, app, db, seed_user, seed_entry_template,
    ):
        """``entry_service.create_entry`` fills the column rather than the schema.

        The keys above refuse a wrong OWNER; nothing refuses an ABSENT account
        except NOT NULL, and the app has exactly one door that creates a
        purchase.  This grades that door against the parent it was handed:
        the parent's account, which is a purchase's default until the door
        takes an account of its own (plan step ``credit_card:CC-5-2``).
        """
        with app.app_context():
            parent = seed_entry_template["transaction"]
            entry = entry_service.create_entry(
                parent.id,
                seed_user["user"].id,
                entry_service.EntryDetails(
                    figure=typed(Decimal("12.50")),
                    description="Kroger",
                    purchased_on=date(2026, 1, 5),
                ),
            )
            db.session.flush()

            assert entry.account_id == parent.account_id
            db.session.rollback()


class TestALinkCannotOutliveItsSettleDay:
    """``ck_*_cleared_needs_settle_day``, and the two doors that back it up.

    A statement cannot have shown money that never moved.  The columns record
    different facts -- WHEN the cash moved and WHICH statement was seen to show
    it -- but one entails the other, so a row carrying a link and no day asserts
    both that a statement showed this line and that nothing has been observed to
    leave the account.

    The constraint matters because both doors that CLEAR a settle day would
    otherwise leave the link behind, and for a PURCHASE that is not inert: the
    entry reservation reads ``is_cleared``, so a released purchase would go on
    reading as cleared and the envelope would go on not holding its budget back.
    """

    def test_a_transaction_may_not_name_a_statement_with_no_settle_day(
        self, app, db, seed_full_user_data,
    ):
        """A link on an undated row is refused at the database."""
        with app.app_context():
            opening = _opening_of(seed_full_user_data["account"].id)
            txn = _make_transaction(
                seed_full_user_data,
                reconciled_by_id=opening.id, settled_on=None,
            )
            db.session.add(txn)
            with pytest.raises(
                sqlalchemy.exc.IntegrityError,
                match="ck_transactions_cleared_needs_settle_day",
            ):
                db.session.flush()
            db.session.rollback()

    def test_a_purchase_may_not_name_a_statement_with_no_settle_day(
        self, app, db, seed_full_user_data,
    ):
        """The purchase twin, and the one with a figure behind it."""
        with app.app_context():
            parent = _make_transaction(seed_full_user_data)
            db.session.add(parent)
            db.session.flush()
            opening = _opening_of(seed_full_user_data["account"].id)

            entry = _make_entry(
                seed_full_user_data, parent,
                reconciled_by_id=opening.id, settled_on=None,
            )
            db.session.add(entry)
            with pytest.raises(
                sqlalchemy.exc.IntegrityError,
                match="ck_transaction_entries_cleared_needs_settle_day",
            ):
                db.session.flush()
            db.session.rollback()

    def test_a_CARD_purchase_may_not_name_a_checking_statement(
        self, app, db, seed_full_user_data,
    ):
        """``ck_transaction_entries_card_purchase_clears_nowhere``.

        A credit-card purchase never touches checking -- it leaves later through
        its own CC Payback sibling -- so the account this link is scoped to is
        not the account the money left, and "the checking statement showed it"
        is false by construction.  The panel already refuses to OFFER one
        (``_purchases._outstanding_scope``'s ``is_credit IS FALSE``); this makes
        the state unwritable rather than merely unoffered, which is the
        difference plan step X-f3b turns into a posting.

        Production carries 18 card entries in history, so the shape is ordinary
        rather than exotic.
        """
        with app.app_context():
            parent = _make_transaction(seed_full_user_data)
            db.session.add(parent)
            db.session.flush()
            opening = _opening_of(seed_full_user_data["account"].id)

            entry = _make_entry(
                seed_full_user_data, parent,
                is_credit=True,
                purchased_on=opening.observed_on,
                **_cleared_by(opening),
            )
            db.session.add(entry)
            with pytest.raises(
                sqlalchemy.exc.IntegrityError,
                match="ck_transaction_entries_card_purchase_clears_nowhere",
            ):
                db.session.flush()
            db.session.rollback()

    def test_leaving_the_settled_band_releases_a_transactions_link(
        self, app, db, seed_full_user_data,
    ):
        """``status_seam`` releases the link where it releases the day.

        The door half, so the CHECK above is a backstop rather than a reachable
        500 on an ordinary revert.  A revert is the user saying the money did
        NOT move; an observation that it appeared on a statement cannot survive
        that.
        """
        with app.app_context():
            opening = _opening_of(seed_full_user_data["account"].id)
            txn = _make_transaction(
                seed_full_user_data,
                # Settled, because that is the only state a linked transaction
                # can legitimately be in: the CHECK pairs the link with a day
                # and the seam pairs the day with the settled band.
                status_id=ref_cache.status_id(StatusEnum.DONE),
                **_cleared_by(opening),
            )
            db.session.add(txn)
            db.session.flush()

            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.PROJECTED),
                settlement=settlement_if_settling(txn, ref_cache.status_id(StatusEnum.PROJECTED)),
            )
            db.session.flush()

            assert txn.settled_on is None
            assert txn.reconciled_by_id is None
            db.session.rollback()

    def test_clearing_a_purchases_posting_day_releases_its_link(
        self, app, db, seed_user, seed_entry_template,
    ):
        """``entry_service.update_entry`` releases the link with the day.

        The purchase twin of the door above, and the one that moves a figure:
        the reservation reads the link, so an entry that kept it would go on
        being subtracted from its envelope's held-back budget after the user
        said the bank had not been seen to take it.
        """
        with app.app_context():
            parent = seed_entry_template["transaction"]
            opening = _opening_of(parent.account_id)
            entry = entry_service.create_entry(
                parent.id,
                seed_user["user"].id,
                entry_service.EntryDetails(
                    figure=typed(Decimal("12.50")),
                    description="Kroger",
                    purchased_on=date(2026, 1, 5),
                ),
            )
            record_settle_day(entry, an_entered_day(date(2026, 1, 6)))
            entry.reconciled_by_id = opening.id
            db.session.flush()

            entry_service.update_entry(
                entry.id, seed_user["user"].id, settle_day=None,
            )

            assert entry.settled_on is None
            assert entry.reconciled_by_id is None
            db.session.rollback()

    def test_moving_a_purchases_posting_day_EARLIER_also_releases_it(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Any MOVE releases it, not only emptying it -- and that is measured.

        A first version of this rule released the link only when the day was
        cleared, on the reasoning that moving it earlier is a user REFINING what
        the panel stamped as an upper bound.  An adversarial review refuted it
        with money: a link whose day the date rule would not pick cannot be
        folded while an assertion resets the ledger, and on a production clone
        one such link made the balance render ``$2,246.58`` on a day its owner
        had asserted ``$2,746.58``.

        So the day wins and the observation drops back to UNKNOWN, where the
        date rule answers it exactly as it did before any of this shipped.  The
        user re-ticks on the next statement, which is one click on the screen
        they are already reading.  Refusing the edit instead would trap them
        against the panel's own copy -- *"correct it if your statement shows a
        different day"*.
        """
        with app.app_context():
            parent = seed_entry_template["transaction"]
            opening = _opening_of(parent.account_id)
            entry = entry_service.create_entry(
                parent.id,
                seed_user["user"].id,
                entry_service.EntryDetails(
                    figure=typed(Decimal("12.50")),
                    description="Kroger",
                    purchased_on=opening.observed_on,
                ),
            )
            record_settle_day(entry, an_entered_day(opening.observed_on))
            entry.reconciled_by_id = opening.id
            db.session.flush()

            entry_service.update_entry(
                entry.id, seed_user["user"].id,
                settle_day=an_entered_day(opening.observed_on + timedelta(days=1)),
            )

            assert entry.settled_on == opening.observed_on + timedelta(days=1)
            assert entry.reconciled_by_id is None
            db.session.rollback()

    def test_an_update_that_leaves_the_day_alone_keeps_the_link(
        self, app, db, seed_user, seed_entry_template,
    ):
        """The control: editing the AMOUNT is not a retraction.

        Without this the suite could not tell "release on a day move" from
        "release on any update at all", and the second would quietly drop every
        observation a user ever recorded the first time they fixed a typo.
        """
        with app.app_context():
            parent = seed_entry_template["transaction"]
            opening = _opening_of(parent.account_id)
            entry = entry_service.create_entry(
                parent.id,
                seed_user["user"].id,
                entry_service.EntryDetails(
                    figure=typed(Decimal("12.50")),
                    description="Kroger",
                    purchased_on=opening.observed_on,
                ),
            )
            record_settle_day(entry, an_entered_day(opening.observed_on))
            entry.reconciled_by_id = opening.id
            db.session.flush()

            entry_service.update_entry(
                entry.id, seed_user["user"].id, figure=typed(Decimal("13.75")),
            )

            assert entry.amount == Decimal("13.75")
            assert entry.reconciled_by_id == opening.id
            db.session.rollback()

    def test_correcting_a_transactions_settle_day_releases_its_link(
        self, app, db, seed_full_user_data,
    ):
        """``status_seam`` releases it on a day CORRECTION, not just a revert.

        The transaction twin, and the door is the grid's correction box: a
        settled row re-submitted with a different day runs the seam's explicit
        arm, which never touched the link until the review above.  The state it
        would otherwise leave -- a row named by a statement that closed on
        another day -- is the one the fold cannot render.
        """
        with app.app_context():
            opening = _opening_of(seed_full_user_data["account"].id)
            txn = _make_transaction(
                seed_full_user_data,
                status_id=ref_cache.status_id(StatusEnum.DONE),
                **_cleared_by(opening),
            )
            db.session.add(txn)
            db.session.flush()

            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.DONE),
                settle_day=an_entered_day(opening.observed_on + timedelta(days=2)),
                settlement=settlement_if_settling(txn, ref_cache.status_id(StatusEnum.DONE)),
            )
            db.session.flush()

            assert txn.settled_on == opening.observed_on + timedelta(days=2)
            assert txn.reconciled_by_id is None
            db.session.rollback()


class TestAnAssertionAlineNamesCannotBeDeleted:
    """``ON DELETE RESTRICT`` refuses rather than un-clearing the line."""

    def test_deleting_a_named_assertion_is_refused(
        self, app, db, seed_full_user_data,
    ):
        """Removing a statement a line still names is refused at the database.

        ``SET NULL`` is the wrong answer for a recorded observation: it would
        convert "this statement showed the money" into "no statement ever did"
        with nothing reporting it, and every figure resting on the difference
        would move silently.  There is no door in ``app/`` that deletes a single
        assertion today -- history goes only with its account -- so the refusal
        costs nothing and is what makes writing such a door a loud failure
        rather than a quiet balance change.
        """
        with app.app_context():
            opening = _opening_of(seed_full_user_data["account"].id)
            txn = _make_transaction(
                seed_full_user_data, **_cleared_by(opening),
            )
            db.session.add(txn)
            db.session.flush()

            # **Two controls now stand here, and this case grades the
            # INNER one** (plan step X-f3c-2c).  ``budget.account_anchor_history``
            # is append-only at the database tier, so a DELETE aimed at one
            # assertion is refused before the foreign key is ever consulted --
            # which would leave ``ON DELETE RESTRICT`` passing and guarding
            # nothing.  Lifting the outer guard for this statement is what
            # keeps the FK measured; the outer guard has its own cases in
            # ``tests/test_models/test_append_only.py``.
            with append_only_guard_lifted(
                db.session, "budget.account_anchor_history",
            ):
                # Raw rather than ``db.session.delete``: the object-layer
                # listener refuses an ORM delete too, and it is not what this
                # case is about.  Lifting only the database trigger and then
                # asking the ORM would grade the listener, not the FK.
                with pytest.raises(
                    sqlalchemy.exc.IntegrityError,
                    match="fk_transactions_reconciled_by",
                ):
                    db.session.execute(sa.text(
                        "DELETE FROM budget.account_anchor_history "
                        "WHERE id = :i"
                    ), {"i": opening.id})
            db.session.rollback()


class TestTheEntryAccountBackfill:
    """The migration's only non-DDL step, driven through its own SQL."""

    def test_it_gives_every_purchase_its_parents_account(
        self, app, db, seed_full_user_data,
    ):
        """The backfill resolves the parent's account for every row.

        Driven through ``_MIGRATION.BACKFILL_ENTRY_ACCOUNT_SQL`` -- the literal
        string ``upgrade()`` executes -- so this test and the production
        migration cannot come to disagree about the derivation.  The column is
        blanked first because the live schema is already backfilled: asserting
        against rows that were never NULL would grade nothing.
        """
        with app.app_context():
            parent = _make_transaction(seed_full_user_data)
            db.session.add(parent)
            db.session.flush()
            entry = _make_entry(seed_full_user_data, parent)
            db.session.add(entry)
            db.session.flush()

            # **Drain the deferred constraint triggers before the DDL** (plan
            # step X-f3c-2b).  The rows staged above queue an event for
            # ``ck_movement_after_books_open``, and PostgreSQL refuses
            # ``ALTER TABLE`` on a table carrying pending trigger events.
            db.session.execute(sa.text("SET CONSTRAINTS ALL IMMEDIATE"))
            # NOT NULL is dropped for the length of the probe and restored in
            # the same transaction, which the rollback below also guarantees.
            db.session.execute(sa.text(
                "ALTER TABLE budget.transaction_entries "
                "ALTER COLUMN account_id DROP NOT NULL"
            ))
            db.session.execute(sa.text(
                "UPDATE budget.transaction_entries SET account_id = NULL"
            ))
            blanked = db.session.execute(sa.text(
                "SELECT count(*) FROM budget.transaction_entries "
                "WHERE account_id IS NULL"
            )).scalar()
            assert blanked > 0, (
                "The probe blanked no row, so the backfill below would have "
                "nothing to resolve and this test would grade nothing."
            )

            db.session.execute(sa.text(
                _MIGRATION.BACKFILL_ENTRY_ACCOUNT_SQL
            ))

            unresolved = db.session.execute(sa.text(
                "SELECT count(*) FROM budget.transaction_entries "
                "WHERE account_id IS NULL"
            )).scalar()
            assert unresolved == 0, (
                f"{unresolved} purchase(s) resolved no parent account, which "
                f"the NOT NULL transaction_id foreign key makes impossible."
            )
            db.session.refresh(entry)
            assert entry.account_id == parent.account_id
            db.session.rollback()
