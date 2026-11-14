"""Undoing a skip, the review pass that stops asking, and a skip moving no money."""

from decimal import Decimal

import pytest

from app.exceptions import ValidationError
from app.models.account_opening import AccountOpening
from app.models.statement_line_skip import StatementLineSkip
from app.models.statement_import import BankStatementLine
from app.services import account_service
from app.services.statement_match import Tab, reconcile_page, review_set, skip_line, unskip_line
from app.services.statement_match._undisposed import undisposed_lines
from tests._ledger_reads import account_net
from tests._test_helpers import open_books_before_the_first_assertion
from tests.test_services.test_statement_import.test_anchor import _seed_import

from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_scope,
    an_envelope,
    an_import,
    an_unexplained_outflow,
)

from ._helpers import (
    _FILE_CHAIN,
    _real_agreement,
    _day_of,
    _real_hero,
    _owner,
    _skip,
    _money_snapshot,
)


class TestUndoingASkip:
    """What :func:`unskip_line` destroys, and what it refuses to touch."""

    def test_it_deletes_the_decision_and_names_the_line(
        self, app, db, seed_user,
    ):
        """The line comes back as the QUESTION it was."""
        line = an_unexplained_outflow(seed_user)
        db.session.flush()
        recorded = _skip(seed_user, line)
        owner_id, account_id = _owner(seed_user)

        freed = unskip_line(recorded.skip_id, owner_id, account_id)

        assert freed == line.id
        assert db.session.query(StatementLineSkip).count() == 0

    def test_a_skip_that_has_gone_is_refused(self, app, db, seed_user):
        """Two presses of one Undo: the second says so rather than passing."""
        line = an_unexplained_outflow(seed_user)
        db.session.flush()
        recorded = _skip(seed_user, line)
        owner_id, account_id = _owner(seed_user)
        unskip_line(recorded.skip_id, owner_id, account_id)

        with pytest.raises(ValidationError):
            unskip_line(recorded.skip_id, owner_id, account_id)

    def test_ANOTHER_owners_skip_is_refused(
        self, app, db, seed_user, seed_second_user,
    ):
        """FIRING CONTROL: the ownership filter on the undo door.

        Drop either term of the filter and this deletes a decision belonging to
        someone else -- and the receipt would name a bank line the caller
        cannot see.
        """
        statement = an_import(seed_second_user)
        theirs = a_bank_line(seed_second_user, statement)
        db.session.flush()
        recorded = skip_line(
            theirs.id, seed_second_user["user"].id,
            seed_second_user["account"].id,
        )
        owner_id, account_id = _owner(seed_user)

        with pytest.raises(ValidationError):
            unskip_line(recorded.skip_id, owner_id, account_id)

        assert db.session.get(StatementLineSkip, recorded.skip_id) is not None

    def test_the_CALLERS_OWN_OTHER_ACCOUNT_cannot_undo_this_ones_skip(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL for the ACCOUNT term, isolated from the owner term.

        :meth:`test_ANOTHER_owners_skip_is_refused` cannot do this: its two
        users differ in BOTH ``account_id`` and ``user_id``, so dropping either
        filter alone still refuses and only dropping both fails the case.  One
        owner holding two accounts separates them -- drop
        ``StatementLineSkip.account_id == account_id`` and this deletes the
        first account's decision and hands back a line id the second account
        does not hold, which is a receipt naming a line the caller cannot see.
        The route proves the ACCOUNT, so this is the term that is load-bearing
        against an ordinary stale form. Named by adversarial test-quality
        review 2026-09-02.
        """
        other = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=seed_user["account"].account_type_id,
                name="Second Checking",
                anchor_balance=Decimal("0.00"),
            ),
        )
        db.session.flush()
        open_books_before_the_first_assertion(db.session, other)
        line = an_unexplained_outflow(seed_user)
        db.session.flush()
        recorded = _skip(seed_user, line)

        with pytest.raises(ValidationError):
            unskip_line(recorded.skip_id, seed_user["user"].id, other.id)

        assert db.session.query(StatementLineSkip).count() == 1

    def test_the_pair_round_trips(self, app, db, seed_user):
        """Skip, undo, skip again: the second skip is a NEW decision.

        The uniqueness rule is on the line, so a line whose skip has been
        undone must be skippable again -- and it must mint a fresh act rather
        than resurrect the old one, because the old one is gone.
        """
        line = an_unexplained_outflow(seed_user)
        db.session.flush()
        owner_id, account_id = _owner(seed_user)
        first = _skip(seed_user, line)
        unskip_line(first.skip_id, owner_id, account_id)

        again = _skip(seed_user, line)

        assert again.skip_id != first.skip_id
        assert again.was_already_skipped is False


class TestTheReviewPassStopsAsking:
    """The whole point of the store: a skip nothing reads is a line that returns."""

    def test_a_skipped_line_leaves_the_undisposed_list(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL for ``statement_match._undisposed.undisposed``'s second term.

        Delete that ``NOT IN`` and this reads 1: the line is back, the card is
        drawn again, and the owner's answer has bought them nothing.
        """
        line = an_unexplained_outflow(seed_user)
        db.session.flush()
        account_id = seed_user["account"].id
        assert len(undisposed_lines(account_id)) == 1

        _skip(seed_user, line)

        assert undisposed_lines(account_id) == []

    def test_the_pass_renders_no_card_for_it_at_all(
        self, app, db, seed_user,
    ):
        """Ruling **R-HP**: a line with an answer is not inbox work.

        **Asked through**
        :meth:`~app.services.statement_match._reads.ReviewSet.card_subject`,
        which is the pass's own membership question and the UNION of ``unmatched`` and every
        proposal's lines -- so one assertion covers both sets a card can be
        drawn from, and it cannot go stale as those lists change.

        *An earlier version asserted four of the set's lists empty AFTER the
        skip.*  Three of them were empty BEFORE it too, so they read the same
        on the broken tree and the fixed one; the fixture line has exactly one
        shape and could only ever have reappeared in ``creatable``.  It also
        claimed three lists where
        :class:`~app.services.statement_match._reads.ReviewSet` carries five.
        Named by adversarial test-quality review 2026-09-02.
        """
        an_envelope(seed_user)
        line = an_unexplained_outflow(seed_user)
        db.session.commit()
        before = review_set(a_scope(seed_user))
        assert before.card_subject(line.id) is not None
        assert len(before.creatable) == 1

        _skip(seed_user, line)
        db.session.commit()

        after = review_set(a_scope(seed_user))
        assert after.card_subject(line.id) is None
        assert after.creatable == ()

    def test_undoing_the_skip_puts_the_line_back(self, app, db, seed_user):
        """The question returns, which is what makes the act reversible."""
        an_envelope(seed_user)
        line = an_unexplained_outflow(seed_user)
        db.session.commit()
        owner_id, account_id = _owner(seed_user)
        recorded = _skip(seed_user, line)
        db.session.commit()

        unskip_line(recorded.skip_id, owner_id, account_id)
        db.session.commit()

        assert len(review_set(a_scope(seed_user)).creatable) == 1

    # **THE GRID BADGE IS GRADED IN** ``test_awaiting_count`` **AND NOT HERE.**
    # A case asserting the badge 1 -> 0 across a skip stood here too, with a
    # different line builder and the identical kill line -- one control wearing
    # two names, which reads as two.  That module is the badge's home and its
    # header carries the predicate count.  Named by adversarial design review
    # 2026-09-02.

    def test_the_reconcile_pages_TO_EXPLAIN_count_drops(
        self, app, db, seed_user,
    ):
        """The hero's own figure, which is what the owner reads.

        The Skipped TAB is plan step ``bank_import:X-gj-4c``'s, so this
        asserts only the count the inbox claims -- the line is gone from To
        explain and, until that leaf ships, is on no tab at all.
        """
        an_envelope(seed_user)
        line = an_unexplained_outflow(seed_user)
        db.session.commit()
        assert reconcile_page(
            a_scope(seed_user), None, Tab.TO_EXPLAIN,
        ).hero.to_explain == 1

        _skip(seed_user, line)
        db.session.commit()

        assert reconcile_page(
            a_scope(seed_user), None, Tab.TO_EXPLAIN,
        ).hero.to_explain == 0

    def test_ANOTHER_accounts_skip_does_not_hide_this_accounts_line(
        self, app, db, seed_user, seed_second_user,
    ):
        """One owner's answer may not narrow another's pass.

        **NOT a firing control, and saying so is the point.**  It targets
        nothing: :func:`~app.services.statement_match._undisposed.skipped`'s
        ``account_id`` term is redundant with the outer
        ``BankStatementLine.account_id`` filter every caller of
        :func:`~app.services.statement_match._undisposed.undisposed` applies, and
        bank line ids come from one sequence, so deleting that term leaves this case GREEN.
        The term is a narrowed subquery and defence in depth, not a control
        this grades.  What this states is the end-to-end property -- two
        accounts, two skips, no leakage -- which is worth stating even where no
        single deletion breaks it.  Named by adversarial test-quality review
        2026-09-02, which measured the claim the earlier docstring made to be
        false.
        """
        mine = an_unexplained_outflow(seed_user)
        statement = an_import(seed_second_user)
        theirs = a_bank_line(seed_second_user, statement)
        db.session.flush()
        skip_line(
            theirs.id, seed_second_user["user"].id,
            seed_second_user["account"].id,
        )

        remaining = undisposed_lines(seed_user["account"].id)

        assert [line.id for line in remaining] == [mine.id]


class TestASkipMovesNoMoney:
    """The claim that makes this leaf safe, measured rather than asserted."""

    def test_it_writes_no_row_in_any_money_table(self, app, db, seed_user):
        """Two tables counted, and the ledger read, before and after both doors.

        A transaction and a purchase are where this app records that money
        moved, and the ledger is what the records book.  A skip records a
        decision about the WORK, so every one must be unchanged, by the skip AND
        by the undo: an undo that removed something would mean the skip made it.
        """
        an_envelope(seed_user)
        line = an_unexplained_outflow(seed_user)
        db.session.commit()
        owner_id, account_id = _owner(seed_user)
        before = _money_snapshot(db, seed_user)
        # Not empty: Checking's $1,000.00 opening + the Projected envelope's 0.00 = 1,000.00.
        assert account_net(db.session, account_id, seed_user["scenario"].id) == Decimal("1000.00")

        recorded = skip_line(line.id, owner_id, account_id)
        db.session.commit()
        after_skip = _money_snapshot(db, seed_user)
        unskip_line(recorded.skip_id, owner_id, account_id)
        db.session.commit()

        assert after_skip == before
        assert _money_snapshot(db, seed_user) == before

    def test_the_banks_own_record_still_shows_the_skipped_line(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL over the producer that prices the bank side.

        **This is the financial point of the whole act.**  Skipping says the
        line explains nothing the owner budgets for; it does not say the money
        did not move.  So the bank's own movement for that day must still carry
        the line's full amount -- and the one way this claim fails in future is
        somebody teaching
        :func:`~app.services.statement_import.bank_daily_movements` to honour
        skips, which would quietly close the gap and let the hero say the two
        records agree.  Add a skip filter to that query and this case fails.

        Arithmetic: the import states two lines on the anchor day, ``-25.00``
        and ``-40.00``, so the bank moved ``-65.00`` that day.  Skipping the
        first leaves ``-65.00``, not ``-40.00``.
        """
        day = seed_user["bootstrap_period"].start_date
        _seed_import(
            db, seed_user["account"], stated="1000.00",
            effective_on=day, evidence=_FILE_CHAIN,
            lines=[(day, "-25.00"), (day, "-40.00")],
        )
        db.session.commit()
        skipped = min(
            db.session.query(BankStatementLine).all(), key=lambda one: one.id,
        )
        before = _day_of(_real_agreement(seed_user), day)
        assert before.bank_lines == Decimal("-65.00")

        _skip(seed_user, skipped)
        db.session.commit()

        after = _day_of(_real_agreement(seed_user), day)
        assert after.bank_lines == Decimal("-65.00")

    def test_the_two_records_still_disagree_by_the_same_figure(
        self, app, db, seed_user,
    ):
        """``off_by`` is re-derived from the REAL producer on both sides.

        **The agreement is BUILT from the database twice, not injected once.**
        A first draft of this case handed ``reconcile_page`` one hand-made
        :class:`~app.services.bank_agreement.BankAgreement` for both reads and
        asserted its own literal against itself -- an equality between two
        reads of one constant, which is the ``[] == []`` shape
        ``_builders``' own header records this arc having already paid for.
        Two adversarial reviews found it independently on 2026-09-02.

        What it now grades: the whole chain from ``budget.bank_statement_lines``
        and the cash walk through to ``Hero.off_by``, re-run after the skip.

        Arithmetic: the seeded account is asserted at `$1,000.00` on its
        bootstrap day, and the import states the bank held `$960.00` on the
        same day.  ``off_by`` is the books LESS the bank
        (:attr:`~app.services.bank_agreement.AgreementDay.gap`), so
        `1000.00 - 960.00 = +40.00` -- **before and after**, while
        ``to_explain`` goes 1 -> 0.  The figure is asserted as an exact
        NON-ZERO value rather than as "unchanged", so the case cannot pass by
        both sides being an absence or by both being zero.

        **What it grades and what it does NOT**, stated because a
        neutrality claim is the easiest kind to overstate.  It grades the LEVEL
        half: the whole hero chain re-derived from the database after the skip.
        It does NOT grade the MOVEMENT half -- this fixture anchors the bank
        balance to the import's own stated figure ON the compared day, so a
        producer taught to drop skipped lines from the bank's movements leaves
        this figure untouched.  Measured: mutating
        :func:`~app.services.statement_import.bank_daily_movements` to filter
        skips fails only the sibling case above, not this one.  The two are a
        PAIR and neither is redundant.
        """
        day = seed_user["bootstrap_period"].start_date
        _seed_import(
            db, seed_user["account"], stated="960.00",
            effective_on=day, evidence=_FILE_CHAIN,
            lines=[(day, "-25.00")],
        )
        db.session.commit()
        line = db.session.query(BankStatementLine).one()
        before = _real_hero(seed_user)
        assert before.off_by == Decimal("40.00")
        assert before.to_explain == 1

        _skip(seed_user, line)
        db.session.commit()

        after = _real_hero(seed_user)
        assert after.off_by == Decimal("40.00")
        assert after.to_explain == 0
    def test_it_does_not_touch_the_accounts_opening(self, app, db, seed_user):
        """The books' own starting figure, which nothing here may restate.

        Named separately from the row counts because an opening is APPEND-ONLY
        and a writer that added one would not show up as an edit anywhere --
        it would show up as a new governing row, silently.
        """
        line = an_unexplained_outflow(seed_user)
        db.session.commit()
        before = db.session.query(AccountOpening).count()

        _skip(seed_user, line)
        db.session.commit()

        assert db.session.query(AccountOpening).count() == before
