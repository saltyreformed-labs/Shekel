"""The UNDO: what releasing a match takes back.

Plan step **bank_import:X-f6f**, ruling **R-GG**.  The create-a-purchase arm
had no inverse (findings **N-333** and **N-340**), and the state that left was
measured through these very doors before a line of the remedy was written:

* releasing a match that CREATED a purchase kept the purchase and the budget
  line it minted -- ``removed_count: 0`` in the release's own log line;
* no door in ``app/`` removed that purchase: ``entry_service.delete_entry``
  refused with *"Transaction 1 has settled; its purchases are closed"*, which
  is finding **N-333**, and all 103 purchases the developer's 2026-08-21 pass
  created sit under exactly that parent;
* recording the same `-$57.96` line again moved the balance **`-$115.92`** and
  left two purchases and two budget lines for one swipe.

Every test below is a FIRING CONTROL over one of those, written so it fails if
the remedy is removed.

**The arithmetic the root-cause half rests on was measured, not argued.**
Removing a POSTED purchase from a ``purchases``-basis close leaves that row's
own cash leg at ``0.00`` before and after and reverses only the purchase's own
leg (``822.04 -> 880.00`` on the account); removing an UNPOSTED one moves the
leg ``-57.96 -> 0.00``, which is the close re-pricing itself on a past day.
:class:`TestTheSettledParentRuleIsTheArithmetic` pins both.
"""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import event

from app import ref_cache
from app.enums import MovementFigureSourceEnum, StatusEnum
from app.exceptions import PageOutOfDate, ValidationError
from app.extensions import db
from app.models.merchant_rule import MerchantRule
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transaction_template import TransactionTemplate
from app.services import (
    balance_at,
    credit_workflow,
    entry_service,
    statement_match,
    status_seam,
    transaction_service,
)
from app.services.balance_at import BalanceContext
from app.services.cash_ledger import in_flight_movements, movement_cash_leg
from app.services.entry_credit_workflow import sync_entry_payback
from app.models.statement_match import (
    StatementMatch,
    StatementMatchCreation,
    StatementMatchMember,
)
from app.services.statement_match import (
    NewEnvelope,
    PurchaseCreation,
    Consent,
    ReviewedBatch,
    RowKind,
)
from app.services.statement_match import _create
from app.services.statement_match._release import planned_removals

from tests._ledger_reads import account_net
from tests._test_helpers import an_entered_day, typed

from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_later_period,
    a_purchase,
    a_rule,
    a_scope,
    a_submission,
    a_transaction,
    accepted_acts,
    an_answers,
    an_envelope,
    an_import,
)


def _record(seed_user, line, minted=None, **destination):
    """Record *line* as a purchase, into whichever destination is named.

    Args:
        seed_user: The seeded user bundle.
        line: The recorded bank line.
        minted: What this REQUEST has already created; ``None`` gives each call
            its own empty registry, which is the HAND path -- one line, one
            request.  A case about a SWEEP passes one registry to several
            calls, because that is what one press is.
        **destination: The ``PurchaseCreation`` destination fields.

    Returns:
        The :class:`~app.services.statement_match.CreatedPurchase`.
    """
    return statement_match.create_purchase_from_line(
        PurchaseCreation(line_id=line.id, **destination),
        a_scope(seed_user),
        minted if minted is not None else _create.MintedEnvelopes.none_yet(),
        an_answers(seed_user),
        applied_by_rule=False,
    )


def _a_new_envelope(seed_user, name="Walmart"):
    """Return the NEW-ENVELOPE answer these tests submit."""
    return NewEnvelope(
        name=name, category_id=seed_user["categories"]["Groceries"].id,
    )


def _release(seed_user, match_id):
    """Release one match through the door under test."""
    return statement_match.release_match(
        match_id, seed_user["user"].id, seed_user["account"].id,
    )


def _balance_on(seed_user, day):
    """Return the checking account's balance as of *day*."""
    ctx = BalanceContext(
        user_id=seed_user["user"].id,
        scenario=seed_user["scenario"], as_of=day,
    )
    return balance_at.balance_at(seed_user["account"], ctx, day)


def _posted_total(seed_user):
    """Return what the posted ledger says this account holds."""
    return account_net(db.session, seed_user["account"].id, seed_user["scenario"].id)


def _a_swipe(seed_user, *, amount="-57.96", offset=5, sequence=0):
    """Return one unexplained card-swipe line, five days into the period."""
    return a_bank_line(
        seed_user, an_import(seed_user), amount=amount,
        posted_on=seed_user["bootstrap_period"].start_date
        + timedelta(days=offset),
        description="POINT OF SALE DEBIT L340 WAL-MART",
        sequence_in_group=sequence,
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


def _a_submission(seed_user, line, txn):
    """Return a one-to-one submission naming *line* and *txn*."""
    return a_submission(
        a_scope(seed_user), lines=[line], transactions=[txn],
    )


class TestTheScreenNamesWhatTheUndoWouldRemove:
    """One derivation: what the panel prints is what the door does."""

    def test_the_accepted_panel_names_the_rows_and_the_figure(
        self, app, db, seed_user,
    ):
        """The Undo button destroys money records, so the row says which.

        A control naming a count and no figure is the bare consent box ruling
        **R-GD(a)** refused one tier up, for a `$2,473.38` figure.
        """
        line = _a_swipe(seed_user)
        created = _record(
            seed_user, line, new_envelope=_a_new_envelope(seed_user),
        )
        db.session.flush()

        group = accepted_acts(seed_user)[0]

        assert group.match_id == created.match_id
        assert len(group.removes.rows) == 2
        assert group.removes.cash_amount == Decimal("-57.96")
        assert group.removes.moves_money is True
        labels = [row.label for row in group.removes.rows]
        assert "Walmart: POINT OF SALE DEBIT L340 WAL-MART" in labels
        assert "Walmart" in labels
        assert [row.is_container for row in group.removes.rows] == [
            False, True,
        ]

    def test_the_preview_and_the_door_remove_the_SAME_rows(
        self, app, db, seed_user,
    ):
        """Two derivations would let the screen promise what the button does not.

        The panel is rendered, then the door is run, and the ids the panel
        printed are exactly the rows that are gone.
        """
        line = _a_swipe(seed_user)
        created = _record(
            seed_user, line, new_envelope=_a_new_envelope(seed_user),
        )
        db.session.flush()
        group = accepted_acts(seed_user)[0]
        promised = [(row.kind, row.row_id) for row in group.removes.rows]

        _release(seed_user, created.match_id)
        db.session.flush()

        for kind, row_id in promised:
            model = (
                TransactionEntry
                if kind is statement_match.RowKind.PURCHASE else Transaction
            )
            assert db.session.get(model, row_id) is None, (
                f"the panel promised {kind} {row_id} would go and it did not"
            )

    def test_a_match_whose_row_was_EDITED_names_the_REFUSAL_not_a_removal(
        self, app, db, seed_user,
    ):
        """The disagreement one derivation exists to prevent.

        A created purchase the owner has edited stops the undo, so a panel
        that went on listing "2 rows, -$57.96" would be promising a removal the
        button refuses.  The refusal travels on the SAME value the door raises
        from.
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

        group = accepted_acts(seed_user)[0]

        assert group.removes.refusal is not None
        assert "you have edited that row since" in group.removes.refusal
        # **A refused act reports NOTHING to remove**, so no reader can print
        # a destruction the press will not perform -- the two fields are
        # exclusive by construction rather than by each reader remembering to
        # branch, which is the defect the import page shipped with until
        # adversarial security review 2026-08-24.
        assert group.removes.rows == ()
        assert group.removes.cash_amount == Decimal("0.00")
        assert group.removes.moves_money is False
        with pytest.raises(ValidationError) as caught:
            _release(seed_user, created.match_id)
        assert str(caught.value) == group.removes.refusal

    def test_a_container_cannot_be_put_BEYOND_the_purchase_door(
        self, app, db, seed_user,
    ):
        """The state this case once drove is unrepresentable since ruling R-BAL78.

        Through plan step ``X-bi-3e`` the owner could put the budget line this
        act created beyond the purchase door -- a stored figure typed over a
        row holding the act's purchase -- and this case graded that the undo
        then refused BEFORE writing anything.  Plan step ``balance:X-bi-4a``
        refuses the typed figure itself (ruling **R-BAL78**: the purchases
        ARE the figure), at the settle verb and at the seam, so the container
        stays on its purchases and the act stays removable; what this case
        grades now is that the door holds and the release's arithmetic arm is
        never reached.  The original account of the path follows, kept
        because it is the record of how the arm was found.

        Found in this step's OWN build, by driving the case rather than
        arguing it.

        The owner puts the budget line this act created beyond the purchase
        door.  ``entry_service`` then refuses to remove the purchase under it,
        and the first build discovered that HALFWAY through the removal: the
        panel had already offered *"Undo removes 1 row"*, and the release
        raised with the act already deleted from the session, which breaks this
        package's promise that a refused act leaves the database exactly as it
        was without depending on the rollback.

        So the preview asks the purchase door's own question, and the refusal
        arrives before anything is written.

        **The specimen was the ARCHIVE until plan step balance:X-am deleted
        that status, and a first attempt DELETED this case on the argument that
        no other container state reaches the arm.  That argument was false**,
        and ``_subject_removal``'s own docstring said so on the same page:
        *"the container this act created can be put beyond that afterwards, by
        being archived OR RE-CLOSED AT A STORED FIGURE."*  Two independent
        adversarial reviews caught it, and the path below was then driven end
        to end rather than reasoned about -- the `lessons.md` entry is *an
        ONLY-way argument is one writer from wrong*.

        The path, all of it through controls the owner has: the container is a
        one-off envelope (a placed row of a rule-less definition since plan
        step balance:X-bi-7b), so its full-edit popover renders *Track
        individual purchases* and lands it on the DEFINITION, and
        ``is_envelope`` is NOT in ``_LOCKED_EDIT_FIELDS`` -- unticking it on a
        settled row is admitted deliberately, because it gives the row its
        own amount back.  ``settles_from_entries`` then goes False, ruling
        **R-FF**'s guard stops biting, and a typed Actual records a
        ``corrected`` basis.  Editing the PARENT does not bump the ENTRY's
        ``version_id``, so the edited-row refusal above does not fire first
        and this arm is the one reached.
        """
        line = _a_swipe(seed_user)
        created = _record(
            seed_user, line, new_envelope=_a_new_envelope(seed_user),
        )
        db.session.flush()
        envelope = db.session.get(Transaction, created.transaction_id)
        transaction_service.settle_transaction(envelope)
        db.session.flush()

        # The flag lives on the DEFINITION since the container is a placed
        # row of one (balance:X-bi-7b: leaf 7b-1 minted it, leaf 7b-2 landed
        # the popover's control there), which is the act the card performs.
        envelope.template.is_envelope = False
        db.session.flush()
        with pytest.raises(ValidationError, match="takes its figure from the purchases"):
            transaction_service.apply_requested_status(
                envelope, envelope.status_id,
                stated=transaction_service.StatedRecord(figure=typed(Decimal("999.99"))),
            )
        db.session.flush()
        assert status_seam.recorded_settlement(envelope) == status_seam.Settlement(
            None, None,
        ), "the container stays on its purchases -- the door held"

        # The undo still names both rows the act created -- the purchase and
        # the container -- and refuses nothing.
        group = accepted_acts(seed_user)[0]
        assert group.removes.refusal is None
        assert {row.kind for row in group.removes.rows} == {
            RowKind.PURCHASE, RowKind.TRANSACTION,
        }
        assert db.session.get(TransactionEntry, created.entry_id) is not None
        assert db.session.get(Transaction, created.transaction_id) is not None

    def test_a_match_that_created_nothing_names_nothing(
        self, app, db, seed_user,
    ):
        """An act between rows that already existed removes nothing.

        What the SCREEN does with that is ruling **bank_import:R-GY**'s and it
        changed at plan step ``bank_import:X-gf-2``: the press still confirms,
        because it still destroys the record of the correspondence, and the
        dialog's wording is what varies
        (``test_statement_reconcile.test_the_settled_tabs
        .TestTheSettledTabsAreWhereAnActIsFoundAndUndone
        .test_an_undo_the_door_would_REFUSE_says_so_and_offers_no_dialog``,
        where that coverage sits now that plan step ``bank_import:X-gi-2`` has
        deleted the register).  What is asserted here is the
        DERIVATION under it -- this undo would take back no row and move no
        money -- which is what that wording is chosen from.
        """
        statement = an_import(seed_user)
        line = a_bank_line(seed_user, statement)
        txn = a_transaction(seed_user, amount="180.00")
        statement_match.accept_match(
            _a_submission(seed_user, line, txn), a_scope(seed_user),
        )
        db.session.flush()

        group = accepted_acts(seed_user)[0]

        assert group.removes.rows == ()
        assert group.removes.moves_money is False


class TestTheBulkPreviewDoesNotScaleWithTheAccount:
    """The import page folds every act; it may not do it one row at a time.

    ``removals_by_match`` was measured at **478 queries and 0.458 s** on the
    developer's own 230-act account carrying 235 creations, against 5 queries
    before the step -- because ``planned_removals`` reached each subject with
    ``session.get``, which is right for the DOOR and wrong for a fold.

    **The remedy was a bulk WARM whose result had to be HELD**, SQLAlchemy's
    identity map keeping weak references -- so a warm nothing pointed at was
    collected before the fold reached it and the per-row queries came straight
    back.  Plan step ``bank_import:X-gf-2`` deleted both halves of that
    discipline: every subject arrives on its creation through
    ``_acts._WHOLE_ACT``, so it is loaded with the act and held by the act.
    A caller can no longer forget either step, because there is no step.
    Found by adversarial security review 2026-08-24.
    """

    @staticmethod
    def _statements_for(seed_user, count, base=0):
        """Return how many SQL statements folding the account's acts costs.

        Args:
            seed_user: The seeded user bundle.
            count: How many further acts to record before folding.
            base: Where this batch's line ordinals start, so two calls in one
                test do not collide on ``uq_bank_statement_lines_identity``.

        Returns:
            The statement count for one fold over every act on the account.
        """
        for index in range(base, base + count):
            _record(
                seed_user,
                _a_swipe(seed_user, sequence=index, amount="-10.00"),
                new_envelope=_a_new_envelope(seed_user, name=f"M{index}"),
            )
        db.session.flush()
        db.session.expire_all()
        seen = []
        listener = lambda *args, **kwargs: seen.append(1)  # noqa: E731
        event.listen(db.engine, "before_cursor_execute", listener)
        try:
            statement_match.removals_by_match(
                seed_user["user"].id, seed_user["account"].id,
                {
                    row.id for row in db.session.query(StatementMatch)
                    .filter(
                        StatementMatch.account_id
                        == seed_user["account"].id,
                    ).all()
                },
            )
        finally:
            event.remove(db.engine, "before_cursor_execute", listener)
        return len(seen)

    def test_the_query_count_is_FLAT_in_the_number_of_acts(
        self, app, db, seed_user,
    ):
        """Two acts and eight acts cost the same handful of statements.

        A count asserted against a constant would drift with any unrelated
        eager load; what this pins is the SHAPE -- that the fold does not pay
        PER ACT.  **Shown to fire**: asking the database for each container's
        remaining entries, which is how ``_container_survives`` was first
        written, takes the eight-act reading to 18 against the two-act 12.

        **It did NOT cover the weak-reference half, and that is now moot
        rather than uncovered.**  Dropping the warm's own reference read
        identically here -- eight subjects are not enough for the collector to
        reach them inside one call -- and cost 478 statements against 9 on the
        developer's own 230-act database.  There is no warm and no reference to
        drop since plan step ``bank_import:X-gf-2``; what would reintroduce the
        cost is removing a chain from ``_acts._WHOLE_ACT``, which THIS
        control sees, because the subjects would then be fetched per act.
        """
        few = self._statements_for(seed_user, 2)
        many = self._statements_for(seed_user, 6, base=2)

        assert many <= few + 2, (
            f"folding 8 acts cost {many} statements where 2 cost {few}: the "
            f"bulk preview is paying per act again"
        )


class TestTheAcceptedFoldDoesNotScaleWithTheAccount:
    """The register folds every act too, and had no control saying so.

    Plan step ``bank_import:X-gf-2``.  ``accepted_groups`` is the same fold as
    ``removals_by_match`` and went without the warm the other one had -- it
    looked cheap only because 218 of the developer's 221 acts pre-date the
    creations relation and short-circuit, so the per-act cost was paid by
    nobody's data yet.  Both are now flat by construction
    (``_acts._WHOLE_ACT``) rather than by a discipline, and this is the
    control that says so for the reader the REGISTER renders.

    It grades the SHAPE, not a constant: a count asserted against a number
    would drift with any unrelated eager load, and what must not happen is
    paying per act.
    """

    @staticmethod
    def _statements_for(seed_user, count, base=0):
        """Return how many SQL statements folding the account's acts costs.

        Args:
            seed_user: The seeded user bundle.
            count: How many further acts to record before folding.
            base: Where this batch's line ordinals start, so two calls in one
                test do not collide on ``uq_bank_statement_lines_identity``.

        Returns:
            The statement count for one fold over every act on the account.
        """
        for index in range(base, base + count):
            _record(
                seed_user,
                _a_swipe(seed_user, sequence=index, amount="-10.00"),
                new_envelope=_a_new_envelope(seed_user, name=f"R{index}"),
            )
        db.session.flush()
        db.session.expire_all()
        seen = []
        listener = lambda *args, **kwargs: seen.append(1)  # noqa: E731
        event.listen(db.engine, "before_cursor_execute", listener)
        try:
            statement_match.accepted_register(
                seed_user["user"].id, seed_user["account"].id, None,
            )
        finally:
            event.remove(db.engine, "before_cursor_execute", listener)
        return len(seen)

    def test_the_query_count_is_FLAT_in_the_number_of_acts(
        self, app, db, seed_user,
    ):
        """Two acts and eight acts cost the same handful of statements.

        **Shown to fire**: dropping the member-subject chains from
        ``_WHOLE_ACT`` -- which is what this reader did by hand until this step
        -- takes the eight-act reading well past the two-act one, because every
        line, row and purchase is then fetched per act.
        """
        few = self._statements_for(seed_user, 2)
        many = self._statements_for(seed_user, 6, base=2)

        assert many <= few + 2, (
            f"folding 8 acts cost {many} statements where 2 cost {few}: the "
            f"register's fold is paying per act"
        )


class TestTheSettledParentRuleIsTheArithmetic:
    """Finding **N-229**'s removal half, corrected at the root by **R-GG**."""

    @staticmethod
    def _closed_holding(seed_user, *, posted=True):
        """Return an envelope closed from two purchases, the second doomed.

        Args:
            seed_user: The seeded user bundle.
            posted: Whether the doomed purchase carries the day the bank took
                it.  That flag is the whole rule: a posted purchase sits in
                the settled figure AND in ``posted_purchase_sum``, so removing
                it moves both and the row's own close books what it always
                booked; an unposted one sits only in the figure, so removing
                it re-prices the close.

        Returns:
            ``(envelope, doomed_entry)``.
        """
        start = seed_user["bootstrap_period"].start_date
        envelope = a_transaction(
            seed_user, name="Groceries", amount="500.00", is_envelope=True,
            template=False,
        )
        a_purchase(
            seed_user, envelope, amount="120.00", description="Kroger",
            purchased_on=start + timedelta(days=1),
            settled_on=start + timedelta(days=1),
        )
        doomed = a_purchase(
            seed_user, envelope, amount="57.96", description="Walmart",
            purchased_on=start + timedelta(days=3),
            settled_on=start + timedelta(days=3) if posted else None,
        )
        db.session.flush()
        transaction_service.settle_from_entries(envelope)
        db.session.flush()
        return envelope, doomed

    def test_a_POSTED_purchase_may_be_removed_and_the_close_is_untouched(
        self, app, db, seed_user,
    ):
        """The measurement the rule is written from.

        The envelope is worth nothing of its own before and after -- a
        ``purchases`` close has no covering movement, its purchases carrying
        the money (ruling **R-BAL81**) -- and the account's posted total
        moves ``822.04 -> 880.00``, which is the removed purchase's own
        `$57.96` line gone from its own day and nothing else.  Before this
        step the removal was refused outright, and 103 purchases a statement
        pass created in error had no door at all.
        """
        envelope, doomed = self._closed_holding(seed_user)
        assert status_seam.covered_cash_leg(envelope, envelope.account_id) == Decimal("0.00")
        assert _posted_total(seed_user) == Decimal("822.04")

        entry_service.delete_entry(doomed.id, seed_user["user"].id)
        db.session.flush()
        db.session.expire(envelope)

        assert status_seam.covered_cash_leg(envelope, envelope.account_id) == Decimal("0.00")
        assert _posted_total(seed_user) == Decimal("880.00")

    def test_an_UNPOSTED_purchase_is_admitted_since_the_row_books_nothing(
        self, app, db, seed_user,
    ):
        """The case the refusal was written about, and ruling R-BAL77 lifts it.

        Through plan step ``X-bi-3e`` removing it moved the envelope's own leg
        ``-57.96 -> 0.00`` -- the close shrinking on a past day with no
        external evidence, already-spent money handed back to the projection.
        Since plan step ``balance:X-bi-4a`` a plan row books nothing of its
        own (ruling **R-BAL80**): the un-dated purchase is a movement in
        flight, and removing it removes that and nothing else.  The row is
        worth nothing to the matcher before and after (ruling **R-BAL81**:
        a ``purchases`` close has no covering movement); through X-bi-4a's
        first cut the matcher's row-leg producer read the removal as
        ``-57.96 -> 0.00``, a figure no other reader booked, and R-BAL81
        deleted it.
        """
        envelope, doomed = self._closed_holding(seed_user, posted=False)
        account_id, scenario_id = envelope.account_id, envelope.scenario_id
        assert status_seam.covered_cash_leg(envelope, envelope.account_id) == Decimal("0.00")
        assert [
            (flight.entry_id, flight.delta)
            for flight in in_flight_movements(account_id, scenario_id)
        ] == [(doomed.id, Decimal("-57.96"))]

        entry_service.delete_entry(doomed.id, seed_user["user"].id)
        db.session.flush()

        assert db.session.get(TransactionEntry, doomed.id) is None
        db.session.expire(envelope)
        assert status_seam.covered_cash_leg(envelope, envelope.account_id) == Decimal("0.00")
        assert in_flight_movements(account_id, scenario_id) == []

    def test_a_STORED_figure_settlement_is_still_refused(
        self, app, db, seed_user,
    ):
        """A ``derived`` close fixed its figure before this purchase existed.

        Its cost cannot fall by the purchase, whose own movement would
        otherwise be counted beside the covering movement that already
        carries the whole close (ruling **R-BAL80**).

        **A fourth case stood beside this one until plan step balance:X-am**:
        an ARCHIVED parent, refused whatever its basis and carrying its own
        sentence, because the shared message told the owner to *set the row
        back to Projected* -- a transition the state machine refused for the
        terminal status, which is finding **N-302**'s shape.  Deleting the
        status deleted both the case and the second sentence: every settled row
        can now take that repair, so one message is true for all of them.
        """
        start = seed_user["bootstrap_period"].start_date
        envelope = a_transaction(
            seed_user, name="Groceries", amount="500.00", is_envelope=True,
            status=StatusEnum.DONE, settled_on=start + timedelta(days=1),
        )
        doomed = a_purchase(
            seed_user, envelope, amount="57.96", description="Walmart",
            purchased_on=start, settled_on=start + timedelta(days=1),
        )
        db.session.flush()
        assert status_seam.recorded_settlement(envelope).source is (
            MovementFigureSourceEnum.RESOLVED
        )

        with pytest.raises(ValidationError, match="records a fixed figure"):
            entry_service.delete_entry(doomed.id, seed_user["user"].id)

        assert db.session.get(TransactionEntry, doomed.id) is not None

    def test_a_CREDIT_purchase_may_be_removed_and_the_payback_follows(
        self, app, db, seed_user,
    ):
        """The arm the rule's own paragraph is written about, graded.

        A card purchase moves no cash through THIS account
        (``cash_ledger.movement_cash_leg`` reads it as ``0.00``; it leaves
        through its CC Payback), so removing it changes nothing the family
        moves here: the movements' cash sum reads the same before and after,
        and the row itself is worth nothing of its own either way (ruling
        **R-BAL81**).  The card's own liability follows: the CC Payback is
        re-derived down by the purchase.  (Through X-bi-4a's first cut this
        pinned ``settled_cash_leg``, whose credit term moved with the figure
        so the row's own close read the same; R-BAL81 deleted it.)

        **It had no test at all until adversarial financial review 2026-08-24
        deleted the ``is_credit`` clause and watched the whole suite pass** --
        the create arm never mints a credit purchase, so the matcher's own
        cases cannot reach it and only a hand delete does.
        """
        start = seed_user["bootstrap_period"].start_date
        envelope = a_transaction(
            seed_user, name="Groceries", amount="500.00", is_envelope=True,
            template=False,
        )
        a_purchase(
            seed_user, envelope, amount="120.00", description="Kroger",
            purchased_on=start + timedelta(days=1),
            settled_on=start + timedelta(days=1),
        )
        a_later_period(seed_user)
        doomed = entry_service.create_entry(
            transaction_id=envelope.id, user_id=seed_user["user"].id,
            details=entry_service.EntryDetails(
                figure=typed(Decimal("57.96")), description="Amazon",
                purchased_on=start + timedelta(days=3), is_credit=True,
            ),
        )
        payback = sync_entry_payback(envelope.id, seed_user["user"].id)
        db.session.flush()
        transaction_service.settle_from_entries(envelope)
        db.session.flush()
        assert movement_cash_leg(envelope, doomed) == Decimal("0.00")
        before = sum(
            (movement_cash_leg(envelope, entry) for entry in envelope.entries),
            Decimal("0.00"),
        )
        assert before == Decimal("-120.00")
        assert status_seam.covered_cash_leg(envelope, envelope.account_id) == Decimal("0.00")
        assert payback.estimated_amount == Decimal("57.96")

        entry_service.delete_entry(doomed.id, seed_user["user"].id)
        db.session.flush()
        db.session.expire(envelope)

        assert db.session.get(TransactionEntry, doomed.id) is None
        # What the family moves through THIS account is untouched: the card
        # purchase moved none of it, which is the whole argument for
        # admitting this.
        assert sum(
            (movement_cash_leg(envelope, entry) for entry in envelope.entries),
            Decimal("0.00"),
        ) == before
        assert status_seam.covered_cash_leg(envelope, envelope.account_id) == Decimal("0.00")
        # ...and the card owes less, because the spend it repays has gone.
        assert db.session.get(Transaction, payback.id) is None

    def test_a_PROJECTED_parent_is_unaffected(self, app, db, seed_user):
        """The rule speaks only about a settled row; an open one is untouched."""
        envelope = a_transaction(
            seed_user, name="Groceries", amount="500.00", is_envelope=True,
        )
        doomed = a_purchase(seed_user, envelope, amount="57.96")
        db.session.flush()

        entry_service.delete_entry(doomed.id, seed_user["user"].id)
        db.session.flush()

        assert db.session.get(TransactionEntry, doomed.id) is None


class TestTheRegisterBoundsWhatItRenders:
    """Ruling **bank_import:R-GX**: the bound falls on the SETTLED remainder.

    Plan step ``bank_import:X-gf-2``.  The accepted list is a log -- one row
    per act the owner ever accepts, 221 of them and 216,637 bytes on the
    developer's own account after a year -- so the register renders the newest
    :data:`~app.services.statement_match.REGISTER_LIMIT` of it and says how
    many it withheld.

    **An act that NO LONGER HOLDS is never withheld**, whatever its age, and
    that is the half worth grading: whether an act still holds is a VALUATION
    over its member rows and not a query, so the fold reaches every act anyway
    -- and an act explaining less than it claims is the one thing on that page
    worth finding.  What is bounded is what is RENDERED, never what is read.
    """

    @staticmethod
    def _an_act(seed_user, ordinal, *, amount="-10.00"):
        """Accept one match, so the register has an act to list.

        Args:
            seed_user: The seeded user bundle.
            ordinal: The line's ordinal, completing its identity.
            amount: The bank line's signed figure.

        Returns:
            The accepted :class:`~app.services.statement_match.AcceptedMatch`.
        """
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, statement, amount=amount, posted_on=day,
            sequence_in_group=ordinal,
        )
        txn = a_transaction(
            seed_user, name=f"Bill {ordinal}", amount=amount.lstrip("-"),
            status=StatusEnum.DONE, settled_on=day,
        )
        db.session.flush()
        act = statement_match.accept_match(
            a_submission(a_scope(seed_user), lines=[line], transactions=[txn]),
            a_scope(seed_user),
        )
        db.session.flush()
        return act

    def test_it_renders_the_LIMIT_and_says_what_it_withheld(
        self, app, db, seed_user,
    ):
        """Three acts, a bound of one: one rendered, two counted.

        The limit is a PARAMETER here rather than the shipped 50, because what
        is under test is the arithmetic and not the number -- and staging 51
        acts to move one boundary would grade the same line at fifty times the
        cost.  ``TestTheRegisterPage`` drives the shipped default through the
        route.
        """
        for ordinal in range(3):
            self._an_act(seed_user, ordinal)

        register = statement_match.accepted_register(
            seed_user["user"].id, seed_user["account"].id, 1,
        )

        assert len(register.shown) == 1
        assert register.withheld_count == 2

    def test_an_act_that_NO_LONGER_HOLDS_is_shown_however_old(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL for the exemption, and for the ORDER.

        The oldest act is the one the bound would drop, so a register that
        applied the limit before the exemption would hide exactly the row this
        page exists to surface.  Its row is broken the way finding **N-349**
        names: something else deleted a row it named.
        """
        doomed = self._an_act(seed_user, 0)
        for ordinal in (1, 2):
            self._an_act(seed_user, ordinal)
        # ...and the OLDEST act stops holding, by the hand edit the accepted
        # list is re-reviewable for -- through the row's own door, which
        # mirrors the day onto the covering movement the act names (plan
        # step ``credit_card:CC-5-4a-1``, ruling **R-CC43**; through
        # ``CC-5-3`` the act named the row and this wrote its column).
        # Developer confirmation 2026-09-21 (rule 5): "Confirm A, B and C as rule-5 re-expressions
        # under R-CC43 -- the member names the payment; a day moves through the seam that mirrors
        # it; a settled row is ticked as its payment."
        member = db.session.query(StatementMatchMember).filter(
            StatementMatchMember.match_id == doomed.match_id,
            StatementMatchMember.transaction_entry_id.isnot(None),
        ).one()
        row = member.entry.transaction
        transaction_service.apply_requested_status(
            row, row.status_id,
            settle_day=an_entered_day(
                seed_user["bootstrap_period"].start_date + timedelta(days=4),
            ),
        )
        db.session.flush()

        register = statement_match.accepted_register(
            seed_user["user"].id, seed_user["account"].id, 1,
        )

        shown = [group.match_id for group in register.shown]
        assert doomed.match_id in shown, (
            "the act that no longer holds was withheld by the bound"
        )
        assert shown[0] == doomed.match_id, (
            "an act that no longer holds must sort above the ones that do"
        )
        assert register.withheld_count == 1

    def test_NO_bound_renders_the_whole_record(self, app, db, seed_user):
        """What the *show everything* link asks for."""
        for ordinal in range(3):
            self._an_act(seed_user, ordinal)

        register = statement_match.accepted_register(
            seed_user["user"].id, seed_user["account"].id, None,
        )

        assert len(register.shown) == 3
        assert register.withheld_count == 0


class TestTheDeleteRemovesTheRowItWasHANDED:
    """Finding **N-371**, plan step ``bank_import:X-gf-3a``.

    ``_remove``'s transaction arm did ``db.session.get(Transaction, row_id)``
    and handed the result to ``transaction_service.delete_transaction``, whose
    signature says *the user the caller proved owns it* and which re-checks
    ``user_id`` nowhere -- so the ownership of a row this path DESTROYS rested
    on where its id had come from.  Its sibling arm never had the gap:
    ``entry_service.delete_entry`` re-validates ownership through the parent
    transaction chain itself, and that asymmetry is what made this one
    invisible.

    **A behavioural test cannot see this.**  Both spellings reach the same
    instance through SQLAlchemy's identity map, so the same rows are deleted
    either way and every figure agrees.  What the cases below grade is that
    ``_remove`` reads :attr:`PlannedRemoval.subject` and not :attr:`row_id`.

    **That is NARROWER than the invariant the finding states**, and saying so
    is the point: *no id re-enters a query on a path that destroys* is not
    graded here and cannot be, because ``db.session.get(Transaction,
    row.subject.id)`` would satisfy every case below while re-introducing
    exactly the lookup N-371 names.  The invariant is held by reading
    ``_remove``, which is four lines long; these cases hold the half a test
    can.
    """

    @staticmethod
    def _an_act_that_created_an_envelope(seed_user):
        """Record a line into a NEW envelope and return its match id.

        The shape with a CONTAINER as well as a purchase, because the container
        is what goes through the transaction arm -- the purchase goes through
        ``entry_service``, which was never the finding.
        """
        line = _a_swipe(seed_user)
        db.session.flush()
        recorded = _record(
            seed_user, line, new_envelope=_a_new_envelope(seed_user),
        )
        db.session.commit()
        return recorded.match_id

    def test_the_planned_removal_carries_the_row_it_names(
        self, app, db, seed_user,
    ):
        """The instance, not just its id -- and it IS that row.

        Asserted by identity against the row read back independently, so a
        field carrying some other transaction of the right shape would fail.
        """
        match_id = self._an_act_that_created_an_envelope(seed_user)
        match = db.session.get(StatementMatch, match_id)

        planned = planned_removals(match)

        container = next(
            row for row in planned.rows if row.is_container
        )
        assert container.subject is db.session.get(
            Transaction, container.row_id,
        )
        assert container.subject.id == container.row_id

    def test_the_row_REMOVED_is_the_one_carried_and_not_the_one_NAMED(
        self, app, db, seed_user,
    ):
        """THE FIRING CONTROL, and the first version of it could not fire.

        That version recorded which object reached
        ``transaction_service.delete_transaction`` and compared it with the one
        the removal carries -- and it passed with the refetch RESTORED, because
        ``db.session.get`` returns the same instance out of the identity map.
        A mutation run measured it: the whole file stayed green with
        ``db.session.get(Transaction, row.row_id)`` back in ``_remove``.

        What distinguishes the two is a removal whose ``subject`` and
        ``row_id`` DISAGREE -- deliberately inconsistent, because *which of the
        two does it use* is the only question here, and no consistent value can
        be asked it.  A door reading the id deletes ``named``; one removing the
        row it was handed deletes ``carried``.
        """
        # NOT RECURRING, because that is the only shape this path ever
        # removes: a residual is a rule-less definition's row since plan step
        # balance:X-bi-7b and a created envelope is built link-less (until
        # 7b-3), so the delete verb's SOFT arm -- keyed on ``recurs`` -- is
        # unreachable from here and its hard delete is what runs.  A RECURRING
        # row would be flagged rather than removed, and both assertions below
        # would read the same either way.  Built link-less here: the question
        # is which of two ids the door reads, not what it deletes.
        carried = a_transaction(
            seed_user, name="Carried", amount="11.00", template=False,
        )
        named = a_transaction(
            seed_user, name="Named", amount="12.00", template=False,
        )
        db.session.commit()
        removal = statement_match.PlannedRemoval(
            kind=statement_match.RowKind.TRANSACTION,
            row_id=named.id,
            # **Agreeing with NEITHER row**, so no reader keyed on the
            # label can produce the right answer either: a first version said
            # "Carried" and a label lookup would have passed.
            label="neither of them",
            cash_amount=Decimal("0.00"),
            is_container=True,
            subject=carried,
        )

        statement_match._release._remove(  # pylint: disable=protected-access
            removal, seed_user["user"].id,
        )
        db.session.flush()

        assert db.session.get(Transaction, carried.id) is None
        assert db.session.get(Transaction, named.id) is not None

    def test_two_removals_naming_ONE_row_are_EQUAL_whatever_they_carry(
        self, app, db, seed_user,
    ):
        """The carried instance is out of equality, and that is deliberate.

        What makes two planned removals the same is which row they NAME; a
        value whose equality depended on which instance happened to be attached
        would make every comparison in this suite an identity-map assertion.

        **A first version compared two `planned_removals` calls and could not
        fail.**  Both run in one session, so both tuples hold the identical
        instances out of the identity map, and `app/models/` defines no
        `__eq__` -- so deleting `compare=False` compared an object with itself
        and stayed green.  Two removals naming ONE row while carrying
        DIFFERENT instances is the only shape that asks the question.
        """
        carried = a_transaction(
            seed_user, name="Carried", amount="11.00", template=False,
        )
        other = a_transaction(
            seed_user, name="Other", amount="12.00", template=False,
        )
        db.session.flush()
        first = statement_match.PlannedRemoval(
            kind=statement_match.RowKind.TRANSACTION,
            row_id=carried.id,
            label="Carried",
            cash_amount=Decimal("0.00"),
            is_container=True,
            subject=carried,
        )

        assert first == replace(first, subject=other)


class TestARevertedSubjectIsRefusedAsAnEdit:
    """A subject the owner reverted since the match refuses the undo as an EDIT.

    **Plan step balance:X-bx made this path live, and nothing entered it
    before it.**  That step widened
    :func:`~app.services.row_valuation.settled_contribution` -- and through it
    the matcher's row-leg producer of the time, ``cash_ledger.settled_cash_leg``
    -- to REFUSE a row that has not settled, where it used to price the row's
    plan column; three readers in this package caught that refusal, because
    they render the review page and a raise there would strand the account
    with no in-app repair (finding **N-302**), and on those three the refusal
    changed an ANSWER rather than failing a test.  ``grep -rn
    AmountUnresolvable tests/test_services/test_statement_match/`` returned
    NOTHING before this class: the step's own full-suite mutation probe could
    not have seen a defect there.  It had one, graded below.

    **Since ruling R-BAL81 (plan step balance:X-bi-4a) nothing here can fail
    to price.**  A row is worth what its covering movement moves, a stored
    figure: a REVERTED subject's kept movement is un-dated, so it is worth
    ``0.00`` (``status_seam.covered_cash_leg``), and the version test refuses
    the undo as the edit it is -- the same row and the same sentence the
    catching arm produced.  The catches and the second sentence ("the app
    can no longer work out what that row is worth", for a refusal with no
    edit behind it) are deleted with the refusal, and the case that pinned
    that arm on an UNEDITED subject is deleted with them: its state, a
    reverted row at its creation revision, was one no door could write.

    The reachable shape is an ordinary REVERT.  A subject a match minted is
    settled when the act records it, so reverting that row to Projected both
    bumps ``version_id`` (the owner has edited it) and un-dates its covering
    movement (it is worth nothing to the undo).
    """

    def test_a_REVERTED_subject_is_refused_for_the_EDIT_not_for_the_price(
        self, app, db, seed_user,
    ):
        """The message defect X-bx introduced, as its own control.

        Both refusals were honest about refusing, so no money moved either
        way and a test asserting "the undo refuses" would pass on the defect.
        What separated them is what the owner is TOLD: the price sentence
        said the app cannot work out what the row is worth and offered no
        repair, which was false here -- the row was reverted, and the edit
        sentence names that and tells them what to do about it.

        ``_subject_removal`` priced BEFORE it compared revisions, so once the
        price started refusing, the price sentence won a race the edit
        sentence used to win; the fix stated the revision test in the refusal
        path too.  Under ruling **R-BAL81** the price cannot refuse -- the
        reverted subject is worth ``0.00`` -- so the edit sentence is the only
        one left, and this case pins that the price sentence is gone.
        """
        subject = a_transaction(
            seed_user, name="Residual", amount="41.00", template=False,
            status=StatusEnum.DONE,
            settled_on=seed_user["bootstrap_period"].start_date,
        )
        db.session.flush()
        creation = StatementMatchCreation(
            match_id=0, account_id=seed_user["account"].id,
            transaction_id=subject.id, transaction_entry_id=None,
            # The revision this act left the row at.
            created_version_id=subject.version_id,
        )

        # The owner REVERTS it: out of the settled band, and a revision later.
        status_seam.apply_status_change(
            subject, ref_cache.status_id(StatusEnum.PROJECTED),
        )
        db.session.flush()
        assert subject.version_id != creation.created_version_id
        # The precondition the case rests on: the reverted row is worth
        # nothing (its kept movement is un-dated, ruling R-BAL81), and
        # nothing refuses to say so.
        assert status_seam.covered_cash_leg(subject, subject.account_id) == Decimal("0.00")

        row, refusal = statement_match._release._subject_removal(  # pylint: disable=protected-access
            creation, subject, subject.account_id,
        )

        assert refusal is not None
        assert "you have edited that row since" in refusal
        assert "can no longer work out what that row is worth" not in refusal
        # A refused act reports nothing to remove.
        assert row.cash_amount == Decimal("0.00")


class TestTheUndoFreesNoLineAnotherActHolds:
    """The one removal that could free ANOTHER act's line is refused as an edit.

    Plan step ``credit_card:CC-5-4a-5`` (leaf 5c-2b), ruling **R-CC135**.  An
    Undo names no bank line, so it opens no press of its own: each removal it
    makes is a press of one call naming nothing, which refuses any line that
    call would free (``match_withdrawal.take_out_of_matches``, ruling
    **R-CC127**: *"A button with no warning sends nothing"*).  The one removal
    that could reach a line ANOTHER act holds is the purchase arm's payback
    teardown: deleting an envelope's last CARD purchase deletes its CC payback
    in the same removal act (``entry_service._doors.delete_entry``), and a
    payback set back to Projected keeps its payment un-dated, which an act may
    still name.

    **No door reaches it, measured through the doors below.**  A purchase the
    create arm records is born a debit one (``_create._born_purchase`` states
    no card flag), and the card total counts ``is_credit`` alone
    (``app.utils.entry_partition``), so the purchase becomes its envelope's
    last card purchase only by a CC tick -- an EDIT, which moves its revision,
    and the Undo refuses an edited subject before it writes anything
    (``_subject_removal``).  An envelope the act minted is settled and
    refuses the tick until it is set back to Projected, another edit on the
    way.  So the default press is a fence that does not fire here, and these
    cases are what hold that: with the revision test deleted, each Undo
    instead reaches the teardown and is refused as out of date.
    """

    @staticmethod
    def _its_payback_matched_and_reverted(seed_user, created):
        """Make the act's purchase the envelope's last card purchase, its payback matched.

        Through the doors the owner has, on a Projected envelope: the
        purchase ticked CC (its payback created in the next period), the
        payback's payment matched to a CARD PAYMENT line by a SECOND act, and
        the payback set back to Projected, which keeps that payment un-dated
        and the second act on it -- the one shape in which deleting the last
        card purchase deletes a payback a match still names.  Commits, so the
        Undo is read against what the database holds.

        Args:
            seed_user: The seeded user bundle.
            created: The create arm's result, its envelope Projected.

        Returns:
            ``(payback_id, payment_line_id)``.
        """
        projected = ref_cache.status_id(StatusEnum.PROJECTED)
        envelope = db.session.get(Transaction, created.transaction_id)
        assert envelope.status_id == projected
        a_later_period(seed_user)
        db.session.flush()
        entry_service.update_entry(
            created.entry_id, seed_user["user"].id, is_credit=True,
        )
        db.session.flush()
        payback = credit_workflow.get_active_payback(envelope.id)
        payment_line = a_bank_line(
            seed_user, an_import(seed_user), amount="-57.96",
            posted_on=seed_user["bootstrap_period"].start_date
            + timedelta(days=6),
            description="CARD PAYMENT",
        )
        db.session.flush()
        statement_match.accept_match(
            a_submission(
                a_scope(seed_user), lines=[payment_line],
                transactions=[payback],
            ),
            a_scope(seed_user),
        )
        transaction_service.apply_requested_status(payback, projected)
        db.session.commit()
        return payback.id, payment_line.id

    @staticmethod
    def _assert_refused_as_an_edit_and_nothing_moved(
        seed_user, created, payback_id, payment_line_id,
    ):
        """Press the Undo; assert the edit sentence, and that both acts stand.

        The session is COMMITTED after the refusal, so anything the door
        wrote before refusing is in the database the re-reads see -- a
        rollback here would hide exactly that.
        """
        with pytest.raises(ValidationError) as caught:
            _release(seed_user, created.match_id)
        assert not isinstance(caught.value, PageOutOfDate), str(caught.value)
        assert "you have edited that row since" in str(caught.value)
        db.session.commit()
        db.session.expire_all()

        assert db.session.get(StatementMatch, created.match_id) is not None
        assert db.session.get(TransactionEntry, created.entry_id) is not None
        assert db.session.get(Transaction, payback_id) is not None
        payment_member = db.session.query(StatementMatchMember).filter(
            StatementMatchMember.bank_statement_line_id == payment_line_id,
        ).one()
        assert payment_member.match_id != created.match_id

    def test_a_purchase_filed_in_an_envelope_the_owner_PICKED(
        self, app, db, seed_user,
    ):
        """Route 1: the line filed in an existing Groceries envelope."""
        envelope = an_envelope(seed_user)
        db.session.flush()
        created = _record(seed_user, _a_swipe(seed_user), transaction_id=envelope.id)
        db.session.commit()
        payback_id, payment_line_id = self._its_payback_matched_and_reverted(
            seed_user, created,
        )

        self._assert_refused_as_an_edit_and_nothing_moved(
            seed_user, created, payback_id, payment_line_id,
        )

    def test_a_purchase_in_an_envelope_the_act_MINTED(
        self, app, db, seed_user,
    ):
        """Route 2: the line recorded into a new envelope the act created.

        The act closes the envelope it mints on the bank's day, and a settled
        envelope refuses the CC tick (``is_credit`` re-costs its purchase), so
        the owner sets the envelope back to Projected first.
        """
        created = _record(
            seed_user, _a_swipe(seed_user),
            new_envelope=_a_new_envelope(seed_user),
        )
        db.session.commit()
        with pytest.raises(ValidationError, match="has settled"):
            entry_service.update_entry(
                created.entry_id, seed_user["user"].id, is_credit=True,
            )
        db.session.rollback()
        transaction_service.apply_requested_status(
            db.session.get(Transaction, created.transaction_id),
            ref_cache.status_id(StatusEnum.PROJECTED),
        )
        db.session.commit()
        payback_id, payment_line_id = self._its_payback_matched_and_reverted(
            seed_user, created,
        )

        self._assert_refused_as_an_edit_and_nothing_moved(
            seed_user, created, payback_id, payment_line_id,
        )
