"""A group's difference becomes a row the owner accepts: it is minted with no category, its two
sides are rounded before they are compared, it books to Uncategorized, and it is a member of the
match.
"""

from datetime import timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import (
    AmountSourceEnum,
    LedgerAccountClassEnum,
    SettledDayBasisEnum,
    StatusEnum,
    TxnTypeEnum,
)
from app.exceptions import ValidationError
from app.models.journal_entry import JournalEntry, Posting
from app.models.ledger_account import LedgerAccount
from app.models.statement_match import StatementMatchMember
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.services import statement_match
from app.services.row_valuation import settled_figure
from app.services.statement_match._sides import MatchSides

from tests.test_services.test_statement_match._builders import (
    accepted_acts,
    a_bank_line,
    a_later_period,
    a_scope,
    a_transaction,
    an_import,
)
from tests._test_helpers import family_journal_filter, resolved_amount

from ._helpers import _submit, _minted, _uncategorized_net, _ALine, _ARow, _payroll


class TestTheDifferenceBecomesARowTheOwnerAccepts:
    """Ruling **R-FN**, on the payroll shape that motivated the step."""

    def test_the_group_is_recorded_and_a_row_is_minted(
        self, app, db, seed_user,
    ):
        """The whole act: the deposit records and the five cents exists."""
        line, salary, allowance = _payroll(seed_user)

        accepted = _submit(
            seed_user, lines=[line], transactions=[salary, allowance],
            residual="0.05",
        )

        assert accepted.residual == Decimal("0.05")
        assert salary.settled_on == line.posted_on
        assert allowance.settled_on == line.posted_on
        rows = _minted(seed_user)
        assert len(rows) == 1
        assert resolved_amount(rows[0]) == Decimal("0.05")

    def test_the_minted_row_is_income_settled_on_the_banks_day(
        self, app, db, seed_user,
    ):
        """Every column the row is born with, asserted rather than assumed."""
        line, salary, allowance = _payroll(seed_user)

        _submit(
            seed_user, lines=[line], transactions=[salary, allowance],
            residual="0.05",
        )

        row = _minted(seed_user)[0]
        assert row.category_id is None
        assert row.transaction_type_id == ref_cache.txn_type_id(
            TxnTypeEnum.INCOME,
        )
        assert row.status_id == ref_cache.status_id(StatusEnum.RECEIVED)
        assert row.settled_on == line.posted_on
        assert row.settled_day_basis_id == ref_cache.settled_day_basis_id(
            SettledDayBasisEnum.OBSERVED,
        )
        assert settled_figure(row) == Decimal("0.05")
        assert row.tracks_purchases is False
        # It is a ONE-OFF since plan step balance:X-bi-7b (R-BAL20 / R-BAL24):
        # a rule-less DEFINITION carrying the name, type and figure plus this
        # placed row, priced by it -- where it used to OWN its figure as a
        # link-less row.  Still no transfer and no card spend.
        assert row.template_id is not None
        assert row.recurs is False
        assert row.amount_source_id == ref_cache.amount_source_id(
            AmountSourceEnum.TEMPLATE,
        )
        assert row.template.category_id is None
        assert row.transfer_id is None
        assert row.credit_payback_for_id is None
        assert row.account_id == seed_user["account"].id

    def test_a_shortfall_the_bank_TOOK_is_an_expense(
        self, app, db, seed_user,
    ):
        """The other direction, which no payroll case can reach.

        The sign of the difference decides the transaction TYPE, and a test
        over income alone would pass an implementation that hardcoded it.
        """
        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="-180.06",
            posted_on=seed_user["bootstrap_period"].start_date,
        )
        one = a_transaction(seed_user, name="Power", amount="100.00")
        two = a_transaction(seed_user, name="Water", amount="80.00")

        accepted = _submit(
            seed_user, lines=[line], transactions=[one, two],
            residual="-0.06",
        )

        assert accepted.residual == Decimal("-0.06")
        row = _minted(seed_user)[0]
        assert row.transaction_type_id == ref_cache.txn_type_id(
            TxnTypeEnum.EXPENSE,
        )
        assert resolved_amount(row) == Decimal("0.06")
        assert row.status_id == ref_cache.status_id(StatusEnum.DONE)
        # The undo dialog names the money the release would take out of the
        # books -- the whole family's (plan step X-bi-3a): the minted row's
        # own leg is zero and its covering movement carries the `$0.06`, and
        # a dialog reading the row alone said the release moved no money
        # (adversarial review, 2026-09-16).
        group = accepted_acts(seed_user)[0]
        assert group.removes.moves_money is True
        assert group.removes.cash_amount == Decimal("-0.06")

    def test_it_is_named_for_the_banks_own_merchant(
        self, app, db, seed_user,
    ):
        """Developer's decision 2026-08-23: what it is, then who paid it."""
        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="2573.43",
            posted_on=seed_user["bootstrap_period"].start_date,
            description="ACH DEPOSIT TOWN OF CLAYTON PAYROLL 0261130059978",
            merchant="TOWN OF CLAYTON PAYROLL",
        )
        salary = a_transaction(
            seed_user, name="Salary", amount="2473.38", income=True,
        )
        allowance = a_transaction(
            seed_user, name="Allowance", amount="100.00", income=True,
        )

        _submit(
            seed_user, lines=[line], transactions=[salary, allowance],
            residual="0.05",
        )

        assert _minted(seed_user)[0].name == (
            "Statement difference: TOWN OF CLAYTON PAYROLL"
        )

    def test_a_MULTI_LINE_group_is_named_for_the_line_it_posts_on(
        self, app, db, seed_user,
    ):
        """One rule for the day and the name, so they describe one movement.

        A match's day is ``max(posted_on)`` over its lines, and the row is
        named for that same line -- so a row dated 06-10 is never captioned
        with a merchant that paid on 06-08.  A mutation sweep found this
        ungraded: every other case here stages ONE line, and with one line
        ``min`` and ``max`` are the same answer.
        """
        statement = an_import(seed_user)
        first = seed_user["bootstrap_period"].start_date
        earlier = a_bank_line(
            seed_user, statement, amount="-100.00", posted_on=first,
            description="POINT OF SALE DEBIT LOWES", merchant="Lowe's",
        )
        later = a_bank_line(
            seed_user, statement, amount="-80.06",
            posted_on=first + timedelta(days=2),
            description="POINT OF SALE DEBIT WAL-MART", merchant="Walmart",
        )
        one = a_transaction(seed_user, name="Power", amount="100.00")
        two = a_transaction(seed_user, name="Water", amount="80.00")

        accepted = _submit(
            seed_user, lines=[earlier, later], transactions=[one, two],
            residual="-0.06",
        )

        row = _minted(seed_user)[0]
        assert accepted.posts_on == later.posted_on
        assert row.settled_on == later.posted_on
        assert row.name == "Statement difference: Walmart"

    def test_it_lands_in_the_period_holding_the_banks_day(
        self, app, db, seed_user,
    ):
        """The money moved on the bank's day, so that paycheck holds it.

        Staged so the members sit in a DIFFERENT period from the line, which
        is what separates "the period the bank's day is in" from "the period
        the other members are in" -- an implementation reading the first
        member's period would pass without it.
        """
        later = a_later_period(seed_user)
        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="2573.43", posted_on=later.start_date,
        )
        salary = a_transaction(
            seed_user, name="Salary", amount="2473.38", income=True,
            settled_on=later.start_date, status=StatusEnum.RECEIVED,
        )
        allowance = a_transaction(
            seed_user, name="Allowance", amount="100.00", income=True,
            settled_on=later.start_date, status=StatusEnum.RECEIVED,
        )

        _submit(
            seed_user, lines=[line], transactions=[salary, allowance],
            residual="0.05",
        )

        row = _minted(seed_user)[0]
        assert row.pay_period_id == later.id
        assert salary.pay_period_id == seed_user["bootstrap_period"].id


class TestTheTwoSidesAreRoundedBeforeTheyAreCompared:
    """``MatchSides``' own rule, graded on a value the database cannot hold.

    Every figure the app can currently produce is a whole number of cents, so
    on real data this rounding is a no-op and the three ways of writing it --
    round each side, round the difference, round neither -- all agree.  What
    picks this one is that BOTH derived values have to be storable: the
    difference becomes an ``estimated_amount`` on a ``Numeric(12, 2)`` column,
    and ``bank_cash_for`` returns the bank side verbatim as the figure a
    one-row match WRITES.  A sweep found the choice ungraded, which is how a
    decision becomes an accident.

    It is graded through the constructor with plain values rather than through
    the door, because that is the only way to hand it a sub-cent figure at all.
    """

    def test_each_side_is_rounded_HALF_UP_away_from_zero(self):
        """Half a cent up on the magnitude, which is ``round_money``'s rule."""
        sides = MatchSides.of(
            [_ALine(Decimal("100.005"))],
            [_ARow(Decimal("-100.005"))],
        )

        assert sides.bank == Decimal("100.01")
        assert sides.app == Decimal("-100.01")
        assert sides.difference == Decimal("200.02")

    def test_the_difference_is_always_a_whole_number_of_cents(self):
        """Which is what makes it storable, and what the browser mirrors."""
        sides = MatchSides.of(
            [_ALine(Decimal("2573.43"))],
            [_ARow(Decimal("2473.384")), _ARow(Decimal("100.00"))],
        )

        assert sides.app == Decimal("2573.38")
        assert sides.difference == Decimal("0.05")
        assert sides.difference.as_tuple().exponent == -2


class TestTheLedgerBooksItToUncategorized:
    """Ruling **R-FN**'s mechanism, graded where it actually happens."""

    def test_an_income_difference_reaches_Uncategorized_Income(
        self, app, db, seed_user,
    ):
        """The fallback account is created and holds the five cents.

        Income is credit-normal, so a `+$0.05` receipt is `-0.05` on the
        debit-positive ledger.
        """
        line, salary, allowance = _payroll(seed_user)
        assert _uncategorized_net(
            seed_user, LedgerAccountClassEnum.INCOME,
        ) is None

        _submit(
            seed_user, lines=[line], transactions=[salary, allowance],
            residual="0.05",
        )

        assert _uncategorized_net(
            seed_user, LedgerAccountClassEnum.INCOME,
        ) == Decimal("-0.05")

    def test_an_expense_difference_reaches_Uncategorized_Expense(
        self, app, db, seed_user,
    ):
        """The other class, which is a SECOND row keyed by class."""
        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="-180.06",
            posted_on=seed_user["bootstrap_period"].start_date,
        )
        one = a_transaction(seed_user, name="Power", amount="100.00")
        two = a_transaction(seed_user, name="Water", amount="80.00")

        _submit(
            seed_user, lines=[line], transactions=[one, two],
            residual="-0.06",
        )

        assert _uncategorized_net(
            seed_user, LedgerAccountClassEnum.EXPENSE,
        ) == Decimal("0.06")

    def test_the_entry_it_posts_BALANCES(self, app, db, seed_user):
        """Both legs, so the row moves cash as well as filling a bucket.

        The counter leg alone would satisfy the two cases above while booking
        nothing against the account the bank statement is about.
        """
        line, salary, allowance = _payroll(seed_user)

        _submit(
            seed_user, lines=[line], transactions=[salary, allowance],
            residual="0.05",
        )

        row = _minted(seed_user)[0]
        # The row's FAMILY (plan step X-bi-3b): the minted paycheck settles
        # covered, its money posted under its movement, so the read widens
        # to the family and the figures stand.
        legs = (
            db.session.query(
                LedgerAccount.is_owner_bucket, db.func.sum(Posting.amount),
            )
            .join(Posting, Posting.ledger_account_id == LedgerAccount.id)
            .join(JournalEntry, JournalEntry.id == Posting.journal_entry_id)
            .filter(family_journal_filter(row))
            .group_by(LedgerAccount.is_owner_bucket)
            .all()
        )
        assert dict(legs) == {True: Decimal("-0.05"), False: Decimal("0.05")}


class TestTheMintedRowIsAMemberOfTheMatch:
    """The identity holds by CONSTRUCTION, which is the step's whole shape."""

    def test_it_is_recorded_as_a_member(self, app, db, seed_user):
        """Three members for two submitted rows.

        The minted row is named through its PAYMENT -- the covering movement
        its settle wrote -- as every row is since plan step
        ``credit_card:CC-5-4a-1`` (ruling **R-CC43**); its creation record
        still names the row.  Through ``CC-5-3`` this looked the member up by
        ``transaction_id``.
        Developer confirmation 2026-09-21 (rule 5): "Confirm A, B and C as rule-5 re-expressions
        under R-CC43 -- the member names the payment; a day moves through the seam that mirrors it;
        a settled row is ticked as its payment."
        Since ``CC-5-4a-2`` the member table has no row column, so "no member
        names the row" is the schema's; the assertion that said so went with it.
        Developer confirmation 2026-09-22 (rule 5): "Confirm all four groups -- all four are
        rule-5 re-expressions under R-CC45."
        """
        line, salary, allowance = _payroll(seed_user)

        accepted = _submit(
            seed_user, lines=[line], transactions=[salary, allowance],
            residual="0.05",
        )

        row = _minted(seed_user)[0]
        (movement,) = row.covering_movements
        member = (
            db.session.query(StatementMatchMember)
            .filter(StatementMatchMember.transaction_entry_id == movement.id)
            .one()
        )
        assert member.match_id == accepted.match_id

    def test_the_accepted_group_AGREES(self, app, db, seed_user):
        """The panel's own re-derivation of the balance this door checked.

        Without the minted row as a member the group would read out of
        agreement forever, on the very act that made it add up.
        """
        line, salary, allowance = _payroll(seed_user)

        _submit(
            seed_user, lines=[line], transactions=[salary, allowance],
            residual="0.05",
        )

        groups = accepted_acts(seed_user)
        assert len(groups) == 1
        assert groups[0].agrees is True
        assert sum(row.cash_amount for row in groups[0].rows) == line.amount

    def test_the_next_pass_does_NOT_re_offer_it(self, app, db, seed_user):
        """A member is claimed, so nothing may match it to something else."""
        line, salary, allowance = _payroll(seed_user)

        _submit(
            seed_user, lines=[line], transactions=[salary, allowance],
            residual="0.05",
        )

        row = _minted(seed_user)[0]
        review = statement_match.review_set(a_scope(seed_user))
        # ``transaction_id``, not ``row_id``: the minted row is SETTLED, and a
        # settled row is offered as its payment, whose ``row_id`` is the
        # movement's.  Compared with ``row.id`` that could never match, so the
        # assertion would hold whether or not the row came back.
        assert not [
            r for r in review.unmatched_rows if r.transaction_id == row.id
        ]

    def test_releasing_the_match_REMOVES_the_row_it_created(
        self, app, db, seed_user,
    ):
        """Developer ruling 2026-08-23, and the postings go with it.

        A settle day survives an undo because it is a fact about money that
        moved; a group's DIFFERENCE is a fact about the grouping, and once the
        grouping is released it states nothing.  So the row goes and its ledger
        legs are reversed -- the Uncategorized bucket is back where it started.
        """
        line, salary, allowance = _payroll(seed_user)
        accepted = _submit(
            seed_user, lines=[line], transactions=[salary, allowance],
            residual="0.05",
        )
        minted_row = _minted(seed_user)[0]
        row_id, definition_id = minted_row.id, minted_row.template_id
        assert definition_id is not None
        assert _uncategorized_net(
            seed_user, LedgerAccountClassEnum.INCOME,
        ) == Decimal("-0.05")

        statement_match.release_match(
            accepted.match_id, seed_user["user"].id, seed_user["account"].id,
        )
        db.session.flush()

        assert db.session.get(Transaction, row_id) is None
        assert not _minted(seed_user)
        # The rule-less definition the door minted goes with its only row
        # (plan step balance:X-bi-7b, R-BAL27).
        assert db.session.get(TransactionTemplate, definition_id) is None
        assert _uncategorized_net(
            seed_user, LedgerAccountClassEnum.INCOME,
        ) == Decimal("0.00")

    def test_UNDO_then_RE_ACCEPT_does_not_double_book(
        self, app, db, seed_user,
    ):
        """The defect the ruling exists to close, as its own control.

        Reproduced by adversarial security review 2026-08-23 in two ordinary
        clicks: undo kept the row, the bank line went back to unexplained, and
        accepting the same group again left TWO `$0.05` rows for one `$0.05`
        difference -- the balance high for good with nothing naming it.
        """
        line, salary, allowance = _payroll(seed_user)
        first = _submit(
            seed_user, lines=[line], transactions=[salary, allowance],
            residual="0.05",
        )
        statement_match.release_match(
            first.match_id, seed_user["user"].id, seed_user["account"].id,
        )
        db.session.flush()

        _submit(
            seed_user, lines=[line], transactions=[salary, allowance],
            residual="0.05",
        )

        assert len(_minted(seed_user)) == 1
        assert _uncategorized_net(
            seed_user, LedgerAccountClassEnum.INCOME,
        ) == Decimal("-0.05")

    def test_a_created_row_the_owner_has_EDITED_refuses_the_undo(
        self, app, db, seed_user,
    ):
        """Their record now, so this act may not take it.

        Deleting it would throw away their work in order to tidy a relation,
        which is the direction the release already refuses to go for a settle
        day.  The predicate is the row's own revision counter, so ANY edit
        counts rather than a guessed-at list of columns.
        """
        line, salary, allowance = _payroll(seed_user)
        accepted = _submit(
            seed_user, lines=[line], transactions=[salary, allowance],
            residual="0.05",
        )
        row = _minted(seed_user)[0]
        row.category_id = seed_user["categories"]["Groceries"].id
        db.session.flush()

        with pytest.raises(ValidationError) as caught:
            statement_match.release_match(
                accepted.match_id, seed_user["user"].id,
                seed_user["account"].id,
            )

        assert "you have edited that row since" in str(caught.value)
        # And the refusal left the act standing, so nothing is half-undone.
        assert db.session.get(Transaction, row.id) is not None
        assert accepted_acts(seed_user)

    def test_a_member_the_act_did_NOT_create_survives_the_undo(
        self, app, db, seed_user,
    ):
        """The rule is what this act CREATED, never what it named.

        Without that distinction an undo would delete the owner's own rows,
        which is the opposite of what a release is for.
        """
        line, salary, allowance = _payroll(seed_user)
        accepted = _submit(
            seed_user, lines=[line], transactions=[salary, allowance],
            residual="0.05",
        )

        statement_match.release_match(
            accepted.match_id, seed_user["user"].id, seed_user["account"].id,
        )
        db.session.flush()

        assert db.session.get(Transaction, salary.id) is not None
        assert db.session.get(Transaction, allowance.id) is not None
        # ...and the question is restored: both are matchable again.
        review = statement_match.review_set(a_scope(seed_user))
        # By ROW: the settle day survives the undo, so each is offered as its
        # payment, whose ``row_id`` is the movement's.
        offered = {row.transaction_id for row in review.unmatched_rows}
        assert {salary.id, allowance.id} <= offered
