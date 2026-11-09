"""Applying a whole reviewed pass in one request -- plan step X-f6a-3c-2.

Finding **N-306**.  The review screen offers two acts and, until this step,
each was its own request through its own money door: 215 round trips on the
developer's own statement, each paying 3.593 s of ``candidates_for`` before it
wrote a row.  This module is about what changed and what must not have.

**The three properties that carry the money risk:**

1. **Isolation.**  A refused item leaves NOTHING behind and the rest still
   land.  On the developer's own statement (2026-08-19) a few proposals were
   refused -- a settled credit-card payback whose derived figure had drifted
   -- so a pass that failed whole would have lost every good correction to
   one divergence.
2. **Freshness.**  One derivation serves the whole pass, so an item must not be
   handed a row or an envelope an EARLIER item in the same pass has claimed.
   That is what :func:`~app.services.statement_match.matched_subjects` being
   re-read per act buys, and it is the arm a snapshot would silently break.
3. **Failing loud.**  A broken ledger invariant (a ``RuntimeError``) is not
   one item's refusal, so it takes the whole request down.

**What the collision cases here fix in place is a REAL case**: on the
developer's own statement some envelopes are both named by a proposal and
offered as a destination, so some of the creatable lines aim at one.
"""

from datetime import timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import LedgerAccountKindEnum, StatusEnum, TxnTypeEnum
from app.exceptions import ValidationError
from app.extensions import db as _db
from app.models.statement_line_skip import StatementLineSkip
from app.models.statement_match import StatementMatch
from app.models.transaction import Transaction
from app.services import (
    balance_at,
    statement_match,
    status_seam,
    transaction_service,
)
from app.services.ledger_lines import account_counter_key
from app.services.row_valuation import settled_figure
from app.services.statement_match import (
    IncomeCreation,
    MatchSubmission,
    NewEnvelope,
    PurchaseCreation,
    Consent,
    ReviewedBatch,
    SkipRequest,
)
from app.services.statement_match._outcome import FiledMerchant

# Pylint: protected-access -- MintedEnvelopes is an internal collaboration
# between two PRIVATE modules of this package and has no importer outside
# it, so exporting it would be the surface rule 13 forbids; a test for a
# module reaches into it, which is the allowance every sibling here takes.
from app.services.statement_match import _create  # pylint: disable=protected-access

from tests._ledger_reads import linked_key_of, nets_by_key_and_day
from tests._test_helpers import (
    an_entered_day,
    purchases_of,
    count_amount_bases,
    last_covered_day,
)

from ._builders import (
    a_bank_line,
    a_later_period,
    a_purchase,
    a_rule,
    a_scope,
    a_submission,
    a_transaction,
    an_answers,
    an_import,
    an_unexplained_outflow,
    an_envelope,
    the_merchant_id,
)
from app.models.amount_ownership import AmountOwnership


def _batch(seed_user, matches=(), creations=(), incomes=(), skips=()):
    """Apply one reviewed pass over the seeded account.

    Args:
        seed_user: The seeded user bundle.
        matches: :class:`MatchSubmission` values.
        creations: :class:`PurchaseCreation` values.
        incomes: :class:`IncomeCreation` values (ruling **bank_import:R-GW**).
        skips: :class:`SkipRequest` values (ruling **bank_import:R-JG**, plan
            step ``bank_import:X-gj-4b``).

    Returns:
        The :class:`~app.services.statement_match.BatchOutcome`.
    """
    return statement_match.apply_reviewed(
        ReviewedBatch(
            consent=Consent.TICKED,
            matches=tuple(matches),
            creations=tuple(creations),
            incomes=tuple(incomes),
            skips=tuple(skips),
        ),
        # DERIVED HERE, so every pass sees the rows this test has staged.  The
        # ROUTE is what builds one in the app; a door that built its own would
        # force its caller to derive a second time for the refusal render.
        a_scope(seed_user),
    )


def _match(seed_user, lines=(), transactions=(), entries=(), residual=None):
    """Return one match item naming exactly these subjects.

    **The reviewed state comes off a freshly derived pass**, exactly as the
    screen's would (plan step ``bank_import:X-f6d-3``): a batch item carries
    the figure and revision the owner was looking at, and the door refuses one
    whose row has moved since.  Deriving here rather than taking the caller's
    scope keeps the helper's contract the same as it was -- a case that wants
    a STALE item builds one deliberately, and several below do.
    """
    return a_submission(
        a_scope(seed_user),
        lines=lines, transactions=transactions, entries=entries,
        residual=residual,
    )


def _balance(seed_user, day):
    """Return the checking account's balance as of *day*.

    Args:
        seed_user: The seeded user bundle.
        day: The day to value the account on.

    Returns:
        The balance.
    """
    return balance_at.balance_at(
        seed_user["account"],
        # Built with the day it is asked for, exactly as ``test_create``'s own
        # money cases do.  ``BalanceContext.build`` resolves an ``as_of`` from
        # the CLOCK, so two reads a few statements apart share one -- which
        # answers the same figure whatever the postings did.
        balance_at.BalanceContext(
            user_id=seed_user["user"].id,
            scenario=seed_user["scenario"],
            as_of=day,
        ),
        day,
    )


def _creation(seed_user, line, *, transaction_id=None, new_envelope=None):
    """Return one creation item for *line*."""
    return PurchaseCreation(
        line_id=line.id,
        transaction_id=transaction_id,
        new_envelope=new_envelope,
    )


class TestARefusedItemDoesNotCostTheOthers:
    """The developer's ruling of 2026-08-19, which is the whole failure policy.

    Not a hypothetical: on the developer's own statement, the day of that
    ruling, a few proposals were refused by a settled card payback's
    re-derive refusal (``entry_credit_workflow.sync_entry_payback``), and
    every other one was a correction worth making.
    """

    @staticmethod
    def _good_and_bad(seed_user):
        """Return (a match that lands, a match that cannot).

        The bad one is an UNBALANCED GROUP -- **finding N-239's own shape**,
        where the bank shows ONE payroll deposit `$0.05` above what the two app
        rows it splits into sum to.

        **It was a one-to-one match until ruling R-GD (2026-08-22)**, and had
        to change with it: a one-to-one difference is no longer a refusal but a
        CORRECTION, because the bank's figure names exactly one row and simply
        becomes it.  A GROUP is what still refuses, and it is the shape this
        docstring always claimed -- nothing says WHICH of the two rows is the
        five cents wrong, which is the indeterminacy R-FV described and R-GD
        left standing.
        """
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date
        good_line = a_bank_line(
            seed_user, statement, amount="-180.00", posted_on=day,
        )
        good_row = a_transaction(
            seed_user, name="Electricity", amount="180.00",
            status=StatusEnum.DONE, settled_on=day + timedelta(days=3),
        )
        bad_line = a_bank_line(
            seed_user, statement, amount="2573.43", posted_on=day,
            sequence_in_group=1,
        )
        bad_rows = [
            a_transaction(
                seed_user, name="Salary", amount="2473.38", income=True,
            ),
            a_transaction(
                seed_user, name="Phone Allowance", amount="100.00",
                income=True,
            ),
        ]
        return (good_line, good_row, bad_line, bad_rows)

    def test_the_good_one_lands_and_the_bad_one_is_quoted(
        self, app, db, seed_user,
    ):
        """One pass, two outcomes, and the refusal keeps its own sentence."""
        with app.app_context():
            good_line, good_row, bad_line, bad_rows = self._good_and_bad(
                seed_user,
            )

            outcome = _batch(seed_user, matches=[
                _match(seed_user, lines=[bad_line], transactions=bad_rows),
                _match(seed_user, lines=[good_line], transactions=[good_row]),
            ])

            assert outcome.applied_count == 1
            assert outcome.refused_count == 1
            assert "do not add up" in outcome.refused[0].reason
            assert outcome.refused[0].line_ids == (bad_line.id,)
            assert outcome.applied[0].line_ids == (good_line.id,)

    def test_the_refused_item_wrote_nothing(self, app, db, seed_user):
        """The item that could not be applied left no trace of trying.

        **This refusal fires BEFORE the item writes anything** -- an unbalanced
        match is rejected before any settle verb runs -- so it grades the
        loop's ``continue`` rather than the savepoint.  The savepoint's own
        property, an item that has ALREADY written when it is refused, is
        :meth:`test_a_HALF_APPLIED_group_is_undone` and
        :meth:`test_a_refused_CREATION_takes_its_new_envelope_with_it`.  Stated
        because a test whose name promises the stronger thing is worse than no
        test: both survive removing the savepoint entirely, and only the two
        named here fail.
        """
        with app.app_context():
            _, good_row, bad_line, bad_rows = self._good_and_bad(seed_user)

            _batch(seed_user, matches=[
                _match(seed_user, lines=[bad_line], transactions=bad_rows),
                _match(
                    seed_user,
                    lines=[db.session.query(
                        type(bad_line),
                    ).filter_by(amount=Decimal("-180.00")).one()],
                    transactions=[good_row],
                ),
            ])
            db.session.flush()

            assert all(row.settled_on is None for row in bad_rows)
            assert good_row.settled_on is not None
            assert db.session.query(StatementMatch).count() == 1

    def test_a_HALF_APPLIED_group_is_undone(self, app, db, seed_user):
        """A match can refuse AFTER it has already moved one of its members.

        The accept door applies each member in turn, and a settle verb can
        refuse on the second -- which is exactly the refusal the developer's
        own statement met, where a settled credit-card payback cannot be
        re-derived.
        Here the shape is narrower and buildable: a purchase whose recorded day
        the bank's stated day would move PAST the day the bank posted, which
        ``entry_service.update_entry`` refuses because money cannot clear
        before it is spent.

        Without a savepoint the first purchase keeps the settle day the refused
        match wrote, so the app records money as having moved on the strength
        of a match it also says was never accepted.
        """
        with app.app_context():
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date
            envelope = a_transaction(
                seed_user, name="Groceries", amount="100.00", is_envelope=True,
            )
            first = a_purchase(
                seed_user, envelope, amount="10.00", description="Aldi",
                purchased_on=day,
            )
            second = a_purchase(
                seed_user, envelope, amount="15.00", description="Kroger",
                purchased_on=day + timedelta(days=3),
            )
            line = a_bank_line(
                seed_user, statement, amount="-25.00", posted_on=day,
                transaction_on=day + timedelta(days=2),
            )

            outcome = _batch(seed_user, matches=[
                _match(seed_user, lines=[line], entries=[first, second]),
            ])
            db.session.flush()

            assert outcome.applied_count == 0
            assert outcome.refused_count == 1
            assert first.settled_on is None, (
                "the first member kept the day a REFUSED match wrote"
            )
            assert second.settled_on is None
            assert db.session.query(StatementMatch).count() == 0

    def test_a_refused_CREATION_takes_its_new_envelope_with_it(
        self, app, db, seed_user,
    ):
        """The arm that stages a budget row BEFORE the refusal can fire.

        A creation stages an envelope, then a purchase, and only then records
        the match -- so an item refused at the last step has a budget line to
        undo.  Without the savepoint that row survives into the commit, and the
        owner gets an envelope for a purchase that was never recorded.

        **The refusal has to fire AFTER the envelope is staged**, or this
        asserts nothing about a savepoint.  The one that does is a line the
        bank says was MADE after it POSTED -- 2 of 361 of the developer's own
        OFX lines are that shape -- because ``entry_service.create_entry``
        refuses a posting day before its purchase day, and by then
        ``_create_envelope`` has already flushed a budget row.  A first draft
        used an INFLOW, which ``_load_line`` refuses before anything at all is
        staged; it passed against a door with no savepoint.
        """
        with app.app_context():
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date
            made_later = a_bank_line(
                seed_user, statement, amount="-12.00", posted_on=day,
                transaction_on=day + timedelta(days=1),
            )
            ordinary = a_bank_line(
                seed_user, statement, amount="-12.00", posted_on=day,
                sequence_in_group=1,
            )
            category = seed_user["categories"]["Groceries"]

            outcome = _batch(seed_user, creations=[
                _creation(
                    seed_user, made_later,
                    new_envelope=NewEnvelope(
                        name="Refused", category_id=category.id,
                    ),
                ),
                _creation(
                    seed_user, ordinary,
                    new_envelope=NewEnvelope(
                        name="Landed", category_id=category.id,
                    ),
                ),
            ])
            db.session.flush()

            assert outcome.applied_count == 1
            assert outcome.refused_count == 1
            assert outcome.refused[0].line_ids == (made_later.id,)
            names = {
                row.name for row in db.session.query(Transaction).all()
            }
            assert "Landed" in names
            assert "Refused" not in names, (
                "the refused item's envelope survived, so the savepoint did "
                "not roll its staged rows back"
            )


class TestOneDerivationStAYSCorrectAcrossThePass:
    """Freshness: what an EARLIER item claims, a later item cannot.

    The scope is derived once for a whole pass -- 3.593 s against 215 acts --
    and holds every row the account could offer, priced.  What it deliberately
    does NOT hold is which of them a match has claimed, because that is exactly
    what the pass changes.  These are the cases that fail against a scope with
    the claims baked in.
    """

    def test_a_second_item_cannot_name_a_row_the_first_matched(
        self, app, db, seed_user,
    ):
        """The same row, twice in one pass.

        ``propose`` partitions its rows, so the screen cannot produce this --
        but a crafted submission can, and ``uq_statement_match_members_
        entry`` -- the row's one payment, since plan step
        ``credit_card:CC-5-4a-2`` dropped the row column's own index -- would
        answer it with an ``IntegrityError`` after the first item had already
        moved a day.
        """
        with app.app_context():
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date
            first = a_bank_line(
                seed_user, statement, amount="-180.00", posted_on=day,
            )
            second = a_bank_line(
                seed_user, statement, amount="-180.00", posted_on=day,
                sequence_in_group=1,
            )
            row = a_transaction(
                seed_user, name="Electricity", amount="180.00",
                status=StatusEnum.DONE, settled_on=day,
            )

            outcome = _batch(seed_user, matches=[
                _match(seed_user, lines=[first], transactions=[row]),
                _match(seed_user, lines=[second], transactions=[row]),
            ])

            assert outcome.applied_count == 1
            assert outcome.refused_count == 1
            assert "no longer available" in outcome.refused[0].reason
            assert db.session.query(StatementMatch).count() == 1

    def test_a_creation_cannot_target_an_envelope_a_match_claimed(
        self, app, db, seed_user,
    ):
        """The collision the developer ruled on, in the order they ruled.

        4 envelopes on the developer's own statement are both named by a
        proposal and offered as a destination for a creatable line, so 15 of
        the 91 lines land here.  Matches run first and the CREATION is refused:
        the proposal explains money the records already hold against a line the
        bank showed, where the line can be re-aimed next pass.

        It also has to be refused for a money reason.  An envelope's cash leg
        already covers its own outstanding purchases, so a match on the
        envelope AND a new purchase inside it would count that purchase twice.
        """
        with app.app_context():
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date
            envelope = a_transaction(
                seed_user, name="Groceries", amount="500.00", is_envelope=True,
            )
            matched_line = a_bank_line(
                seed_user, statement, amount="-500.00", posted_on=day,
            )
            swipe = a_bank_line(
                seed_user, statement, amount="-57.96", posted_on=day,
                sequence_in_group=1,
            )

            outcome = _batch(
                seed_user,
                matches=[_match(
                    seed_user, lines=[matched_line], transactions=[envelope],
                )],
                creations=[_creation(
                    seed_user, swipe, transaction_id=envelope.id,
                )],
            )
            db.session.flush()

            assert outcome.applied_count == 1
            assert outcome.refused_count == 1
            assert "not one this purchase can go into" in (
                outcome.refused[0].reason
            )
            assert outcome.refused[0].line_ids == (swipe.id,)
            # No PURCHASE was created; the envelope the match closed holds
            # only the covering movement the seam wrote for its close (plan
            # step X-bi-3a).
            assert purchases_of(envelope) == []

    def test_a_matched_purchase_blocks_a_later_match_on_its_PARENT(
        self, app, db, seed_user,
    ):
        """The interaction that makes a shared PRICE safe at all.

        Through plan step ``balance:X-bi-4a``'s first cut a candidate's
        figure was ``gross - card entries - posted purchases``, so the way
        one item could move a figure another item named was by posting or
        adding a purchase under it -- a parent and its own child -- and
        matching the purchase first dropped the envelope's leg by the
        purchase's amount, so the second item would have been accepted
        against a price the app no longer held.  Under ruling **R-BAL81** an
        envelope holding purchases is worth ``0`` to the offer in EITHER
        order, so the second item is refused as unavailable rather than
        re-priced; what this case still grades is that the pass refuses it
        (a scope with the claims baked in would offer it), and the re-pricing
        of a row another item moves is
        :class:`TestASIBLINGWriteCannotBookAgainstAStalePrice`'s.

        **The sentence is the availability one, not the double-count one.**
        A row worth nothing can match no bank line, so ``resolve_rows``
        refuses it as unavailable.  ``record_match``'s guard against an
        envelope named beside its own purchase, which this paragraph said was
        never asked here, is deleted since plan step
        ``credit_card:CC-5-4a-5``, leaf 5c-2c-1 (finding **CC-386**, ruling
        **R-CC144**); its last live shape -- an envelope matched EMPTY,
        reverted, then given a purchase -- was a false refusal and matches
        since leaf 5c-2b (finding **CC-385**, ruling **R-CC143**;
        ``test_accept.py::TestEveryOtherRefusalFires::
        test_a_purchase_under_a_REVERTED_matched_envelope_matches``).
        """
        with app.app_context():
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date
            envelope = a_transaction(
                seed_user, name="Groceries", amount="100.00", is_envelope=True,
            )
            purchase = a_purchase(
                seed_user, envelope, amount="25.00", purchased_on=day,
            )
            child_line = a_bank_line(
                seed_user, statement, amount="-25.00", posted_on=day,
            )
            parent_line = a_bank_line(
                seed_user, statement, amount="-100.00", posted_on=day,
                sequence_in_group=1,
            )

            outcome = _batch(seed_user, matches=[
                _match(seed_user, lines=[child_line], entries=[purchase]),
                _match(
                    seed_user, lines=[parent_line], transactions=[envelope],
                ),
            ])

            assert outcome.applied_count == 1
            assert outcome.refused_count == 1
            assert "no longer available" in outcome.refused[0].reason
            assert envelope.settled_on is None, (
                "the envelope was matched against a figure the pass had "
                "already moved"
            )


    def test_an_envelope_item_is_refused_and_its_purchase_item_lands(
        self, app, db, seed_user,
    ):
        """The other direction, and under ruling R-BAL81 it cannot start.

        Through plan step ``balance:X-bi-4a``'s first cut an envelope matched
        FIRST kept its figure, so re-pricing had nothing to say about the
        purchase inside it, and what refused the second item was the guard
        that knew WHY (``_reject_parent_and_its_own_purchase``: the envelope's
        cash leg covered its own outstanding purchases, so naming both counted
        that purchase in two terms; measured on a production clone at plan
        step X-f6a-2, two matched line-sets worth `-284.33` backed by
        `-265.69` of ledger, the projected balance `$18.64` high).  A row that
        settles from its purchases is worth ``0`` to the offer now and is not
        a candidate, so the FIRST item is the one refused -- as unavailable,
        by ``_resolve`` -- and the purchase item lands on its own.  The guard
        was never reached from this shape after that ruling, and it is deleted
        since plan step ``credit_card:CC-5-4a-5``, leaf 5c-2c-1 (finding
        **CC-386**, ruling **R-CC144**).
        """
        with app.app_context():
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date
            envelope = a_transaction(
                seed_user, name="Groceries", amount="300.00", is_envelope=True,
            )
            purchase = a_purchase(
                seed_user, envelope, amount="25.00", purchased_on=day,
            )
            parent_line = a_bank_line(
                seed_user, statement, amount="-25.00", posted_on=day,
            )
            child_line = a_bank_line(
                seed_user, statement, amount="-25.00", posted_on=day,
                sequence_in_group=1,
            )

            outcome = _batch(seed_user, matches=[
                _match(
                    seed_user, lines=[parent_line], transactions=[envelope],
                ),
                _match(seed_user, lines=[child_line], entries=[purchase]),
            ])

            assert outcome.applied_count == 1
            assert outcome.refused_count == 1
            assert outcome.refused[0].line_ids == (parent_line.id,)
            assert "no longer available" in outcome.refused[0].reason
            assert purchase.settled_on == day
            assert envelope.settled_on is None


class TestASIBLINGWriteCannotBookAgainstAStalePrice:
    """The counterexample that refuted this step's first safety argument.

    That argument was: one act can only move a figure another act names by
    adding a purchase to that row or posting one under it, which makes the two
    an envelope and its own child -- and
    ``_reject_parent_and_its_own_purchase`` refused exactly that.  **Measured
    false by adversarial financial review 2026-08-19**, with a booked figure.
    That guard is deleted since plan step ``credit_card:CC-5-4a-5``, leaf
    5c-2c-1 (finding **CC-386**, ruling **R-CC144**): under ruling **R-BAL81**
    a row that settles from its purchases is worth ``0`` to the offer, so the
    pair it refused is refused as no longer available instead.

    ``entry_service.update_entry`` -- which every matched PURCHASE goes through
    -- calls ``entry_credit_workflow.sync_entry_payback``, and that WRITES the
    envelope's CC Payback figure (its ``estimated_amount``, through
    ``amount_ownership.state_own_amount``) to the sum of its card entries.  A
    payback is a transaction on the SAME account, so it is a candidate priced
    off that column; and it is the purchase's SIBLING under one envelope, not
    its parent, so a guard keyed on a parent and its own child could never
    have seen the relation.

    Against a once-derived price the second act was accepted at the stale
    figure: the ledger booked `$50.00` for a `-$60.00` bank line and the
    account read **`$10.00` high**.  The answer is that every act re-prices the
    rows it names (:func:`~app.services.statement_match._valuation.repriced`),
    which is total where an enumeration of sibling writers is one writer from
    being wrong again, and then refuses a row that moved after the screen was
    reviewed (``_resolve._reject_moved_since_review``, finding **N-336**).  So
    the case pins: the purchase's match lands, and the payback's is refused as
    "reviewed against different figures", naming `-60.00` and `-50.00`, with
    the payback left unsettled.

    **Both acts are ordinary screen proposals in the same sweep class**, so one
    click of "tick all that mark a row as having happened" plus Apply submits
    them together.
    """

    @staticmethod
    def _drifted_payback(db, seed_user):
        """Stage an envelope, a card entry, its payback, and a debit purchase.

        The payback's own figure is moved off the sum of its card entries --
        which is what an owner does when they correct a projected CC Payback to
        what their card statement says, through the ordinary transaction edit
        door.  It is the state that arms the defect.
        """
        envelope = a_transaction(
            seed_user, name="Groceries", amount="300.00", is_envelope=True,
        )
        a_purchase(
            seed_user, envelope, amount="50.00", description="Card",
            purchased_on=seed_user["bootstrap_period"].start_date,
            is_credit=True,
        )
        debit = a_purchase(
            seed_user, envelope, amount="25.00", description="Aldi",
            purchased_on=seed_user["bootstrap_period"].start_date,
        )
        # **Built the way ``credit_workflow._create_payback`` builds one**, and
        # it has to be: ``ck_transactions_one_pricing_link`` admits at most one
        # of ``template_id`` / ``transfer_id`` / ``credit_payback_for_id``, so
        # the sibling builder's template makes a payback the database refuses.
        # A fixture the app could not produce would grade an unreachable case.
        payback = Transaction(
            account_id=seed_user["account"].id,
            template_id=None,
            user_id=seed_user['bootstrap_period'].user_id,
            pay_period_id=seed_user["bootstrap_period"].id,
            scenario_id=seed_user["scenario"].id,
            status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            name="CC Payback: Groceries",
            category_id=seed_user["categories"]["Groceries"].id,
            transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
            # DRIFTED off the sum of its card entries, which is what an owner
            # correcting a projected payback to their card statement produces.
            amount_ownership=AmountOwnership.own(Decimal("60.00")),
            credit_payback_for_id=envelope.id,
        )
        _db.session.add(payback)
        _db.session.flush()
        return envelope, debit, payback

    def test_the_stale_sibling_is_REFUSED_rather_than_booked(
        self, app, db, seed_user,
    ):
        """The whole finding, as one pass."""
        with app.app_context():
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date
            _, debit, payback = self._drifted_payback(db, seed_user)
            debit_line = a_bank_line(
                seed_user, statement, amount="-25.00", posted_on=day,
            )
            payback_line = a_bank_line(
                seed_user, statement, amount="-60.00", posted_on=day,
                sequence_in_group=1,
            )

            outcome = _batch(seed_user, matches=[
                _match(seed_user, lines=[debit_line], entries=[debit]),
                _match(
                    seed_user, lines=[payback_line], transactions=[payback],
                ),
            ])
            _db.session.flush()

            assert outcome.applied_count == 1, (
                "the purchase match must land -- otherwise the second act was "
                "never offered the stale price and this grades nothing"
            )
            assert outcome.refused_count == 1
            # The FIGURES, because that is what went wrong: the pass moved the
            # payback to 50.00 and the bank line says 60.00.
            #
            # **The refusal's VEHICLE moved at plan step
            # ``bank_import:X-f6d-3`` and the subject did not.**  It used to be
            # ``_reject_unbalanced`` -- the re-priced row no longer summed to
            # its line -- and it is now the N-336 guard, which fires first
            # because the row moved AFTER this item was reviewed.  That is the
            # better diagnosis of the same fact: the unbalance was the SYMPTOM
            # and "the row you reviewed at -60.00 now stands at -50.00" is the
            # cause, named.  ``_reject_unbalanced`` still owns the case where
            # nothing moved and the two sides simply disagree -- the `$0.05`
            # payroll shortfall (finding **N-239**), which ``test_accept``'s
            # own ``test_a_five_cent_shortfall_is_refused`` fires on.
            assert "reviewed against different figures" in (
                outcome.refused[0].reason
            )
            assert "-60.00" in outcome.refused[0].reason
            assert "-50.00" in outcome.refused[0].reason
            assert payback.settled_on is None, (
                "a -60.00 bank line was explained by a row worth 50.00"
            )


class TestTheReceiptSaysWhatHappened:
    """A pass that reports only "done" is a pass nobody can check."""

    def test_it_counts_each_EFFECT_separately(self, app, db, seed_user):
        """Settling, re-dating and recording are three different acts.

        A single "3 items applied" would hide which: settling books money the
        projection was holding forward, moving a day moves money already
        booked, and recording adds a movement the app did not have.
        """
        with app.app_context():
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date
            settle_line = a_bank_line(
                seed_user, statement, amount="-180.00", posted_on=day,
            )
            projected = a_transaction(
                seed_user, name="Electricity", amount="180.00",
            )
            correct_line = a_bank_line(
                seed_user, statement, amount="-42.00", posted_on=day,
                sequence_in_group=1,
            )
            settled = a_transaction(
                seed_user, name="Water", amount="42.00",
                status=StatusEnum.DONE, settled_on=day + timedelta(days=2),
            )
            swipe = a_bank_line(
                seed_user, statement, amount="-9.99", posted_on=day,
                sequence_in_group=2,
            )

            outcome = _batch(
                seed_user,
                matches=[
                    _match(
                        seed_user, lines=[settle_line],
                        transactions=[projected],
                    ),
                    _match(
                        seed_user, lines=[correct_line], transactions=[settled],
                    ),
                ],
                creations=[_creation(
                    seed_user, swipe,
                    new_envelope=NewEnvelope(
                        name="Amazon",
                        category_id=seed_user["categories"]["Groceries"].id,
                    ),
                )],
            )

            assert outcome.applied_count == 3
            assert outcome.settled_count == 1
            assert outcome.corrected_count == 1
            assert outcome.recorded_count == 1
            assert outcome.envelopes_created == 1
            assert outcome.moved_nothing is False

    def test_only_the_CREATE_arm_names_the_merchant_it_filed_for(
        self, app, db, seed_user,
    ):
        """``AppliedItem.merchant`` is the create door's fact and nobody else's.

        Plan step ``bank_import:X-gx``, finding **BI-495**: the receipt's
        standing-rule offer is a filter over the items that name a merchant,
        so a match, an income or a skip that named one would be asked about
        as spending.  Four arms, one merchant-bearing creation, one
        merchant-less creation: exactly one item carries a
        :class:`FiledMerchant`, and it is the line's own merchant as the door
        read it.  FIRING CONTROL: pass ``merchant=None`` from the creations
        arm and the named line's item reads ``None``.
        """
        with app.app_context():
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date
            matched_line = a_bank_line(
                seed_user, statement, amount="-180.00", posted_on=day,
                merchant="Duke Energy",
            )
            projected = a_transaction(
                seed_user, name="Electricity", amount="180.00",
            )
            named = a_bank_line(
                seed_user, statement, amount="-9.99", posted_on=day,
                sequence_in_group=1, merchant="Amazon",
            )
            nameless = a_bank_line(
                seed_user, statement, amount="-4.50", posted_on=day,
                sequence_in_group=2,
            )
            deposit = a_bank_line(
                seed_user, statement, amount="0.15", posted_on=day,
                sequence_in_group=3, merchant="Dividend Earned",
                description="DIVIDEND EARNED (Dividend Earned)",
            )
            to_skip = a_bank_line(
                seed_user, statement, amount="-1.00", posted_on=day,
                sequence_in_group=4, merchant="Walmart",
            )
            envelope = an_envelope(seed_user)
            db.session.commit()

            outcome = _batch(
                seed_user,
                matches=[_match(
                    seed_user, lines=[matched_line], transactions=[projected],
                )],
                creations=[
                    _creation(seed_user, named, transaction_id=envelope.id),
                    _creation(seed_user, nameless, transaction_id=envelope.id),
                ],
                incomes=[IncomeCreation(line_id=deposit.id)],
                skips=[SkipRequest(line_id=to_skip.id)],
            )

            assert outcome.applied_count == 5, outcome.refused
            by_line = {item.line_ids: item.merchant for item in outcome.applied}
            assert by_line == {
                (matched_line.id,): None,
                (named.id,): FiledMerchant(
                    merchant_id=the_merchant_id(seed_user, "Amazon"),
                    name="Amazon",
                ),
                (nameless.id,): None,
                (deposit.id,): None,
                (to_skip.id,): None,
            }

    def test_a_pass_that_only_CONFIRMS_says_it_moved_nothing(
        self, app, db, seed_user,
    ):
        """An applied item is not the same as a changed record.

        A match on a row already carrying the bank's own day writes no column,
        so a receipt counting applied items would claim work that did not
        happen -- the distinction ``AcceptedMatch`` already draws for one act.
        """
        with app.app_context():
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date
            line = a_bank_line(
                seed_user, statement, amount="-180.00", posted_on=day,
            )
            row = a_transaction(
                seed_user, name="Electricity", amount="180.00",
                status=StatusEnum.DONE, settled_on=day,
            )

            outcome = _batch(seed_user, matches=[
                _match(seed_user, lines=[line], transactions=[row]),
            ])

            assert outcome.applied_count == 1
            assert outcome.moved_nothing is True

    def test_a_pass_that_only_REPRICES_does_NOT_say_it_moved_nothing(
        self, app, db, seed_user,
    ):
        """The receipt was not merely silent about an amount -- it was FALSE.

        Plan step **bank_import:X-f6d-1**, found by adversarial design review
        2026-08-22.  ``_apply_day`` decides its outcome on the DAY, so a
        repricing whose row ALREADY carries the bank's day reports
        ``"unchanged"`` and lands in neither the settled nor the corrected
        tally.  With no count of its own, this pass rewrote what a payment cost
        and the panel rendered *"Nothing moved. Everything that was applied
        confirmed a day you already had."*

        The fixture is the case above with ONE difference -- the figures
        disagree by four cents -- which is what makes it a control on the
        amount rather than on anything else.
        """
        with app.app_context():
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date
            line = a_bank_line(
                seed_user, statement, amount="-180.04", posted_on=day,
                merchant="Duke",
            )
            row = a_transaction(
                seed_user, name="Duke Energy", amount="180.00",
                status=StatusEnum.DONE, settled_on=day,
            )

            # -180.04 bank against a -180.00 row is -0.04, and every match
            # carries the difference it was reviewed against since plan step
            # bank_import:X-gj-1b.
            outcome = _batch(seed_user, matches=[
                _match(
                    seed_user, lines=[line], transactions=[row],
                    residual="-0.04",
                ),
            ])

            assert outcome.applied_count == 1
            assert outcome.settled_count == 0
            assert outcome.corrected_count == 0
            assert outcome.redated_count == 0
            assert outcome.repriced_count == 1
            assert outcome.moved_nothing is False
            assert "corrected the amount on 1 row(s)" in (
                outcome.applied[0].summary
            )
            db.session.expire_all()
            assert settled_figure(row) == Decimal("180.04")

    def test_an_EMPTY_pass_is_not_an_error(self, app, db, seed_user):
        """Ticking nothing and pressing Apply is an ordinary thing to do."""
        with app.app_context():
            outcome = _batch(seed_user)

            assert outcome.applied_count == 0
            assert outcome.refused_count == 0
            assert outcome.moved_nothing is True


class TestWhatIsNOTCaughtPerItem:
    """The other half of the isolation rule, and it has to be asserted.

    A savepoint that swallowed everything would turn a broken ledger invariant
    into one line of a receipt, on a screen whose whole job is to move money
    correctly.  ``ValidationError`` is this project's DESIGNED refusal -- a
    sentence written for the person who submitted the form -- and it is the
    only thing the loop catches.
    """

    def test_a_broken_invariant_fails_the_whole_pass(
        self, app, db, seed_user, monkeypatch,
    ):
        """A broken invariant is a fact about the account, not about an item.

        Raised as the ``RuntimeError`` the walks raise on one (``cash_ledger``'s
        readers, for instance).  It was a ``PostingError`` until plan task
        plan#3132, the stored ledger's error, which no door can raise once its
        writers are not called.
        """
        with app.app_context():
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date
            line = a_bank_line(
                seed_user, statement, amount="-180.00", posted_on=day,
            )
            row = a_transaction(
                seed_user, name="Electricity", amount="180.00",
            )

            def _boom(*_args, **_kwargs):
                raise RuntimeError("the ledger does not balance")

            monkeypatch.setattr(
                statement_match._batch, "accept_match", _boom,
            )

            with pytest.raises(RuntimeError, match="the ledger does not balance"):
                _batch(seed_user, matches=[
                    _match(seed_user, lines=[line], transactions=[row]),
                ])

    def test_a_ValidationError_is_the_ONLY_thing_reported_per_item(
        self, app, db, seed_user, monkeypatch,
    ):
        """The control for the arm above: the designed refusal IS caught."""
        with app.app_context():
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date
            line = a_bank_line(
                seed_user, statement, amount="-180.00", posted_on=day,
            )
            row = a_transaction(
                seed_user, name="Electricity", amount="180.00",
            )

            def _refuse(*_args, **_kwargs):
                raise ValidationError("a designed refusal")

            monkeypatch.setattr(
                statement_match._batch, "accept_match", _refuse,
            )

            outcome = _batch(seed_user, matches=[
                _match(seed_user, lines=[line], transactions=[row]),
            ])

            assert outcome.refused_count == 1
            assert outcome.refused[0].reason == "a designed refusal"


class TestThePassIsOneUnitOfWorkForTheCALLER:
    """The savepoints bound a REFUSAL; they do not commit anything.

    ``apply_reviewed`` does not commit, so a caller that abandons the request
    leaves no trace of a pass that had already "applied" 195 items -- which is
    what makes the route's own "nothing was changed" true on a database error.
    """

    def test_nothing_survives_a_rollback_by_the_caller(
        self, app, db, seed_user,
    ):
        """A released savepoint is still inside the outer transaction."""
        with app.app_context():
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date
            line = a_bank_line(
                seed_user, statement, amount="-180.00", posted_on=day,
            )
            row = a_transaction(
                seed_user, name="Electricity", amount="180.00",
            )

            outcome = _batch(seed_user, matches=[
                _match(seed_user, lines=[line], transactions=[row]),
            ])
            assert outcome.applied_count == 1

            _db.session.rollback()

            assert db.session.query(StatementMatch).count() == 0


class TestTheScopeIsDerivedONCE:
    """The step's own measurement, asserted rather than described.

    12.88 minutes of derivation became 5.80 s because the account is derived
    once per PASS rather than once per ACT.  A later change that gave a door
    its own scope again would restore the cost silently -- every test above
    would still pass, and the only thing that would move is the wall clock.
    """

    def test_the_offer_set_is_built_once_however_many_items_run(
        self, app, db, seed_user, monkeypatch,
    ):
        """The firing control for the whole step."""
        with app.app_context():
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date
            items = []
            for index in range(4):
                line = a_bank_line(
                    seed_user, statement, amount=f"-{100 + index}.00",
                    posted_on=day, sequence_in_group=index,
                )
                row = a_transaction(
                    seed_user, name=f"Bill {index}", amount=f"{100 + index}.00",
                    status=StatusEnum.DONE, settled_on=day,
                )
                items.append(_match(
                    seed_user, lines=[line], transactions=[row],
                ))

            # **The counter goes in AFTER the fixture is staged**, and that is
            # a property of the subject rather than a convenience: what is
            # under test is how many times the BATCH derives the account, and
            # ``_match`` derives one of its own to read the reviewed state a
            # tick carries (plan step ``bank_import:X-f6d-3``) exactly as the
            # SCREEN does.  Counting the screen's derivation as the door's
            # would grade the fixture.
            calls = []
            real = statement_match._scope.candidates_for

            def _counted(account_id, calendar, basis):
                calls.append(account_id)
                return real(account_id, calendar, basis)

            monkeypatch.setattr(
                statement_match._scope, "candidates_for", _counted,
            )

            outcome = _batch(seed_user, matches=items)

            assert outcome.applied_count == 4
            assert calls == [seed_user["account"].id], (
                "the account was derived once per ACT again, which is the "
                "12.88 minutes finding N-306 measures"
            )


def test_a_scope_serves_the_screen_and_the_doors_alike(app, db, seed_user):
    """One derivation, two consumers, and they must agree about the account.

    The route builds a scope, renders the screen from it and applies the pass
    against it.  A reader and a writer disagreeing about what an account holds
    is the class of defect this package's one-scope rule exists to remove.
    """
    with app.app_context():
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, statement, amount="-180.00", posted_on=day,
        )
        a_transaction(
            seed_user, name="Electricity", amount="180.00",
            status=StatusEnum.DONE, settled_on=day + timedelta(days=3),
        )
        scope = a_scope(seed_user)

        review = statement_match.review_set(scope)

        assert len(review.proposals) == 1
        proposal = review.proposals[0]
        outcome = statement_match.apply_reviewed(ReviewedBatch(
            consent=Consent.TICKED,
            incomes=(),
            skips=(),
            matches=(MatchSubmission(
                line_ids=frozenset(
                    bank.line_id for bank in proposal.lines
                ),
                rows=frozenset(
                    statement_match.as_reviewed(row) for row in proposal.rows
                ),
            ),),
            creations=(),
        ), scope)

        assert outcome.applied_count == 1
        assert outcome.corrected_count == 1
        assert line.id in {
            item.line_ids[0] for item in outcome.applied
        }


class TestABatchBooksWhatTheSameActsBookOneAtATime:
    """**The firing control for the whole step**, and it was missing.

    Everything else in this module grades COUNTS, refusal sentences and row
    existence.  None of that can see the thing the step actually risks: one
    derivation is shared across every act, and a candidate's price from that
    derivation feeds exactly one consumer -- ``_accept._reject_unbalanced``,
    which is a money gate.  A stale price admits a match a fresh derivation
    would refuse, or refuses one it would admit, and the difference shows up in
    a FIGURE rather than in a count.

    So this applies the same acts twice, once as a batch against one shared
    scope and once one at a time against a fresh scope each, and compares the
    money to the cent.  Each run is bracketed by its own SAVEPOINT so the
    second starts from the state the first did.

    Found missing by adversarial test-quality review 2026-08-19, which put it
    exactly right: *"That premise is graded by nothing."*
    """

    @staticmethod
    def _money(app_db, seed_user, account):
        """Return every figure the two runs must agree on.

        Not just a balance: a balance can agree while the rows underneath it
        disagree in offsetting ways, so the per-row settle facts travel with
        it.

        Args:
            app_db: The test's ``db`` fixture.
            seed_user: The seeded user bundle.
            account: The account being reviewed.

        Returns:
            A comparable dict.
        """
        ctx = balance_at.BalanceContext.build(seed_user["user"].id)
        period = seed_user["bootstrap_period"]
        days = [
            period.start_date,
            period.start_date + timedelta(days=7),
            last_covered_day(period),
            last_covered_day(period) + timedelta(days=30),
        ]
        # **No surrogate keys.**  A row this pass CREATES gets a sequence
        # value, and a rolled-back sequence does not rewind -- so the two runs
        # differ on ``id`` and on nothing else that matters.  An id is not
        # money; what the two runs must agree on is what each row RECORDS.
        rows = app_db.session.execute(_db.text(
            "SELECT name, settled_on, status_id, estimated_amount"
            "  FROM budget.transactions ORDER BY name, id"
        )).all()
        entries = app_db.session.execute(_db.text(
            "SELECT t.name, e.description, e.amount, e.purchased_on,"
            "       e.settled_on, e.is_credit"
            "  FROM budget.transaction_entries e"
            "  JOIN budget.transactions t ON t.id = e.transaction_id"
            " ORDER BY t.name, e.description, e.amount"
        )).all()
        # The LEDGER, not just the projection.  A balance can agree while the
        # lines underneath it differ, so the ledger is where a re-dated settle
        # leaving money on its old day would show up.
        return {
            "balances": {
                day.isoformat(): str(
                    balance_at.cash_balance_at(account, ctx, day),
                )
                for day in days
            },
            "transactions": [tuple(str(v) for v in row) for row in rows],
            "entries": [tuple(str(v) for v in row) for row in entries],
            "ledger": nets_by_key_and_day(app_db.session, seed_user["scenario"].id),
        }

    @staticmethod
    def _acts(db, seed_user):
        """Stage a pass that exercises all three effects, and return its items.

        One proposal that SETTLES a projected row, one that CORRECTS a settled
        row's day, one that re-dates a PURCHASE, and one line RECORDED into an
        existing envelope -- so the comparison covers every write the door
        performs rather than the cheapest one.
        """
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date
        matches, creations = [], []

        projected = a_transaction(
            seed_user, name="Electricity", amount="180.00",
        )
        settle_line = a_bank_line(
            seed_user, statement, amount="-180.00", posted_on=day,
        )
        matches.append(_match(
            seed_user, lines=[settle_line], transactions=[projected],
        ))

        settled = a_transaction(
            seed_user, name="Water", amount="42.00", status=StatusEnum.DONE,
            settled_on=day + timedelta(days=4),
        )
        correct_line = a_bank_line(
            seed_user, statement, amount="-42.00", posted_on=day,
            sequence_in_group=1,
        )
        matches.append(_match(
            seed_user, lines=[correct_line], transactions=[settled],
        ))

        envelope = a_transaction(
            seed_user, name="Groceries", amount="300.00", is_envelope=True,
        )
        # **Recorded LATER than the bank posted it**, which is the only shape
        # ruling R-FW re-dates: the owner's own assertion that this line IS
        # this purchase refutes a purchase day after the day it cleared.
        late = a_purchase(
            seed_user, envelope, amount="31.00", description="Aldi",
            purchased_on=day + timedelta(days=5),
        )
        redate_line = a_bank_line(
            seed_user, statement, amount="-31.00",
            posted_on=day + timedelta(days=3),
            transaction_on=day + timedelta(days=2), sequence_in_group=2,
        )
        matches.append(_match(
            seed_user, lines=[redate_line], entries=[late],
        ))

        swipe = a_bank_line(
            seed_user, statement, amount="-57.96", posted_on=day,
            sequence_in_group=3,
        )
        creations.append(_creation(
            seed_user, swipe, transaction_id=envelope.id,
        ))
        db.session.flush()
        return tuple(matches), tuple(creations)

    def test_the_two_agree_to_the_cent(self, app, db, seed_user):
        """One shared derivation books what four fresh ones book."""
        with app.app_context():
            from app.models.account import Account  # pylint: disable=import-outside-toplevel

            matches, creations = self._acts(db, seed_user)
            account = db.session.get(Account, seed_user["account"].id)

            batched = _db.session.begin_nested()
            outcome = statement_match.apply_reviewed(
                ReviewedBatch(
                    consent=Consent.TICKED,
                    incomes=(),
                    skips=(),
                    matches=matches, creations=creations,
                ),
                a_scope(seed_user),
            )
            _db.session.flush()
            as_a_batch = self._money(db, seed_user, account)
            batched.rollback()
            # **EXPIRE, or the second run reads the first run's writes.**  A
            # savepoint rollback restores the DATABASE, and SQLAlchemy returns
            # the instance already in its identity map for a known primary key
            # -- so without this the second run saw the purchase the first run
            # had settled and reported "unchanged", booking nothing.  Two
            # REQUESTS have two sessions; this is what models that, and a
            # harness that does not is comparing a fresh run against a stale
            # one.  Measured while writing this test: the two runs differed by
            # exactly the `$31.00` purchase.
            _db.session.expire_all()

            one_at_a_time = _db.session.begin_nested()
            singly_did = []
            for submission in matches:
                singly_did.append(statement_match.accept_match(
                    submission, a_scope(seed_user),
                ))
                _db.session.flush()
            for creation in creations:
                # Its OWN registry per call, because acting one at a time is
                # several REQUESTS and a request is the registry's scope.
                # **This control does NOT measure convergence** and a comment
                # here once claimed it did: its fixture holds a single creation
                # naming an EXISTING envelope, so neither path consults the
                # registry and both create zero envelopes.  Two adversarial
                # reviews measured that independently 2026-08-20.  What does
                # measure it is
                # ``TestConvergingMovesTheSameMoneyAsNotConverging`` below,
                # which needs its own fixture and its own comparison because
                # the row-count comparison here is exactly what convergence is
                # SUPPOSED to change.
                singly_did.append(statement_match.create_purchase_from_line(
                    creation, a_scope(seed_user),
                    _create.MintedEnvelopes.none_yet(), an_answers(seed_user),
                    applied_by_rule=False,
                ))
                _db.session.flush()
            singly = self._money(db, seed_user, account)
            one_at_a_time.rollback()
            _db.session.expire_all()

            assert all(
                getattr(did, "match_id", None) is not None
                for did in singly_did
            ), "an act in the one-at-a-time run did not record a match"

            assert outcome.applied_count == 4, (
                "the pass must APPLY its items, or the comparison is between "
                "two states in which nothing happened"
            )
            # **TWO settles, and the second one is the point.**  The late
            # purchase was never marked as having happened, so the match
            # SETTLES it -- and it is re-dated in the same act, which is
            # exactly why ``redated_count`` cuts across the settled/corrected
            # partition rather than joining it.  A first draft of this fixture
            # expected one settle and one correction and was wrong about the
            # purchase; the counts are the code's, checked against the rows.
            assert outcome.settled_count == 2
            assert outcome.corrected_count == 1
            assert outcome.redated_count == 1
            assert outcome.recorded_count == 1

            assert as_a_batch["balances"] == singly["balances"]
            assert as_a_batch["transactions"] == singly["transactions"]
            assert as_a_batch["entries"] == singly["entries"]
            assert as_a_batch["ledger"] == singly["ledger"]
            # The figures, or that comparison could hold between two empty
            # ledgers.  The bank's day is the seed's assertion day, so Checking's
            # own line nets nothing there (the assertion takes the -279.96 back
            # as a true-up): the money shows on its counter, Electricity 180.00
            # + Water corrected onto it 42.00 + the swipe 57.96 = 279.96 moved
            # off Anchor equity.  Three days on, the purchase re-dated to where
            # it posted.  Water's old day holds nothing.
            cash = linked_key_of(_db.session, seed_user["account"].id)
            equity = account_counter_key(
                LedgerAccountKindEnum.ANCHOR_EQUITY, seed_user["account"].id,
            )
            day = seed_user["bootstrap_period"].start_date
            assert singly["ledger"][(equity, day)] == Decimal("-279.96")
            assert singly["ledger"][(cash, day + timedelta(days=3))] == Decimal("-31.00")
            assert (cash, day + timedelta(days=4)) not in singly["ledger"]


class TestOnePressMintsOneEnvelopePerAnswerPerPeriod:
    """Finding **N-327**, developer ruling 2026-08-20 (plan step X-f6a-4).

    A ``new envelope called X`` answer minted one PER LINE, so a sweep
    fragmented the budget line the policy names.  Measured on the developer's
    own statement: a ``Lowe's`` answer places 4 lines over 3 pay periods, so
    ONE press made 4 envelopes -- two of them in the SAME period -- and the
    next statement made more beside them.

    **The convergence is scoped to one REQUEST**, which is what
    :class:`~app.services.statement_match.MintedEnvelopes` is: the
    cross-statement half is answered by the SUGGESTION, which degrades to a
    ``RECORD_IN`` against an envelope of that name already in the period and is
    printed where the owner can override it.

    **It moves NO money**, which the batch-vs-one-at-a-time control above now
    measures: each purchase carries its own posting day and is its own cash
    movement, so an envelope's own leg is `0.00` whether one envelope holds
    four purchases or four hold one each.
    """

    def test_two_lines_in_ONE_period_share_one_envelope(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL: this is the same-press duplication itself."""
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date
        first = a_bank_line(
            seed_user, statement, amount="-30.00", posted_on=day,
            description="LOWES ONE",
        )
        second = a_bank_line(
            seed_user, statement, amount="-45.00", posted_on=day,
            description="LOWES TWO", sequence_in_group=0,
        )
        category = seed_user["categories"]["Groceries"]
        answer = NewEnvelope(name="Home Improvement", category_id=category.id)

        outcome = _batch(seed_user, creations=[
            _creation(seed_user, first, new_envelope=answer),
            _creation(seed_user, second, new_envelope=answer),
        ])
        db.session.flush()

        assert outcome.applied_count == 2
        assert outcome.recorded_count == 2
        assert outcome.envelopes_created == 1
        envelopes = db.session.query(Transaction).filter_by(
            name="Home Improvement",
        ).all()
        assert len(envelopes) == 1
        assert sorted(entry.amount for entry in envelopes[0].entries) == [
            Decimal("30.00"), Decimal("45.00"),
        ]

    def test_the_shared_envelope_moves_NO_cash_leg_of_its_own(
        self, app, db, seed_user,
    ):
        """MONEY: the second purchase carries its own posting day.

        An envelope's own cash leg is what its purchases have NOT already
        taken (ruling **R-FM**), and every purchase this door writes carries
        the day the bank took it -- so the envelope contributes nothing itself
        whether it holds one purchase or four.  Asserted directly, because
        "the budget stopped fragmenting" would be worth nothing if the money
        moved with it.
        """
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date + timedelta(days=5)
        lines = [
            a_bank_line(
                seed_user, statement, amount=amount, posted_on=day,
                description=f"LOWES {amount}",
            )
            for amount in ("-30.00", "-45.00")
        ]
        category = seed_user["categories"]["Groceries"]
        answer = NewEnvelope(name="Home Improvement", category_id=category.id)
        # Valued AFTER the posting day, never on the anchor's own: an anchor
        # RESETS the ledger for its day, so a balance read there answers the
        # assertion whatever the postings say.
        measured_on = day + timedelta(days=2)
        before = _balance(seed_user, measured_on)

        _batch(seed_user, creations=[
            _creation(seed_user, line, new_envelope=answer) for line in lines
        ])
        db.session.flush()

        assert _balance(seed_user, measured_on) == before - Decimal("75.00")

    def test_lines_in_TWO_periods_get_one_envelope_EACH(
        self, app, db, seed_user,
    ):
        """The key carries the period, because an envelope belongs to one.

        FIRING CONTROL against the over-correction: converging on the NAME
        alone would file a March swipe into an April budget line, which is the
        one thing a pay-period app may never do.
        """
        statement = an_import(seed_user)
        first_day = seed_user["bootstrap_period"].start_date
        later = a_later_period(seed_user).start_date
        one = a_bank_line(
            seed_user, statement, amount="-30.00", posted_on=first_day,
            description="LOWES EARLY",
        )
        two = a_bank_line(
            seed_user, statement, amount="-45.00", posted_on=later,
            description="LOWES LATE",
        )
        category = seed_user["categories"]["Groceries"]
        answer = NewEnvelope(name="Home Improvement", category_id=category.id)

        outcome = _batch(seed_user, creations=[
            _creation(seed_user, one, new_envelope=answer),
            _creation(seed_user, two, new_envelope=answer),
        ])
        db.session.flush()

        assert outcome.applied_count == 2
        assert outcome.envelopes_created == 2
        envelopes = db.session.query(Transaction).filter_by(
            name="Home Improvement",
        ).all()
        assert len({row.pay_period_id for row in envelopes}) == 2

    def test_one_NAME_under_TWO_categories_stays_two_envelopes(
        self, app, db, seed_user,
    ):
        """The key carries the category, and that is not decoration.

        Two answers naming one word under two categories are two budget lines,
        and merging them would file spending under a category the owner did not
        pick -- which is what every spending report groups by.
        """
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date
        one = a_bank_line(
            seed_user, statement, amount="-30.00", posted_on=day,
            description="ONE",
        )
        two = a_bank_line(
            seed_user, statement, amount="-45.00", posted_on=day,
            description="TWO",
        )
        groceries = seed_user["categories"]["Groceries"]
        other = next(
            category for name, category in seed_user["categories"].items()
            if name != "Groceries"
        )

        outcome = _batch(seed_user, creations=[
            _creation(seed_user, one, new_envelope=NewEnvelope(
                name="Shared Name", category_id=groceries.id,
            )),
            _creation(seed_user, two, new_envelope=NewEnvelope(
                name="Shared Name", category_id=other.id,
            )),
        ])
        db.session.flush()

        assert outcome.envelopes_created == 2
        assert db.session.query(Transaction).filter_by(
            name="Shared Name",
        ).count() == 2

    def test_a_REFUSED_creation_leaves_no_envelope_for_the_next_line(
        self, app, db, seed_user,
    ):
        """Ruling **R-FZ**: a refused item leaves nothing behind.

        A creation rolled back inside its own SAVEPOINT must not leave a later
        line pointing at a row that no longer exists -- so the registry is
        written only once the act has returned.  The second line therefore
        mints the envelope the first one failed to.
        """
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date
        made_later = a_bank_line(
            seed_user, statement, amount="-30.00", posted_on=day,
            transaction_on=day + timedelta(days=3), description="REFUSED",
        )
        ordinary = a_bank_line(
            seed_user, statement, amount="-45.00", posted_on=day,
            description="LANDS",
        )
        category = seed_user["categories"]["Groceries"]
        answer = NewEnvelope(name="Home Improvement", category_id=category.id)

        outcome = _batch(seed_user, creations=[
            _creation(seed_user, made_later, new_envelope=answer),
            _creation(seed_user, ordinary, new_envelope=answer),
        ])
        db.session.flush()

        assert outcome.refused_count == 1
        assert outcome.applied_count == 1
        assert outcome.envelopes_created == 1
        envelope = db.session.query(Transaction).filter_by(
            name="Home Improvement",
        ).one()
        assert [entry.amount for entry in envelope.entries] == [
            Decimal("45.00"),
        ]


class TestAConvergedEnvelopeClosesOnTheLatestDayItHolds:
    """Finding from adversarial financial review 2026-08-20.

    A press mints ONE envelope per answer per pay period, and the second line
    to reach it used to leave the container recording the FIRST line's posting
    day.  Measured before the fix: two lines in one period submitted 01-05 then
    01-09 closed the envelope on **2024-01-05 while it held `$45.00` the bank
    did not take until 01-09**, and submitting them the other way round
    recorded 01-09 -- so the close day was whichever line happened to be filed
    first.

    **No figure moved either way** (each purchase carries its own posting day,
    so the envelope's own cash leg is `0.00`), which is why no balance
    assertion could see it.  What was wrong is that a row recorded closing
    before money it holds.  The rule applied is this arc's own for a group:
    a match's day is ``max(posted_on)`` over its lines.
    """

    def _sweep(self, seed_user, days, name="Home Improvement"):
        """Record one line per day in *days*, in that order, under one answer."""
        statement = an_import(seed_user)
        category = seed_user["categories"]["Groceries"]
        answer = NewEnvelope(name=name, category_id=category.id)
        lines = [
            a_bank_line(
                seed_user, statement, amount=amount, posted_on=day,
                description=f"LOWES {amount}",
            )
            for amount, day in days
        ]
        return _batch(seed_user, creations=[
            _creation(seed_user, line, new_envelope=answer) for line in lines
        ])

    def test_it_closes_on_the_LATEST_day_whichever_order_they_arrive(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL, and the order-independence is the point.

        The two submissions differ only in which line is filed first, and the
        recorded close day must not.
        """
        first = seed_user["bootstrap_period"].start_date + timedelta(days=1)
        later = first + timedelta(days=4)

        self._sweep(seed_user, [("-30.00", first), ("-45.00", later)])
        db.session.flush()
        ascending = db.session.query(Transaction).filter_by(
            name="Home Improvement",
        ).one()

        assert ascending.settled_on == later

    def test_the_order_does_not_decide_the_close_day(
        self, app, db, seed_user,
    ):
        """The same two lines, filed newest first."""
        first = seed_user["bootstrap_period"].start_date + timedelta(days=1)
        later = first + timedelta(days=4)

        self._sweep(seed_user, [("-45.00", later), ("-30.00", first)])
        db.session.flush()
        descending = db.session.query(Transaction).filter_by(
            name="Home Improvement",
        ).one()

        assert descending.settled_on == later

    def test_re_closing_it_moves_NO_cash_leg_of_its_own(
        self, app, db, seed_user,
    ):
        """MONEY: re-stamping the close day must not move a figure.

        The envelope settles from its own entries and every purchase carries
        the day the bank took it, so the container contributes nothing itself
        -- before the re-close and after it: a ``purchases`` close has no
        covering movement and is worth nothing of its own (ruling
        **R-BAL81**), and the balance moves by the purchases alone.
        """
        first = seed_user["bootstrap_period"].start_date + timedelta(days=1)
        later = first + timedelta(days=4)
        before = _balance(seed_user, later + timedelta(days=2))

        self._sweep(seed_user, [("-30.00", first), ("-45.00", later)])
        db.session.flush()

        envelope = db.session.query(Transaction).filter_by(
            name="Home Improvement",
        ).one()
        assert envelope.covering_movements == []
        assert status_seam.covered_cash_leg(envelope, envelope.account_id) == Decimal("0.00")
        assert _balance(seed_user, later + timedelta(days=2)) == (
            before - Decimal("75.00")
        )

    def test_an_EXISTING_envelope_keeps_its_own_close_day(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL against over-correcting.

        A row the owner already closed is THEIR record.  Recording a purchase
        into it is the shipped destination arm and it leaves that day alone; a
        rule that re-dated it here would edit the owner's record rather than
        complete this press's own.
        """
        closed_on = seed_user["bootstrap_period"].start_date + timedelta(days=1)
        # Closed FROM ITS PURCHASES through the verb, on the owner's day: the
        # one settled state a new purchase may join (ruling R-FX), and the one
        # a ``purchases`` record is -- no covering movement, its purchases the
        # record (ruling R-BAL78).  This laid the close on bare and flipped
        # its basis by hand, a row no door writes.
        envelope = a_transaction(
            seed_user, name="Groceries", amount="500.00", is_envelope=True,
        )
        a_purchase(
            seed_user, envelope, amount="30.00", description="Kroger",
            purchased_on=closed_on, settled_on=closed_on,
        )
        transaction_service.settle_transaction(
            envelope, settle_day=an_entered_day(closed_on),
        )
        db.session.flush()
        assert status_seam.recorded_settlement(envelope) == status_seam.Settlement(
            None, None,
        )
        assert envelope.covering_movements == []
        statement = an_import(seed_user)
        line = a_bank_line(
            seed_user, statement, amount="-20.00",
            posted_on=closed_on + timedelta(days=5), description="LATER",
        )

        _batch(seed_user, creations=[
            _creation(seed_user, line, transaction_id=envelope.id),
        ])
        db.session.flush()

        db.session.refresh(envelope)
        assert envelope.settled_on == closed_on


class TestConvergingMovesTheSameMoneyAsNotConverging:
    """The money half of finding **N-327**, measured rather than asserted.

    A press mints ONE envelope per answer per pay period.  The claim that the
    convergence is money-neutral was stated in three docstrings and graded by
    nothing until two adversarial reviews said so 2026-08-20 -- the
    batch-versus-one-at-a-time control could not see it (its fixture creates no
    envelope at all), and the direct balance case passes with convergence
    disabled, because two envelopes holding one purchase each and one envelope
    holding two both take the same cash.

    **So this compares the two paths on what convergence must PRESERVE and
    deliberately not on what it must CHANGE.**  Balances, ledger postings and
    the purchases themselves must be identical; the number of budget rows is
    the thing that differs, and comparing it would grade the fixture rather
    than the money.
    """

    def _facts(self, db, seed_user, days):
        """Return the money facts both paths must agree on."""
        account = seed_user["account"]
        ctx = balance_at.BalanceContext.build(seed_user["user"].id)
        entries = _db.session.execute(_db.text(
            "SELECT e.description, e.amount, e.purchased_on, e.settled_on"
            "  FROM budget.transaction_entries e"
            " ORDER BY e.amount, e.settled_on"
        )).all()
        return {
            "balances": {
                day.isoformat(): str(
                    balance_at.cash_balance_at(account, ctx, day),
                )
                for day in days
            },
            "entries": [tuple(str(v) for v in row) for row in entries],
            "ledger": nets_by_key_and_day(_db.session, seed_user["scenario"].id),
        }

    def test_the_two_paths_book_the_same_money(self, app, db, seed_user):
        """FIRING CONTROL for the money claim, on a fixture that converges."""
        statement = an_import(seed_user)
        category = seed_user["categories"]["Groceries"]
        answer = NewEnvelope(name="Home Improvement", category_id=category.id)
        day = seed_user["bootstrap_period"].start_date + timedelta(days=2)
        later = day + timedelta(days=3)
        lines = [
            a_bank_line(
                seed_user, statement, amount="-30.00", posted_on=day,
                description="LOWES ONE",
            ),
            a_bank_line(
                seed_user, statement, amount="-45.00", posted_on=later,
                description="LOWES TWO",
            ),
        ]
        creations = [
            _creation(seed_user, line, new_envelope=answer) for line in lines
        ]
        days = [day, later, later + timedelta(days=7)]
        _db.session.flush()

        converged_run = _db.session.begin_nested()
        outcome = _batch(seed_user, creations=creations)
        _db.session.flush()
        assert outcome.envelopes_created == 1, "the fixture did not converge"
        converged = self._facts(db, seed_user, days)
        converged_run.rollback()
        _db.session.expire_all()

        separate_run = _db.session.begin_nested()
        for creation in creations:
            statement_match.create_purchase_from_line(
                creation, a_scope(seed_user),
                _create.MintedEnvelopes.none_yet(), an_answers(seed_user),
                applied_by_rule=False,
            )
            _db.session.flush()
        assert _db.session.execute(_db.text(
            "SELECT count(*) FROM budget.transactions"
            " WHERE name = 'Home Improvement'"
        )).scalar() == 2, "the un-converged path did not make two envelopes"
        separate = self._facts(db, seed_user, days)
        separate_run.rollback()
        _db.session.expire_all()

        assert converged["balances"] == separate["balances"]
        assert converged["entries"] == separate["entries"]
        assert converged["ledger"] == separate["ledger"]
        # The figures, or that comparison could hold between two empty ledgers:
        # each purchase books its own line on the day its line posted.
        cash = linked_key_of(_db.session, seed_user["account"].id)
        assert separate["ledger"][(cash, day)] == Decimal("-30.00")
        assert separate["ledger"][(cash, later)] == Decimal("-45.00")


class TestThePassHoldsONEAmountBasis:
    """Plan step **X-au-j**, finding **N-309**: the derivations are the PASS's.

    An :class:`~app.services.cash_ledger.AmountBasis` holds the owner's live
    derivations -- the paycheck engine run over the whole pay-period set, and
    each loan's P&I, payment day and escrow history.  ``amount_basis``'s own
    docstring says calling those per row is finding **N-228**, and N-309
    measured this pass doing exactly that: **609 salary-pricing and 609
    loan-pricing constructions** over 825 candidates, `4.7 s` to render, with
    the accept door paying it all again.

    It is :class:`TestTheScopeIsDerivedONCE` one column over and it fails the
    same way: a later change that let a producer build its own would restore
    the cost in SILENCE, because every figure would still be right.  Only the
    wall clock moves, which is why the count is asserted and the timing is not.
    """

    def test_one_basis_serves_every_candidate_the_pass_prices(
        self, app, db, seed_user, monkeypatch,
    ):
        """The firing control: ONE construction, however many rows are priced.

        **The rows are PROJECTED deliberately, and that is what makes this
        control sharp.**  A settled row is valued at its covering movement
        (``status_seam.covered_cash_leg``, ruling **R-BAL81**) and never
        reaches the resolver, so a pass of settled rows builds one basis
        whether or not this step shipped -- the assertion would hold over a
        broken tree.  Each of these prices.
        """
        with app.app_context():
            for index in range(8):
                a_transaction(
                    seed_user, name=f"Bill {index}",
                    amount=f"{100 + index}.00",
                )
            _db.session.commit()

            built = count_amount_bases(monkeypatch)
            scope = statement_match.ReviewScope.build(
                seed_user["user"].id, seed_user["account"].id,
            )

            assert len(scope.candidates.rows) >= 8, (
                "the pass must price several rows -- otherwise one basis and "
                "one per row are the same number and this grades nothing"
            )
            assert len(built) == 1, (
                f"the pass built {len(built)} amount bases for "
                f"{len(scope.candidates.rows)} candidates; X-au-j makes it one"
            )
            assert built == [
                (seed_user["user"].id, seed_user["scenario"].id),
            ]

    def test_the_accept_door_prices_against_the_PASS_basis(
        self, app, db, seed_user, monkeypatch,
    ):
        """And the accept door builds none of its own either.

        ``repriced`` re-derives every row an act names -- that is finding
        N-306's fix and it stays -- but re-deriving a ROW is not re-deriving
        the OWNER: N-309's measurement says the accept door paid for the whole
        salary and loan derivation again, per act, on top of the pass that had
        just built one.  Asserted separately from the scope build above,
        because the two are different callers and a change could restore the
        cost at either.
        """
        with app.app_context():
            statement = an_import(seed_user)
            day = seed_user["bootstrap_period"].start_date
            items = []
            for index in range(4):
                line = a_bank_line(
                    seed_user, statement, amount=f"-{100 + index}.00",
                    posted_on=day, sequence_in_group=index,
                )
                # PROJECTED, for the reason the control above states: a
                # settled row is valued from its record and never reaches the
                # resolver, so a batch of settled rows would build one basis
                # whether or not this step shipped.  A first draft of THIS
                # case used settled rows and passed against a planted defect.
                row = a_transaction(
                    seed_user, name=f"Bill {index}", amount=f"{100 + index}.00",
                )
                items.append(_match(
                    seed_user, lines=[line], transactions=[row],
                ))

            built = count_amount_bases(monkeypatch)
            outcome = _batch(seed_user, matches=items)

            assert outcome.applied_count == 4
            # ONE for the pass, plus ONE per SETTLED ROW -- and the second term
            # is a different question that this step does not answer.  A settle
            # is a single-row WRITE and resolves its own basis by design
            # (``transaction_service._settle.settle_transaction``: "ONE
            # settle resolves ONE basis"); what X-au-j fixes is the READ under
            # it, which used to build one per row PRICED on top of all of that.
            #
            # **Counted in ROWS, not matches**, because those are two different
            # numbers and only the fixture makes them equal: each match here
            # names exactly one transaction.  A first version asserted
            # ``1 + outcome.applied_count`` and would have gone silently wrong
            # -- while still printing the per-settle rule -- the moment a case
            # gave one match two rows (adversarial review 2026-08-20).
            settled_rows = sum(
                1 for item in items for row in item.rows
                if row.kind is statement_match.RowKind.TRANSACTION
            )
            assert settled_rows == 4, "the fixture pins one row per match"
            assert len(built) == 1 + settled_rows, (
                f"{len(built)} amount bases were built settling "
                f"{settled_rows} rows; the pass holds ONE for every row it "
                "prices, and each settle resolves its own"
            )


class TestTheReceiptSaysWhichWayTheMoneyWENT:
    """A refund's receipt must not say the bank TOOK it.

    Plan step ``bank_import:X-gj-2b``, ruling **R-II**.  Under ruling **R-GH**
    the pass receipt is the ONLY notice the owner gets for money an act moved,
    so a sentence that inverts the direction is the mis-describing-text-over-a-
    real-movement shape this package has already paid for once.

    **Measured wrong before the fix**: ``Recorded $-42.00 your bank took on
    ...`` -- the wrong SIGN and the wrong VERB in one sentence, and it
    contradicted ``AppliedItem.amount`` on the same item, which negates onto
    the bank's convention. Nothing caught it because ``_story()`` in
    ``test_filing.py`` captures counts and reasons and never the summary.

    **Both directions, because the verb is derived from the sign** -- a fix
    that hard-coded *gave back* would pass a refund-only case.
    """

    def _applied(self, seed_user, line, envelope):
        """Apply one creation and return its single AppliedItem."""
        outcome = _batch(seed_user, creations=[
            _creation(seed_user, line, transaction_id=envelope.id),
        ])
        assert outcome.applied_count == 1, outcome.refused
        return outcome.applied[0]

    def test_a_REFUND_reads_as_money_the_bank_GAVE_BACK(
        self, app, db, seed_user,
    ):
        """The line the merchant credit arrives on."""
        with app.app_context():
            envelope = a_transaction(
                seed_user, name="Amazon", amount="0.00", is_envelope=True,
            )
            a_rule(seed_user, "Amazon", template_id=envelope.template_id)
            line = a_bank_line(
                seed_user, an_import(seed_user), amount="42.00",
                merchant="Amazon",
                posted_on=seed_user["bootstrap_period"].start_date,
            )
            db.session.commit()

            item = self._applied(seed_user, line, envelope)

            assert "gave back" in item.summary
            assert "took" not in item.summary
            # The FIGURE agrees with the verb, and with the bank's own sign
            # on the same item.
            assert "$42.00" in item.summary
            assert "$-42.00" not in item.summary
            assert item.amount == Decimal("42.00")

    def test_an_ORDINARY_swipe_still_reads_as_money_the_bank_TOOK(
        self, app, db, seed_user,
    ):
        """The control, so the verb is derived and not hard-coded."""
        with app.app_context():
            envelope = a_transaction(
                seed_user, name="Groceries", amount="60.00", is_envelope=True,
            )
            line = a_bank_line(
                seed_user, an_import(seed_user), amount="-10.89",
                posted_on=seed_user["bootstrap_period"].start_date,
            )
            db.session.commit()

            item = self._applied(seed_user, line, envelope)

            assert "took" in item.summary
            assert "gave back" not in item.summary
            assert "$10.89" in item.summary
            assert item.amount == Decimal("-10.89")


class TestAPassCarriesTheSKIPVerb:
    """Plan step ``bank_import:X-gj-4b``, ruling **bank_import:R-JG**.

    The fourth act class a reviewed pass can hold, and the only one that
    writes no money at all.  What these cases hold is that the receipt tells
    the truth about it: an act that moved nothing still CHANGED something, a
    repeat changed nothing and says so, and the one sentence on this screen
    that is easiest to misread -- *this closed no difference* -- is actually
    on the receipt.
    """

    def test_it_records_the_decision_and_counts_it(self, app, db, seed_user):
        """The ordinary press."""
        an_envelope(seed_user)
        line = an_unexplained_outflow(
            seed_user, merchant="Kroger", amount="-41.18",
        )
        db.session.commit()

        outcome = _batch(seed_user, skips=(SkipRequest(line_id=line.id),))

        assert outcome.skipped_count == 1
        assert outcome.refused_count == 0
        assert _db.session.query(StatementLineSkip).count() == 1

    def test_a_skip_only_pass_does_NOT_render_nothing_moved(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL for :attr:`BatchOutcome.moved_nothing`.

        Drop ``skipped_count`` from that predicate and this fails: the receipt
        renders *"Nothing moved.  Everything that was applied confirmed a day
        you already had"*, whose second sentence is false of an act that
        confirms no day at all.  The skip moved no MONEY, which is why the
        arithmetic below asserts the difference is untouched -- the two facts
        are both true and the receipt has to carry both.
        """
        an_envelope(seed_user)
        line = an_unexplained_outflow(
            seed_user, merchant="Kroger", amount="-41.18",
        )
        db.session.commit()

        outcome = _batch(seed_user, skips=(SkipRequest(line_id=line.id),))

        assert not outcome.moved_nothing
        assert outcome.settled_count == 0
        assert outcome.recorded_count == 0
        assert outcome.deposited_count == 0
        assert outcome.residual_total == Decimal("0.00")

    def test_the_receipt_names_the_figure_the_day_and_what_it_did_NOT_do(
        self, app, db, seed_user,
    ):
        """Ruling **R-GD(a)**: a consent naming a count and no figure is a
        consent to an amount nobody stated.

        The line is `-$41.18` on the bank's convention, so the sentence states
        the magnitude and says the bank TOOK it.
        """
        an_envelope(seed_user)
        line = an_unexplained_outflow(
            seed_user, merchant="Kroger", amount="-41.18",
        )
        db.session.commit()

        outcome = _batch(seed_user, skips=(SkipRequest(line_id=line.id),))

        (item,) = outcome.applied
        assert item.line_ids == (line.id,)
        assert "$41.18" in item.summary
        # The DAY, which this case's own name promises and an earlier draft
        # did not grade (adversarial review 2026-09-04).
        assert str(line.posted_on) in item.summary
        assert "took" in item.summary
        assert "closes no difference" in item.summary
        # The BANK's own signed figure, unflipped: a skip has no app-side
        # amount to convert from.
        assert item.amount == Decimal("-41.18")

    def test_a_REPEAT_is_applied_reports_itself_and_counts_NOTHING(
        self, app, db, seed_user,
    ):
        """``was_already_skipped`` is a fact about a PRESS, not about a row.

        A second press states the same decision, so it is an outcome rather
        than a refusal -- and it wrote nothing, so counting it would claim a
        record changed when none did.
        """
        an_envelope(seed_user)
        line = an_unexplained_outflow(
            seed_user, merchant="Kroger", amount="-41.18",
        )
        db.session.commit()
        _batch(seed_user, skips=(SkipRequest(line_id=line.id),))

        again = _batch(seed_user, skips=(SkipRequest(line_id=line.id),))

        assert again.refused_count == 0
        assert again.applied_count == 1
        assert again.skipped_count == 0
        assert again.already_skipped_count == 1
        assert "already recorded" in again.applied[0].summary
        assert _db.session.query(StatementLineSkip).count() == 1
        # FIRING CONTROL for the second counter (adversarial review
        # 2026-09-04): with only ``skipped_count`` in ``moved_nothing`` this
        # pass rendered *"Nothing moved.  Everything that was applied
        # confirmed a day you already had"* over an act that confirmed no day.
        assert not again.moved_nothing

    def test_a_line_the_door_refuses_is_ONE_refusal_and_the_rest_land(
        self, app, db, seed_user,
    ):
        """The savepoint policy, on the fourth act class.

        A card payment (**R-JI**) and an ordinary line in one press: the first
        is refused in the door's own words, the second still lands.
        """
        an_envelope(seed_user)
        barred = an_unexplained_outflow(
            seed_user, merchant="Capital One Credit Card", amount="-793.23",
            source_category="Financial Services/Credit Card Payment",
        )
        ordinary = an_unexplained_outflow(
            seed_user, merchant="Kroger", amount="-41.18",
        )
        db.session.commit()

        outcome = _batch(seed_user, skips=(
            SkipRequest(line_id=barred.id),
            SkipRequest(line_id=ordinary.id),
        ))

        assert outcome.skipped_count == 1
        assert outcome.refused_count == 1
        assert "another account you hold" in outcome.refused[0].reason
        assert _db.session.query(StatementLineSkip).count() == 1


class TestARuleConsentedPassMayNotSkip:
    """Plan step ``bank_import:X-gj-4b``, and the refusal is the STORE's.

    Ruling **R-GH** would permit it on its own terms -- a skip creates a
    decision and modifies no row the owner made by hand.  What refuses it is
    that ``budget.statement_line_skips`` carries no ``applied_by_rule``
    column, so nothing could record that a rule performed the act.
    """

    def test_it_is_refused_at_the_VALUE(self, app, db, seed_user):
        """A programming error, so a ``ValueError`` and not a refusal
        sentence: no wire value reaches the consent field."""
        with pytest.raises(ValueError) as caught:
            ReviewedBatch(
                consent=Consent.STANDING_RULE,
                matches=(), creations=(), incomes=(),
                skips=(SkipRequest(line_id=1),),
            )

        assert "applied_by_rule" in str(caught.value)

    def test_a_TICKED_pass_carrying_the_same_skip_is_legal(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL: the refusal is keyed on the CONSENT and not on the
        act class, so the identical batch under a tick constructs."""
        batch = ReviewedBatch(
            consent=Consent.TICKED,
            matches=(), creations=(), incomes=(),
            skips=(SkipRequest(line_id=1),),
        )

        assert batch.item_count == 1
