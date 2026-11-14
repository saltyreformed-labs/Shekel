"""What an accepted match records: a balanced one-line-one-row match, a group that must sum, no
clearing link, releasing one, and a purchase as a movement of its own.
"""

from datetime import timedelta
from decimal import Decimal

import pytest

from app.enums import StatusEnum
from app.exceptions import ValidationError
from app.models.transaction import Transaction
from app.models.statement_match import StatementMatch, StatementMatchMember
from app.services import statement_match

from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_purchase,
    a_transaction,
    an_import,
)

from ._helpers import _submit


class TestABalancedMatchIsRecorded:
    """The ordinary one-line-one-row case."""

    def test_the_row_takes_the_banks_day(self, app, db, seed_user):
        """The correction this whole arc exists to make.

        Measured on the developer's own statement: of 58 lines an exact-amount
        predicate pairs uniquely with a row, only 23 carry the day the app had
        recorded.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(seed_user, statement, posted_on=bank_day)
        txn = a_transaction(
            seed_user, amount="180.00",
            status=StatusEnum.DONE, settled_on=bank_day + timedelta(days=3),
        )

        accepted = _submit(seed_user, lines=[line], transactions=[txn])

        assert txn.settled_on == bank_day
        assert accepted.corrected_count == 1
        assert accepted.settled_count == 0
        assert accepted.posts_on == bank_day

    def test_a_projected_row_is_settled(self, app, db, seed_user):
        """The bank is evidence the money moved, so the row says so.

        11 rows inside the developer's own statement span had never been
        marked as having happened.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(seed_user, statement, posted_on=bank_day)
        txn = a_transaction(seed_user, amount="180.00")

        accepted = _submit(seed_user, lines=[line], transactions=[txn])

        assert txn.settled_on == bank_day
        assert txn.status.is_settled
        assert accepted.settled_count == 1
        assert accepted.corrected_count == 0

    def test_a_row_already_on_the_banks_day_counts_as_neither(
        self, app, db, seed_user,
    ):
        """Confirming is not correcting, and the report must not claim it is."""
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(seed_user, statement, posted_on=bank_day)
        txn = a_transaction(
            seed_user, amount="180.00",
            status=StatusEnum.DONE, settled_on=bank_day,
        )

        accepted = _submit(seed_user, lines=[line], transactions=[txn])

        assert (accepted.settled_count, accepted.corrected_count) == (0, 0)

    def test_the_match_is_recorded_with_one_member_per_subject(
        self, app, db, seed_user,
    ):
        """The relation is what makes a re-import stop re-proposing it.

        **The app-side member names the row's PAYMENT, not the row** (plan
        step ``credit_card:CC-5-4a-1``, ruling **R-CC43**, developer
        2026-09-21: *the MOVEMENT is the subject of every settled match on
        every screen; a row is a candidate only while Projected*).  The
        owner ticked a Projected bill; the accept settled it through its own
        door, which wrote its covering movement, and that movement -- the
        money the bank showed -- is what the act records.  Through
        ``CC-5-3`` this asserted ``transaction_id == txn.id``.
        Developer confirmation 2026-09-21 (rule 5): "Confirm A, B and C as rule-5 re-expressions
        under R-CC43 -- the member names the payment; a day moves through the seam that mirrors it;
        a settled row is ticked as its payment."
        Since ``CC-5-4a-2`` the member table has no row column, so "no member
        names the row" is the schema's; the assertion that said so went with it.
        Developer confirmation 2026-09-22 (rule 5): "Confirm all four groups -- all four are
        rule-5 re-expressions under R-CC45."
        """
        statement = an_import(seed_user)
        line = a_bank_line(seed_user, statement)
        txn = a_transaction(seed_user, amount="180.00")

        accepted = _submit(seed_user, lines=[line], transactions=[txn])

        members = (
            db.session.query(StatementMatchMember)
            .filter(StatementMatchMember.match_id == accepted.match_id)
            .all()
        )
        assert len(members) == 2
        assert {m.bank_statement_line_id for m in members} == {line.id, None}
        (movement,) = txn.covering_movements
        assert {m.transaction_entry_id for m in members} == {movement.id, None}
        assert movement.settled_on == line.posted_on

    def test_a_reviewed_act_is_recorded_as_a_TICK_and_not_as_a_rule(
        self, app, db, seed_user,
    ):
        """Ruling **R-GT**, at the door rather than at the column.

        This is the reviewed-pass door: a row here exists because a person read
        a proposal and pressed Apply, which is the whole of ruling **R-FP**'s
        surviving half.  ``applied_by_rule`` is NOT NULL with no default, so
        the fact is stated at the call site or nothing is written at all -- and
        what it must state HERE is ``False``.  Plan step ``bank_import:X-ge``
        builds the door that states ``True``; measured on the developer's dev
        database 2026-08-26, all 221 recorded acts are ticks.
        """
        statement = an_import(seed_user)
        line = a_bank_line(seed_user, statement)
        txn = a_transaction(seed_user, amount="180.00")

        accepted = _submit(seed_user, lines=[line], transactions=[txn])

        assert db.session.get(
            StatementMatch, accepted.match_id,
        ).applied_by_rule is False


class TestTheGroupMustSum:
    """The developer's ruling of 2026-08-17, and finding **N-239**'s own data.

    **Amended by plan step ``bank_import:X-f6d-4``**: the sides must still
    meet, but a difference the owner ACCEPTS is closed by a member this door
    mints rather than by sending them away (ruling **R-FN**).  What is graded
    here is the refusal that stands when nobody accepts one;
    :mod:`tests.test_services.test_statement_match.test_residual` grades the
    other half.
    """

    def test_a_five_cent_shortfall_is_refused(self, app, db, seed_user):
        """The payroll shape: the bank paid more than the app's rows say.

        7 payroll deposits on a production clone of the developer's own data
        sit `$0.04`-`$0.06` apart from what the app holds.  A tolerance would
        absorb exactly the defect the matcher is the first instrument able to
        see -- and this refusal is what stands when the owner has NOT agreed
        to record the difference.
        """
        statement = an_import(seed_user)
        line = a_bank_line(seed_user, statement, amount="2573.43")
        salary = a_transaction(
            seed_user, name="Salary", amount="2473.38", income=True,
        )
        allowance = a_transaction(
            seed_user, name="Allowance", amount="100.00", income=True,
        )

        with pytest.raises(ValidationError) as caught:
            _submit(
                seed_user, lines=[line], transactions=[salary, allowance],
            )

        assert "0.05" in str(caught.value)
        # It says what to do about it, which is the half X-f6d-4 added: before
        # that step the only advice was to go and edit a row.  *It said "tick
        # the box" until plan step bank_import:X-gp*: a group's consent is one
        # control whose OPTIONS are the acts now, and a refusal is the
        # service's own words about the control it offers (R-FZ(a)), so the
        # sentence names the choice and both acts.
        assert "choose what to do with the difference" in str(caught.value)
        assert "row with no category" in str(caught.value)
        assert salary.settled_on is None
        assert allowance.settled_on is None

    def test_a_group_that_sums_is_accepted(self, app, db, seed_user):
        """The control: the same shape, correct to the cent."""
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, statement, amount="2573.38", posted_on=bank_day,
        )
        salary = a_transaction(
            seed_user, name="Salary", amount="2473.38", income=True,
        )
        allowance = a_transaction(
            seed_user, name="Allowance", amount="100.00", income=True,
        )

        accepted = _submit(
            seed_user, lines=[line], transactions=[salary, allowance],
        )

        assert accepted.settled_count == 2
        assert salary.settled_on == bank_day
        assert allowance.settled_on == bank_day

    def test_every_member_takes_the_LATEST_bank_day(self, app, db, seed_user):
        """N lines, one row: the row is not wholly moved until the last posts.

        Taking the earliest would let a balance asserted between the two absorb
        money that had not all left the account.
        """
        statement = an_import(seed_user)
        first = seed_user["bootstrap_period"].start_date
        last = first + timedelta(days=2)
        line_a = a_bank_line(
            seed_user, statement, amount="-100.00", posted_on=first,
        )
        line_b = a_bank_line(
            seed_user, statement, amount="-80.00", posted_on=last,
        )
        txn = a_transaction(seed_user, amount="180.00")

        accepted = _submit(
            seed_user, lines=[line_a, line_b], transactions=[txn],
        )

        assert accepted.posts_on == last
        assert txn.settled_on == last


class TestTheClearingLinkIsNotWritten:
    """Ruling **R-FV**: identity is stored, absorption is derived."""

    def test_no_member_gains_a_clearing_link(self, app, db, seed_user):
        """A bank line names no ``account_anchor_history`` row, so nor does this."""
        statement = an_import(seed_user)
        line = a_bank_line(seed_user, statement)
        txn = a_transaction(seed_user, amount="180.00")

        _submit(seed_user, lines=[line], transactions=[txn])

        assert txn.reconciled_by_id is None

    def test_moving_a_day_RELEASES_an_existing_link(self, app, db, seed_user):
        """The bank has just contradicted the day that link was recorded on.

        The release is the settle doors' own rule (``status_seam``); this pins
        that the accept door goes THROUGH them rather than writing the column,
        which is what makes the two facts unable to disagree.
        """
        from app.models.account import AccountAnchorHistory  # local: this test

        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        anchor = (
            db.session.query(AccountAnchorHistory)
            .filter_by(account_id=seed_user["account"].id)
            .first()
        )
        assert anchor is not None, "create_account writes the opening assertion"
        txn = a_transaction(
            seed_user, amount="180.00",
            status=StatusEnum.DONE, settled_on=bank_day + timedelta(days=2),
        )
        txn.reconciled_by_id = anchor.id
        db.session.flush()
        line = a_bank_line(seed_user, statement, posted_on=bank_day)

        _submit(seed_user, lines=[line], transactions=[txn])

        assert txn.settled_on == bank_day
        assert txn.reconciled_by_id is None


class TestReleasingAMatch:
    """The repair door finding **N-302** says a refusal owes."""

    def test_it_deletes_the_act_and_its_members(self, app, db, seed_user):
        """The bank lines become unexplained again.

        **The receipt is a value rather than a count since plan step
        ``bank_import:X-f6f``** (ruling **R-GG**), because the door can now
        destroy rows and a receipt that said only "2" could not tell an undo
        that moved nothing from one that removed a `$213.49` purchase.  This
        act created nothing, so all three removal facts are zero -- which is
        the control for the ones below that are not.
        """
        statement = an_import(seed_user)
        line = a_bank_line(seed_user, statement)
        txn = a_transaction(seed_user, amount="180.00")
        accepted = _submit(seed_user, lines=[line], transactions=[txn])

        released = statement_match.release_match(
            accepted.match_id, seed_user["user"].id, seed_user["account"].id,
        )

        assert released.released_count == 2
        assert released.removed_rows == 0
        assert released.removed_cash == Decimal("0.00")
        assert released.kept_containers == 0
        assert db.session.query(StatementMatch).count() == 0
        assert db.session.query(StatementMatchMember).count() == 0
        # A match between rows that already existed leaves the row standing:
        # the release removes what the act CREATED, never what it named.
        assert db.session.get(Transaction, txn.id) is not None

    def test_it_does_NOT_put_the_day_back(self, app, db, seed_user):
        """The bank is still the best record of when that money moved.

        Reverting the correction to tidy a relation would throw away the fact
        and keep the bookkeeping.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(seed_user, statement, posted_on=bank_day)
        txn = a_transaction(
            seed_user, amount="180.00",
            status=StatusEnum.DONE, settled_on=bank_day + timedelta(days=4),
        )
        accepted = _submit(seed_user, lines=[line], transactions=[txn])

        statement_match.release_match(
            accepted.match_id, seed_user["user"].id, seed_user["account"].id,
        )

        assert txn.settled_on == bank_day

    def test_the_row_is_matchable_again(self, app, db, seed_user):
        """What a release restores is the QUESTION."""
        statement = an_import(seed_user)
        line = a_bank_line(seed_user, statement)
        txn = a_transaction(seed_user, amount="180.00")
        accepted = _submit(seed_user, lines=[line], transactions=[txn])
        statement_match.release_match(
            accepted.match_id, seed_user["user"].id, seed_user["account"].id,
        )

        again = _submit(seed_user, lines=[line], transactions=[txn])

        assert again.match_id != accepted.match_id

    def test_another_owners_match_is_refused(self, app, db, seed_user):
        """404-for-both, as a refusal because this door names one act."""
        with pytest.raises(ValidationError, match="no longer there"):
            statement_match.release_match(
                999999, seed_user["user"].id, seed_user["account"].id,
            )


class TestAPurchaseIsMatchableToo:
    """A purchase is a cash movement of its own (plan step ``balance:X-f3b``)."""

    def test_a_purchase_takes_the_banks_posting_day(self, app, db, seed_user):
        """The 267 card-swipe lines on the developer's statement are these."""
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        envelope = a_transaction(
            seed_user, name="Groceries", amount="100.00", is_envelope=True,
        )
        purchase = a_purchase(
            seed_user, envelope, amount="25.00", purchased_on=bank_day,
        )
        line = a_bank_line(
            seed_user, statement, amount="-25.00", posted_on=bank_day,
        )

        accepted = _submit(seed_user, lines=[line], entries=[purchase])

        assert purchase.settled_on == bank_day
        assert accepted.settled_count == 1
