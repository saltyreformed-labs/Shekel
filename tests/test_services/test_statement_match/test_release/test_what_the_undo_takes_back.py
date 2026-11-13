"""What the undo takes back of what a pass created (finding N-333), the one container one press
records, and what the undo leaves standing.
"""

from datetime import timedelta
from decimal import Decimal

import pytest

from app.exceptions import ValidationError
from app.models.merchant_rule import MerchantRule
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transaction_template import TransactionTemplate
from app.services import entry_service, statement_match
from app.models.statement_match import StatementMatchCreation
from app.services.statement_match import PurchaseCreation, Consent, ReviewedBatch
from app.services.statement_match import _create

from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_purchase,
    a_rule,
    a_scope,
    a_transaction,
    accepted_acts,
    an_import,
)

from ._helpers import (
    _record,
    _a_new_envelope,
    _release,
    _balance_on,
    _posted_total,
    _a_swipe,
    _a_submission,
)


class TestTheCreateArmHasAnInverse:
    """Finding **N-333**: the pass could create a purchase and never remove it."""

    def test_the_undo_removes_the_purchase_and_the_balance_returns(
        self, app, db, seed_user,
    ):
        """The whole point of the step, asserted as MONEY.

        Recording a `-$57.96` line takes the balance down by `$57.96`;
        undoing it puts the balance back exactly, and the account's posted
        ledger returns to the `$1,000.00` its anchor asserts.  Before this
        step the balance stayed down and the purchase stayed in the books
        with no bank line explaining it.
        """
        line = _a_swipe(seed_user)
        read_on = line.posted_on + timedelta(days=2)
        before = _balance_on(seed_user, read_on)

        created = _record(
            seed_user, line, new_envelope=_a_new_envelope(seed_user),
        )
        db.session.flush()
        assert _balance_on(seed_user, read_on) == before - Decimal("57.96"), (
            "the recording must really have moved money, or the undo below "
            "would be a no-op and prove nothing"
        )

        released = _release(seed_user, created.match_id)
        db.session.flush()

        assert db.session.get(TransactionEntry, created.entry_id) is None
        assert _balance_on(seed_user, read_on) == before
        assert _posted_total(seed_user) == Decimal("1000.00")
        assert released.removed_rows == 2
        assert released.removed_cash == Decimal("-57.96")
        assert released.kept_containers == 0

    def test_the_undo_removes_the_budget_line_it_minted(
        self, app, db, seed_user,
    ):
        """Ruling **R-GG**: an empty, untouched container states nothing.

        The owner never asked for a budget line in the abstract; they
        answered "where does THIS line go" with "a new envelope".  Withdraw
        the line and the answer means nothing, which is the same argument
        ruling **R-FN**'s residual is removed on.
        """
        line = _a_swipe(seed_user)
        created = _record(
            seed_user, line, new_envelope=_a_new_envelope(seed_user),
        )
        db.session.flush()
        envelope_id = created.transaction_id
        assert db.session.get(Transaction, envelope_id) is not None

        _release(seed_user, created.match_id)
        db.session.flush()

        assert db.session.get(Transaction, envelope_id) is None

    def test_the_undo_puts_the_merchants_answer_back_before_the_definition_goes(
        self, app, db, seed_user,
    ):
        """Leaf 7b-3 of ``balance:X-bi-7b``: the flip has an inverse too.

        The merchant's NEW-ENVELOPE answer came true at the act, so the rule
        was rewritten to TEMPLATE naming the definition it minted (N-328's
        ruling).  Left so, the undo met the transaction delete's own
        refusal -- a merchant rule names the last row's definition -- on the
        register's "DESTROYS 2 rows" promise (found by both of 7b-3's
        adversarial reviews).  The rule is put back to the answer the owner
        stated, from the definition's own name and category, and the
        definition goes with its row.
        """
        line = a_bank_line(
            seed_user, an_import(seed_user), amount="-57.96",
            posted_on=seed_user["bootstrap_period"].start_date + timedelta(days=5),
            description="POINT OF SALE DEBIT L340 WAL-MART", merchant="Walmart",
        )
        category = seed_user["categories"]["Groceries"]
        a_rule(
            seed_user, "Walmart", envelope_name="Walmart",
            category_id=category.id,
        )
        db.session.flush()
        created = _record(
            seed_user, line, new_envelope=_a_new_envelope(seed_user),
        )
        db.session.flush()
        stored = db.session.query(MerchantRule).one()
        assert stored.template_id == created.template_id, (
            "the act must really have rewritten the answer, or the undo "
            "below restores nothing and proves nothing"
        )

        released = _release(seed_user, created.match_id)
        db.session.flush()

        assert released.removed_rows == 2
        assert db.session.get(Transaction, created.transaction_id) is None
        assert db.session.get(TransactionTemplate, created.template_id) is None
        stored = db.session.query(MerchantRule).one()
        assert stored.template_id is None
        assert stored.envelope_name == "Walmart"
        assert stored.category_id == category.id

    def test_undo_then_RE_RECORD_does_not_double_book(
        self, app, db, seed_user,
    ):
        """The defect this step closes, as its own control.

        Measured before the remedy: one `-$57.96` line recorded, released and
        recorded again moved the balance **`-$115.92`** and left two purchases
        and two budget lines for one swipe.  That is finding **N-340**'s shape
        on the create arm, on a real swipe rather than a `$0.05` residual.
        """
        line = _a_swipe(seed_user)
        read_on = line.posted_on + timedelta(days=2)
        before = _balance_on(seed_user, read_on)

        first = _record(
            seed_user, line, new_envelope=_a_new_envelope(seed_user),
        )
        db.session.flush()
        _release(seed_user, first.match_id)
        db.session.flush()
        _record(seed_user, line, new_envelope=_a_new_envelope(seed_user))
        db.session.flush()

        assert _balance_on(seed_user, read_on) == before - Decimal("57.96")
        assert db.session.query(TransactionEntry).filter(
            TransactionEntry.description == "POINT OF SALE DEBIT L340 WAL-MART",
        ).count() == 1
        assert db.session.query(Transaction).filter(
            Transaction.name == "Walmart",
        ).count() == 1

    def test_a_purchase_the_owner_EDITED_refuses_the_undo(
        self, app, db, seed_user,
    ):
        """Their record now, so this act may not take it.

        The predicate is the row's own revision counter, so ANY edit counts
        rather than a guessed-at list of columns -- and the refusal leaves the
        act standing, so nothing is half-undone.
        """
        line = _a_swipe(seed_user)
        created = _record(
            seed_user, line, new_envelope=_a_new_envelope(seed_user),
        )
        db.session.flush()
        entry_service.update_entry(
            created.entry_id, seed_user["user"].id,
            description="Walmart -- garden hose",
        )
        db.session.flush()

        with pytest.raises(ValidationError) as caught:
            _release(seed_user, created.match_id)

        assert "you have edited that row since" in str(caught.value)
        assert db.session.get(TransactionEntry, created.entry_id) is not None
        assert db.session.get(Transaction, created.transaction_id) is not None
        assert accepted_acts(seed_user)

    def test_the_line_is_unexplained_again(self, app, db, seed_user):
        """What a release restores is the QUESTION.

        With the purchase gone there is nothing left for the matcher to pair
        the line with, so it returns to the CREATABLE list -- which is where
        it started.  Before this step it came back as a PROPOSAL against the
        orphan the undo had left standing.
        """
        line = _a_swipe(seed_user)
        created = _record(
            seed_user, line, new_envelope=_a_new_envelope(seed_user),
        )
        db.session.flush()

        _release(seed_user, created.match_id)
        db.session.flush()

        review = statement_match.review_set(a_scope(seed_user))
        assert [row.line.line_id for row in review.creatable] == [line.id]
        assert not review.proposals


class TestOnePressRecordsONEContainer:
    """The real press path, not a simulated registry.

    ``apply_reviewed`` is what the screen posts to and what threads the
    within-press envelope registry (finding **N-327**).  A sweep filing two
    lines into one new-envelope answer must record TWO purchase creations and
    exactly ONE container creation -- a second container record would offer the
    same envelope for removal twice, and the second undo would find it gone.
    """

    def test_a_sweep_records_two_purchases_and_one_container(
        self, app, db, seed_user,
    ):
        """Measured shape: 11 of the developer's 47 envelopes hold 2-4."""
        answer = _a_new_envelope(seed_user)
        first_line = _a_swipe(seed_user, sequence=0)
        second_line = _a_swipe(seed_user, sequence=1, amount="-18.64")

        outcome = statement_match.apply_reviewed(
            ReviewedBatch(skips=(), consent=Consent.TICKED, matches=(), incomes=(), creations=(
                PurchaseCreation(
                    line_id=first_line.id, new_envelope=answer,
                ),
                PurchaseCreation(
                    line_id=second_line.id, new_envelope=answer,
                ),
            )),
            a_scope(seed_user),
        )
        db.session.flush()

        assert not outcome.refused, [item.reason for item in outcome.refused]
        creations = db.session.query(StatementMatchCreation).all()
        assert len([
            row for row in creations
            if row.transaction_entry_id is not None
        ]) == 2
        containers = [
            row for row in creations if row.transaction_id is not None
        ]
        assert len(containers) == 1
        assert db.session.query(Transaction).filter(
            Transaction.name == "Walmart",
        ).count() == 1


class TestWhatTheUndoLEAVESStanding:
    """A container is decided on different terms, and it never refuses."""

    def test_an_envelope_still_holding_a_purchase_STAYS(
        self, app, db, seed_user,
    ):
        """One press can file several lines into one envelope.

        Releasing the act that MADE the container while another line's
        purchase is still filed under it must not take the container -- and
        must not refuse either, because the container is not what the act is
        about.  The receipt says it was kept.
        """
        minted = _create.MintedEnvelopes.none_yet()
        first_line = _a_swipe(seed_user, sequence=0)
        second_line = _a_swipe(seed_user, sequence=1, amount="-18.64")
        answer = _a_new_envelope(seed_user)
        first = _record(
            seed_user, first_line, minted=minted, new_envelope=answer,
        )
        minted.remember(PurchaseCreation(line_id=first_line.id, new_envelope=answer), first)
        second = _record(
            seed_user, second_line, minted=minted, new_envelope=answer,
        )
        db.session.flush()
        assert second.transaction_id == first.transaction_id, (
            "one press mints one envelope per answer, or this case is not "
            "the one it claims to be"
        )

        released = _release(seed_user, first.match_id)
        db.session.flush()

        assert db.session.get(Transaction, first.transaction_id) is not None
        assert db.session.get(TransactionEntry, second.entry_id) is not None
        assert released.removed_rows == 1
        assert released.kept_containers == 1

    def test_an_envelope_the_owner_EDITED_stays_without_refusing(
        self, app, db, seed_user,
    ):
        """A budget line they have adopted is theirs, and the undo says so.

        The asymmetry with a created PURCHASE is the ruling: a purchase the
        owner edited REFUSES the undo, because it is what the act is about;
        a container they edited simply stays, because leaving one standing
        costs nothing -- it budgets `0.00` and books nothing.
        """
        line = _a_swipe(seed_user)
        created = _record(
            seed_user, line, new_envelope=_a_new_envelope(seed_user),
        )
        db.session.flush()
        envelope = db.session.get(Transaction, created.transaction_id)
        envelope.name = "Home improvement"
        db.session.flush()

        released = _release(seed_user, created.match_id)
        db.session.flush()

        assert db.session.get(TransactionEntry, created.entry_id) is None
        assert db.session.get(Transaction, created.transaction_id) is not None
        assert released.kept_containers == 1

    def test_an_envelope_the_owner_PICKED_is_never_removed(
        self, app, db, seed_user,
    ):
        """The destination arm creates no container, so none can go.

        The purchase goes and the envelope's recorded cost falls back by
        exactly what the bank showed -- a ``purchases`` close IS its entries,
        so the inverse of ruling **R-FX**'s addition is exact.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", amount="500.00", is_envelope=True,
        )
        a_purchase(seed_user, envelope, amount="120.00", description="Kroger")
        db.session.flush()
        line = _a_swipe(seed_user)
        read_on = line.posted_on + timedelta(days=2)
        before = _balance_on(seed_user, read_on)
        created = _record(seed_user, line, transaction_id=envelope.id)
        db.session.flush()
        assert sum(
            (entry.amount for entry in envelope.entries), Decimal("0"),
        ) == Decimal("177.96")

        released = _release(seed_user, created.match_id)
        db.session.flush()
        db.session.expire(envelope)

        assert db.session.get(Transaction, envelope.id) is not None
        assert sum(
            (entry.amount for entry in envelope.entries), Decimal("0"),
        ) == Decimal("120.00")
        assert released.removed_rows == 1
        assert released.kept_containers == 0
        # The FIGURE on this path had no assertion until adversarial financial
        # review 2026-08-24 said so: a count without one is the bare consent
        # box this arc refuses one door over.
        assert released.removed_cash == Decimal("-57.96")
        assert _balance_on(seed_user, read_on) == before

    def test_a_match_between_EXISTING_rows_removes_nothing(
        self, app, db, seed_user,
    ):
        """The rule is what the act CREATED, never what it named."""
        statement = an_import(seed_user)
        line = a_bank_line(seed_user, statement)
        txn = a_transaction(seed_user, amount="180.00")
        accepted = statement_match.accept_match(
            _a_submission(seed_user, line, txn), a_scope(seed_user),
        )
        db.session.flush()

        released = _release(seed_user, accepted.match_id)
        db.session.flush()

        assert db.session.get(Transaction, txn.id) is not None
        assert released.removed_rows == 0
        assert released.removed_cash == Decimal("0.00")
