"""What a recorded deposit is: the row the door writes, the money it moves, and the match that
explains its line until an undo removes it.
"""

from datetime import timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import AmountSourceEnum, LedgerAccountClassEnum, SettledDayBasisEnum, TxnTypeEnum

from app.models.journal_entry import JournalEntry, Posting
from app.models.ledger_account import LedgerAccount
from app.models.statement_match import StatementMatchCreation, StatementMatchMember
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.services import statement_match
from app.services.statement_match import review_set

from tests.test_services.test_statement_match._builders import a_later_period, a_scope
from tests._test_helpers import family_journal_filter, last_covered_day, resolved_amount

from ._helpers import _a_deposit, _record


class TestTheRowItWrites:
    """What a recorded deposit IS, clause by clause."""

    def test_it_is_an_INCOME_row_at_the_banks_own_figure(
        self, app, db, seed_user,
    ):
        """The direction comes from the sign and the figure from the line."""
        line = _a_deposit(seed_user, amount="0.15")

        recorded = _record(seed_user, line)

        row = db.session.get(Transaction, recorded.transaction_id)
        assert row.transaction_type_id == ref_cache.txn_type_id(
            TxnTypeEnum.INCOME,
        )
        # The MAGNITUDE is what the row is worth and the direction is the
        # type.  Read through the one resolver: the row is priced by the
        # definition the door mints since plan step balance:X-bi-7b
        # (R-BAL21), so the column it used to store the figure in is empty.
        assert resolved_amount(row) == Decimal("0.15")
        assert row.template.default_amount == Decimal("0.15")

    def test_it_carries_NO_category(self, app, db, seed_user):
        """R-FN's clause: the app does not know what this money was."""
        line = _a_deposit(seed_user)

        recorded = _record(seed_user, line)

        assert db.session.get(
            Transaction, recorded.transaction_id,
        ).category_id is None

    def test_it_settles_on_the_BANKS_day_as_an_OBSERVED_one(
        self, app, db, seed_user,
    ):
        """A statement SHOWED the money, so the basis is not a bound."""
        day = seed_user["bootstrap_period"].start_date + timedelta(days=3)
        line = _a_deposit(seed_user, posted_on=day)

        recorded = _record(seed_user, line)

        row = db.session.get(Transaction, recorded.transaction_id)
        assert row.settled_on == day
        assert row.settled_day_basis_id == ref_cache.settled_day_basis_id(
            SettledDayBasisEnum.OBSERVED,
        )
        assert row.status.is_settled

    def test_it_is_a_ONE_OFF_priced_by_its_rule_less_definition(
        self, app, db, seed_user,
    ):
        """It names a definition with no rule and reads its price from it.

        **It asserted "OWNS its amount, names no template" until plan step
        balance:X-bi-7b** (rulings R-BAL20 / R-BAL24): every plan item has
        exactly one definition, so the row a bank line's money requires is
        born through the one-off producer -- a rule-less definition carrying
        the name, type and figure (one version dated on the row's due date)
        plus this placed row, priced ``derived(TEMPLATE)``.  A link-less row
        owning its figure was a third meaning of ``template_id IS NULL``.
        It still names no transfer and no card spend.
        """
        line = _a_deposit(seed_user)

        recorded = _record(seed_user, line)

        row = db.session.get(Transaction, recorded.transaction_id)
        assert row.template_id is not None
        assert row.recurs is False
        assert row.template.recurrence_rule is None
        assert row.amount_source_id == ref_cache.amount_source_id(
            AmountSourceEnum.TEMPLATE,
        )
        assert row.estimated_amount is None
        assert row.transfer_id is None
        assert row.credit_payback_for_id is None
        # The definition carries what the row used to: no category (R-FN),
        # the deposit's type, the merchant's name.
        assert row.template.category_id is None
        assert row.template.transaction_type_id == row.transaction_type_id
        assert row.template.name == row.name
        # Dated at its paycheck's start (R-BAL22) and answering that day (R-BAL25).
        assert row.due_date is not None
        assert row.occurs_on == row.due_date

    def test_it_is_placed_by_the_POSTING_day(self, app, db, seed_user):
        """The residual's rule, not the purchase's: this row IS the movement.

        Staged so the two clocks DISAGREE across a period boundary, which is
        the only shape where the choice is observable: the bank states a
        transaction day inside the bootstrap period and posts it inside the
        next one.  Reading ``transaction_on`` would file this deposit against
        the earlier paycheck.
        """
        later = a_later_period(seed_user)
        line = _a_deposit(
            seed_user,
            transaction_on=last_covered_day(seed_user["bootstrap_period"]),
            posted_on=later.start_date,
        )

        recorded = _record(seed_user, line)

        assert recorded.pay_period_id == later.id
        assert db.session.get(
            Transaction, recorded.transaction_id,
        ).pay_period_id == later.id

    def test_it_is_NAMED_for_the_merchant_not_the_whole_line(
        self, app, db, seed_user,
    ):
        """A row nobody can read is the one thing this name must not be."""
        line = _a_deposit(seed_user, merchant="Dividend Earned")

        recorded = _record(seed_user, line)

        assert db.session.get(
            Transaction, recorded.transaction_id,
        ).name == "Dividend Earned"

    def test_it_falls_back_to_the_DESCRIPTION_where_no_merchant_is_named(
        self, app, db, seed_user,
    ):
        """``transactions.name`` is NOT NULL and this door writes it directly.

        The majority case on a real statement: 179 of the developer's 361
        lines state no merchant at all.
        """
        line = _a_deposit(seed_user, merchant=None)

        recorded = _record(seed_user, line)

        assert db.session.get(
            Transaction, recorded.transaction_id,
        ).name == "DIVIDEND EARNED (Dividend Earned)"


class TestItMovesTheMoney:
    """The whole point: the books stop being under the bank."""

    def test_the_entry_it_posts_BALANCES(self, app, db, seed_user):
        """Both legs, so it moves CASH as well as filling a bucket.

        The counter leg alone would satisfy the bucket case below while
        booking nothing against the account the statement is about -- which is
        the failure that would leave the books exactly as under-stated as
        before, with a row on the grid saying otherwise.
        """
        line = _a_deposit(seed_user, amount="0.15")

        recorded = _record(seed_user, line)

        # **Named by ACCOUNT, never grouped by ``is_owner_bucket``.**  This owner
        # holds more than one non-fallback ledger account -- the checking ASSET
        # and a ``Checking -- Opening`` EQUITY row among them -- so a grouping
        # by that flag reads the same whether the debit lands on the cash
        # account or on opening equity, and only one of those is this door
        # booking a deposit.  Measured on the developer's own dev database
        # 2026-08-27, where a hand true-up posts a second entry doing exactly
        # that reversal.
        # The row's FAMILY (plan step X-bi-3b): a settled paycheck's money is
        # posted under its covering movement, as a bill's is since X-bi-3a,
        # so the read widens to the family and the figures stand.
        legs = dict(
            db.session.query(
                LedgerAccount.id, db.func.sum(Posting.amount),
            )
            .join(Posting, Posting.ledger_account_id == LedgerAccount.id)
            .join(JournalEntry, JournalEntry.id == Posting.journal_entry_id)
            .filter(family_journal_filter(recorded.transaction_id))
            .group_by(LedgerAccount.id)
            .all()
        )
        cash = (
            db.session.query(LedgerAccount.id)
            .filter(
                LedgerAccount.account_id == seed_user["account"].id,
                LedgerAccount.class_id == ref_cache.ledger_account_class_id(
                    LedgerAccountClassEnum.ASSET,
                ),
            )
            .scalar()
        )
        bucket = (
            db.session.query(LedgerAccount.id)
            .filter(
                LedgerAccount.user_id == seed_user["user"].id,
                LedgerAccount.is_owner_bucket.is_(True),
                LedgerAccount.class_id == ref_cache.ledger_account_class_id(
                    LedgerAccountClassEnum.INCOME,
                ),
            )
            .scalar()
        )
        # Debit-positive: the CHECKING ASSET gains 0.15 and Uncategorized
        # Income is credited the same.  Two legs and no others.
        assert legs == {cash: Decimal("0.15"), bucket: Decimal("-0.15")}

    def test_it_books_to_the_per_owner_UNCATEGORIZED_INCOME_account(
        self, app, db, seed_user,
    ):
        """The routing a NULL category produces, which is why it is NULL.

        Asserted over the LEDGER rather than the row, because the row carrying
        no category is only half the claim: what makes the mechanism real is
        that ``posting_service`` sends such a row's counter leg to the
        per-(owner, class) fallback -- and INCOME is the class this door is the
        first writer of.
        """
        line = _a_deposit(seed_user, amount="0.15")

        _record(seed_user, line)

        account = (
            db.session.query(LedgerAccount)
            .filter(
                LedgerAccount.user_id == seed_user["user"].id,
                LedgerAccount.is_owner_bucket.is_(True),
                LedgerAccount.class_id == ref_cache.ledger_account_class_id(
                    LedgerAccountClassEnum.INCOME,
                ),
            )
            .one()
        )
        net = (
            db.session.query(db.func.sum(Posting.amount))
            .filter(Posting.ledger_account_id == account.id)
            .scalar()
        )
        assert net == Decimal("-0.15")


class TestTheLineStopsBeingUnexplained:
    """A recorded deposit is MATCHED to its own row, like every other act."""

    def test_it_records_a_match_naming_both(self, app, db, seed_user):
        """Without this the line would be re-offered on the next render.

        The app-side member names the recorded row's PAYMENT -- its covering
        movement, the money the bank showed -- and not the row (plan step
        ``credit_card:CC-5-4a-1``, ruling **R-CC43**: *the MOVEMENT is the
        subject of every settled match*); through ``CC-5-3`` this asserted
        ``transaction_id == recorded.transaction_id``.  The row is still what
        the act CREATED, which the undo reads from the other relation.
        Developer confirmation 2026-09-21 (rule 5): "Confirm A, B and C as rule-5 re-expressions
        under R-CC43 -- the member names the payment; a day moves through the seam that mirrors it;
        a settled row is ticked as its payment."
        Since ``CC-5-4a-2`` the member table has no row column, so "no member
        names the row" is the schema's; the assertion that said so went with it.
        Developer confirmation 2026-09-22 (rule 5): "Confirm all four groups -- all four are
        rule-5 re-expressions under R-CC45."
        """
        line = _a_deposit(seed_user)

        recorded = _record(seed_user, line)

        members = (
            db.session.query(StatementMatchMember)
            .filter(StatementMatchMember.match_id == recorded.match_id)
            .all()
        )
        assert {m.bank_statement_line_id for m in members} == {line.id, None}
        (movement,) = db.session.get(
            Transaction, recorded.transaction_id,
        ).covering_movements
        assert {m.transaction_entry_id for m in members} == {None, movement.id}

    def test_the_awaiting_count_FALLS(self, app, db, seed_user):
        """The figure the grid badge renders, measured on both sides.

        This is the whole reason the step exists: before it, no act on the
        review screen could move this number for an inflow.
        """
        line = _a_deposit(seed_user)
        opens = a_scope(seed_user).calendar.opening_bound()
        before = statement_match.awaiting_review_count(
            seed_user["user"].id, seed_user["account"].id, opens,
        )

        _record(seed_user, line)

        after = statement_match.awaiting_review_count(
            seed_user["user"].id, seed_user["account"].id, opens,
        )
        assert (before, after) == (1, 0)

    def test_it_leaves_the_recordable_list(self, app, db, seed_user):
        """The screen's own re-derivation agrees with the count."""
        line = _a_deposit(seed_user)
        assert [
            item.line.line_id
            for item in review_set(a_scope(seed_user)).recordable_inflows
        ] == [line.id]

        _record(seed_user, line)

        assert review_set(a_scope(seed_user)).recordable_inflows == ()

    def test_the_row_is_recorded_as_this_ACTS_creation(
        self, app, db, seed_user,
    ):
        """R-GG: an undo takes back what the act MADE, not what it names."""
        line = _a_deposit(seed_user)

        recorded = _record(seed_user, line)

        created = (
            db.session.query(StatementMatchCreation)
            .filter(StatementMatchCreation.match_id == recorded.match_id)
            .all()
        )
        assert [c.transaction_id for c in created] == [
            recorded.transaction_id,
        ]

    def test_UNDOING_the_match_removes_the_row_again(
        self, app, db, seed_user,
    ):
        """The inverse, end to end: the books go back to where they were.

        **The DEFINITION goes with the row** (plan step balance:X-bi-7b,
        R-BAL27): the door mints a rule-less definition per deposit, and an
        undo that left one behind with no row would leave a definition that
        defines nothing, once per undo, listed nowhere.
        """
        line = _a_deposit(seed_user)
        recorded = _record(seed_user, line)
        db.session.commit()
        definition_id = db.session.get(
            Transaction, recorded.transaction_id,
        ).template_id
        assert definition_id is not None

        statement_match.release_match(
            recorded.match_id, seed_user["user"].id, seed_user["account"].id,
        )
        db.session.commit()

        assert db.session.get(Transaction, recorded.transaction_id) is None
        assert db.session.get(TransactionTemplate, definition_id) is None
        assert [
            item.line.line_id
            for item in review_set(a_scope(seed_user)).recordable_inflows
        ] == [line.id]
