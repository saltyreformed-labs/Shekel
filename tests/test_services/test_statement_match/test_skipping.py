"""The SKIP act: what recording one does, what undoing one does, and what neither does.

Plan step ``bank_import:X-gj-4a``, rulings **bank_import:R-HP** and **R-JG**.
SKIP is the fourth verb a bank line can end on and the only one that names no
row of the owner's, so it needed a store of its own; these are its two doors.

**Three subjects, and the third is the one that matters most.**  What the doors
WRITE is one; what the review pass then STOPS asking about is the second; and
the third is that a skip moves NO money -- which is a claim about four tables
this leaf does not touch and about a hero figure it must not move.  A leaf that
only proved the first two would have proved that the rows appear, not that they
are harmless.

**The verb is still SHUT on the screen**, and that is deliberate:
``X-gj-4b`` lights the control.  Nothing here renders anything, so nothing here
asserts about a card.

**The READER is the fourth subject** (plan step ``bank_import:X-gj-4c-2``):
what the Skipped tab lists, in what order, and for whom.  It is graded here
rather than beside the page because it is a query -- who the rows belong to and
how they are sorted -- where :mod:`.test_reconcile` grades what the page makes
of them.
"""

from datetime import timedelta
from decimal import Decimal

import pytest

# Pylint: ``shekel-private-module-import`` -- a test of a service's INTERNALS
# reaches for them by name, which is the convention this package's own test
# modules already keep (``test_bars``, ``test_candidates``, ``test_reconcile``).
# pylint: disable=shekel-private-module-import
from app.exceptions import ValidationError
from app.models.account_opening import AccountOpening
from app.models.journal_entry import JournalEntry, Posting
from app.models.statement_line_skip import StatementLineSkip
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.enums import StatementBalanceEvidenceEnum
from app.models.statement_import import BankStatementLine
from app.models.statement_match import StatementMatch
from app.services import account_service, bank_agreement, statement_match
from app.services.balance_at import BalanceContext
from app.services.statement_match import (
    MintedEnvelopes,
    PurchaseCreation,
    Tab,
    awaiting_review_count,
    reconcile_page,
    review_set,
    skip_line,
    unskip_line,
)
from app.services.statement_match._accepted_view import REGISTER_LIMIT
from app.services.statement_match._skipping import (
    skipped_acts,
    skipped_count,
)
from app.services.statement_match._undisposed import (
    skipped_among,
    undisposed_lines,
)
from tests._test_helpers import open_books_before_the_first_assertion
from tests.test_services.test_statement_import.test_anchor import _seed_import

from ._builders import (
    a_bank_line,
    a_scope,
    a_sighting,
    a_submission,
    a_transaction,
    an_answers,
    an_envelope,
    an_import,
    an_unexplained_outflow,
)

#: The evidence class an anchored import states, so the agreement can price a
#: bank balance at all.  The cases that need a REAL comparison use it; the rest
#: pass ``None`` for the agreement, which is the state of an account no import
#: has anchored.
_FILE_CHAIN = StatementBalanceEvidenceEnum.FILE_CHAIN


def _real_agreement(seed_user):
    """Return the account's comparison, built from the DATABASE.

    **Built rather than injected**, which is the whole correction two
    adversarial reviews made on 2026-09-02: a hand-made
    :class:`~app.services.bank_agreement.BankAgreement` passed to both sides of
    a before/after comparison is an equality between two reads of one constant.

    Args:
        seed_user: The seeded user bundle.

    Returns:
        The :class:`~app.services.bank_agreement.BankAgreement`.
    """
    return bank_agreement.bank_agreement(
        seed_user["account"], BalanceContext.build(seed_user["user"].id),
    )


def _day_of(agreement, on):
    """Return the one compared day for *on*.

    Args:
        agreement: The :class:`~app.services.bank_agreement.BankAgreement`.
        on: The civil day.

    Returns:
        Its :class:`~app.services.bank_agreement.AgreementDay`.
    """
    return next(row for row in agreement.days if row.day == on)


def _real_hero(seed_user):
    """Return the Reconcile hero over a REAL agreement and a fresh pass.

    Args:
        seed_user: The seeded user bundle.

    Returns:
        The :class:`~app.services.statement_match._hero.Hero`.
    """
    return reconcile_page(
        a_scope(seed_user), _real_agreement(seed_user), Tab.TO_EXPLAIN,
    ).hero


def _owner(seed_user):
    """Return the seeded owner's id and account id, as the doors take them.

    Args:
        seed_user: The seeded user bundle.

    Returns:
        ``(owner_id, account_id)``.
    """
    return seed_user["user"].id, seed_user["account"].id


def _skip(seed_user, line):
    """Record a skip of *line* through the door under test.

    Args:
        seed_user: The seeded user bundle.
        line: The bank line to dispose of.

    Returns:
        The :class:`~app.services.statement_match.SkippedLine`.
    """
    owner_id, account_id = _owner(seed_user)
    return skip_line(line.id, owner_id, account_id)


def _money_row_counts(db):
    """Return how many rows the four money tables hold, right now.

    **The four the app records a movement in**: a transaction, a purchase
    against one, and the two ledger tables a posting lands in.  Counted rather
    than compared field by field because the claim under test is that this door
    writes NONE of them -- an absence, which a count states exactly and a
    field comparison would state only for the rows it thought to look at.

    Args:
        db: The session fixture.

    Returns:
        A dict of table name to row count.
    """
    return {
        "transactions": db.session.query(Transaction).count(),
        "transaction_entries": db.session.query(TransactionEntry).count(),
        "journal_entries": db.session.query(JournalEntry).count(),
        "account_postings": db.session.query(Posting).count(),
    }


class TestRecordingASkip:
    """What :func:`skip_line` writes, and what it refuses to write."""

    def test_it_records_the_decision_with_who_and_when(
        self, app, db, seed_user,
    ):
        """The three facts the table exists to hold.

        ``user_id`` is the one that is not derivable from anything else here,
        which is why the column exists at all -- the same argument ruling
        **R-GT** makes for ``statement_matches.applied_by_rule``.
        """
        line = an_unexplained_outflow(seed_user)
        db.session.flush()

        recorded = _skip(seed_user, line)

        # **Read as COLUMNS out of the database, not off the instance the door
        # added.**  ``session.get`` answers from the identity map, so three of
        # these assertions would be "the object the door built holds what the
        # door set" -- a tautology at the ORM tier, and the shape
        # ``test_undo``'s own ``_rows`` helper exists to refuse.  Named by
        # adversarial test-quality review 2026-09-02.
        stored = db.session.execute(
            db.text(
                "SELECT bank_statement_line_id, account_id, user_id, "
                "created_at FROM budget.statement_line_skips WHERE id = :id"
            ),
            {"id": recorded.skip_id},
        ).one()

        assert recorded.line_id == line.id
        assert recorded.was_already_skipped is False
        assert stored.bank_statement_line_id == line.id
        assert stored.account_id == seed_user["account"].id
        assert stored.user_id == seed_user["user"].id
        assert stored.created_at is not None

    def test_a_repeat_returns_the_standing_row_and_writes_nothing(
        self, app, db, seed_user,
    ):
        """A stale double-submit states the same decision, so it is absorbed.

        **The SECOND call is the one under test**, which is the shape a
        producer correct on the first write can be wrong on: a door that
        inserted again would raise ``IntegrityError`` on a press the owner is
        entitled to make twice, and one that returned a fresh id would let the
        page offer an undo for a row that is not there.
        """
        line = an_unexplained_outflow(seed_user)
        db.session.flush()
        first = _skip(seed_user, line)

        second = _skip(seed_user, line)

        assert second.skip_id == first.skip_id
        assert second.line_id == line.id
        assert second.was_already_skipped is True
        assert db.session.query(StatementLineSkip).count() == 1

    def test_a_line_on_ANOTHER_account_is_refused(
        self, app, db, seed_user, seed_second_user,
    ):
        """The security response rule at the service tier.

        The refusal names no reason, so "not there" and "not yours" answer
        alike.  **It is a FIRING CONTROL for the account filter**: the
        composite foreign key would refuse the write anyway, but as an
        ``IntegrityError`` naming a constraint, which is not an answer a screen
        can render.
        """
        statement = an_import(seed_second_user)
        theirs = a_bank_line(seed_second_user, statement)
        db.session.flush()
        owner_id, account_id = _owner(seed_user)

        with pytest.raises(ValidationError):
            skip_line(theirs.id, owner_id, account_id)

        assert db.session.query(StatementLineSkip).count() == 0

    def test_ANOTHER_owners_line_AND_account_is_refused_by_NAME(
        self, app, db, seed_second_user, seed_user,
    ):
        """FIRING CONTROL for the OWNER join, which nothing else reaches.

        The sibling case above passes THIS account with THEIR line, which the
        pre-existing ``account_id`` filter catches.  This passes their line AND
        their account with the caller's own id -- the shape that gets past that
        filter and is otherwise stopped only by
        ``fk_statement_line_skips_owner`` at flush, as an ``IntegrityError``:
        a 500 and an aborted transaction rather than an answer a screen can
        render.  Delete the ``Account.user_id == owner_id`` join and this case
        fails on the exception TYPE, which is the whole difference.  Named by
        adversarial design review 2026-09-02.
        """
        statement = an_import(seed_second_user)
        theirs = a_bank_line(seed_second_user, statement)
        db.session.flush()

        with pytest.raises(ValidationError):
            skip_line(
                theirs.id, seed_user["user"].id,
                seed_second_user["account"].id,
            )

        assert db.session.query(StatementLineSkip).count() == 0

    def test_a_line_that_does_not_exist_is_refused(self, app, db, seed_user):
        """The same answer for a line id nothing has ever recorded."""
        owner_id, account_id = _owner(seed_user)

        with pytest.raises(ValidationError):
            skip_line(999_999, owner_id, account_id)

    def test_a_line_an_accepted_MATCH_explains_is_refused(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL: ruling **R-HP**'s "exactly one of the four".

        A line carrying both a match and a skip would be answered twice, and
        the two answers contradict: one says it IS a row the books hold, the
        other that it is explained by nothing.  The refusal names the remedy,
        because there is one -- undo the match.
        """
        statement = an_import(seed_user)
        line = a_bank_line(seed_user, statement, amount="-180.00")
        txn = a_transaction(seed_user, amount="180.00")
        scope = a_scope(seed_user)
        statement_match.accept_match(
            a_submission(scope, lines=[line], transactions=[txn]), scope,
        )
        db.session.flush()
        owner_id, account_id = _owner(seed_user)

        with pytest.raises(ValidationError) as caught:
            skip_line(line.id, owner_id, account_id)

        assert "already explained by a match" in str(caught.value)
        assert db.session.query(StatementLineSkip).count() == 0


class TestALinePayingAnAccountTheyHoldIsNotExplainedByNothing:
    """Ruling **bank_import:R-JI** (developer, 2026-09-02), at the DOOR.

    A line the source files under its card-payment category is money that moved
    between two accounts the owner holds; the card arc will pair the two sides.
    Recording *explained by nothing* for it would store a decision the app
    already knows to be false.

    **It is a door and not a shut tab**, which is ruling **R-GJ**'s lesson one
    verb over -- that ruling cost `$7,412.94` to learn that a warning paragraph
    above a working control is not a refusal.  ``X-gj-4b`` renders SKIP shut
    for these lines; without this the shut tab would sit over an open door.

    **And it moves a rendered MONEY figure**, which is what makes it this
    leaf's business rather than the tab's: such a line is counted on the
    Reconcile hero's *waiting for the account they paid* chip, whose label
    carries a magnitude, so skipping one drops the chip's count AND its dollar
    total.  Named by adversarial review 2026-09-02.
    """

    #: What SECU files a card payment under, verbatim, which is the one string
    #: :data:`~._vocabulary.ACCOUNT_PAYMENT_CATEGORIES` holds for that adapter.
    CARD_PAYMENT = "Financial Services/Credit Card Payment"

    def test_the_door_refuses_it(self, app, db, seed_user):
        """FIRING CONTROL: delete the refusal and the skip lands."""
        an_envelope(seed_user)
        line = an_unexplained_outflow(
            seed_user, merchant="Capital One Credit Card", amount="-793.23",
            source_category=self.CARD_PAYMENT,
        )
        db.session.commit()
        owner_id, account_id = _owner(seed_user)

        with pytest.raises(ValidationError) as caught:
            skip_line(line.id, owner_id, account_id)

        assert "another account you hold" in str(caught.value)
        assert db.session.query(StatementLineSkip).count() == 0

    def test_the_transfers_chips_money_figure_is_unmoved(
        self, app, db, seed_user,
    ):
        """The consequence, asserted as the FIGURE rather than as the refusal.

        Arithmetic: one parked card payment of `-$793.23` gives a chip
        reading 1 and `$793.23` -- a MAGNITUDE, because every parked line is
        an outflow and the label supplies the direction.  A door that admitted
        the skip would take both to zero and delete the chip.
        """
        an_envelope(seed_user)
        line = an_unexplained_outflow(
            seed_user, merchant="Capital One Credit Card", amount="-793.23",
            source_category=self.CARD_PAYMENT,
        )
        db.session.commit()
        owner_id, account_id = _owner(seed_user)
        chip = reconcile_page(
            a_scope(seed_user), None, Tab.TO_EXPLAIN,
        ).chips[0]
        assert chip.count == 1
        assert chip.amount == Decimal("793.23")

        with pytest.raises(ValidationError):
            skip_line(line.id, owner_id, account_id)
        db.session.commit()

        after = reconcile_page(
            a_scope(seed_user), None, Tab.TO_EXPLAIN,
        ).chips[0]
        assert after.count == 1
        assert after.amount == Decimal("793.23")

    def test_a_line_whose_source_names_no_such_category_is_skippable(
        self, app, db, seed_user,
    ):
        """The refusal is about the BANK's filing, not about every outflow.

        Paired with the two above deliberately: a guard that refused every
        line, or every line with a merchant, would pass both of them and fail
        here -- which is the only way to tell a narrow refusal from a broad
        one.
        """
        an_envelope(seed_user)
        line = an_unexplained_outflow(
            seed_user, merchant="Amazon", amount="-57.96",
        )
        db.session.commit()

        recorded = _skip(seed_user, line)

        assert recorded.was_already_skipped is False


class TestTheExclusivityRunsBOTHWays:
    """Ruling **R-HP**: a bank line ends on EXACTLY ONE of the four verbs.

    :class:`TestRecordingASkip` grades the direction the skip door owns -- a
    line a live match answers may not be skipped.  This grades the mirror,
    which no key can hold because the rule spans two tables: a line the owner
    has skipped may not then be MATCHED.

    **Without the mirror the exclusivity is one-directional and the state it
    admits is silent**: the line carries a match AND a skip, renders a card on
    the Explained tab and another on the Skipped tab, is absent from the inbox
    for two independent reasons, and nothing raises.  It is refused in
    :func:`~._resolve.load_lines`, which is the ONE place all three match
    doors resolve a submitted line, so a fourth door inherits it by calling
    that function.
    """

    def _skipped_line_and_a_row(self, db, seed_user):
        """Stage one skipped bank line and one app row it could pair with.

        Args:
            db: The session fixture.
            seed_user: The seeded user bundle.

        Returns:
            ``(line, transaction)``.
        """
        statement = an_import(seed_user)
        line = a_bank_line(seed_user, statement, amount="-180.00")
        txn = a_transaction(seed_user, amount="180.00")
        db.session.flush()
        _skip(seed_user, line)
        return line, txn

    def test_matching_a_skipped_line_is_refused(self, app, db, seed_user):
        """FIRING CONTROL: delete the ``load_lines`` term and this passes.

        The submission is one a browser can produce -- a tab held open across
        a skip made in another -- so the refusal has to be a sentence rather
        than an ``IntegrityError``, which no key would raise here anyway.
        """
        line, txn = self._skipped_line_and_a_row(db, seed_user)
        scope = a_scope(seed_user)

        with pytest.raises(ValidationError) as caught:
            statement_match.accept_match(
                a_submission(scope, lines=[line], transactions=[txn]), scope,
            )

        assert "already skipped" in str(caught.value)

    def test_the_refusal_leaves_the_skip_and_writes_no_match(
        self, app, db, seed_user,
    ):
        """It fires before anything is written, so the state is unchanged."""
        line, txn = self._skipped_line_and_a_row(db, seed_user)
        scope = a_scope(seed_user)
        before = _money_row_counts(db)

        with pytest.raises(ValidationError):
            statement_match.accept_match(
                a_submission(scope, lines=[line], transactions=[txn]), scope,
            )

        assert db.session.query(StatementLineSkip).count() == 1
        # **The act table itself, which the money counts do not cover.**  The
        # refusal fires inside ``load_lines``, which ``_accept`` evaluates as
        # an argument BEFORE ``record_match`` runs -- so nothing is staged
        # today.  Counting the acts is what would catch that refusal being
        # relocated below the INSERT, which is the one mutation this case is
        # named for.  Named by adversarial test-quality review 2026-09-02.
        assert db.session.query(StatementMatch).count() == 0
        assert _money_row_counts(db) == before

    def test_the_ADD_door_inherits_the_same_refusal(
        self, app, db, seed_user,
    ):
        """The claim that this lives in ONE place, MEASURED at a second door.

        :func:`~._resolve.load_lines` is called by
        :func:`~._accept.accept_match`, by
        :func:`~._create.create_purchase_from_line`, by the income door and by
        the preview.  The class docstring says a door inherits the refusal by
        calling it; asserting that of only ``accept_match`` leaves the claim
        untested at every other door.  ADD is one of ruling **R-HP**'s own four
        verbs, so *a skipped line becomes a purchase* is exactly the
        two-answers state this class forbids.  Named by adversarial
        test-quality review 2026-09-02.
        """
        envelope = an_envelope(seed_user)
        line = an_unexplained_outflow(seed_user)
        db.session.flush()
        _skip(seed_user, line)

        with pytest.raises(ValidationError) as caught:
            statement_match.create_purchase_from_line(
                PurchaseCreation(
                    line_id=line.id, transaction_id=envelope.id,
                ),
                a_scope(seed_user),
                MintedEnvelopes.none_yet(),
                an_answers(seed_user),
                applied_by_rule=False,
            )

        assert "already skipped" in str(caught.value)

    def test_undoing_the_skip_makes_the_line_matchable_again(
        self, app, db, seed_user,
    ):
        """The refusal is about the STANDING answer, not about the line.

        Paired with the case above deliberately: a guard that refused the line
        forever -- because of something the skip left behind -- would pass the
        refusal cases and fail here, which is the only way to tell the two
        apart.
        """
        line, txn = self._skipped_line_and_a_row(db, seed_user)
        owner_id, account_id = _owner(seed_user)
        standing = db.session.query(StatementLineSkip).one()
        unskip_line(standing.id, owner_id, account_id)
        db.session.flush()
        scope = a_scope(seed_user)

        accepted = statement_match.accept_match(
            a_submission(scope, lines=[line], transactions=[txn]), scope,
        )

        assert accepted.match_id is not None


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
        """FIRING CONTROL for :func:`~._undisposed.undisposed`'s second term.

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

        **Asked through** :meth:`~._reads.ReviewSet.card_subject`, which is the
        pass's own membership question and the UNION of ``unmatched`` and every
        proposal's lines -- so one assertion covers both sets a card can be
        drawn from, and it cannot go stale as those lists change.

        *An earlier version asserted four of the set's lists empty AFTER the
        skip.*  Three of them were empty BEFORE it too, so they read the same
        on the broken tree and the fixed one; the fixture line has exactly one
        shape and could only ever have reappeared in ``creatable``.  It also
        claimed three lists where :class:`~._reads.ReviewSet` carries five.
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
        nothing: :func:`~._undisposed.skipped`'s ``account_id`` term is
        redundant with the outer ``BankStatementLine.account_id`` filter every
        caller of :func:`~._undisposed.undisposed` applies, and bank line ids
        come from one sequence, so deleting that term leaves this case GREEN.
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
        """Four tables, counted before and after both doors.

        A transaction, a purchase, a journal entry and a posting are where
        this app records that money moved.  A skip records a decision about
        the WORK, so every one of these counts must be unchanged -- by the
        skip AND by the undo, because an undo that removed something would
        mean the skip had created it.
        """
        an_envelope(seed_user)
        line = an_unexplained_outflow(seed_user)
        db.session.commit()
        owner_id, account_id = _owner(seed_user)
        before = _money_row_counts(db)

        recorded = skip_line(line.id, owner_id, account_id)
        db.session.commit()
        after_skip = _money_row_counts(db)
        unskip_line(recorded.skip_id, owner_id, account_id)
        db.session.commit()

        assert after_skip == before
        assert _money_row_counts(db) == before

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


class TestTheDoorsAreLogged:
    """A decision about money the bank moved leaves a structured event."""

    def test_recording_one_emits_its_event(self, app, db, seed_user, caplog):
        """``statement_line_skipped``, carrying the line and the act."""
        line = an_unexplained_outflow(seed_user)
        db.session.flush()

        with caplog.at_level("INFO"):
            recorded = _skip(seed_user, line)

        emitted = [
            record for record in caplog.records
            if getattr(record, "event", None) == "statement_line_skipped"
        ]
        assert len(emitted) == 1
        assert emitted[0].line_id == line.id
        assert emitted[0].skip_id == recorded.skip_id

    def test_a_repeat_emits_NOTHING(self, app, db, seed_user, caplog):
        """It wrote nothing, so it says nothing.

        An event asserting a skip was recorded, emitted for a call that
        recorded none, would make "how many lines did they skip" unanswerable
        from the log -- which is the whole reason these events exist.
        """
        line = an_unexplained_outflow(seed_user)
        db.session.flush()
        _skip(seed_user, line)

        with caplog.at_level("INFO"):
            caplog.clear()
            _skip(seed_user, line)

        assert [
            record for record in caplog.records
            if getattr(record, "event", None) == "statement_line_skipped"
        ] == []

    def test_undoing_one_emits_its_own_event(
        self, app, db, seed_user, caplog,
    ):
        """``statement_line_unskipped``, its own event and not a direction."""
        line = an_unexplained_outflow(seed_user)
        db.session.flush()
        recorded = _skip(seed_user, line)
        owner_id, account_id = _owner(seed_user)

        with caplog.at_level("INFO"):
            caplog.clear()
            unskip_line(recorded.skip_id, owner_id, account_id)

        emitted = [
            record for record in caplog.records
            if getattr(record, "event", None) == "statement_line_unskipped"
        ]
        assert len(emitted) == 1
        assert emitted[0].line_id == line.id


class TestSkippedAmongAnswersAnEmptySubmission:
    """The early return, which no door exercises and every door reaches.

    :func:`~._undisposed.skipped_among` short-circuits an empty ``line_ids``
    rather than issuing ``IN ()``.  Every caller today passes at least one id,
    so the branch is unreached by the cases above -- and an untested branch on
    a refusal path is one that could return the wrong thing and stay green.
    """

    def test_an_empty_submission_answers_an_empty_set(
        self, app, db, seed_user,
    ):
        """Empty in, empty out, with no query issued and no refusal raised."""
        line = an_unexplained_outflow(seed_user)
        db.session.flush()
        _skip(seed_user, line)

        assert skipped_among(frozenset(), seed_user["account"].id) == frozenset()

    def test_it_answers_only_the_ids_it_was_ASKED_about(
        self, app, db, seed_user,
    ):
        """The narrowing, which the empty case cannot see.

        Two lines, one skipped: asking about the OTHER must come back empty,
        and asking about both must name only the skipped one.  A reader that
        ignored ``line_ids`` and returned the account's whole skip set would
        pass the empty case and fail here.
        """
        skipped_line = an_unexplained_outflow(seed_user)
        # A DIFFERENT amount, because a line's identity is
        # ``(account, posted_on, amount, sequence_in_group)`` and both builders
        # post on the bootstrap day at ordinal 0.
        other = an_unexplained_outflow(
            seed_user, merchant="Walmart", amount="-18.06",
        )
        db.session.flush()
        _skip(seed_user, skipped_line)
        account_id = seed_user["account"].id

        assert skipped_among(frozenset({other.id}), account_id) == frozenset()
        assert skipped_among(
            frozenset({skipped_line.id, other.id}), account_id,
        ) == frozenset({skipped_line.id})


class TestABoundedLineCannotBeSkipped:
    """The two DAY bounds hold ahead of this door, and stay the pass's job.

    A line before the pay calendar opens, or one the account's opening equity
    already accounts for, reaches no card -- so nothing offers a skip for it.
    The door does NOT re-state those bounds, and that is the right split: they
    are facts about what the pass may act on, and re-spelling them here would
    be a second place for them to be wrong.  What IS asserted is the
    consequence: skipping such a line changes no count, because it was in none.
    """

    def test_skipping_a_pre_calendar_line_changes_no_count(
        self, app, db, seed_user,
    ):
        """It was never counted, so it cannot stop being counted.

        The assertion is the pair: the badge reads 0 before AND after, so a
        door that had somehow made the line visible would fail here rather
        than in whichever screen later rendered it.
        """
        opens = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, an_import(seed_user),
            posted_on=opens - timedelta(days=1),
        )
        db.session.flush()
        account_id = seed_user["account"].id
        assert awaiting_review_count(
            seed_user["user"].id, account_id, opens,
        ) == 0

        _skip(seed_user, line)

        assert awaiting_review_count(
            seed_user["user"].id, account_id, opens,
        ) == 0
        assert db.session.query(StatementLineSkip).count() == 1


class TestTheReaderOverWhatTheDoorsRecorded:
    """Plan step ``bank_import:X-gj-4c-2``: what the Skipped tab lists.

    **Three claims, and each has its own way of being wrong.**  WHOSE rows come
    back is an ownership question a missing filter answers wrongly and silently;
    WHAT ORDER they come back in is a decision the locked direction states and
    a reader can only get wrong once; and whether the COUNT agrees with the LIST
    is finding **N-389**'s defect, which shipped a caption promising a card its
    tab did not render.
    """

    def _skipped_line(self, seed_user, db, **fields):
        """Stage one line, skip it, and return the line and the act.

        Args:
            seed_user: The seeded user bundle.
            db: The session fixture.
            **fields: Passed to :func:`a_bank_line`.

        Returns:
            ``(line, SkippedLine)``.
        """
        line = a_bank_line(seed_user, an_import(seed_user), **fields)
        db.session.flush()
        return line, _skip(seed_user, line)

    def test_it_carries_the_act_id_the_undo_door_accepts(
        self, app, db, seed_user,
    ):
        """The pairing the whole tab rests on.

        A card offers ``skip_id`` and :func:`unskip_line` takes it, so a reader
        returning any other id would render an Undo that refuses -- which is
        the reason the reader and the door are one module.  **The door is
        actually CALLED**, because a test comparing the reader's id with the
        door's own return value would grade two spellings of one field rather
        than the pairing.
        """
        owner_id, account_id = _owner(seed_user)
        line, recorded = self._skipped_line(seed_user, db)

        listed = skipped_acts(owner_id, account_id).shown

        assert [act.skip_id for act in listed] == [recorded.skip_id]
        assert unskip_line(listed[0].skip_id, owner_id, account_id) == line.id
        assert skipped_acts(owner_id, account_id).shown == ()

    def test_a_line_TWO_imports_sighted_is_one_act_and_the_bound_counts_it_once(
        self, app, db, seed_user,
    ):
        """The window count and the bound are over LINES, not joined rows.

        The line's ``sightings`` ride its query as a JOIN (plan step
        ``bank_import:X-f6b-1``), so a line two imports showed is two joined
        rows; ``count() OVER ()`` and ``LIMIT`` have to see one.  Two skipped
        lines, each sighted twice, at a bound of two: both shown, nothing
        withheld -- where a count over joined rows would report two withheld.
        """
        owner_id, account_id = _owner(seed_user)
        lines = [
            self._skipped_line(
                seed_user, db, amount="-9.99", sequence_in_group=ordinal,
                description=f"LINE {ordinal}",
            )[0]
            for ordinal in range(2)
        ]
        # Staged AFTER the two imports above, so by act order it is the
        # LATEST and its wording is what each line reads.
        again = an_import(seed_user)
        for ordinal, line in enumerate(lines):
            a_sighting(seed_user, again, line, description=f"LINE {ordinal} AGAIN")
        db.session.flush()

        acts = skipped_acts(owner_id, account_id, limit=2)

        assert len(acts.shown) == 2
        assert len({act.line.line_id for act in acts.shown}) == 2
        assert acts.withheld_count == 0
        assert {act.line.description for act in acts.shown} == {
            "LINE 0 AGAIN", "LINE 1 AGAIN",
        }

    def test_it_carries_the_BANK_facts_the_card_prints(
        self, app, db, seed_user,
    ):
        """Every field the card renders, from the line the skip names.

        **The MERCHANT is the one worth naming.**  It is not a column on the
        line -- it is reached through a relationship -- so a reader that
        selected columns rather than the row would have to load it per card,
        which is the N+1 finding **N-309** already paid for.
        """
        owner_id, account_id = _owner(seed_user)
        line, _recorded = self._skipped_line(
            seed_user, db, amount="-9.99", merchant="Target",
            description="POINT OF SALE DEBIT L340 TARGET",
            source_category="Retail/Department Store",
        )

        act = skipped_acts(owner_id, account_id).shown[0]

        assert act.line.line_id == line.id
        assert act.line.merchant == "Target"
        assert act.line.merchant_id == line.merchant_id
        assert act.line.amount == Decimal("-9.99")
        assert act.line.posted_on == line.posted_on
        assert act.line.description == "POINT OF SALE DEBIT L340 TARGET"
        assert act.line.source_category == "Retail/Department Store"

    def test_the_NEWEST_bank_day_is_first_and_a_tie_is_stable(
        self, app, db, seed_user,
    ):
        """The locked direction's order, and what happens on one day.

        **Three lines over two days**, because a two-line case cannot tell a
        descending sort from an ascending one that happens to have been
        inserted backwards, and a case with no tie cannot grade the tie-break
        at all.  The pass hands lines over ASCENDING, so a reader that did not
        sort would return exactly the wrong order -- which is the defect plan
        step ``bank_import:X-gj-1b`` found in the inbox's own sections.
        """
        owner_id, account_id = _owner(seed_user)
        older = seed_user["bootstrap_period"].start_date
        newer = older + timedelta(days=1)
        first, _a = self._skipped_line(
            seed_user, db, posted_on=older, sequence_in_group=0,
        )
        same_day_a, _b = self._skipped_line(
            seed_user, db, posted_on=newer, sequence_in_group=1,
        )
        same_day_b, _c = self._skipped_line(
            seed_user, db, posted_on=newer, sequence_in_group=2,
        )

        listed = skipped_acts(owner_id, account_id).shown

        assert [act.line.line_id for act in listed] == [
            same_day_b.id, same_day_a.id, first.id,
        ]

    def test_it_returns_NOTHING_of_another_owners(
        self, app, db, seed_user, seed_second_user,
    ):
        """The ownership narrowing, with a real row on the other side.

        **Both owners really have a skip**, so an unfiltered reader returns two
        and this fails; a case where only one owner had ever skipped anything
        would pass with the filter deleted.
        """
        mine, _a = self._skipped_line(seed_user, db)
        theirs = a_bank_line(seed_second_user, an_import(seed_second_user))
        db.session.flush()
        skip_line(
            theirs.id, seed_second_user["user"].id,
            seed_second_user["account"].id,
        )
        db.session.flush()

        listed = skipped_acts(*_owner(seed_user)).shown

        assert [act.line.line_id for act in listed] == [mine.id]
        assert skipped_count(*_owner(seed_user)) == 1
        assert skipped_count(
            seed_second_user["user"].id, seed_second_user["account"].id,
        ) == 1

    def test_the_owner_id_is_a_FILTER_and_not_a_decoration(
        self, app, db, seed_user, seed_second_user,
    ):
        """FIRING CONTROL for the ``user_id`` term of :func:`_mine`.

        The sibling above passes each owner their OWN account, which the
        ``account_id`` term alone answers correctly.  This asks for another
        owner's account under this caller's id -- the pairing that gets past
        that term -- and the answer must be nothing rather than their rows.
        Delete ``StatementLineSkip.user_id == owner_id`` and this case fails
        while every other one here still passes.
        """
        theirs = a_bank_line(seed_second_user, an_import(seed_second_user))
        db.session.flush()
        skip_line(
            theirs.id, seed_second_user["user"].id,
            seed_second_user["account"].id,
        )
        db.session.flush()

        crossed = seed_user["user"].id, seed_second_user["account"].id

        assert skipped_acts(*crossed).shown == ()
        assert skipped_count(*crossed) == 0

    def test_the_COUNT_equals_the_LIST_at_every_size(
        self, app, db, seed_user,
    ):
        """Finding **N-389**'s defect, refused before it can happen.

        The caption is one query and the cards are another, so the two could
        disagree; what makes them agree structurally is the shared clause plus
        an INNER JOIN a foreign key guarantees.  Asserted at four sizes,
        including zero, because an equality that holds only where both are
        empty grades nothing.
        """
        owner_id, account_id = _owner(seed_user)

        assert skipped_count(owner_id, account_id) == 0
        assert skipped_acts(owner_id, account_id).shown == ()

        for expected in range(1, 4):
            self._skipped_line(seed_user, db, sequence_in_group=expected)
            register = skipped_acts(owner_id, account_id)

            assert skipped_count(owner_id, account_id) == expected
            assert len(register.shown) == expected
            assert register.withheld_count == 0

    def test_an_UNDONE_skip_leaves_both_readers(self, app, db, seed_user):
        """Ruling **R-JG**: undoing DELETES the row, so the tab empties.

        **The pair is the point.**  A reader that answered from a cache, or one
        the door's ``flush`` did not reach, would go on listing a card whose
        Undo the door can no longer find -- which is the state
        :func:`unskip_line` flushes to prevent, asked here of the surface that
        would show it.
        """
        owner_id, account_id = _owner(seed_user)
        _line, recorded = self._skipped_line(seed_user, db)
        assert skipped_count(owner_id, account_id) == 1

        unskip_line(recorded.skip_id, owner_id, account_id)

        assert skipped_count(owner_id, account_id) == 0
        assert skipped_acts(owner_id, account_id).shown == ()


class TestTheSkippedTabIsBOUNDEDAndSaysWhatItWithheld:
    """Ruling **bank_import:R-GX**'s shape on a third tab (**R-JW**).

    **The bound governs BYTES, which is what R-GX actually says.**  This step
    first shipped the tab unbounded and defended it on the claim that the
    settled tabs bound because they VALUE every act they render -- adversarial
    review measured that false against
    :data:`~app.services.statement_match._accepted_view.REGISTER_LIMIT`'s own
    docstring: the fold reads every act either way, and what is bounded is
    what is RENDERED.  Measured 2026-09-04 on the real page, a skip card costs
    1,427 bytes against about 980 for a settled act, so the tab that had no
    bound cost more per card than the one that did.

    **A truncated list that does not say it is truncated is a page claiming to
    be the whole record**, and this tab is the only surface a skipped line can
    be found and undone on -- so what the bound withholds has to be reachable
    rather than merely unlisted.

    **The bound is the PAGE's one** (:data:`~app.services.statement_match
    ._accepted_view.REGISTER_LIMIT`), not a constant of this tab's own: a
    first version of this step declared a ``SKIPPED_LIMIT`` the page never
    read, so these cases were green against a literal that governed nothing.
    Asserting the constant the ROUTE threads is what makes them grade the
    bound in force.
    """

    def _skips(self, seed_user, db, how_many):
        """Record *how_many* skips on the seeded account.

        Args:
            seed_user: The seeded user bundle.
            db: The session fixture.
            how_many: How many lines to stage and skip.

        Returns:
            Nothing; the rows are recorded and committed.
        """
        owner_id, account_id = _owner(seed_user)
        for index in range(how_many):
            line = a_bank_line(
                seed_user, an_import(seed_user), sequence_in_group=index,
            )
            db.session.flush()
            skip_line(line.id, owner_id, account_id)
        db.session.commit()

    def test_it_CUTS_at_the_limit_and_says_how_many_it_withheld(
        self, app, db, seed_user,
    ):
        """One skip past the boundary, so the bound must actually fire.

        **An equal pair of counts would be satisfied by an account that never
        reached the bound at all**, which is why this stages
        :data:`REGISTER_LIMIT` + 1 rather than a round number: the rendered
        list is the limit, the withheld count is one, and the two sum to the
        whole record.
        """
        owner_id, account_id = _owner(seed_user)
        self._skips(seed_user, db, REGISTER_LIMIT + 1)

        register = skipped_acts(owner_id, account_id)

        assert len(register.shown) == REGISTER_LIMIT
        assert register.withheld_count == 1
        assert len(register.shown) + register.withheld_count == (
            skipped_count(owner_id, account_id)
        )

    def test_None_LIFTS_the_bound_and_withholds_nothing(
        self, app, db, seed_user,
    ):
        """What the *show the other N* link asks for.

        The whole record, and a withheld count of zero -- which is what tells
        the surface it may stop offering the link.
        """
        owner_id, account_id = _owner(seed_user)
        self._skips(seed_user, db, REGISTER_LIMIT + 1)

        register = skipped_acts(owner_id, account_id, limit=None)

        assert len(register.shown) == REGISTER_LIMIT + 1
        assert register.withheld_count == 0

    def test_what_it_KEEPS_is_the_newest_and_what_it_drops_is_the_oldest(
        self, app, db, seed_user,
    ):
        """The bound cuts the OLD end, which is what makes it survivable.

        A bound that dropped the newest would hide the skip the owner just
        made, which is the one they are most likely to be undoing.  **Asserted
        on the actual bank days**, not on ids: the order is
        ``posted_on DESC, id DESC`` and a case reading ids alone would pass on
        an insertion order that happened to agree.

        **It grades the DIRECTION of the cut and not its SIZE**, deliberately:
        it would pass under any bound from 2 to the limit, because it never
        asserts ``len(shown)``.  The size is
        :meth:`test_it_CUTS_at_the_limit_and_says_how_many_it_withheld`'s, and
        the two are separate cases so that a wrong size and a wrong end fail
        distinguishably.  Named by adversarial review.
        """
        owner_id, account_id = _owner(seed_user)
        opens = seed_user["bootstrap_period"].start_date
        for index in range(REGISTER_LIMIT + 2):
            line = a_bank_line(
                seed_user, an_import(seed_user),
                posted_on=opens + timedelta(days=index),
                sequence_in_group=index,
            )
            db.session.flush()
            skip_line(line.id, owner_id, account_id)
        db.session.commit()

        shown = skipped_acts(owner_id, account_id).shown
        days = [act.line.posted_on for act in shown]

        assert days == sorted(days, reverse=True)
        assert days[0] == opens + timedelta(days=REGISTER_LIMIT + 1)
        # The two OLDEST are the ones withheld, which is the whole claim.
        assert opens not in days
        assert opens + timedelta(days=1) not in days

    def test_the_bound_and_the_COUNT_answer_different_questions(
        self, app, db, seed_user,
    ):
        """The tab bar states the whole record; the tab renders part of it.

        **This is the caption-over-a-count defect finding N-389 measured**, in
        the one state where it can now appear: the bar says 51 and the list
        holds 50, and the difference is SAID rather than silent.  A caption
        that fell to the rendered figure would tell the owner they have fewer
        skips than they do.
        """
        owner_id, account_id = _owner(seed_user)
        # **SEVEN past the bound, not one.**  Its sibling above stages one, and
        # two cases asserting the same triple over the same fixture are one
        # case with two names -- a remainder greater than 1 also catches an
        # off-by-one that a remainder OF 1 cannot distinguish from a constant.
        self._skips(seed_user, db, REGISTER_LIMIT + 7)

        register = skipped_acts(owner_id, account_id)

        assert skipped_count(owner_id, account_id) == REGISTER_LIMIT + 7
        assert len(register.shown) == REGISTER_LIMIT
        assert register.withheld_count == 7
